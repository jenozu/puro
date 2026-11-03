"""Offline tests for the VPS CI dispatcher (scripts/autopilot/puro_ci.py).

The dispatcher is the only thing the GitHub deploy key can run on the VPS, so its
allowlist, redaction and Development-only guards are tested here without any network.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("puro_ci", ROOT / "scripts" / "autopilot" / "puro_ci.py")
ci = importlib.util.module_from_spec(spec)
sys.modules["puro_ci"] = ci  # dataclasses look the module up while the class is built
spec.loader.exec_module(ci)

DEV_WSDL = '<definitions><service><port><soap:address location="https://devwebservices.purolator.com/EWS/V2/Shipping/ShippingService.asmx"/></port></service></definitions>'
PROD_WSDL = DEV_WSDL.replace("devwebservices", "webservices")


@pytest.fixture
def layout(tmp_path):
    lay = ci.Layout(tmp_path)
    lay.repo.mkdir()
    (lay.wsdl_dir / "Development").mkdir(parents=True)
    (lay.wsdl_dir / "Development" / "ShippingService.wsdl").write_text(DEV_WSDL)
    lay.env_file.write_text(
        "PUROLATOR_ENV=development\nPURO_ENABLE_CARRIER=false\nPURO_PRODUCTION_APPROVED=false\n"
        "PUROLATOR_API_KEY=abcKEY123secret\nPUROLATOR_API_PASSWORD='p@ss-word-99'\n"
        "PUROLATOR_ACCOUNT_NUMBER=9999999\nDEFAULT_SENDER_STREET=Sender Lane\n")
    lay.testaid_file.write_text(
        "NAME=Test Aid Person\nSTREET_NO=123\nSTREET=Fictional Street\nCITY=Ottawa\n"
        "PROVINCE=ON\nPOSTAL=K1A0B1\nPHONE=6135550100\n")
    lay.work.mkdir()
    return lay


# ------------------------------------------------------------------ allowlist
@pytest.mark.parametrize("raw", [
    "", "bash", "rm -rf /", "test; id", "test main && id", "test $(id)", "test `id`",
    "test main extra", "stage-b-create production", "test ../etc", "test refs/heads/main",
    "test feature/x", "log-tail 9999", "log-tail abc", "test main\nid", "x" * 300,
    "stage-b-production", "deploy main",
])
def test_refuses_anything_off_the_allowlist(raw):
    with pytest.raises(ci.Refused):
        ci.parse_command(raw)


@pytest.mark.parametrize("raw,expected", [
    ("status", ("status", None, None)),
    ("test", ("test", None, None)),
    ("test main", ("test", "main", None)),
    ("test auto/12-dev-validation", ("test", "auto/12-dev-validation", None)),
    ("stage-a ops/stage-ab-prep", ("stage-a", "ops/stage-ab-prep", None)),
    ("verify-wsdl a711c20", ("verify-wsdl", "a711c20", None)),
    ("stage-b-validate main", ("stage-b-validate", "main", None)),
    ("log-tail 50", ("log-tail", None, "50")),
    ("log-tail main 50", ("log-tail", None, "50")),
    ("status main", ("status", None, None)),
])
def test_accepts_allowlisted_actions(raw, expected):
    assert ci.parse_command(raw) == expected


def test_main_refuses_without_running_anything(layout, capsys):
    assert ci.main("bash -i", layout) == 2
    assert "PURO-CI REFUSED" in capsys.readouterr().out
    assert not layout.logs.exists()


# ------------------------------------------------------------------ redaction
def test_redacts_secrets_postal_phone_email_and_pins(layout):
    secrets = ci.secret_values(layout)
    text = ("key abcKEY123secret pw p@ss-word-99 acct 9999999\n"
            "Recipient: Test Aid Person, 123 Fictional Street, Ottawa ON K1A0B1\n"
            "postal K1A 0B1 phone (613) 555-0100 alt 613.555.0100 mail a.b@example.com\n"
            "Result: created | shipment PIN: 329014521622\n")
    out = ci.redact(text, secrets)
    for leaked in ("abcKEY123secret", "p@ss-word-99", "9999999", "Test Aid Person",
                   "K1A0B1", "K1A 0B1", "555-0100", "555.0100", "a.b@example.com", "329014521622"):
        assert leaked not in out
    assert "Recipient: [redacted]" in out
    assert "[#..1622]" in out  # last 4 kept so a run can still be matched on the VPS


def test_redaction_keeps_ordinary_test_output():
    line = "1 failed, 214 passed in 12.31s | ValidShipment: True | exit=0"
    assert ci.redact(line, []) == line


# ------------------------------------------------------------------ carrier guards
def test_carrier_guard_passes_for_development_only(layout):
    ci.carrier_guard(layout)


def test_carrier_guard_refuses_production_env(layout):
    layout.env_file.write_text("PUROLATOR_ENV=production\n")
    with pytest.raises(ci.Refused, match="not development"):
        ci.carrier_guard(layout)


def test_carrier_guard_refuses_production_approval(layout):
    layout.env_file.write_text("PUROLATOR_ENV=development\nPURO_PRODUCTION_APPROVED=true\n")
    with pytest.raises(ci.Refused, match="PRODUCTION_APPROVED"):
        ci.carrier_guard(layout)


def test_carrier_guard_refuses_production_wsdl_folder(layout):
    (layout.wsdl_dir / "Production").mkdir()
    with pytest.raises(ci.Refused, match="Production WSDL"):
        ci.carrier_guard(layout)


def test_carrier_guard_refuses_wsdl_pointing_at_production_host(layout):
    (layout.wsdl_dir / "Development" / "ShippingService.wsdl").write_text(PROD_WSDL)
    with pytest.raises(ci.Refused, match="not devwebservices"):
        ci.carrier_guard(layout)


def test_child_env_pins_development_even_if_env_file_changes(layout):
    env = ci.child_env(layout, carrier=True)
    assert env["PUROLATOR_ENV"] == "development"
    assert env["PURO_PRODUCTION_APPROVED"] == "false"
    assert ci.child_env(layout, carrier=False)["PURO_ENABLE_CARRIER"] == "false"


def test_testaid_values_come_from_vps_file(layout):
    args = ci.testaid_args(layout)
    assert args[args.index("--postal") + 1] == "K1A0B1"
    layout.testaid_file.write_text("NAME=x\n")
    with pytest.raises(ci.Refused, match="missing"):
        ci.testaid_args(layout)


def test_create_steps_use_a_fresh_folder_and_explicit_dev_approval(layout):
    (step,) = ci.action_steps(layout, "stage-b-create")
    assert step.carrier is True
    assert "--ship" in step.argv and "--approve-development-shipment" in step.argv
    assert str(layout.work / "stageB") in step.argv[step.argv.index("--data-dir") + 1]
    (validate,) = ci.action_steps(layout, "stage-b-validate")
    assert "--validate-only" in validate.argv and "--ship" not in validate.argv


# ------------------------------------------------------------------ daily cap / lock
def test_daily_cap_counts_only_last_24_hours(layout):
    now = time.time()
    ci.creates_file(layout).write_text(json.dumps([now - 90000, now - 100, now - 50]))
    ci.create_guard(layout, now)  # 2 in window
    ci.record_create(layout, now)
    with pytest.raises(ci.Refused, match="daily cap"):
        ci.create_guard(layout, now)


def test_reconciliation_lock_blocks_further_creates(layout):
    ci.lock_file(layout).write_text("exit=4")
    with pytest.raises(ci.Refused, match="reconciliation"):
        ci.create_guard(layout)


def test_unclear_create_outcome_writes_lock(layout, monkeypatch, capsys):
    monkeypatch.setattr(ci, "run_steps", lambda *a, **k: (4, False))
    monkeypatch.setattr(ci, "checkout_steps", lambda *a: [])
    assert ci.main("stage-b-create", layout) == 4
    assert ci.lock_file(layout).exists()
    assert len(ci.creates_last_24h(layout)) == 1
    out = capsys.readouterr().out
    assert "PURO-CI RESULT: action=stage-b-create" in out and "exit=4" in out


def test_timeout_during_create_writes_lock(layout, monkeypatch):
    monkeypatch.setattr(ci, "run_steps", lambda *a, **k: (124, True))
    ci.main("stage-b-create", layout)
    assert ci.lock_file(layout).exists()


def test_log_is_private_and_stdout_is_redacted(layout, monkeypatch, capsys):
    def fake_run(lay, steps, log, timeout):
        log.write("Recipient: Test Aid Person, K1A0B1\nPIN 329014521622\n")
        return 0, False
    monkeypatch.setattr(ci, "run_steps", fake_run)
    assert ci.main("stage-b-validate", layout) == 0
    out = capsys.readouterr().out
    assert "Test Aid Person" not in out and "329014521622" not in out
    (log,) = layout.logs.glob("*-stage-b-validate.log")
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    assert "329014521622" in log.read_text()  # full detail stays on the VPS
