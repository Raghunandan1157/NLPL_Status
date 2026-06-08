"""Automatic 3-day retention for EOD run artifacts.

Physically deletes (not hides) run artifacts older than the retention window so
disk doesn't grow without bound, while PROTECTING everything the EOD module
needs to keep working:

  KEEP  · Demand Master file        (backend/Demand_Sheet_Master_*)
        · DuckDB database            (db/storage.duckdb)
        · Email recipient config     (backend/email_config.csv)
        · WhatsApp contacts          (whatsapp_contacts.csv)
        · Auto-assign / branch config (email_sheet_config.xlsx, branch_emails.xlsx)
        · Misc small config/state    (*.json configs, .target_date, cache_history.csv)

  PURGE (when older than RETENTION_DAYS) ·
        · Per-session archives       (archive/**/Session_*)
        · Cached PAR/Collection       (db/cache/*)
        · Generated report outputs    (backend/EOD_Output_*, EOD_Report_*, Employee_Report_*)
        · Extracted email sheets      (backend/sheets/*)
        · Rendered email body image   (backend/eod_body*.png)
        · Temp + reports scratch      (temp/*, reports/*)

A background daemon runs one sweep at startup and then every
NLPL_RETENTION_SWEEP_HOURS hours. Every delete is guarded by try/except and the
protected-list, and the sweep only ever touches paths INSIDE the data dir.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path

RETENTION_DAYS = int(os.environ.get("NLPL_RETENTION_DAYS", "3"))
SWEEP_INTERVAL_HOURS = int(os.environ.get("NLPL_RETENTION_SWEEP_HOURS", "12"))

# Files that must never be removed (EOD relies on them).
_PROTECTED_NAMES = {
    "storage.duckdb",
    "email_config.csv",
    "whatsapp_contacts.csv",
    "cache_history.csv",
    "branch_emails.xlsx",
    "email_sheet_config.xlsx",
    ".target_date",
}
_PROTECTED_PREFIXES = ("Demand_Sheet_Master", "Last_Month_PAR")
_PROTECTED_SUFFIXES = (".json",)  # data-root config jsons (gdrive/colldb/etc.)

# Output files in the backend dir that ARE purgeable once stale.
_OUTPUT_GLOBS = (
    "EOD_Output_*.xlsx",
    "EOD_Report_*.xlsx",
    "Employee_Report_*.xlsx",
    "Employee_Report_Accounts_*.xlsx",
    "eod_body*.png",
    "Hourly_Collection_Report_*.xlsx",
    "Hourly_Fast_Report_*.xlsx",
    "HourlyDaily_*",
    # migrated report modules
    "Quick_Report_*.xlsx",
    "Quick_Month_End_Employee_*.xlsx",
    "DB_Disbursement_Report_*.xlsx",
)


_state = {
    "days": RETENTION_DAYS,
    "sweepIntervalHours": SWEEP_INTERVAL_HOURS,
    "lastRun": None,
    "deletedFiles": 0,
    "deletedDirs": 0,
    "freedBytes": 0,
    "errors": 0,
}
_lock = threading.Lock()
_started = False
_data_dir: Path | None = None


def status() -> dict:
    with _lock:
        return dict(_state)


def _is_protected(p: Path) -> bool:
    name = p.name
    if name in _PROTECTED_NAMES:
        return True
    if name.startswith(_PROTECTED_PREFIXES):
        return True
    if p.suffix.lower() in _PROTECTED_SUFFIXES:
        return True
    return False


def _older_than(p: Path, cutoff: float) -> bool:
    try:
        return p.stat().st_mtime < cutoff
    except OSError:
        return False


def _safe_under(p: Path, root: Path) -> bool:
    """Guard: only ever delete things strictly inside the data dir."""
    try:
        p.resolve().relative_to(root.resolve())
        return p.resolve() != root.resolve()
    except (ValueError, OSError):
        return False


def _delete_file(p: Path, root: Path, acc: dict) -> None:
    if not _safe_under(p, root):
        return
    try:
        size = p.stat().st_size
        p.unlink()
        acc["deletedFiles"] += 1
        acc["freedBytes"] += size
    except OSError as e:
        acc["errors"] += 1
        logging.warning("retention: could not delete %s: %s", p, e)


def _delete_dir(p: Path, root: Path, acc: dict) -> None:
    if not _safe_under(p, root):
        return
    try:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        size = 0
    try:
        shutil.rmtree(p)
        acc["deletedDirs"] += 1
        acc["freedBytes"] += size
    except OSError as e:
        acc["errors"] += 1
        logging.warning("retention: could not delete dir %s: %s", p, e)


def _sweep_files(directory: Path, cutoff: float, root: Path, acc: dict, globs=None, recursive=False) -> None:
    if not directory.exists():
        return
    items = directory.rglob("*") if recursive else directory.glob("*")
    for p in list(items):
        if not p.is_file() or _is_protected(p):
            continue
        if globs and not any(p.match(g) for g in globs):
            continue
        if _older_than(p, cutoff):
            _delete_file(p, root, acc)


def _newest_mtime(directory: Path) -> float:
    """Most recent mtime among a directory's files (its real 'last activity').
    Dir mtimes are unreliable across platforms, so judge by contents."""
    newest = 0.0
    has_files = False
    for f in directory.rglob("*"):
        try:
            if f.is_file():
                has_files = True
                newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    if not has_files:
        try:
            newest = directory.stat().st_mtime
        except OSError:
            newest = 0.0
    return newest


def _prune_empty_dirs(rootdir: Path) -> None:
    if not rootdir.exists():
        return
    for d in sorted([p for p in rootdir.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


def _sweep_archive(archive: Path, cutoff: float, root: Path, acc: dict) -> None:
    if not archive.exists():
        return
    for session in list(archive.rglob("Session_*")):
        # A session is purgeable once its NEWEST file is older than the cutoff.
        if session.is_dir() and _newest_mtime(session) < cutoff:
            _delete_dir(session, root, acc)
    _prune_empty_dirs(archive)


def run_once(data_dir: Path | None = None) -> dict:
    """Run a single retention sweep. Returns a summary dict."""
    root = Path(data_dir or _data_dir or ".").resolve()
    cutoff = time.time() - RETENTION_DAYS * 86400
    acc = {"deletedFiles": 0, "deletedDirs": 0, "freedBytes": 0, "errors": 0}

    backend = root / "backend"
    _sweep_archive(root / "archive", cutoff, root, acc)
    _sweep_files(root / "db" / "cache", cutoff, root, acc)                         # cached PAR/Collection
    _sweep_files(backend, cutoff, root, acc, globs=_OUTPUT_GLOBS)                  # generated outputs
    _sweep_files(backend / "sheets", cutoff, root, acc, recursive=True)           # extracted email sheets
    _sweep_files(root / "reports", cutoff, root, acc)                             # report scratch
    # report history (EOD + Hourly): layout is <archive>/<date>/<runId>/.
    # Delete individual expired RUN folders so fresh same-day runs survive,
    # then prune empty date folders. A whole date folder that is itself stale
    # (covers the legacy flat layout) is dropped outright.
    for arch_name in ("reports_archive", "reports_archive_hourly",
                      "reports_archive_quick", "reports_archive_qme",
                      "reports_archive_db"):
        arch = root / arch_name
        if not arch.exists():
            continue
        for date_dir in list(arch.iterdir()):
            if not date_dir.is_dir():
                continue
            if _newest_mtime(date_dir) < cutoff:
                _delete_dir(date_dir, root, acc)  # whole date stale (incl. legacy)
                continue
            for run_dir in list(date_dir.iterdir()):
                if run_dir.is_dir() and _newest_mtime(run_dir) < cutoff:
                    _delete_dir(run_dir, root, acc)
        _prune_empty_dirs(arch)
    # temp: delete stale files anywhere underneath, then prune empty dirs
    _sweep_files(root / "temp", cutoff, root, acc, recursive=True)
    _prune_empty_dirs(root / "temp")

    with _lock:
        _state.update(
            lastRun=time.strftime("%Y-%m-%d %H:%M:%S"),
            deletedFiles=acc["deletedFiles"],
            deletedDirs=acc["deletedDirs"],
            freedBytes=acc["freedBytes"],
            errors=acc["errors"],
        )
    if acc["deletedFiles"] or acc["deletedDirs"]:
        logging.info(
            "retention: purged %d file(s), %d dir(s), freed %.1f MB (older than %d days)",
            acc["deletedFiles"], acc["deletedDirs"], acc["freedBytes"] / 1024 / 1024, RETENTION_DAYS,
        )
    return status()


def _loop(data_dir: Path) -> None:
    while True:
        try:
            run_once(data_dir)
        except Exception:  # never let the daemon die
            logging.exception("retention sweep failed")
        time.sleep(max(1, SWEEP_INTERVAL_HOURS) * 3600)


def start(data_dir) -> None:
    """Start the background retention daemon (idempotent). Runs a sweep at
    startup and then every SWEEP_INTERVAL_HOURS hours."""
    global _started, _data_dir
    if _started:
        return
    _data_dir = Path(data_dir)
    _started = True
    threading.Thread(target=_loop, args=(_data_dir,), daemon=True, name="retention").start()
    logging.info("retention: started (keep %d days, sweep every %dh)", RETENTION_DAYS, SWEEP_INTERVAL_HOURS)
