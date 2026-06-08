"""
Shared File Manager - handles file upload, validation, cleanup across all modules.
"""
import shutil
import logging
from datetime import datetime
from pathlib import Path
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


def validate_excel_file(file_obj):
    """Validate that an uploaded file is an Excel file."""
    if not file_obj or not file_obj.filename:
        return False, "No file provided"
    ext = Path(file_obj.filename).suffix.lower()
    if ext not in ('.xlsx', '.xls'):
        return False, f"Invalid file type: {ext}. Expected .xlsx or .xls"
    return True, "OK"


def save_uploaded_file(file_obj, target_dir, prefix=None, replace_pattern=None):
    """
    Save an uploaded file to target directory.
    If replace_pattern is given, delete matching files first.
    If prefix is given, prepend it to the filename.
    Returns the saved file path.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Delete existing files matching pattern
    if replace_pattern:
        for old_file in target_dir.glob(replace_pattern):
            try:
                old_file.unlink()
                logger.info(f"Deleted old file: {old_file}")
            except PermissionError:
                logger.warning(f"Could not delete {old_file} (permission denied)")

        # Also clean up Excel temp files (Windows: ~$filename, macOS: .~lock.filename#)
        for temp_pattern in [f"~${replace_pattern}", f".~lock.{replace_pattern}#"]:
            for temp_file in target_dir.glob(temp_pattern):
                try:
                    temp_file.unlink()
                except (PermissionError, OSError):
                    pass

    # Save new file (sanitize filename to prevent path traversal)
    filename = secure_filename(file_obj.filename) or 'uploaded_file.xlsx'
    if prefix:
        filename = f"{prefix}{filename}"
    filepath = target_dir / filename
    file_obj.save(str(filepath))
    logger.info(f"Saved file: {filepath}")
    return filepath


def find_file_by_pattern(directory, pattern):
    """Find a file in directory matching a glob pattern. Returns path or None."""
    directory = Path(directory)
    matches = list(directory.glob(pattern))
    if matches:
        return matches[0]
    return None


def save_file_to_backend(source_path, target_dir, prefix="EOD_Output_AUTOFLOW_"):
    """
    Copy a local file (not a Flask upload) to target_dir.
    Deletes existing EOD_Output_* files first, then saves with a timestamped name.
    The AUTOFLOW_ marker distinguishes auto-flow copies from manual uploads.
    Returns the saved file path.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Delete existing EOD_Output_* files
    for old_file in target_dir.glob("EOD_Output_*"):
        try:
            old_file.unlink()
            logger.info(f"Auto-flow: deleted old EOD output: {old_file.name}")
        except PermissionError:
            logger.warning(f"Auto-flow: could not delete {old_file} (permission denied)")

    # Save with timestamped name
    timestamp = datetime.now().strftime("%d-%b-%Y_%H-%M-%S")
    new_filename = f"{prefix}{timestamp}.xlsx"
    dest_path = target_dir / new_filename
    shutil.copy2(source_path, dest_path)
    logger.info(f"Auto-flow: saved EOD output to backend as {new_filename}")
    return dest_path


def save_to_downloads(source_path, filename=None):
    """
    Copy a file to ~/Downloads with dedup naming.
    If a file with the same name already exists, appends (1), (2), etc.
    Returns the saved file path.
    """
    downloads_dir = Path.home() / 'Downloads'
    downloads_dir.mkdir(parents=True, exist_ok=True)

    source_path = Path(source_path)
    if filename is None:
        filename = source_path.name

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    dest_path = downloads_dir / filename

    counter = 1
    while dest_path.exists():
        dest_path = downloads_dir / f"{stem} ({counter}){suffix}"
        counter += 1

    shutil.copy2(source_path, dest_path)
    logger.info(f"Saved to Downloads: {dest_path.name}")
    return dest_path


def format_file_size(size_bytes):
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
