#!/usr/bin/env python3
"""Fail public CI when tracked files look like secrets, SAP exports or labels.

This is a guardrail, not a comprehensive secret scanner. Use organization's
approved secret-scanning tools before deploying or merging sensitive changes.
"""
from pathlib import PurePosixPath
import subprocess

BLOCKED_SUFFIXES = {
    ".xlsx", ".xls", ".xlsm", ".csv", ".zip", ".wsdl", ".xsd",
    ".pdf", ".sqlite", ".sqlite3", ".db", ".pem", ".key",
}
BLOCKED_DIRS = {"private", "local_data", "labels", "exports", "uploads", "backups"}


def check() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True,
    )
    paths = [
        PurePosixPath(p.decode("utf-8", errors="replace"))
        for p in result.stdout.split(b"\0") if p
    ]
    forbidden = []
    for path in paths:
        parts = set(path.parts)
        if (
            path.name == ".env"
            or (path.name.startswith(".env.") and path.name != ".env.example")
            or path.suffix.lower() in BLOCKED_SUFFIXES
            or bool(parts & BLOCKED_DIRS)
        ):
            forbidden.append(str(path))
    if forbidden:
        raise SystemExit(
            "Private/proprietary files tracked in public repo: "
            + ", ".join(sorted(forbidden))
        )
    print(f"Public-repository file privacy check passed ({len(paths)} tracked files).")


if __name__ == "__main__":
    check()
