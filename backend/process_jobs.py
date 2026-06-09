"""Process job registry — lightweight, in-process tracking for long-running
report generations so the frontend can show status and request cancellation.

IMPORTANT — cancellation model (read this before expecting magic):
The report engines (EOD/Hourly/...) run SYNCHRONOUSLY inside the HTTP request on
CPU-bound pandas / DuckDB / xlsxwriter calls. Those library calls are NOT
interruptible from Python. So cancellation here is *cooperative*: a cancel
request sets a flag, and the processing code checks that flag at phase
boundaries via ``checkpoint(job_id)``. Between two checkpoints the current
operation runs to completion; once the next checkpoint is reached the job aborts
cleanly (lock released, temp files removed). It does NOT kill an in-flight
pandas read mid-call. This is honest partial cancellation — good enough to stop
a multi-phase run promptly without rewriting the vendored engine.

The registry is a single-process dict guarded by a lock. It is not shared across
worker processes (the app runs single-process with a global processing lock, so
only one heavy job runs at a time anyway).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


class JobCancelled(Exception):
    """Raised by checkpoint() when a job has been asked to cancel."""


_lock = threading.Lock()
_jobs: dict[str, dict] = {}

# Keep finished jobs around briefly so a late status poll still resolves.
_RETAIN_SECONDS = 120

# A RUNNING/CANCELLING job older than this is considered stale (crashed/stuck);
# the start path reaps it and reclaims the processing slot. Generous so a slow
# real run is never killed.
def _stale_seconds() -> int:
    try:
        return int(os.environ.get("NLPL_JOB_STALE_SECONDS", "900"))
    except ValueError:
        return 900

# Terminal states (a new process may start when the active job is one of these).
_TERMINAL = ("completed", "cancelled", "error", "abandoned", "unknown")


def _prune_locked() -> None:
    now = time.time()
    stale = [
        jid
        for jid, j in _jobs.items()
        if j["status"] in ("completed", "cancelled", "error")
        and (now - j.get("ended_at", now)) > _RETAIN_SECONDS
    ]
    for jid in stale:
        _jobs.pop(jid, None)


def start(job_id: Optional[str], module: str) -> str:
    """Register a new running job. If job_id is falsy a uuid is generated.
    Returns the job id actually used (echo the client's so cancel can match)."""
    jid = (job_id or "").strip() or uuid.uuid4().hex
    with _lock:
        _prune_locked()
        _jobs[jid] = {
            "id": jid,
            "module": module,
            "status": "running",
            "started_at": time.time(),
            "ended_at": None,
            "cancel": threading.Event(),
            "temp_dirs": [],
            "error": None,
        }
    return jid


def get(job_id: str) -> Optional[dict]:
    with _lock:
        return _jobs.get(job_id)


def is_cancelled(job_id: str) -> bool:
    j = get(job_id)
    return bool(j and j["cancel"].is_set())


def checkpoint(job_id: Optional[str]) -> None:
    """Cooperative cancellation point — call between processing phases.
    Raises JobCancelled if the job has been asked to stop."""
    if not job_id:
        return
    if is_cancelled(job_id):
        raise JobCancelled(job_id)


def request_cancel(module: str, job_id: str) -> dict:
    """Flag a job for cancellation. Returns a status dict for the client."""
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"found": False, "status": "unknown", "cancelled": False}
        if j["module"] != module:
            # Module mismatch — still honor by id, but report it.
            pass
        if j["status"] == "running":
            j["cancel"].set()
            j["status"] = "cancelling"
        return {
            "found": True,
            "status": j["status"],
            "cancelled": True,
        }


def add_temp(job_id: Optional[str], path) -> None:
    """Track a temp directory so it can be removed if the job is cancelled."""
    if not job_id or not path:
        return
    with _lock:
        j = _jobs.get(job_id)
        if j:
            j["temp_dirs"].append(str(path))


def cleanup(job_id: Optional[str]) -> None:
    """Remove any tracked temp directories for a job (best-effort)."""
    if not job_id:
        return
    with _lock:
        j = _jobs.get(job_id)
        dirs = list(j["temp_dirs"]) if j else []
    for d in dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def finish(job_id: Optional[str], status: str = "completed", error: Optional[str] = None) -> None:
    """Mark a job done. If it was cancelling, the final status becomes 'cancelled'."""
    if not job_id:
        return
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return
        if j["status"] == "cancelling" or j["cancel"].is_set():
            j["status"] = "cancelled"
        else:
            j["status"] = status
        j["error"] = error
        j["ended_at"] = time.time()


def active() -> Optional[dict]:
    """Return a snapshot of the job currently holding the processing slot
    (status 'running' or 'cancelling'), or None."""
    now = time.time()
    with _lock:
        for j in _jobs.values():
            if j["status"] in ("running", "cancelling"):
                return {
                    "id": j["id"],
                    "module": j["module"],
                    "status": j["status"],
                    "age": round(now - j["started_at"], 1),
                }
    return None


def reap_stale() -> list:
    """Mark any running/cancelling job older than the stale timeout as
    'abandoned'. Returns the ids reaped — the caller should force-release the
    processing lock so a new run can start (covers a crashed/stuck job)."""
    reaped = []
    now = time.time()
    limit = _stale_seconds()
    with _lock:
        for j in _jobs.values():
            if j["status"] in ("running", "cancelling") and (now - j["started_at"]) > limit:
                j["status"] = "abandoned"
                j["ended_at"] = now
                j["error"] = "stale-timeout"
                reaped.append(j["id"])
    return reaped


def status_of(module: str, job_id: str) -> dict:
    """Status snapshot for the /status endpoint."""
    j = get(job_id)
    if not j:
        return {"id": job_id, "module": module, "status": "unknown", "found": False}
    return {
        "id": job_id,
        "module": j["module"],
        "status": j["status"],
        "found": True,
        "elapsed": round(time.time() - j["started_at"], 1),
        "error": j["error"],
    }
