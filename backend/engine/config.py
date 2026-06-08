"""
Unified Collection Report Server - Configuration

All settings can be overridden via environment variables prefixed with COLLECTION_.
Example: COLLECTION_HOST=127.0.0.1  COLLECTION_PORT=8080  python app.py
"""
import os
from pathlib import Path

# Load .env file if present (no dependency on python-dotenv)
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _key, _, _val = _line.partition('=')
                os.environ.setdefault(_key.strip(), _val.strip())

from services.hardware_profile import (
    DB_CACHE_MAX_MB as _HP_DB_CACHE_MAX_MB,
    INSTANT_HISTORY_MAX_MB as _HP_INSTANT_HISTORY_MAX_MB,
    TOTAL_CACHE_MAX_MB as _HP_TOTAL_CACHE_MAX_MB,
    WSGI_THREADS as _HP_WSGI_THREADS,
)

# Base directory (where this file lives)
BASE_DIR = Path(__file__).parent.resolve()

# Server - overridable via COLLECTION_HOST / COLLECTION_PORT
HOST = os.environ.get('COLLECTION_HOST', '127.0.0.1')
PORT = int(os.environ.get('COLLECTION_PORT', '5000'))
DEBUG = os.environ.get('COLLECTION_DEBUG', '').lower() in ('1', 'true', 'yes')

# WSGI server threads (used by waitress / gunicorn)
THREADS = _HP_WSGI_THREADS

# Upload limits
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB (month-end needs 4 large files)
MAX_FORM_MEMORY_SIZE = 1 * 1024 * 1024   # 1 MB (files > 1MB stream to disk)

# Data directories - base can be overridden via COLLECTION_DATA_DIR
DATA_DIR = Path(os.environ.get('COLLECTION_DATA_DIR', str(BASE_DIR / 'data')))
BACKEND_DATA_DIR = DATA_DIR / 'backend'
BACKEND_MONTHLY_DIR = DATA_DIR / 'backend-monthly'
DB_DIR = DATA_DIR / 'db'
DB_CACHE_DIR = DB_DIR / 'cache'
REPORTS_DIR = DATA_DIR / 'reports'
ARCHIVE_DIR = DATA_DIR / 'archive'
TEMP_DIR = DATA_DIR / 'temp'
INSTANT_HISTORY_DIR = DATA_DIR / 'instant-history'
OD_BACKUP_DATA_DIR = DATA_DIR / 'od-backup'
OD_INS_TEMP_DIR = DATA_DIR / 'od-ins-temp'

# Instant cache settings
INSTANT_CACHE_MAX_DAYS = int(os.environ.get('COLLECTION_INSTANT_CACHE_MAX_DAYS', '90'))

# Cache eviction settings
CACHE_MAX_AGE_DAYS = int(os.environ.get('COLLECTION_CACHE_MAX_AGE_DAYS', '30'))
BACKEND_MONTHLY_MAX_MONTHS = int(os.environ.get('COLLECTION_BACKEND_MONTHLY_MAX_MONTHS', '6'))

# Disk pressure thresholds
DISK_PRESSURE_THRESHOLD_PCT = float(os.environ.get('COLLECTION_DISK_PRESSURE_PCT', '10'))
DISK_CRITICAL_THRESHOLD_BYTES = int(os.environ.get('COLLECTION_DISK_CRITICAL_GB', '2')) * 1024 * 1024 * 1024

# Cache size limits (in MB)
DB_CACHE_MAX_SIZE_MB = _HP_DB_CACHE_MAX_MB
INSTANT_HISTORY_MAX_SIZE_MB = _HP_INSTANT_HISTORY_MAX_MB
TOTAL_CACHE_MAX_SIZE_MB = _HP_TOTAL_CACHE_MAX_MB

# Database
DUCKDB_PATH = DB_DIR / 'storage.duckdb'

# Static files
STATIC_DIR = BASE_DIR / 'static'

# Large file threshold
LARGE_FILE_THRESHOLD_MB = 50

# Google Drive (no API key — public folder scraping + public export download)
GDRIVE_DOWNLOAD_DIR = TEMP_DIR / 'gdrive'
GDRIVE_CONFIG_PATH = DATA_DIR / 'gdrive_config.json'           # Hourly module
EOD_GDRIVE_CONFIG_PATH = DATA_DIR / 'eod_gdrive_config.json'   # EOD module

for d in [BACKEND_DATA_DIR, BACKEND_MONTHLY_DIR, DB_DIR, DB_CACHE_DIR, REPORTS_DIR, ARCHIVE_DIR, TEMP_DIR, INSTANT_HISTORY_DIR, OD_BACKUP_DATA_DIR, OD_INS_TEMP_DIR, GDRIVE_DOWNLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Email configuration
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# Branch email mapping (copied from Greetings project into this project's data/)
BRANCH_EMAILS_XLSX = DATA_DIR / 'branch_emails.xlsx'
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))

# EC2 Dashboard
EC2_UPLOAD_URL = os.environ.get('EC2_UPLOAD_URL', '')

# Coll_Db Dashboard
# Resolution order:
#   1. COLLDB_URL env var
#   2. data/colldb_config.json  {"url": "..."} (written by settings UI)
#   3. Hardcoded production default (EC2 deployment behind nginx/Apache HTTPS)
# Endpoints used:
#   POST {COLLDB_URL}/api/upload         — EOD Employee Report → collection tab
#   POST {COLLDB_URL}/api/upload-hourly  — Quick/Hourly Report → hourly tab
COLLDB_CONFIG_PATH = DATA_DIR / 'colldb_config.json'
_COLLDB_DEFAULT_URL = 'https://growwithme.navachetanalivelihoods.com'

def _load_colldb_url():
    env = os.environ.get('COLLDB_URL', '').strip()
    if env:
        return env
    try:
        if COLLDB_CONFIG_PATH.exists():
            import json as _json
            cfg = _json.loads(COLLDB_CONFIG_PATH.read_text())
            u = (cfg.get('url') or '').strip()
            if u:
                return u
    except Exception:
        pass
    return _COLLDB_DEFAULT_URL

COLLDB_URL = _load_colldb_url()

# API Authentication
EOD_API_KEY = os.environ.get('EOD_API_KEY', '')

# Supabase (Grow_With_Me schema) — EOD daily_performance mirror.
# EOD push writes to Grow_With_Me._stage_daily_performance via the
# public.eod_sync_stage_daily / eod_check_stage_date RPC wrappers.
# SUPABASE_SERVICE_KEY is the service_role JWT — keep it out of git (.env).
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://knbijsnghjcaocwtjvvw.supabase.co')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
