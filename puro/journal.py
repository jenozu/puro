"""Crash-resistant Purolator transaction journal.

No API client is called from this module. The caller must persist an attempt
BEFORE sending a non-idempotent CreateShipment request. Unknown network results
must be reconciled rather than automatically retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import sqlite3
import tempfile
import uuid
from typing import Iterator

from .store import Store


def _time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ShipmentStateError(ValueError):
    pass


class ShipmentJournal:
    def __init__(self, db_path: str | Path):
        self.store = Store(db_path)
        self._migrate()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        db = self.store._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _migrate(self) -> None:
        """Incrementally upgrade legacy v0.1 DB (existing placeholder tables)."""
        with self._write() as db:
            # SQLite's initial Store._init created these base tables.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shipments_order "
                       "ON shipments(doc_entry)")
            for table, column, definition in (
                ("shipments", "updated_at", "TEXT"),
                ("shipments", "last_error", "TEXT"),
                ("shipments", "request_digest", "TEXT"),
                ("shipment_attempts", "correlation_id", "TEXT"),
                ("shipment_attempts", "last_error", "TEXT"),
            ):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_attempts_shipment "
                       "ON shipment_attempts(shipment_id)")
            db.execute("PRAGMA user_version=2")

    def claim(self, doc_entry: str, *, environment: str,
              request_digest: str) -> tuple[str, str]:
        """One operator-authorized order -> one carrier attempt, atomically.

        This deliberately forbids automatic repeat attempts even if the
        previous carrier response was lost. v0.1 groups one shipment/order.
        """
        if environment not in {"development", "production"}:
            raise ShipmentStateError("Invalid Purolator environment")
        if not request_digest or len(request_digest) < 8:
            raise ShipmentStateError("A request digest is required")
        with self._write() as db:
            order = db.execute(
                "SELECT status FROM orders WHERE doc_entry=?", (str(doc_entry),)
            ).fetchone()
            if order is None:
                raise ShipmentStateError("Order does not exist")
            if order["status"] != "ready":
                raise ShipmentStateError("Order is not ready or has already been submitted")
            if db.execute(
                "SELECT id FROM shipments WHERE doc_entry=?", (str(doc_entry),)
            ).fetchone():
                raise ShipmentStateError("Shipment already exists; reconcile instead of retry")
            shipment_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
            db.execute(
                """INSERT INTO shipments
                (id,doc_entry,environment,status,created_at,updated_at,request_digest)
                VALUES (?,?,?,'creating',?,?,?)""",
                (shipment_id, str(doc_entry), environment,
                 _time(), _time(), request_digest),
            )
            db.execute(
                """INSERT INTO shipment_attempts
                (id,shipment_id,state,started_at,correlation_id)
                VALUES (?,?,'creating',?,?)""",
                (attempt_id, shipment_id, _time(), attempt_id),
            )
            db.execute(
                "UPDATE orders SET status='creating',updated_at=? WHERE doc_entry=?",
                (_time(), str(doc_entry)),
            )
            db.execute(
                "INSERT INTO audit_events(doc_entry,action,occurred_at) VALUES(?,?,?)",
                (str(doc_entry), "shipment_claimed", _time()),
            )
            return shipment_id, attempt_id

    def _transition(self, shipment_id: str, expected: str, target: str,
                    *, pin: str | None = None, error: str | None = None,
                    attempt_state: str | None = None) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT doc_entry,status FROM shipments WHERE id=?", (shipment_id,)
            ).fetchone()
            if not row or row["status"] != expected:
                raise ShipmentStateError(f"Expected shipment state {expected}")
            db.execute(
                """UPDATE shipments SET status=?,shipment_pin=COALESCE(?,shipment_pin),
                last_error=?,updated_at=? WHERE id=?""",
                (target, pin, error, _time(), shipment_id),
            )
            db.execute(
                "UPDATE orders SET status=?,updated_at=? WHERE doc_entry=?",
                (target, _time(), row["doc_entry"]),
            )
            if attempt_state:
                db.execute(
                    """UPDATE shipment_attempts SET state=?,completed_at=?,last_error=?
                    WHERE shipment_id=? AND state='creating'""",
                    (attempt_state, _time(), error, shipment_id),
                )
            db.execute(
                "INSERT INTO audit_events(doc_entry,action,occurred_at) VALUES(?,?,?)",
                (row["doc_entry"], f"shipment_{target}", _time()),
            )

    def mark_unknown(self, shipment_id: str, reason_code: str = "unknown_response",
                     shipment_pin: str | None = None) -> None:
        """Network uncertainty => never automatically retry; keep known PIN."""
        self._transition(shipment_id, "creating", "needs_reconciliation",
                         pin=shipment_pin, error=reason_code, attempt_state="unknown")

    def mark_rejected(self, shipment_id: str, code: str) -> None:
        """Use only for a definitive carrier rejection (not a timeout)."""
        self._transition(shipment_id, "creating", "failed",
                         error=code, attempt_state="rejected")

    def mark_created(self, shipment_id: str, shipment_pin: str,
                     piece_pins: list[str]) -> None:
        if not shipment_pin or not piece_pins or any(not p for p in piece_pins):
            raise ShipmentStateError("Shipment and piece PINs are required")
        with self._write() as db:
            row = db.execute(
                "SELECT doc_entry,status FROM shipments WHERE id=?", (shipment_id,)
            ).fetchone()
            if not row or row["status"] not in {"creating", "needs_reconciliation"}:
                raise ShipmentStateError("Shipment is not awaiting carrier confirmation")
            db.execute(
                "UPDATE shipments SET status='created',shipment_pin=?,updated_at=? WHERE id=?",
                (str(shipment_pin), _time(), shipment_id),
            )
            for i, pin in enumerate(piece_pins, 1):
                db.execute(
                    """INSERT INTO shipment_pieces(shipment_id,piece_no,piece_pin)
                    VALUES(?,?,?)""",
                    (shipment_id, i, str(pin)),
                )
            db.execute(
                "UPDATE orders SET status='created',updated_at=? WHERE doc_entry=?",
                (_time(), row["doc_entry"]),
            )
            db.execute(
                """UPDATE shipment_attempts SET state='success',completed_at=?
                WHERE shipment_id=? AND state IN ('creating','unknown')""",
                (_time(), shipment_id),
            )
            db.execute(
                "INSERT INTO audit_events(doc_entry,action,occurred_at) VALUES(?,?,?)",
                (row["doc_entry"], "shipment_created", _time()),
            )

    def mark_label_pending(self, shipment_id: str) -> None:
        self._transition(shipment_id, "created", "label_pending")

    def archive_pdf(self, shipment_id: str, data: bytes, labels_dir: str | Path) -> Path:
        """Atomic local file write followed by journal record.

        A bare carrier success is NOT enough to mark a PDF ready.
        """
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            raise ShipmentStateError("Carrier document is not a complete PDF")
        folder = Path(labels_dir)
        folder.mkdir(parents=True, exist_ok=True)
        with self._write() as db:
            row = db.execute(
                "SELECT doc_entry,status FROM shipments WHERE id=?", (shipment_id,)
            ).fetchone()
            if not row or row["status"] not in {"created", "label_pending"}:
                raise ShipmentStateError("Shipment is not awaiting documents")
            target = folder / f"{shipment_id}.pdf"
            fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            db.execute(
                """INSERT INTO documents(id,shipment_id,filename,checksum,created_at)
                VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), shipment_id, str(target), sha256(data).hexdigest(), _time()),
            )
            db.execute(
                "UPDATE shipments SET status='label_ready',updated_at=? WHERE id=?",
                (_time(), shipment_id),
            )
            db.execute(
                "UPDATE orders SET status='label_ready',updated_at=? WHERE doc_entry=?",
                (_time(), row["doc_entry"]),
            )
            db.execute(
                "INSERT INTO audit_events(doc_entry,action,occurred_at) VALUES(?,?,?)",
                (row["doc_entry"], "label_archived", _time()),
            )
        return target

    def get(self, shipment_id: str) -> dict:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
            if row is None:
                raise ShipmentStateError("Shipment not found")
            pieces = db.execute(
                "SELECT piece_no,piece_pin FROM shipment_pieces WHERE shipment_id=? ORDER BY piece_no",
                (shipment_id,),
            ).fetchall()
            documents = db.execute(
                "SELECT filename,checksum FROM documents WHERE shipment_id=?",
                (shipment_id,),
            ).fetchall()
            attempts = db.execute(
                "SELECT state,correlation_id FROM shipment_attempts WHERE shipment_id=?",
                (shipment_id,),
            ).fetchall()
            return {
                **dict(row),
                "pieces": [dict(p) for p in pieces],
                "documents": [dict(d) for d in documents],
                "attempts": [dict(a) for a in attempts],
            }
