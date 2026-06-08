"""
Centralized error classifier and user-friendly error formatter.
================================================================
Classifies Python exceptions into user-facing categories so that
no raw tracebacks, internal file paths, or variable names ever
reach the client.  Every category includes a concrete next-step
suggestion.

Exports:
    classify(exc)    -> category string
    user_error(exc)  -> dict with user_message, suggestion, category
"""

import logging
import traceback

try:
    import duckdb as _duckdb
    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error categories -- each has a user-facing message and suggestion
# ---------------------------------------------------------------------------
_CATEGORIES = {
    'oom': {
        'message': 'Not enough memory to process these files.',
        'suggestion': (
            'Try closing other applications, then run the report again. '
            'If the problem persists, try selecting a shorter date range.'
        ),
    },
    'duckdb': {
        'message': 'A database error occurred while processing your files.',
        'suggestion': (
            'The app will retry using a simpler processing method. '
            'If this happens again, try restarting the application.'
        ),
    },
    'file_format': {
        'message': 'One of the uploaded files could not be read.',
        'suggestion': (
            "Make sure all three files are valid Excel files (.xlsx) "
            "and the sheets are named 'Sheet1'."
        ),
    },
    'processing': {
        'message': 'An error occurred while processing your files.',
        'suggestion': (
            'Try running the report again. If the problem continues, '
            'close other applications to free memory.'
        ),
    },
    'generic': {
        'message': 'Something went wrong while processing your request.',
        'suggestion': (
            'Please try again. If the problem persists, restart the application.'
        ),
    },
}

# Keywords that signal a file-format issue in ValueError/KeyError messages
_FILE_KEYWORDS = frozenset(['sheet', 'column', 'header', 'excel', 'parquet'])


def classify(exc: Exception) -> str:
    """Return an error category string for the given exception.

    Categories: 'oom', 'duckdb', 'file_format', 'processing', 'generic'.
    """
    if isinstance(exc, MemoryError):
        return 'oom'

    if _HAS_DUCKDB and isinstance(exc, _duckdb.Error):
        # DuckDB can raise its own OOM errors
        exc_type_name = type(exc).__name__
        exc_str_lower = str(exc).lower()
        if 'OutOfMemory' in exc_type_name or 'out of memory' in exc_str_lower:
            return 'oom'
        return 'duckdb'

    if isinstance(exc, (ValueError, KeyError)):
        exc_str_lower = str(exc).lower()
        if any(kw in exc_str_lower for kw in _FILE_KEYWORDS):
            return 'file_format'
        # Known column names that typically signal a missing Excel column
        _KNOWN_COLS = frozenset([
            'trxdate', 'accountid', 'collectiontotal', 'reversetotal',
            'days group', 'dpd group', 'dpd days', 'loan status',
            'regular demand', 'meeting date', 'product name',
        ])
        if any(col in exc_str_lower for col in _KNOWN_COLS):
            return 'file_format'
        return 'processing'

    if isinstance(exc, OSError):
        if 'memory' in str(exc).lower():
            return 'oom'
        return 'processing'

    if isinstance(exc, (TypeError, AttributeError)):
        return 'processing'

    return 'generic'


def user_error(exc: Exception, context: str = '') -> dict:
    """Build a user-friendly error dict from an exception.

    Logs the full traceback server-side for debugging, then returns
    a sanitised dict safe to send to the client.

    Returns:
        dict with keys: user_message, suggestion, category
    """
    logger.error(
        "Processing error [context=%s]: %s\n%s",
        context or 'unknown',
        exc,
        traceback.format_exc(),
    )
    cat = classify(exc)
    entry = _CATEGORIES[cat]
    exc_type = type(exc).__name__
    exc_msg = str(exc)[:160]
    return {
        'user_message': f"{entry['message']} ({exc_type}: {exc_msg})",
        'suggestion': entry['suggestion'],
        'category': cat,
    }
