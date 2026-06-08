"""
Shared Excel Reader - common Excel reading utilities.
Uses calamine engine for fast reading, openpyxl for formatting-aware reading.
Provides streaming hash and save-to-temp utilities for memory-efficient file handling.
"""
import hashlib
import uuid
from pathlib import Path

import pandas as pd
import logging

import config

logger = logging.getLogger(__name__)


def compute_file_hash(file_path, chunk_size=1024 * 1024):
    """Compute MD5 hash of the first *chunk_size* bytes of a file on disk.

    Reads at most *chunk_size* bytes (default 1 MB) so it never loads the
    entire file into RAM.  Returns a 16-character hex digest suitable for
    cache keys.

    Args:
        file_path: Path or str pointing to a file on disk.
        chunk_size: Maximum number of bytes to read (default 1 MB).

    Returns:
        str: 16-char hex hash string.
    """
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read(chunk_size))
    return hasher.hexdigest()[:16]


def save_upload_to_temp(upload_file, prefix="upload_", suffix=".xlsx"):
    """Save a Flask FileStorage object to a temporary file without calling read().

    Uses ``upload_file.save()`` which streams directly to disk, avoiding
    loading the entire file into memory.

    Args:
        upload_file: A Flask/Werkzeug ``FileStorage`` object.
        prefix: Filename prefix (default ``"upload_"``).
        suffix: Filename suffix / extension (default ``".xlsx"``).

    Returns:
        pathlib.Path: Path to the saved temporary file in ``config.TEMP_DIR``.
    """
    temp_dir = Path(config.TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{prefix}{uuid.uuid4().hex}{suffix}"
    temp_path = temp_dir / unique_name
    upload_file.save(str(temp_path))
    return temp_path


def read_excel_fast(file_path, sheet_name=0, usecols=None):
    """Read Excel file using calamine engine (fast, no formatting)."""
    try:
        return pd.read_excel(file_path, sheet_name=sheet_name, usecols=usecols, engine='calamine')
    except Exception:
        # Fallback to default engine
        return pd.read_excel(file_path, sheet_name=sheet_name, usecols=usecols)


def smart_read_excel(file_path, preferred_sheet="Sheet1", usecols=None):
    """
    Read Excel with fallback sheet logic and calamine engine for speed.
    Tries preferred_sheet first, falls back to first available sheet.
    Uses calamine (Rust-based) engine for ~30% faster reads on large files,
    with automatic openpyxl fallback for edge-case files.

    Auto-detects the header row when the first row yields mostly 'Unnamed:'
    columns (common when a title row sits above the real headers).
    """
    logger.debug(f"smart_read_excel: reading {file_path} with calamine engine")

    def _has_real_headers(df):
        """Return True if the DataFrame's columns look like actual headers."""
        if len(df.columns) == 0:
            return False
        unnamed_count = sum(1 for c in df.columns if str(c).startswith('Unnamed:'))
        return unnamed_count <= len(df.columns) * 0.3  # <= 30% unnamed

    def _try_read(xls, sheet_name, header):
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=header, usecols=usecols)
            if _has_real_headers(df):
                logger.info(f"smart_read_excel: using header row {header} for sheet '{sheet_name}'")
                return df
        except Exception:
            pass
        return None

    try:
        try:
            xls = pd.ExcelFile(file_path, engine='calamine')
        except Exception as e:
            logger.warning(f"calamine engine failed for {file_path}, falling back to openpyxl: {e}")
            xls = pd.ExcelFile(file_path)

        with xls:
            sheet_name = preferred_sheet if preferred_sheet in xls.sheet_names else xls.sheet_names[0]
            if sheet_name != preferred_sheet:
                logger.info(f"Sheet '{preferred_sheet}' not found, using '{sheet_name}'")

            # Try default header=0 first
            df = _try_read(xls, sheet_name, header=0)
            if df is not None:
                return df

            # If header=0 produced only unnamed columns, scan rows 1-4
            for header in range(1, 5):
                df = _try_read(xls, sheet_name, header=header)
                if df is not None:
                    logger.info(f"Auto-detected header at row {header + 1} (0-based header={header})")
                    return df

            # Fallback: return whatever header=0 gave us so the caller sees the real columns
            logger.warning(f"Could not auto-detect header row for '{sheet_name}'; returning raw row 0")
            return pd.read_excel(xls, sheet_name=sheet_name, header=0, usecols=usecols)
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        raise
