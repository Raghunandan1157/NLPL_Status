"""
Centralized memory monitoring, GC checkpoint, and processing gate.

Provides:
  - get_rss_mb()           -- current process RSS in megabytes
  - gc_checkpoint(step)    -- collect garbage and log delta
  - is_memory_pressure()   -- True when RSS exceeds budget OR system RAM low
  - is_system_critical()   -- True when system available RAM < 10% of total
  - try_acquire_processing() / release_processing()
                           -- serialization gate for heavy requests
  - MEMORY_BUDGET_BYTES    -- soft budget (from hardware_profile tier)

This is a *utility* module -- callers invoke gc_checkpoint at their
processing boundaries.  Nothing here triggers GC automatically.
"""

import gc
import logging
import os
import threading

from services.hardware_profile import (
    MEMORY_BUDGET_MB as _HP_MEMORY_BUDGET_MB,
    SYSTEM_PRESSURE_PCT,
    SYSTEM_CRITICAL_PCT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# psutil (optional -- graceful degradation if missing)
# ---------------------------------------------------------------------------
_HAS_PSUTIL = True
_process = None

try:
    import psutil
    _process = psutil.Process(os.getpid())
except ImportError:
    _HAS_PSUTIL = False
    logger.warning("psutil not installed -- memory monitoring disabled")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MEMORY_BUDGET_BYTES = _HP_MEMORY_BUDGET_MB * 1024 * 1024

# ---------------------------------------------------------------------------
# Memory measurement
# ---------------------------------------------------------------------------

def get_rss_mb() -> float:
    """Return current process RSS in megabytes (0.0 if psutil unavailable)."""
    if not _HAS_PSUTIL or _process is None:
        return 0.0
    return _process.memory_info().rss / (1024 * 1024)


# ---------------------------------------------------------------------------
# GC checkpoint
# ---------------------------------------------------------------------------

def gc_checkpoint(step_name: str) -> float:
    """Force garbage collection and log the memory delta.

    Returns the *after* RSS value in MB.
    """
    before = get_rss_mb()
    gc.collect()
    after = get_rss_mb()
    freed = before - after
    logger.info("GC [%s]: %.0fMB -> %.0fMB (freed %.0fMB)", step_name, before, after, freed)
    return after


# ---------------------------------------------------------------------------
# Memory pressure detection
# ---------------------------------------------------------------------------

def is_memory_pressure() -> bool:
    """Return True when memory pressure is detected.

    Two independent checks (either triggers pressure):
      1. Process RSS exceeds soft memory budget
      2. System-wide available RAM < SYSTEM_PRESSURE_PCT of total

    Conservative: returns False when psutil is unavailable (won't reject
    requests without actual measurement data).
    """
    if not _HAS_PSUTIL or _process is None:
        return False
    # Check 1: process RSS vs budget
    if _process.memory_info().rss > MEMORY_BUDGET_BYTES:
        return True
    # Check 2: system-wide available RAM
    try:
        vm = psutil.virtual_memory()
        if vm.available < vm.total * SYSTEM_PRESSURE_PCT:
            return True
    except Exception:
        pass
    return False


def is_system_critical() -> bool:
    """Return True when system available RAM is critically low.

    Triggers when available RAM < SYSTEM_CRITICAL_PCT (10%) of total.
    Returns False when psutil is unavailable.
    """
    if not _HAS_PSUTIL:
        return False
    try:
        vm = psutil.virtual_memory()
        return vm.available < vm.total * SYSTEM_CRITICAL_PCT
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Processing gate  (serializes heavy requests)
# ---------------------------------------------------------------------------
_processing_lock = threading.Lock()


def try_acquire_processing() -> bool:
    """Try to acquire the processing lock.

    * Pre-check: if system RAM critically low, force GC before proceeding.
    * Fast path (no contention): returns True immediately.
    * Lock held: always returns False (caller should 503).
      Never blocks — multi-device (ngrok) requires instant rejection.
    """
    # Force GC if system RAM is critically low (does not reject)
    if is_system_critical():
        gc_checkpoint('system-critical-pressure')

    if _processing_lock.acquire(blocking=False):
        return True

    # Lock is held — always reject (no blocking/waiting)
    return False


def release_processing() -> None:
    """Release the processing lock (safe to call even if not held)."""
    try:
        _processing_lock.release()
    except RuntimeError:
        pass  # already released -- defensive
