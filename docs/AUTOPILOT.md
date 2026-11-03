# Puro autopilot

status: paused

<!-- The line above is the on/off switch. `status: running` lets scheduled Claude
     sessions work; anything else makes every tick stop immediately. Edit it on
     github.com to pause. -->

The autopilot is a scheduled Claude session (a "tick") that does **one subtask** per run: it implements the work, tests it offline and on the VPS through `.github/workflows/vps.yml`, merges if everything is green, and logs the result in [AUTOPILOT_LOG.md](AUTOPILOT_LOG.md). The rules here add to [AGENTS.md](../AGENTS.md) and never relax it.

## How the VPS is reached

GitHub-hosted runners SSH to the VPS as `puro-ci` with a deploy key pinned to `/usr/local/bin/puro-ci` (`scripts/autopilot/puro_ci.py`). That dispatcher accepts only these actions. Each action takes an optional ref, which it checks out in its own clone at `/home/puro-ci/puro` first. The live app in `/home/puro/puro` is never touched.

| Action | What it runs | Purolator |
| --- | --- | --- |
| `status` | repo head, Python, disk, service, Test Aid present, shipment count, lock, carrier guard | none |
| `test` | security check, compileall, `pytest -q` | none |
| `stage-a` | `scripts/stage_a_check.py` | none |
| `verify-wsdl` | `scripts/verify_wsdl_wrappers.py` on the Development WSDLs | none |
| `stage-b-validate` | `stage_b_dev_shipment.py --validate-only` with the VPS-only Test Aid recipient | Development ValidateShipment |
| `stage-b-create` | one Development shipment + label, **max 3 per 24 h**; an unclear result writes a lock that only a human clears | Development only |
| `service-health` | `systemctl is-active puro` + local health URL | none |
| `log-tail` | last N lines of the latest full log, redacted | none |

Before any carrier action, the dispatcher refuses unless the CI `.env` says Development and not production-approved, no Production WSDL folder exists, and every WSDL points at `devwebservices.purolator.com`. Child processes always run with `PUROLATOR_ENV=development` and `PURO_PRODUCTION_APPROVED=false`. **There is no Production path.**

Output in Actions logs is public, so the VPS redacts it: credentials, the Test Aid recipient, postal codes, phone numbers and emails are removed, and PINs keep only their last 4 digits. Full logs stay in `/home/puro-ci/logs`.

## One tick, step by step

