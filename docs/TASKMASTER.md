# Task Master AI — Puro project workflow

This repository uses [Task Master AI](https://github.com/eyaltoledano/claude-task-master) for developer task planning, dependency tracking and optional AI-assisted implementation. **It is NOT required to run the shipping application.**

## Already committed

| File | Purpose |
| --- | --- |
| `.taskmaster/docs/prd.txt` | Task Master PRD input, grounded in [docs/PRD.md](PRD.md) |
| `.taskmaster/tasks/tasks.json` | **13 seeded tasks** with status, priority, dependencies, implementation details and acceptance tests |
| `.taskmaster/config.json` | Task Master project configuration (no secrets); defaults to the optional Codex CLI provider |
| `.cursor/mcp.json` | Cursor MCP server pointing to `npx -y task-master-ai` |
| `docs/BUILD_PLAN.md` | Human-readable execution order |
| `AGENTS.md` | Engineering rules for an AI coding agent |

You **do not** need to re-run `task-master init` to start from this tracked PRD/backlog. Running it in an existing project may overwrite scaffold files; back up/commit first.

## Install and inspect the seeded backlog (Windows PowerShell)

Node.js/npm is required for the **development tool**, not for the eventual Python shipping application.

```powershell
git clone https://github.com/jenozu/puro.git
cd puro
npm install -g task-master-ai
task-master list
task-master next
task-master show 1
```

If the CLI cannot see the project, ensure you're in the repository root and `.taskmaster/tasks/tasks.json` exists. Task status inspection may be possible without an AI provider; AI generation requires a supported model.

## Model configuration

The checked-in config uses an **optional default Codex CLI provider** with models listed in upstream Task Master model documentation. This requires a working, authenticated local Codex CLI; it does **not** automatically become authenticated merely because the user has a ChatGPT subscription.

If using a different provider, run `task-master models --setup` and choose a supported provider/model that you have authorized locally. Provider secrets can be placed in your **local, ignored** `.env` or private editor MCP settings. Never commit provider keys or Purolator credentials.

For Cursor, open this folder, enable the `task-master-ai` MCP server from `.cursor/mcp.json` in Settings, then connect a supported model locally. Restart Cursor if tools aren't loaded.

## Daily developer workflow

```powershell
task-master list
task-master next
task-master show 1
task-master set-status --id=1 --status=in-progress
# Code and test task 1; commit changes.
task-master set-status --id=1 --status=done
task-master next
```

Only mark tasks done after their `testStrategy` and requirements pass. Don't mark live environment verification complete after mocked tests alone. Task 12 explicitly requires supervised Development and Production checks with operator approval.

## PRD parsing vs seeded backlog

```powershell
task-master parse-prd .taskmaster/docs/prd.txt
```

The above **generates/replaces a backlog**; it should be an intentional replanning step, not the normal first command because tasks are already seeded. Before running, commit your current tasks, review parsed task output/diff, and reconcile dependency changes; use `task-master list` to inspect new tasks. If you've changed requirements in `docs/PRD.md`, mirror the changes into `.taskmaster/docs/prd.txt` before parsing.

## Recommended work order

1. Task 1: app/config/testing skeleton.
2. Task 2: durable SQLite schema + attempts journal.
3. Tasks 3–5: XLSX import, order-level Ship To validation, review UI.
4. Task 6: WSDL-based SOAP client with mocks (can proceed alongside importer).
5. Task 7: preflight/payer/multi-piece mapping.
6. Tasks 8–10: safe shipment creation, saved PDFs, batch/history.
7. Task 11: automated acceptance and secret-safety checks.
8. Task 12: **separately authorized** real Development + one supervised Production test.
9. Task 13: optional post-MVP rates/service availability/void/manifests.

Task dependencies in `tasks.json` are authoritative for `task-master next`.

## CI policy

The included documentation/Task Master validation workflow checks JSON structure, dependencies and required files using Python's standard library. It does not start a carrier transaction and needs no secrets. Once implementation begins, extend CI with `pytest` and formatting/type checks, still **without Purolator Production credentials**.

## Task Master references

- [Official repository](https://github.com/eyaltoledano/claude-task-master)
- [Task Structure](https://github.com/eyaltoledano/claude-task-master/blob/main/docs/task-structure.md)
- [Command Reference](https://github.com/eyaltoledano/claude-task-master/blob/main/docs/command-reference.md)
- [Configuration](https://github.com/eyaltoledano/claude-task-master/blob/main/docs/configuration.md)

Task Master AI is a developer workflow tool; its AI model/provider credentials are **independent** of Purolator's SOAP production/development authentication.
