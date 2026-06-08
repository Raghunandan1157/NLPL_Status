"""
Centralized cache lifecycle management -- disk pressure detection,
directory size calculation, age+size eviction, and startup cleanup.

Provides:
  - get_disk_free_bytes(path)   -- free bytes on the filesystem
  - get_disk_free_pct(path)     -- free percentage (0-100)
  - is_disk_pressure(path)      -- True when free% below threshold
  - is_disk_critical(path)      -- True when free bytes below critical
  - get_dir_size_bytes(dir)     -- total size of directory tree
  - get_file_age_days(path)     -- age in days based on mtime
  - ensure_cache_budget(...)    -- three-phase eviction engine
  - startup_cleanup()           -- one-shot sweep at app startup

Mirrors services/memory_manager.py in structure and style.
All operations are synchronous -- no background threads.
"""

import logging
import shutil
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Disk pressure detection
# ---------------------------------------------------------------------------

def get_disk_free_bytes(path=None):
    """Return free disk space in bytes for the filesystem containing *path*.

    Defaults to config.DATA_DIR.  Returns None on OSError.
    """
    if path is None:
        path = config.DATA_DIR
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free
    except OSError:
        logger.warning("Could not determine disk usage for %s", path)
        return None


def get_disk_free_pct(path=None):
    """Return free disk space as a percentage (0-100).

    Defaults to config.DATA_DIR.  Returns None on OSError.
    """
    if path is None:
        path = config.DATA_DIR
    try:
        usage = shutil.disk_usage(str(path))
        return (usage.free / usage.total) * 100 if usage.total > 0 else 0
    except OSError:
        return None


def is_disk_pressure(path=None):
    """Return True when free disk drops below DISK_PRESSURE_THRESHOLD_PCT.

    Conservative: returns False when disk_usage fails (don't block
    caching without actual measurement data).
    """
    free_pct = get_disk_free_pct(path)
    if free_pct is None:
        return False
    return free_pct < config.DISK_PRESSURE_THRESHOLD_PCT


def is_disk_critical(path=None):
    """Return True when free bytes drop below DISK_CRITICAL_THRESHOLD_BYTES."""
    free = get_disk_free_bytes(path)
    if free is None:
        return False
    return free < config.DISK_CRITICAL_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# Directory size calculation
# ---------------------------------------------------------------------------

def get_dir_size_bytes(directory):
    """Return total size of all files in *directory* tree (bytes).

    Returns 0 if directory does not exist.  Catches OSError per file.
    """
    directory = Path(directory)
    if not directory.exists():
        return 0
    total = 0
    try:
        for f in directory.rglob('*'):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass  # skip inaccessible files
    except OSError as exc:
        logger.warning("Error calculating size of %s: %s", directory, exc)
    return total


# ---------------------------------------------------------------------------
# Age helpers
# ---------------------------------------------------------------------------

def get_file_age_days(path):
    """Return file/directory age in days based on mtime.

    Returns float('inf') on OSError (treat inaccessible as infinitely old).
    """
    try:
        mtime = Path(path).stat().st_mtime
        return (time.time() - mtime) / 86400
    except OSError:
        return float('inf')


# ---------------------------------------------------------------------------
# Eviction engine
# ---------------------------------------------------------------------------

def _remove_entry(path):
    """Remove a file or directory, handling Windows file locks gracefully."""
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    logger.info("Cache eviction: removed %s", path)


def _find_oldest_entry(directory, skip_fn=None):
    """Return the entry with the oldest mtime in *directory*.

    Skips entries starting with '.'.  If *skip_fn* is provided, also skips
    entries for which ``skip_fn(entry)`` returns True.
    Returns None if directory is empty.
    """
    directory = Path(directory)
    if not directory.exists():
        return None
    oldest = None
    oldest_mtime = float('inf')
    try:
        for entry in directory.iterdir():
            if entry.name.startswith('.'):
                continue
            if skip_fn and skip_fn(entry):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0  # treat inaccessible as oldest
            if mtime < oldest_mtime:
                oldest_mtime = mtime
                oldest = entry
    except OSError:
        pass
    return oldest