1. **Check the switch.** Read this file on `main`. If the first `status:` line is not `status: running`, stop and do nothing else.
2. **Check for open blockers.** If an open issue labelled `needs-human` exists, or an `auto/*` PR is waiting for review, don't start new work. Retry any failed VPS run once if it looks transient (network, `BUSY`), otherwise stop.
3. **Pick the work.** Follow *Current focus* below. Otherwise take the first task in `.taskmaster/tasks/tasks.json` whose status is `pending` or `in-progress` and whose dependencies are all `done`. Within it, take the first subtask that isn't done. If the task has no subtasks, split it into 2 to 6 subtasks first (commit that split as its own tick). Never regenerate tasks from the PRD.
4. **Branch.** Use `auto/<task id>-<short-slug>` from the latest `main`.
5. **Build.** Implement the subtask with tests, following every rule in `AGENTS.md`. Run `python scripts/check_repo_security.py`, `python scripts/validate_taskmaster.py` and `python -m pytest -q` locally until green.
6. **Push and open a PR** with the REST API (GraphQL isn't available to Claude sessions): `gh api repos/jenozu/puro/pulls -f title=... -f head=auto/... -f base=main -f body=...`. Title format: `auto: <task>.<subtask> <summary>`.
7. **Verify on the VPS** when the change touches carrier code, WSDL handling, scripts, the UI or dependencies. Claude sessions can't use the "Run workflow" button (the API returns 403), so VPS runs are started by **pushing a commit whose message contains a tag**, e.g. `[vps: test stage-a]`. The actions run in order against that commit and stop at the first failure. To re-run without a code change: `git commit --allow-empty -m "[vps: stage-b-validate]" && git push`. Then find the run by commit (`gh api "repos/jenozu/puro/actions/runs?head_sha=<sha>"`), poll until it completes, and read the job log (`gh api repos/jenozu/puro/actions/jobs/<job id>/logs`). Each action prints `PURO-CI RESULT: ... exit=N`.
8. **Fix loop.** On failure, read the log (`log-tail` if needed), fix and push again. Allow at most **3 attempts per subtask per tick**.
9. **Merge** only when all of these hold: GitHub CI is green (`python-tests.yml`, `docs-check.yml`), the VPS actions from step 7 pass, no protected path changed, and the diff adds no file the security check would block. Squash-merge with `gh api -X PUT repos/jenozu/puro/pulls/<n>/merge -f merge_method=squash`, then delete the branch.
10. **Record it.** Set the subtask, and the task if all its subtasks are done, to `done` in `tasks.json` (inside the PR). Add one line at the top of the table in `AUTOPILOT_LOG.md`.
11. **Notify** with a push notification only when blocked, when a task (not a subtask) completes, or when the backlog is empty.

## Protected paths (PR only, human merges)

`.github/workflows/**`, `scripts/autopilot/**`, `AGENTS.md`, `docs/AUTOPILOT.md` (except flipping `status:` to `paused`), `docs/SECURITY.md`, `scripts/check_repo_security.py`, `.gitignore`, `deploy/**`.

A change to any of these is opened as a PR with the label `needs-human` and left unmerged. This way the autopilot can never loosen its own limits.

## Stop and ask (open a `needs-human` issue, notify, end the tick)

- **Secrets or private data:** a new credential, Purolator confidential guides, a real SAP export or customer data is needed. The human puts those on the VPS.
- **Production:** anything that needs Production, `PURO_PRODUCTION_APPROVED=true`, or the supervised go-live half of Task 12. Prepare a checklist PR and stop.
- **Irreversible actions:** dropping data in a migration, force-pushing `main`, deleting branches other than its own `auto/*`, or touching the live `/home/puro/puro` service.
- **Unclear carrier outcome:** a `stage-b-create` exits 4 or 5, times out, or the dispatcher reports a reconciliation lock. Never retry a CreateShipment.
- **No guessing (AGENTS rule 8):** a WSDL doesn't show a needed field, operation or service.
- **Stuck:** the same subtask failed in 3 ticks in a row, or 8 ticks passed with no merged change. In that case, also set `status: paused` here.
- **Daily limits:** 3 Development shipments per 24 h, enforced on the VPS.

## Current focus

1. **Merge `ops/stage-ab-prep`.** Run VPS `test`, `stage-a` and `verify-wsdl` with `ref=ops/stage-ab-prep`. If all pass, open a PR from that branch, check CI, and merge it. Its `deploy/**` files are protected, so if anything there needs changing, stop for review instead.
2. **Task 12, Development half:** `stage-b-validate`, then one `stage-b-create`. Confirm the PDF and journal results in the redacted output, record them in `docs/MANUAL_VALIDATION.md`, and mark those subtasks done. Leave the Production subtasks for the human.
3. **Task 13** (rates, availability, void/manifest, printer), one subtask per tick, Development only.

## Scheduled task prompt

Every tick is started with this prompt:

> Attach the GitHub repo jenozu/puro with push access and clone it. Read docs/AUTOPILOT.md on main and follow "One tick, step by step" exactly, together with AGENTS.md. If the status line is not `status: running`, stop immediately. Do at most one subtask. Use only the REST API for GitHub (no GraphQL). Never run or request anything Production. End by adding a line to docs/AUTOPILOT_LOG.md (through the PR or a direct log-only commit to main), and send a push notification only if blocked, a task completed, or the backlog is empty.

## Off switches

1. Set `status: paused` at the top of this file.
2. Disable the scheduled task in Claude.
3. Set the repository variable `AUTOPILOT_VPS_ENABLED=false` (Settings → Secrets and variables → Actions → Variables) to block all VPS runs.
4. On the VPS, run `sudo truncate -s0 /home/puro-ci/.ssh/authorized_keys` to cut GitHub's access entirely.

## One-time setup (human)

1. Merge the PR that adds these files.
2. On the VPS: `curl -fsSLo /tmp/puro-ci-install.sh https://raw.githubusercontent.com/jenozu/puro/main/scripts/autopilot/install_vps_ci.sh && sudo bash /tmp/puro-ci-install.sh`. Enter the Test Aid recipient when asked.
3. Add the 4 printed values as repository secrets (Settings → Secrets and variables → Actions): `VPS_HOST`, `VPS_PORT`, `VPS_KNOWN_HOSTS`, `VPS_SSH_KEY`. The deploy key is generated on the VPS and printed once. Claude sessions can't set secrets.
4. GitHub → Settings → Actions → General → Fork pull request workflows → **Require approval for all external contributors**.
5. Tell Claude it's done. Claude pushes a `[vps: status test]` smoke commit, checks the redacted output, and sets `status: running`.
