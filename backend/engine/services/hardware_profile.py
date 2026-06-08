"""
Hardware detection and auto-tuning constants.

Detects RAM, CPU cores, and classifies the machine into a tier (low/medium/high)
at import time.  Exposes computed tuning constants used by config.py and
memory_manager.py.

Environment variables ALWAYS override any auto-detected value:
  COLLECTION_MEMORY_BUDGET_MB, COLLECTION_DUCKDB_MEMORY_MB,
  COLLECTION_DUCKDB_THREADS, COLLECTION_THREADS,
  COLLECTION_DB_CACHE_MAX_MB, COLLECTION_INSTANT_HISTORY_MAX_MB,
  COLLECTION_TOTAL_CACHE_MAX_MB, COLLECTION_MERGED_DF_MAX_ENTRIES

IMPORTANT: This module must NOT import config.py (avoids circular imports).
"""

import logging
import os
import platform
import shutil
import sys

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# psutil (optional)
# ---------------------------------------------------------------------------
_HAS_PSUTIL = True
try:
    import psutil
except ImportError:
    _HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Detection helpers (called once at module level)
# ---------------------------------------------------------------------------

def _detect_total_ram_bytes():
    """Return total physical RAM in bytes, or None if detection fails."""
    if _HAS_PSUTIL:
        try:
            return psutil.virtual_memory().total
        except Exception:
            pass
    # POSIX fallback
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        page_size = os.sysconf('SC_PAGE_SIZE')
        return pages * page_size
    except (AttributeError, ValueError, OSError):
        pass
    return None


def _detect_cpu_cores():
    """Return physical CPU core count (best effort)."""
    if _HAS_PSUTIL:
        try:
            cores = psutil.cpu_count(logical=False)
            if cores and cores > 0:
                return cores
        except Exception:
            pass
    # Fallback: half of logical cores (approximation of physical)
    try:
        logical = os.cpu_count()
        if logical and logical > 0:
            return max(1, logical // 2)
    except Exception:
        pass
    return 2  # ultimate fallback


def _detect_available_ram_bytes():
    """Return currently available RAM in bytes, or None if psutil unavailable."""
    if _HAS_PSUTIL:
        try:
            return psutil.virtual_memory().available
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Raw detected values (module-level constants)
# ---------------------------------------------------------------------------
TOTAL_RAM_BYTES = _detect_total_ram_bytes()
TOTAL_RAM_MB = (TOTAL_RAM_BYTES or 8 * 1024**3) // (1024 * 1024)
CPU_CORES = _detect_cpu_cores()

# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

def _classify_tier(ram_mb, cores):
    """Classify hardware into low / medium / high tier.

    Uses 10GB ceiling for 'low' (not 8GB) because Windows 8GB machines
    report 7.6-7.9GB usable RAM.
    """
    if ram_mb <= 10240 or cores <= 2:
        return 'low'
    if ram_mb <= 20480 and cores <= 8:
        return 'medium'
    return 'high'


TIER = _classify_tier(TOTAL_RAM_MB, CPU_CORES)

# ---------------------------------------------------------------------------
# Tier profiles
# ---------------------------------------------------------------------------
_TIER_PROFILES = {
    'low': {
        'memory_budget_mb': 3072,
        'duckdb_memory_mb': 1024,
        'duckdb_threads': 2,
        'wsgi_threads': 4,
        'db_cache_max_mb': 150,
        'instant_history_max_mb': 300,
        'total_cache_max_mb': 500,
        'merged_df_max_entries': 3,
    },
    'medium': {
        'memory_budget_mb': 6144,
        'duckdb_memory_mb': 2048,
        'duckdb_threads': 4,
        'wsgi_threads': 4,
        'db_cache_max_mb': 300,
        'instant_history_max_mb': 500,
        'total_cache_max_mb': 1000,
        'merged_df_max_entries': 5,
    },
    'high': {
        'memory_budget_mb': 8192,
        'duckdb_memory_mb': 4096,
        'duckdb_threads': min(CPU_CORES, 8),
        'wsgi_threads': min(CPU_CORES, 8),
        'db_cache_max_mb': 500,
        'instant_history_max_mb': 1000,
        'total_cache_max_mb': 2000,
        'merged_df_max_entries': 10,
    },
}

PROFILE = _TIER_PROFILES[TIER]

# ---------------------------------------------------------------------------
# Exposed constants (env var overrides)
# ---------------------------------------------------------------------------
MEMORY_BUDGET_MB = int(os.environ.get('COLLECTION_MEMORY_BUDGET_MB', str(PROFILE['memory_budget_mb'])))
DUCKDB_MEMORY_MB = int(os.environ.get('COLLECTION_DUCKDB_MEMORY_MB', str(PROFILE['duckdb_memory_mb'])))
DUCKDB_THREADS = int(os.environ.get('COLLECTION_DUCKDB_THREADS', str(PROFILE['duckdb_threads'])))
WSGI_THREADS = int(os.environ.get('COLLECTION_THREADS', str(PROFILE['wsgi_threads'])))
DB_CACHE_MAX_MB = int(os.environ.get('COLLECTION_DB_CACHE_MAX_MB', str(PROFILE['db_cache_max_mb'])))
INSTANT_HISTORY_MAX_MB = int(os.environ.get('COLLECTION_INSTANT_HISTORY_MAX_MB', str(PROFILE['instant_history_max_mb'])))
TOTAL_CACHE_MAX_MB = int(os.environ.get('COLLECTION_TOTAL_CACHE_MAX_MB', str(PROFILE['total_cache_max_mb'])))
MERGED_DF_MAX_ENTRIES = int(os.environ.get('COLLECTION_MERGED_DF_MAX_ENTRIES', str(PROFILE['merged_df_max_entries'])))

# ---------------------------------------------------------------------------
# System pressure threshold constants
# ---------------------------------------------------------------------------
SYSTEM_PRESSURE_PCT = 0.15   # pressure when available RAM < 15% of total
SYSTEM_CRITICAL_PCT = 0.10   # critical when available RAM < 10% of total

# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------
logger.info(
    "Hardware profile: tier=%s RAM=%dMB cores=%d budget=%dMB duckdb=%dMB/%dt",
    TIER, TOTAL_RAM_MB, CPU_CORES, MEMORY_BUDGET_MB, DUCKDB_MEMORY_MB, DUCKDB_THREADS,
)
