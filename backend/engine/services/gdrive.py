"""
Google Drive helpers for downloading files from public shared folders.

No API key required — works with publicly shared folders ("Anyone with the link can view").
Uses the Google Drive embed page to list files, and the public export URL to download.
Uses `requests` (already in requirements.txt) — no new dependencies.
"""

import re
import json
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL / ID parsing
# ---------------------------------------------------------------------------
_FOLDER_ID_PATTERNS = [
    re.compile(r'folders/([a-zA-Z0-9_-]+)'),           # .../folders/FOLDER_ID
    re.compile(r'id=([a-zA-Z0-9_-]+)'),                # ?id=FOLDER_ID
    re.compile(r'^([a-zA-Z0-9_-]{20,})$'),              # raw folder ID
]


def parse_folder_id(url: str) -> str | None:
    """Extract Google Drive folder ID from various URL formats."""
    url = url.strip()
    for pattern in _FOLDER_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# List files in a public folder via the embed page (no API key needed)
# ---------------------------------------------------------------------------
# The embed page (embeddedfolderview) returns lightweight HTML with file
# entries in <div class="flip-entry" id="entry-{FILE_ID}"> elements and
# file names in <div class="flip-entry-title">{name}</div>.
_EMBED_ENTRY_RE = re.compile(
    r'class="flip-entry"[^>]*id="entry-([a-zA-Z0-9_-]+)"'  # file ID
    r'.*?class="flip-entry-title">([^<]+)</div>',            # file name
    re.DOTALL,
)


def list_folder_files_public(folder_id: str) -> list[dict]:
    """
    List files in a public Google Drive folder using the embed page.
    Returns list of {id, name} dicts. No API key needed.
    """
    url = f'https://drive.google.com/embeddedfolderview?id={folder_id}#list'

    resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    resp.raise_for_status()
    html = resp.text

    files = []
    for m in _EMBED_ENTRY_RE.finditer(html):
        fid, fname = m.group(1), m.group(2).strip()
        files.append({'id': fid, 'name': fname})

    logger.info(f"GDrive embed scan: found {len(files)} file(s) in folder {folder_id}")
    return files


# ---------------------------------------------------------------------------
# Match required files (PAR + Collection) — collects ALL matches
# ---------------------------------------------------------------------------
def find_required_files(files: list[dict]) -> dict:
    """
    Filter for files whose names start with 'par' or 'collection' (case-insensitive).
    Returns {'par': [list of matches], 'collection': [list of matches]}.
    Each match is {id, name}.
    """
    result = {'par': [], 'collection': []}
    for f in files:
        name_lower = f['name'].lower()
        if name_lower.startswith('par'):
            result['par'].append({'id': f['id'], 'name': f['name']})
        elif name_lower.startswith('collection'):
            result['collection'].append({'id': f['id'], 'name': f['name']})
    return result


