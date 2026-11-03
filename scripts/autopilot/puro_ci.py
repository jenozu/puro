#!/usr/bin/python3 -I
"""puro-ci: the ONLY program the GitHub Actions deploy key can run on the VPS.

Installed root-owned and read-only as /usr/local/bin/puro-ci and pinned in
~puro-ci/.ssh/authorized_keys with ``restrict,command="/usr/local/bin/puro-ci"``.
Whatever the SSH client asks for arrives in SSH_ORIGINAL_COMMAND and is checked
against a fixed allowlist; there is no shell, no generic "run" and no Production action.

Usage (via SSH):  <action> [ref] [arg]
  status | test | stage-a | verify-wsdl | stage-b-validate | stage-b-create |
  service-health | log-tail
``ref`` (branch or SHA) is checked out in the CI clone before the action runs.

Output on stdout is REDACTED because Actions logs on a public repo are public.
The full, unredacted log stays on the VPS in ~puro-ci/logs (mode 600).

Standard library only; runs with the system python3 in isolated mode.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- layout
HOME = Path("/home/puro-ci")


@dataclass(frozen=True)
class Layout:
    home: Path
    repo: Path = field(init=False)
    venv_python: Path = field(init=False)
    env_file: Path = field(init=False)
    testaid_file: Path = field(init=False)
    wsdl_dir: Path = field(init=False)
    work: Path = field(init=False)
    logs: Path = field(init=False)

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "repo", self.home / "puro")
        set_(self, "venv_python", self.home / "puro" / ".venv" / "bin" / "python")
        set_(self, "env_file", self.home / "puro" / ".env")
        set_(self, "testaid_file", self.home / "secure" / "testaid.env")
        set_(self, "wsdl_dir", self.home / "secure" / "wsdl")
        set_(self, "work", self.home / "ci-work")
        set_(self, "logs", self.home / "logs")


# --------------------------------------------------------------------------- policy
ACTIONS = {
    "status": 30,
    "test": 900,
    "stage-a": 600,
    "verify-wsdl": 300,
    "stage-b-validate": 300,
    "stage-b-create": 300,
    "service-health": 30,
    "log-tail": 30,
}
CARRIER_ACTIONS = {"stage-b-validate", "stage-b-create"}
NO_CHECKOUT = {"status", "service-health", "log-tail"}
DAILY_CREATE_CAP = 3
MAX_COMMAND_LEN = 200
STDOUT_TAIL_LINES = 200
DEV_HOST = "devwebservices.purolator.com"

REF_RE = re.compile(r"^(main|(auto|task|ops|test)/[A-Za-z0-9._-]{1,80}|[0-9a-f]{7,40})$")
TESTAID_KEYS = ("NAME", "STREET_NO", "STREET", "CITY", "PROVINCE", "POSTAL", "PHONE")
SECRET_KEY_RE = re.compile(r"(KEY|PASSWORD|ACCOUNT|SENDER|TOKEN|SECRET)", re.I)


class Refused(Exception):
    """The request is not allowed. Nothing was run."""


# --------------------------------------------------------------------------- parsing
def parse_command(raw: str | None) -> tuple[str, str | None, str | None]:
    """Return (action, ref, arg) or raise Refused."""
    raw = (raw or "").strip()
    if not raw:
        raise Refused("no action given; allowed: " + ", ".join(sorted(ACTIONS)))
    if len(raw) > MAX_COMMAND_LEN:
        raise Refused("command too long")
    if any(ch in raw for ch in ";&|`$<>\\\n\r"):
        raise Refused("shell metacharacters are not allowed")
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        raise Refused(f"could not parse command: {exc}") from None
    action, rest = parts[0], parts[1:]
    if action not in ACTIONS:
        raise Refused(f"unknown action {action!r}; allowed: " + ", ".join(sorted(ACTIONS)))
    if len(rest) > 2:
        raise Refused("too many arguments")
    ref = rest[0] if rest else None
    arg = rest[1] if len(rest) > 1 else None
    if action == "log-tail":
        # log-tail takes only a line count, in either position.
        n = arg or ref
        if n is not None and not (n.isdigit() and 1 <= int(n) <= 400):
            raise Refused("log-tail takes a line count between 1 and 400")
        return action, None, n
    if arg is not None:
        raise Refused(f"{action} takes no extra argument")
    if ref is not None and not REF_RE.match(ref):
        raise Refused("ref must be main, auto/*, task/*, ops/*, test/* or a commit SHA")
    if ref is not None and action in NO_CHECKOUT:
        ref = None  # harmless: these never check out code
    return action, ref, None


# --------------------------------------------------------------------------- env files
def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def secret_values(layout: Layout) -> list[str]:
    """Exact strings that must never reach stdout (longest first)."""
    found: set[str] = set()
    for key, value in read_env_file(layout.env_file).items():
        if SECRET_KEY_RE.search(key) and len(value) >= 3:
            found.add(value)
    for value in read_env_file(layout.testaid_file).values():
        if len(value) >= 3:
            found.add(value)
    return sorted(found, key=len, reverse=True)


# --------------------------------------------------------------------------- redaction
_POSTAL = re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[A-Z][ -]?\d[A-Z]\d\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
_LONGNUM = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_RECIPIENT = re.compile(r"^(\s*Recipient:).*$", re.M)


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "[redacted]")
    text = _RECIPIENT.sub(r"\1 [redacted]", text)
    text = _EMAIL.sub("[email]", text)
    text = _POSTAL.sub("[postal]", text)
    text = _PHONE.sub("[phone]", text)
    text = _LONGNUM.sub(lambda m: f"[#..{m.group(0)[-4:]}]", text)
    return text


# --------------------------------------------------------------------------- guards
def carrier_guard(layout: Layout) -> None:
    """Refuse unless the CI copy is provably Development-only."""
    env = read_env_file(layout.env_file)
    if env.get("PUROLATOR_ENV", "development").strip().lower() != "development":
        raise Refused("CI .env PUROLATOR_ENV is not development")
    if env.get("PURO_PRODUCTION_APPROVED", "false").strip().lower() == "true":
        raise Refused("CI .env has PURO_PRODUCTION_APPROVED=true")
    if (layout.wsdl_dir / "Production").exists():
        raise Refused("a Production WSDL folder exists in the CI copy; remove it")
    dev = layout.wsdl_dir / "Development"
    wsdls = list(dev.glob("*.wsdl")) if dev.is_dir() else []
    if not wsdls:
        raise Refused("no Development WSDLs in the CI copy")
    for wsdl in wsdls:
        text = wsdl.read_text(encoding="utf-8", errors="replace")
        hosts = set(re.findall(r"location=\"https?://([^/\"]+)", text))
        if hosts and hosts != {DEV_HOST}:
            raise Refused(f"{wsdl.name} points at {sorted(hosts)}, not {DEV_HOST}")


def testaid_args(layout: Layout) -> list[str]:
    values = read_env_file(layout.testaid_file)
    missing = [k for k in TESTAID_KEYS if not values.get(k)]
    if missing:
        raise Refused(f"{layout.testaid_file} is missing: {', '.join(missing)}")
    args = []
    for key in TESTAID_KEYS:
        args += [f"--{key.lower().replace('_', '-')}", values[key]]
    if values.get("UNIT"):
        args += ["--unit", values["UNIT"]]
    return args


def lock_file(layout: Layout) -> Path:
    return layout.work / "STAGEB_LOCK"


def creates_file(layout: Layout) -> Path:
    return layout.work / "stageb-creates.json"


def creates_last_24h(layout: Layout, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    try:
        stamps = json.loads(creates_file(layout).read_text())
    except (OSError, ValueError):
        stamps = []
    return [s for s in stamps if isinstance(s, (int, float)) and now - s < 86400]


def create_guard(layout: Layout, now: float | None = None) -> None:
    lock = lock_file(layout)
    if lock.exists():
        raise Refused("a previous Development shipment needs reconciliation "
                      f"({lock}); a human must check it and delete the lock file")
    if len(creates_last_24h(layout, now)) >= DAILY_CREATE_CAP:
        raise Refused(f"daily cap of {DAILY_CREATE_CAP} Development shipments reached")


def record_create(layout: Layout, now: float | None = None) -> None:
    stamps = creates_last_24h(layout, now) + [time.time() if now is None else now]
    creates_file(layout).write_text(json.dumps(stamps))


# --------------------------------------------------------------------------- running
def child_env(layout: Layout, carrier: bool) -> dict[str, str]:
    # Shell variables beat .env (python-dotenv does not override), so these pins win.
    return {
        "PATH": f"{layout.venv_python.parent}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(layout.home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PUROLATOR_ENV": "development",
        "PURO_PRODUCTION_APPROVED": "false",
        "PURO_ENABLE_CARRIER": "true" if carrier else "false",
        "PUROLATOR_WSDL_DIR": str(layout.wsdl_dir),
        "PURO_DATA_DIR": str(layout.work / "data"),
        "PURO_LABELS_DIR": str(layout.work / "labels"),
    }


@dataclass
class Step:
    argv: list[str]
    carrier: bool = False


def run_steps(layout: Layout, steps: list[Step], log, timeout: int) -> tuple[int, bool]:
    """Run steps in order, stop at first failure. Returns (exit code, timed_out)."""
    deadline = time.monotonic() + timeout
    for step in steps:
        log.write(f"\n$ {' '.join(shlex.quote(a) for a in step.argv)}\n")
        log.flush()
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            proc = subprocess.run(step.argv, cwd=layout.repo, env=child_env(layout, step.carrier),
                                  stdout=log, stderr=subprocess.STDOUT, timeout=remaining,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            log.write(f"\nTIMEOUT after {timeout}s\n")
            return 124, True
        except OSError as exc:
            log.write(f"\nCould not start: {exc}\n")
            return 127, False
        if proc.returncode != 0:
            return proc.returncode, False
    return 0, False


def checkout_steps(layout: Layout, ref: str) -> list[Step]:
    target = ref if re.fullmatch(r"[0-9a-f]{7,40}", ref) else f"origin/{ref}"
    py = str(layout.venv_python)
    return [
        Step(["git", "fetch", "--prune", "--quiet", "origin"]),
        Step(["git", "checkout", "--quiet", "--force", "--detach", target]),
        Step(["git", "log", "--oneline", "-1"]),
        Step([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
              "-r", "requirements-dev.txt"]),
    ]


def action_steps(layout: Layout, action: str) -> list[Step]:
    py = str(layout.venv_python)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if action == "test":
        return [Step([py, "scripts/check_repo_security.py"]),
                Step([py, "-m", "compileall", "-q", "puro", "streamlit_app.py", "tests"]),
                Step([py, "-m", "pytest", "-q", "-p", "no:cacheprovider"])]
    if action == "stage-a":
        return [Step([py, "scripts/stage_a_check.py", "--out", str(layout.work / "stageA")])]
    if action == "verify-wsdl":
        return [Step([py, "scripts/verify_wsdl_wrappers.py", "--wsdl-dir", str(layout.wsdl_dir)])]
    if action in CARRIER_ACTIONS:
        base = [py, "scripts/stage_b_dev_shipment.py"]
        run_dir = layout.work / "stageB" / stamp
        dirs = ["--data-dir", str(run_dir / "data"), "--labels-dir", str(run_dir / "labels")]
        mode = (["--validate-only"] if action == "stage-b-validate"
                else ["--ship", "--approve-development-shipment"])
        return [Step(base + mode + testaid_args(layout) + dirs, carrier=True)]
    raise Refused(f"no steps for {action}")  # pragma: no cover


def status_text(layout: Layout) -> str:
    def sh(*argv: str) -> str:
        try:
            out = subprocess.run(argv, cwd=layout.repo, capture_output=True, text=True, timeout=10)
            return (out.stdout or out.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"unavailable ({exc.__class__.__name__})"
    disk = shutil.disk_usage(layout.home)
    logs = sorted(layout.logs.glob("*.log"))[-5:] if layout.logs.exists() else []
    lines = [
        f"repo: {sh('git', 'log', '--oneline', '-1')}",
        f"python: {sh(str(layout.venv_python), '--version')}",
        f"disk free: {disk.free // 2**30} GiB",
        f"puro.service: {sh('systemctl', 'is-active', 'puro') or 'unknown'}",
        f"testaid configured: {all(read_env_file(layout.testaid_file).get(k) for k in TESTAID_KEYS)}",
        f"dev shipments in last 24h: {len(creates_last_24h(layout))}/{DAILY_CREATE_CAP}",
        f"reconciliation lock: {'PRESENT' if lock_file(layout).exists() else 'none'}",
        "recent runs: " + (", ".join(p.stem for p in logs) or "none"),
    ]
    try:
        carrier_guard(layout)
        lines.append("carrier guard: OK (Development only)")
    except Refused as exc:
        lines.append(f"carrier guard: REFUSES ({exc})")
    return "\n".join(lines)


def service_health_text(layout: Layout) -> tuple[int, str]:
    active = subprocess.run(["systemctl", "is-active", "puro"], capture_output=True, text=True)
    try:
        health = subprocess.run(["curl", "-fsS", "-m", "5", "http://127.0.0.1:8501/_stcore/health"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        health = ""
    ok = active.stdout.strip() == "active" and health == "ok"
    return (0 if ok else 1), f"puro.service: {active.stdout.strip()} | health: {health or 'no answer'}"


def log_tail_text(layout: Layout, n: int, secrets: list[str]) -> str:
    logs = sorted(layout.logs.glob("*.log")) if layout.logs.exists() else []
    logs = [p for p in logs if "-log-tail" not in p.name and "-status" not in p.name]
    if not logs:
        return "no logs yet"
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    return f"== {logs[-1].name} (last {n} lines) ==\n" + redact("\n".join(lines), secrets)


# --------------------------------------------------------------------------- main
def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def main(raw: str | None, layout: Layout) -> int:
    try:
        action, ref, arg = parse_command(raw)
    except Refused as exc:
        emit(f"PURO-CI REFUSED: {exc}")
        return 2

    for d in (layout.work, layout.logs):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_handle = open(layout.work / ".busy", "w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        emit("PURO-CI BUSY: another action is running; try again shortly")
        return 75

    secrets = secret_values(layout)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = layout.logs / f"{stamp}-{action}.log"
    started = time.monotonic()

    if action == "status":
        emit(status_text(layout))
        return 0
    if action == "service-health":
        code, text = service_health_text(layout)
        emit(text)
        return code
    if action == "log-tail":
        emit(log_tail_text(layout, int(arg or 80), secrets))
        return 0

    try:
        if action in CARRIER_ACTIONS:
            carrier_guard(layout)
        if action == "stage-b-create":
            create_guard(layout)
        steps = (checkout_steps(layout, ref) if ref else []) + action_steps(layout, action)
    except Refused as exc:
        emit(f"PURO-CI REFUSED: {exc}")
        return 2

    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as log:
        log.write(f"action={action} ref={ref or '(current)'} started={stamp}\n")
        if action == "stage-b-create":
            record_create(layout)  # counts against the cap even if it fails
        code, timed_out = run_steps(layout, steps, log, ACTIONS[action])
        log.write(f"\nexit={code}\n")

    if action == "stage-b-create" and (code in (4, 5) or timed_out):
        lock_file(layout).write_text(
            f"{stamp} exit={code} timed_out={timed_out} log={log_path}\n"
            "Do NOT create another shipment. Check the outcome with Purolator, then delete this file.\n")

    body = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = body[-STDOUT_TAIL_LINES:]
    if len(body) > len(tail):
        tail.insert(0, f"... ({len(body) - len(tail)} earlier lines kept on the VPS)")
    emit(redact("\n".join(tail), secrets))
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=layout.repo,
                          capture_output=True, text=True).stdout.strip()
    emit(f"PURO-CI RESULT: action={action} ref={ref or '(current)'} head={head or '?'} "
         f"exit={code} seconds={int(time.monotonic() - started)} log={log_path.name}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(os.environ.get("SSH_ORIGINAL_COMMAND") or " ".join(sys.argv[1:]),
                          Layout(HOME)))
