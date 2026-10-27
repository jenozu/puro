"""Local persistent order journal (pre-carrier MVP).

All writes are explicit SQLite transactions. Shipped/unknown-result states
cannot be reimported into a fresh ready queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .orders import ParsedImport, review_errors


PROTECTED = {
    "creating", "needs_reconciliation", "created", "label_pending",
    "label_ready", "shipped", "voided",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS imports (
                digest TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                order_count INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                doc_entry TEXT PRIMARY KEY,
                doc_num TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                data_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'needs_review',
                import_digest TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                FOREIGN KEY(import_digest) REFERENCES imports(digest)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_entry TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            # Reserved carrier journal; no create-shipment code is wired yet.
            db.execute("""CREATE TABLE IF NOT EXISTS shipments (
                id TEXT PRIMARY KEY,
                doc_entry TEXT NOT NULL,
                environment TEXT NOT NULL,
                status TEXT NOT NULL,
                shipment_pin TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(doc_entry) REFERENCES orders(doc_entry)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS shipment_attempts (
                id TEXT PRIMARY KEY,
                shipment_id TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(shipment_id) REFERENCES shipments(id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS shipment_pieces (
                shipment_id TEXT NOT NULL,
                piece_no INTEGER NOT NULL,
                piece_pin TEXT,
                PRIMARY KEY(shipment_id, piece_no),
                FOREIGN KEY(shipment_id) REFERENCES shipments(id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                shipment_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(shipment_id) REFERENCES shipments(id)
            )""")

    def import_orders(self, parsed: ParsedImport, filename: str) -> tuple[int, int]:
        """Insert new source orders; NEVER overwrite existing order work or status.

        Returns (new, previously_existing). Existing orders can be corrected in
        the review UI; future changes require an explicit reconcile/update feature.
        """
        inserted = existing = 0
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO imports(digest, filename, created_at, order_count) "
                "VALUES(?,?,?,?)",
                (parsed.sha256, filename, _now(), len(parsed.orders)),
            )
            for order in parsed.orders:
                key = order["doc_entry"]
                if db.execute("SELECT 1 FROM orders WHERE doc_entry=?", (key,)).fetchone():
                    existing += 1
                    continue
                db.execute(
                    """INSERT INTO orders(doc_entry, doc_num, customer_name,
                       data_json, status, import_digest, updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        key, order["doc_num"], order["customer_name"],
                        json.dumps(order, ensure_ascii=False),
                        "needs_review", parsed.sha256, _now(),
                    ),
                )
                db.execute(
                    "INSERT INTO audit_events(doc_entry, action, occurred_at) VALUES(?,?,?)",
                    (key, "imported", _now()),
                )
                inserted += 1
        return inserted, existing

    def list_orders(self, query: str = "") -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM orders ORDER BY doc_num DESC LIMIT 5000"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["data"] = json.loads(item.pop("data_json"))
            if query and query.casefold() not in " ".join(
                (item["doc_entry"], item["doc_num"],
                 item["customer_name"], item["data"].get("card_code", ""))
            ).casefold():
                continue
            out.append(item)
        return out

    def get_order(self, doc_entry: str) -> dict[str, Any] | None:
        with self._connect() as db:
            r = db.execute(
                "SELECT * FROM orders WHERE doc_entry=?", (str(doc_entry),)
            ).fetchone()
        if r is None:
            return None
        result = dict(r)
        result["data"] = json.loads(result.pop("data_json"))
        return result

    def save_draft(self, doc_entry: str, values: dict[str, Any]) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT data_json, status FROM orders WHERE doc_entry=?",
                (str(doc_entry),),
            ).fetchone()
            if row is None:
                raise ValueError("Order not found")
            if row["status"] in PROTECTED:
                raise ValueError("This order is locked by a shipment record")
            current = json.loads(row["data_json"])
            allowed = {
                "ship_to_name", "street_no", "street", "building", "address2",
                "address3", "city", "province", "postal_code", "country",
                "phone", "weight_lb", "length_in", "width_in", "height_in",
                "pieces", "service", "payer", "receiver_account"
            }
            current.update({k: v for k, v in values.items() if k in allowed})
            db.execute(
                """UPDATE orders SET data_json=?, status='needs_review',
                   updated_at=?, reviewed_at=NULL WHERE doc_entry=?""",
                (json.dumps(current, ensure_ascii=False), _now(), str(doc_entry)),
            )
            db.execute(
                "INSERT INTO audit_events(doc_entry, action, occurred_at) VALUES(?,?,?)",
                (str(doc_entry), "draft_updated_review_revoked", _now()),
            )

    def confirm_ready(self, doc_entry: str) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT data_json, status FROM orders WHERE doc_entry=?",
                (str(doc_entry),),
            ).fetchone()
            if row is None:
                raise ValueError("Order not found")
            if row["status"] in PROTECTED:
                raise ValueError("Cannot re-approve a shipment in progress or complete")
            order = json.loads(row["data_json"])
            errors = review_errors(order)
            if errors:
                raise ValueError("; ".join(errors))
            db.execute(
                """UPDATE orders SET status='ready', reviewed_at=?, updated_at=?
                   WHERE doc_entry=?""",
                (_now(), _now(), str(doc_entry)),
            )
            db.execute(
                "INSERT INTO audit_events(doc_entry, action, occurred_at) VALUES(?,?,?)",
                (str(doc_entry), "review_confirmed_ready", _now()),
            )

    def summary(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}
