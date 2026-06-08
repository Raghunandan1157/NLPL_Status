"""Isolated test for the per-run report history + retention.

Runs against a throwaway data dir (no Flask/DuckDB needed) and verifies:
  - multiple same-day runs each get a unique run folder (no overwrite)
  - history is grouped date -> runs (newest first)
  - a missing report is marked unavailable for that run (not back-filled)
  - per-run file_path resolves the exact run's archived file
  - retention physically deletes expired run folders but keeps fresh ones
    and protected files (storage.duckdb, masters, configs)
"""
import os
import sys
import time
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

import report_history as rh  # noqa: E402
import retention  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def write(p: Path, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def age(path: Path, days):
    t = time.time() - days * 86400
    for f in path.rglob("*"):
        if f.is_file():
            os.utime(f, (t, t))
    os.utime(path, (t, t))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nlpl_hist_"))
    backend = tmp / "backend"
    # seed the *_Latest files both modules snapshot from
    write(backend / "EOD_Output_Latest.xlsx", "eod-output-v")
    write(backend / "EOD_Report_Latest.xlsx", "eod-report-v")
    write(backend / "Hourly_Collection_Report_Latest.xlsx", "h-coll-v")
    write(backend / "Hourly_Fast_Report_Latest.xlsx", "h-fast-v")

    print("> EOD: three same-day runs")
    runs = []
    for i in range(3):
        # change the latest content so each run archives a distinct file
        write(backend / "EOD_Output_Latest.xlsx", f"eod-output-{i}")
        write(backend / "EOD_Report_Latest.xlsx", f"eod-report-{i}")
        r = rh.snapshot(tmp, "eod", "04-06-2026")
        check(r.get("success") and r.get("runId"), f"run {i} created runId={r.get('runId')}")
        runs.append(r["runId"])
        time.sleep(0.05)
    check(len(set(runs)) == 3, f"3 unique run ids (got {runs})")

    hist = rh.list_history(tmp, "eod")
    check(len(hist) == 1, f"1 date group (got {len(hist)})")
    check(hist[0]["date"] == "2026-06-04", "date normalised to 2026-06-04")
    check(hist[0]["runCount"] == 3, f"3 runs in the date (got {hist[0]['runCount']})")
    newest_first = [r["runId"] for r in hist[0]["runs"]]
    check(newest_first == sorted(newest_first, reverse=True), "runs newest-first")
    check(all(rr["available"] == ["output", "report"] for rr in hist[0]["runs"]),
          "every run has both reports available")

    print("> per-run download resolves the exact run's file")
    run_a, run_c = hist[0]["runs"][-1]["runId"], hist[0]["runs"][0]["runId"]  # oldest, newest
    pa = rh.file_path(tmp, "eod", "2026-06-04", run_a, "output")
    pc = rh.file_path(tmp, "eod", "2026-06-04", run_c, "output")
    check(pa and pa.exists(), "oldest run output file exists")
    check(pc and pc.exists(), "newest run output file exists")
    check(pa.read_text() != pc.read_text(),
          f"different runs return different files ({pa.read_text()!r} vs {pc.read_text()!r})")
    check(rh.file_path(tmp, "eod", "2026-06-04", "../x", "output") is None, "path traversal blocked")

    print("> missing report marked unavailable (not back-filled)")
    (backend / "EOD_Report_Latest.xlsx").unlink()
    r = rh.snapshot(tmp, "eod", "04-06-2026")
    rep = r["reports"]
    check(rep["output"]["available"] and not rep["report"]["available"],
          "run with only output: report=unavailable")

    print("> Hourly: two runs, time label preserved")
    h1 = rh.snapshot(tmp, "hourly", "04-06-2026", "10:00 AM")
    time.sleep(0.05)
    h2 = rh.snapshot(tmp, "hourly", "04-06-2026", "12:30 PM")
    hh = rh.list_history(tmp, "hourly")
    check(hh and hh[0]["runCount"] == 2, "hourly has 2 runs")
    times = {rr["time"] for rr in hh[0]["runs"]}
    check({"10:00 AM", "12:30 PM"} <= times, f"hourly time labels preserved ({times})")

    print("> retention: expire the oldest EOD run, keep the rest + protected files")
    write(tmp / "db" / "storage.duckdb", "DB")            # protected
    write(backend / "Demand_Sheet_Master_June.xlsx", "M")  # protected
    eod_root = rh.archive_root(tmp, "eod") / "2026-06-04"
    run_dirs = sorted([p for p in eod_root.iterdir() if p.is_dir()])
    age(run_dirs[0], 4)  # oldest run -> 4 days old (> 3-day window)
    os.environ["NLPL_RETENTION_DAYS"] = "3"
    retention.run_once(tmp)
    check(not run_dirs[0].exists(), "expired run folder physically deleted")
    surviving = [p for p in eod_root.iterdir() if p.is_dir()]
    check(len(surviving) >= 1, f"fresh runs survive ({len(surviving)} left)")
    check((tmp / "db" / "storage.duckdb").exists(), "storage.duckdb protected")
    check((backend / "Demand_Sheet_Master_June.xlsx").exists(), "Demand Master protected")

    hist2 = rh.list_history(tmp, "eod")
    check(all(run_dirs[0].name != rr["runId"] for rr in (hist2[0]["runs"] if hist2 else [])),
          "deleted run no longer listed")

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for m in FAILS:
            print("  -", m)
        sys.exit(1)
    print("SUCCESS: report history + retention behave correctly.")


if __name__ == "__main__":
    main()
