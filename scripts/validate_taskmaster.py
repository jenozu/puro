#!/usr/bin/env python3
"""Validate the committed Task Master backlog and project planning contract.

Uses Python's standard library only; safe to run in public CI without credentials.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / ".taskmaster" / "tasks" / "tasks.json"
CONFIG_PATH = ROOT / ".taskmaster" / "config.json"
MCP_PATH = ROOT / ".cursor" / "mcp.json"

REQUIRED = [
    "README.md",
    "AGENTS.md",
    "docs/MVP.md",
    "docs/PRD.md",
    "docs/BUILD_PLAN.md",
    "docs/SAP_EXPORT.md",
    "docs/SECURITY.md",
    "docs/TASKMASTER.md",
    ".taskmaster/docs/prd.txt",
    ".taskmaster/tasks/tasks.json",
    ".taskmaster/config.json",
    ".cursor/mcp.json",
    ".env.example",
    ".gitignore",
]


def run() -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    if missing:
        raise ValueError(f"Missing required planning files: {missing}")

    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    mcp = json.loads(MCP_PATH.read_text(encoding="utf-8"))

    tasks = data.get("master", {}).get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Expected nonempty Task Master 'master.tasks' list")

    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)) or any(type(i) is not int for i in ids):
        raise ValueError("Task IDs must be unique integers")

    for task in tasks:
        ident = task["id"]
        required_fields = (
            "title", "description", "details", "testStrategy",
            "status", "priority", "dependencies", "subtasks"
        )
        if any(field not in task for field in required_fields):
            raise ValueError(f"Task {ident} missing required project fields")
        if task["status"] not in {
            "pending", "in-progress", "done", "review", "deferred", "cancelled"
        }:
            raise ValueError(f"Task {ident} has invalid status")
        if task["priority"] not in {"high", "medium", "low"}:
            raise ValueError(f"Task {ident} has invalid priority")
        if not isinstance(task["dependencies"], list):
            raise ValueError(f"Task {ident} dependencies must be a list")
        for dep in task["dependencies"]:
            if dep not in ids or dep == ident:
                raise ValueError(f"Task {ident} has unknown/self-dependency {dep}")
            if dep >= ident:
                raise ValueError(f"Task {ident}: dependencies must precede task ID")

    if "models" not in config or "global" not in config:
        raise ValueError("Invalid Task Master model/global configuration")
    if "task-master-ai" not in mcp.get("mcpServers", {}):
        raise ValueError("Missing Cursor MCP Task Master definition")

    prd = (ROOT / ".taskmaster" / "docs" / "prd.txt").read_text(
        encoding="utf-8"
    )
    for term in ("Ship To", "CreateShipment", "GetDocuments", "SQLite"):
        if term not in prd:
            raise ValueError(f"Task Master PRD missing essential term: {term}")

    print(f"Task Master project validation passed: {len(tasks)} tasks, no secrets needed.")


if __name__ == "__main__":
    run()