def ensure_cache_budget(cache_dir, max_size_mb, max_age_days=None,
                        skip_fn=None):
    """Three-phase eviction: age -> size -> disk pressure.

    Phase 1 (age):  Remove entries older than *max_age_days*.
    Phase 2 (size): Remove oldest entries until dir < *max_size_mb* MB.
    Phase 3 (pressure): Remove oldest entries while disk pressure detected.

    If *skip_fn* is provided, entries for which ``skip_fn(entry)`` returns
    True are never evicted.

    Returns the number of entries evicted.
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0

    evicted = 0

    # Phase 1: age-based eviction
    if max_age_days is not None:
        entries = []
        try:
            entries = sorted(cache_dir.iterdir(), key=lambda e: e.name)
        except OSError:
            pass
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            if skip_fn and skip_fn(entry):
                continue
            if get_file_age_days(entry) > max_age_days:
                _remove_entry(entry)
                evicted += 1

    # Phase 2: size-based eviction (oldest first)
    max_size_bytes = max_size_mb * 1024 * 1024
    while get_dir_size_bytes(cache_dir) > max_size_bytes:
        oldest = _find_oldest_entry(cache_dir, skip_fn=skip_fn)
        if oldest is None:
            break
        _remove_entry(oldest)
        evicted += 1

    # Phase 3: disk pressure eviction (emergency)
    while is_disk_pressure():
        oldest = _find_oldest_entry(cache_dir, skip_fn=skip_fn)
        if oldest is None:
            break
        _remove_entry(oldest)
        evicted += 1

    if evicted:
        logger.info(
            "Cache eviction: removed %d entries from %s", evicted, cache_dir
        )

    return evicted


# ---------------------------------------------------------------------------
# Startup cleanup  (called once from app.py)
# ---------------------------------------------------------------------------

def _clean_temp_files():
    """Remove files/folders in TEMP_DIR older than 1 hour.

    Catches leaked temp files from interrupted sessions.
    Returns bytes freed.
    """
    temp_dir = config.TEMP_DIR
    if not temp_dir.exists():
        return 0

    freed = 0
    one_hour_seconds = 3600
    try:
        for entry in list(temp_dir.iterdir()):
            age_seconds = time.time() - entry.stat().st_mtime
            if age_seconds > one_hour_seconds:
                size = (
                    get_dir_size_bytes(entry) if entry.is_dir()
                    else entry.stat().st_size
                )
                _remove_entry(entry)
                freed += size
    except OSError as exc:
        logger.warning("Error cleaning temp files: %s", exc)
    return freed


def _is_last_cache_file(entry):
    """Return True for files that must survive eviction.

    Protects:
      - daily_*_last.* (the "use last cache" copies)
      - daily_*_cache_*.parquet (active DuckDB parquet caches — recreating
        these from Excel costs ~10s each, which is the main processing bottleneck)
    """
    name = entry.name
    if name.startswith('daily_') and '_last.' in name:
        return True
    if name.startswith('daily_') and name.endswith('.parquet'):
        return True
    return False


def _evict_old_db_cache():
    """Enforce age and size limits on DB cache directory.

    Protects ``daily_*_last.*`` files (the "use last cache" copies) from
    eviction so they survive server restarts.

    Returns bytes freed.
    """
    before = get_dir_size_bytes(config.DB_CACHE_DIR)
    ensure_cache_budget(
        config.DB_CACHE_DIR,
        config.DB_CACHE_MAX_SIZE_MB,
        config.CACHE_MAX_AGE_DAYS,
        skip_fn=_is_last_cache_file,
    )
    after = get_dir_size_bytes(config.DB_CACHE_DIR)
    return max(before - after, 0)


def _evict_old_instant_history():
    """Enforce age and size limits on instant history directory.

    Returns bytes freed.
    """
    before = get_dir_size_bytes(config.INSTANT_HISTORY_DIR)
    ensure_cache_budget(
        config.INSTANT_HISTORY_DIR,
        config.INSTANT_HISTORY_MAX_SIZE_MB,
        config.INSTANT_CACHE_MAX_DAYS,
    )
    after = get_dir_size_bytes(config.INSTANT_HISTORY_DIR)
    return max(before - after, 0)


def _evict_old_backend_monthly():
    """Remove backend-monthly folders older than BACKEND_MONTHLY_MAX_MONTHS.

    Uses folder mtime (not contents) for speed per Research Pitfall 5.
    Returns bytes freed.
    """
    monthly_dir = config.BACKEND_MONTHLY_DIR
    if not monthly_dir.exists():
        return 0

    max_age_days = config.BACKEND_MONTHLY_MAX_MONTHS * 30  # approximate
    freed = 0
    try:
        for folder in list(monthly_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            if get_file_age_days(folder) > max_age_days:
                size = get_dir_size_bytes(folder)
                _remove_entry(folder)
                freed += size
    except OSError as exc:
        logger.warning("Error evicting backend-monthly: %s", exc)
    return freed


def startup_cleanup():
    """Run once at app startup.  Clean all cache directories and log summary."""
    logger.info("Cache manager: startup cleanup starting")

    total_freed = 0

    # 1. Clean stale temp files
    total_freed += _clean_temp_files()

    # 2. Evict old DB cache entries
    total_freed += _evict_old_db_cache()

    # 3. Evict old instant history entries
    total_freed += _evict_old_instant_history()

    # 4. Evict old backend-monthly folders
    total_freed += _evict_old_backend_monthly()

    # 5. Log disk status summary
    free_pct = get_disk_free_pct()
    total_data_mb = get_dir_size_bytes(config.DATA_DIR) / (1024 * 1024)
    logger.info(
        "Cache manager: startup complete. Freed %.1fMB. "
        "Total data dir: %.1fMB. Disk free: %.1f%%",
        total_freed / (1024 * 1024),
        total_data_mb,
        free_pct if free_pct is not None else -1,
    )