# ---------------------------------------------------------------------------
# Download a file by ID (public export URL — no API key)
# ---------------------------------------------------------------------------
def download_file(file_id: str, dest_path: Path, progress_fn=None) -> Path:
    """
    Download a Google Drive file by ID using the public export URL:
    https://drive.google.com/uc?export=download&id=FILE_ID

    Handles large-file virus-scan confirmation automatically.
    Streams to disk. Validates the result is an Excel file (PK/ZIP magic bytes).

    progress_fn: optional callback(downloaded_bytes, total_bytes_or_none)
                 called every ~1MB to report progress.
    """
    # Use drive.usercontent.google.com — Google's newer direct download domain.
    # The old drive.google.com/uc URL returns HTML confirmation pages for large
    # files that are increasingly hard to bypass without an API key.
    url = f'https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t'
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    resp = session.get(url, stream=True, timeout=120)
    resp.raise_for_status()

    # Peek at the first chunk to detect HTML virus-scan confirmation page
    first_chunk = next(resp.iter_content(chunk_size=4096), b'')

    if first_chunk[:2] != b'PK' and (b'<html' in first_chunk.lower() or b'<!doctype' in first_chunk.lower()):
        # Large file → Google shows a virus-scan confirmation page
        # Strategy 1: extract the real confirm token from the page
        # Strategy 2: use confirm=t as fallback
        # Strategy 3: use the download_warning cookie Google sets
        logger.info("GDrive: large file detected, attempting confirmation bypass")
        # Read the full HTML to extract the confirm token
        html_body = first_chunk + resp.content
        resp.close()

        # Try to extract confirm token from the HTML form/link
        confirm_token = None
        m = re.search(r'confirm=([a-zA-Z0-9_-]+)', html_body.decode('utf-8', errors='ignore'))
        if m:
            confirm_token = m.group(1)
            logger.info(f"GDrive: extracted confirm token: {confirm_token}")

        # Also check for download_warning cookie
        if not confirm_token:
            for cookie_name, cookie_val in session.cookies.items():
                if 'download_warning' in cookie_name:
                    confirm_token = cookie_val
                    logger.info(f"GDrive: using download_warning cookie: {confirm_token}")
                    break

        # Use extracted token, or fall back to 't'
        params = {'confirm': confirm_token or 't'}
        resp = session.get(url, params=params, stream=True, timeout=300)
        resp.raise_for_status()
        first_chunk = next(resp.iter_content(chunk_size=4096), b'')

        # If still HTML, try the old drive.google.com URL as last resort
        if first_chunk[:2] != b'PK' and (b'<html' in first_chunk[:200].lower() or b'<!doctype' in first_chunk[:200].lower()):
            logger.info("GDrive: confirm token failed, trying legacy download URL")
            resp.close()
            alt_url = f'https://drive.google.com/uc?export=download&id={file_id}&confirm=t'
            resp = session.get(alt_url, stream=True, timeout=300)
            resp.raise_for_status()
            first_chunk = next(resp.iter_content(chunk_size=4096), b'')

    # Get total size from Content-Length header (may be absent)
    total = resp.headers.get('Content-Length')
    total = int(total) if total else None

    # Write first chunk + rest to disk, reporting progress
    downloaded = 0
    last_report = 0
    REPORT_INTERVAL = 1024 * 1024  # report every ~1 MB

    with open(dest_path, 'wb') as f:
        f.write(first_chunk)
        downloaded += len(first_chunk)

        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)

            if progress_fn and (downloaded - last_report) >= REPORT_INTERVAL:
                progress_fn(downloaded, total)
                last_report = downloaded

    # Final progress report
    if progress_fn:
        progress_fn(downloaded, total or downloaded)

    file_size = dest_path.stat().st_size

    # Validate: Excel files are ZIP archives (start with PK), CSV files are plain text
    is_csv = dest_path.suffix.lower() in ('.csv', '.tsv')
    with open(dest_path, 'rb') as f:
        magic = f.read(2)
    if not is_csv and magic != b'PK':
        # Check if it's actually a valid CSV/text file (not HTML error page)
        with open(dest_path, 'rb') as f:
            head = f.read(512)
        is_html = b'<html' in head.lower() or b'<!doctype' in head.lower()
        if is_html:
            dest_path.unlink(missing_ok=True)
            raise ValueError(
                f"Downloaded file is an HTML page, not the actual file. "
                "The Google Drive file may not be publicly accessible."
            )
        # Non-PK, non-HTML: likely a CSV with wrong extension — allow it
        logger.info(f"Downloaded file starts with {magic!r} (not PK/ZIP). "
                     f"Treating as plain-text/CSV file.")

    logger.info(f"Downloaded {dest_path.name} ({file_size / (1024*1024):.1f} MB)")
    return dest_path


# ---------------------------------------------------------------------------
# Persistent config (saved link)
# ---------------------------------------------------------------------------
def load_gdrive_config(config_path: Path) -> dict:
    """Load saved GDrive config (folder URL, etc.) from disk."""
    config_path = Path(config_path)
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_gdrive_config(config_path: Path, data: dict) -> None:
    """Save GDrive config to disk."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2))
