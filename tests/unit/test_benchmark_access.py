"""scripts/benchmark_access.py on SQLite: the same build, timing and checks that CI runs on an Access file in a
network folder (the scale-network job), so a change that breaks the benchmark fails here first."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_access.py"
PHASES = {"connect", "change_check", "masters", "overview", "ledger_index", "item_index", "trial_balance",
          "profit_and_loss", "balance_sheet", "stock_summary", "ledger_statement", "bills_outstanding", "dashboard",
          "day_book", "trial_balance_again", "change_check_again"}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--backend", "sqlite", *args],
                          capture_output=True, text=True, timeout=300, check=False)


def test_build_measure_and_report(tmp_path):
    results, summary = tmp_path / "results.json", tmp_path / "summary.md"
    summary.write_text("earlier steps\n", encoding="utf-8")
    run = _run("--vouchers", "300", "--root", str(tmp_path / "bench"), "--json", str(results),
               "--summary-md", str(summary), "--assert-max-seconds", "index=120", "--assert-max-seconds", "reports=60")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Tran2 (MasterCode1, RecType)" in run.stdout  # the indexes created are named
    report = json.loads(results.read_text(encoding="utf-8"))
    assert report["ok"] and report["violations"] == [] and report["limits"] == {"index": 120.0, "reports": 60.0}
    (loc,) = report["locations"]
    assert set(loc["phases"]) == PHASES and loc["error"] is None
    assert len(loc["checks"]) == 6 and all(c["ok"] for c in loc["checks"]), loc["checks"]
    assert loc["phases"]["trial_balance_again"]["queries"] == 0  # answered from memory
    assert loc["phases"]["ledger_index"]["queries"] == 1  # one aggregate query builds the account index
    company = report["company"]
    assert company["vouchers"] == 300 and company["expected_active_vouchers"] == 312
    assert all(ix["created"] for ix in company["indexes"])
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier steps\n")  # appended, as $GITHUB_STEP_SUMMARY needs
    assert "| `ledger_index` |" in text and "| trial balance tallies | pass |" in text and "RESULT: OK" in text


def test_measure_only_limits_and_safety(tmp_path):
    root = tmp_path / "bench"
    assert _run("--vouchers", "100", "--root", str(root), "--build-only").returncode == 0
    assert (root / "DATA" / "COMP0001" / "db12024.sqlite").exists()

    run = _run("--data-dir", str(root / "DATA"), "--json", str(tmp_path / "r.json"),
               "--assert-max-seconds", "trial_balance=0")
    assert run.returncode == 1 and "LIMIT EXCEEDED [1] trial_balance" in run.stdout
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert not report["ok"] and all(c["ok"] for c in report["locations"][0]["checks"])

    # Rebuilding over an earlier benchmark company is fine; a folder with other data is never touched.
    assert _run("--vouchers", "50", "--root", str(root), "--build-only").returncode == 0
    foreign = tmp_path / "busy" / "DATA" / "COMP0001"
    foreign.mkdir(parents=True)
    (foreign / "db12024.bds").write_bytes(b"real data")
    run = _run("--root", str(tmp_path / "busy"), "--build-only")
    assert run.returncode == 1 and "not a benchmark company" in run.stderr
    assert (foreign / "db12024.bds").read_bytes() == b"real data"

    run = _run("--data-dir", str(tmp_path / "nowhere"), "--json", str(tmp_path / "r2.json"))
    assert run.returncode == 1 and "ERROR" in run.stdout  # reported per folder, not a crash
    assert _run("--assert-max-seconds", "nonsense=1").returncode == 2
