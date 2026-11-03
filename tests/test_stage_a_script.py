"""The Stage A operator helper must keep passing against the importer rules."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stage_a_check.py"


def test_stage_a_script_passes_and_writes_workbooks(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL PASSED" in result.stdout
    assert (tmp_path / "synthetic_legacy_rdr12.xlsx").is_file()
    assert (tmp_path / "synthetic_sap_copy_table.xlsx").is_file()
