"""EOD per-run report history — thin wrapper over ``report_history``.

Kept as a module so the app factory's import stays stable. Each EOD generation
creates its own run folder under ``eod_data/reports_archive/<date>/<runId>/``;
nothing is overwritten. See ``report_history`` for the implementation.
"""
from __future__ import annotations

import report_history as _h

MODULE = "eod"


def snapshot(data_dir, date_str: str) -> dict:
    return _h.snapshot(data_dir, MODULE, date_str)


def list_history(data_dir) -> list:
    return _h.list_history(data_dir, MODULE)


def file_path(data_dir, date: str, run_id: str, type_: str):
    return _h.file_path(data_dir, MODULE, date, run_id, type_)
