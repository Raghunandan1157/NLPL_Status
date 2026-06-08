"""Unit test for the 3-day retention sweep — runs against a throwaway data dir,
so real eod_data is never touched. Verifies that:
  - stale run artifacts are physically deleted
  - protected files survive even when stale
  - recent files survive
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import retention  # noqa: E402

OLD = time.time() - 5 * 86400      # 5 days old → should be purged
RECENT = time.time() - 1 * 86400   # 1 day old → should survive


def touch(path: Path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)
    os.utime(path, (mtime, mtime))


def main():
    root = Path(tempfile.mkdtemp(prefix="nlpl_ret_"))

    # --- should be DELETED (stale run artifacts) ---
    should_delete = [
        root / "archive/2026/June/01/Session_1/Output_Files/Output.xlsx",
        root / "archive/2026/June/01/Session_1/Input_Files/PAR.xlsx",
        root / "db/cache/daily_par_last.xlsx",
        root / "db/cache/daily_collection_last.xlsx",
        root / "backend/EOD_Output_Latest.xlsx",
        root / "backend/EOD_Report_Latest.xlsx",
        root / "backend/Employee_Report_Latest.xlsx",
        root / "backend/eod_body.png",
        root / "backend/sheets/area_BADAMI.xlsx",
        root / "reports/scratch.xlsx",
        root / "temp/gdrive/old.tmp",
        root / "backend/Hourly_Collection_Report_Latest.xlsx",
        root / "backend/Hourly_Fast_Report_Latest.xlsx",
        root / "backend/HourlyDaily_Upload.xlsx",
        root / "reports_archive_hourly/2026-06-01_10-20-30/Hourly Collection Report.xlsx",
        root / "reports_archive_hourly/2026-06-01_10-20-30/meta.json",
    ]
    for p in should_delete:
        touch(p, OLD)

    # --- should be KEPT (protected, even though stale) ---
    protected = [
        root / "backend/Demand_Sheet_Master_May.xlsx",
        root / "db/storage.duckdb",
        root / "backend/email_config.csv",
        root / "whatsapp_contacts.csv",
        root / "backend/cache_history.csv",
        root / "email_sheet_config.xlsx",
        root / "gdrive_config.json",
    ]
    for p in protected:
        touch(p, OLD)

    # --- should be KEPT (recent run artifacts) ---
    recent = [
        root / "backend/EOD_Output_Latest_recent.xlsx",
        root / "db/cache/fresh.xlsx",
        root / "archive/2026/June/04/Session_9/Output_Files/Output.xlsx",
        root / "backend/Hourly_Collection_Report_fresh.xlsx",
        root / "reports_archive_hourly/2026-06-04_10-20-30/Hourly Collection Report.xlsx",
    ]
    for p in recent:
        touch(p, RECENT)
    # keep the recent session dir itself recent
    os.utime(root / "archive/2026/June/04/Session_9", (RECENT, RECENT))
    os.utime(root / "reports_archive_hourly/2026-06-04_10-20-30", (RECENT, RECENT))

    summary = retention.run_once(root)
    print("Sweep summary:", summary)

    failures = []
    for p in should_delete:
        if p.exists():
            failures.append(f"NOT deleted (should be): {p.relative_to(root)}")
    for p in protected + recent:
        if not p.exists():
            failures.append(f"WRONGLY deleted (protected/recent): {p.relative_to(root)}")

    import shutil
    shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print(f"\nSUCCESS: {len(should_delete)} stale artifacts deleted, "
          f"{len(protected)} protected + {len(recent)} recent kept.")


if __name__ == "__main__":
    main()
