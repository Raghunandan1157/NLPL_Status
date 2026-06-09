"""
HOURLY Blueprint - Hourly Collection Report processing.
Merges hourly collection data onto EOD Output to produce "Hourly Collection Report.xlsx".
"""

import logging
import hashlib
import io
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, send_from_directory

import config
import process_jobs
from services.column_matcher import find_column
from services import file_manager
from services.excel_reader import compute_file_hash, save_upload_to_temp
from services.memory_manager import try_acquire_processing, release_processing, gc_checkpoint
from services.error_handler import user_error
from services.gdrive import (
    parse_folder_id,
    list_folder_files_public,
    download_file as gdrive_download_file,
    load_gdrive_config,
    save_gdrive_config,
)

logger = logging.getLogger(__name__)

hourly_bp = Blueprint('hourly', __name__)


def _norm_join_key(series):
    """Normalise an AccountID column to a stable string key so the merge matches
    regardless of dtype/format. The #1 cause of "collection shows 0" is a key
    mismatch (EOD ID is int64 while the uploaded collection ID is read as text or
    float). Coercing both sides to a trimmed string — dropping a trailing '.0'
    that float parsing adds — makes 104010001484700, '104010001484700' and
    104010001484700.0 all match. No values are altered, only the join key."""
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
    )

HOURLY_STATIC = str(config.STATIC_DIR / 'hourly')


# ── Static file serving ────────────────────────────────────────────────

@hourly_bp.route('/')
def index():
    return send_from_directory(HOURLY_STATIC, 'index.html')


@hourly_bp.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(HOURLY_STATIC, filename)


# ── Helper: locate EOD Output file ─────────────────────────────────────

def _find_eod_output():
    """
    Find the EOD Output file.  Checks:
      1. config.BACKEND_DATA_DIR for EOD_Output_* files (primary)
      2. config.ARCHIVE_DIR for EOD_Output_* files (auto-flow from EOD module)
    Returns (path, source_label) or (None, None).
    """
    # Primary: BACKEND_DATA_DIR
    eod_file = file_manager.find_file_by_pattern(config.BACKEND_DATA_DIR, "EOD_Output_*")
    if eod_file:
        return eod_file, 'backend'

    # Fallback: ARCHIVE_DIR (auto-flow from EOD module)
    eod_file = file_manager.find_file_by_pattern(config.ARCHIVE_DIR, "EOD_Output_*")
    if eod_file:
        return eod_file, 'archive'

    return None, None


def _get_file_hash(file_path):
    """MD5 hash of first 1MB, truncated to 16 hex chars (same as EOD)."""
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read(1024 * 1024))
    return hasher.hexdigest()[:16]


def _get_bytes_hash(data_bytes):
    """MD5 hash of first 1MB of bytes, truncated to 16 hex chars."""
    hasher = hashlib.md5()
    hasher.update(data_bytes[:1024 * 1024])
    return hasher.hexdigest()[:16]


def _find_eod_cache():
    """Find existing Parquet cache for EOD Output. Returns (cache_path, hash) or (None, None)."""
    caches = list(config.DB_CACHE_DIR.glob("hourly_eod_cache_*.parquet"))
    if caches:
        return caches[0], caches[0].stem.split('_')[-1]
    return None, None


def _find_collection_cache():
    """Find existing Parquet cache for Collection. Returns (cache_path, hash) or (None, None)."""
    caches = list(config.DB_CACHE_DIR.glob("hourly_collection_cache_*.parquet"))
    if caches:
        return caches[0], caches[0].stem.split('_')[-1]
    return None, None


def _make_short_vba_path(real_path):
    """
    VBA's Workbooks.Open fails when the file path exceeds ~218 characters.
    If the resolved path is too long, copy to ~/Desktop so Excel can access it.
    Returns the short path, or the original path if it's short enough.
    """
    real_path = Path(real_path).resolve()
    abs_str = str(real_path)
    if len(abs_str) <= 200:
        return abs_str

    import shutil
    short_path = Path.home() / 'Desktop' / real_path.name
    try:
        if short_path.exists():
            short_path.unlink()
        shutil.copy2(real_path, short_path)
        logger.info(f"VBA short path (Desktop copy): {short_path} <- {real_path}")
        return str(short_path)
    except OSError as e:
        logger.warning(f"Could not copy to Desktop: {e}, using original path")
        return abs_str


def _check_hourly_daily_status():
    """
    Check if a HourlyDaily_* file exists and whether it's expired (past midnight).
    Uses the file's modification time as the upload timestamp.
    Returns (filename, abs_path, timestamp_str, is_expired) or (None, None, None, None).
    """
    found = file_manager.find_file_by_pattern(config.BACKEND_DATA_DIR, 'HourlyDaily_*')
    if not found or found.name.startswith('~$'):
        return None, None, None, None

    try:
        mtime = found.stat().st_mtime
    except OSError:
        return None, None, None, None

    upload_dt = datetime.fromtimestamp(mtime)
    midnight = upload_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    is_expired = datetime.now() >= midnight
    timestamp_str = upload_dt.strftime("%d-%b-%Y %I:%M:%S %p")

    vba_path = _make_short_vba_path(found.resolve())
    return found.name, vba_path, timestamp_str, is_expired


# ── Check EOD duplicate ─────────────────────────────────────────────────

@hourly_bp.route('/check-eod-duplicate', methods=['GET'])
def check_eod_duplicate():
    try:
        eod_path, _ = _find_eod_output()
        exists = eod_path is not None
        filename = eod_path.name if eod_path else None
        return jsonify({'exists': exists, 'filename': filename})
    except Exception as e:
        err = user_error(e, context='hourly-check-eod')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Save Hourly Daily Collection Report (for Merge) ─────────────────────

@hourly_bp.route('/save-hourly-daily', methods=['POST'])
def save_hourly_daily():
    """Save the Hourly Daily Collection Report and return its absolute path."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No filename'}), 400

        saved_path = file_manager.save_uploaded_file(
            file,
            config.BACKEND_DATA_DIR,
            prefix='HourlyDaily_',
            replace_pattern='HourlyDaily_*'
        )
        abs_path = str(saved_path.resolve())
        vba_path = _make_short_vba_path(saved_path)
        logger.info(f"Saved Hourly Daily file: {abs_path}")

        return jsonify({
            'success': True,
            'filename': saved_path.name,
            'path': vba_path
        })

    except Exception as e:
        err = user_error(e, context='hourly-save-daily')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Save backend file (EOD Output only) ─────────────────────────────────

@hourly_bp.route('/save-backend-file', methods=['POST'])
def save_backend_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        file_type = request.form.get('type')

        if not file_type or file_type != 'eodOutput':
            return jsonify({'error': 'Invalid file type'}), 400

        if not file.filename:
            return jsonify({'error': 'No filename'}), 400

        saved_path = file_manager.save_uploaded_file(
            file,
            config.BACKEND_DATA_DIR,
            prefix='EOD_Output_',
            replace_pattern='EOD_Output_*'
        )
        logger.info(f"Saved EOD Output: {saved_path}")

        return jsonify({
            'success': True,
            'message': 'File saved successfully',
            'filename': saved_path.name
        })

    except Exception as e:
        err = user_error(e, context='hourly-save-backend')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Cache EOD Output ─────────────────────────────────────────────────────

@hourly_bp.route('/cache-eod-output', methods=['POST'])
def cache_eod_output():
    """Convert current EOD Output Excel to Parquet cache for fast reads."""
    try:
        eod_path, source = _find_eod_output()
        if not eod_path:
            return jsonify({'error': 'No EOD Output file found'}), 404

        file_hash = _get_file_hash(eod_path)
        config.DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        progress_messages = []

        # Delete old caches
        deleted = 0
        for old in config.DB_CACHE_DIR.glob("hourly_eod_cache_*.parquet"):
            try:
                old.unlink()
                deleted += 1
                progress_messages.append({'type': 'delete', 'message': f'Deleted old cache: {old.name}'})
            except Exception:
                pass
        if deleted:
            progress_messages.append({'type': 'cleanup', 'message': f'Cleared {deleted} old cache file(s)'})

        # Read Excel and convert to Parquet
        import time
        t_start = time.time()
        progress_messages.append({'type': 'cache', 'message': f'Caching EOD Output (hash: {file_hash})...'})

        eod_df = pd.read_excel(eod_path, engine='calamine')
        cache_path = config.DB_CACHE_DIR / f"hourly_eod_cache_{file_hash}.parquet"
        eod_df.to_parquet(cache_path, index=False)

        elapsed = time.time() - t_start
        original_size = eod_path.stat().st_size
        cache_size = cache_path.stat().st_size

        msg = f"Cached EOD Output: {cache_path.name} ({elapsed:.1f}s, {original_size/1024/1024:.1f}MB -> {cache_size/1024/1024:.1f}MB)"
        logger.info(msg)
        progress_messages.append({'type': 'success', 'message': msg})

        return jsonify({
            'success': True,
            'cached': True,
            'hash': file_hash,
            'time': round(elapsed, 2),
            'originalSize': original_size,
            'cacheSize': cache_size,
            'message': f'EOD Output cached successfully in {elapsed:.1f}s',
            'progress': progress_messages
        })

    except Exception as e:
        err = user_error(e, context='hourly-cache-eod')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Cache Collection ─────────────────────────────────────────────────────

@hourly_bp.route('/cache-collection', methods=['POST'])
def cache_collection():
    """Convert uploaded Collection Excel to Parquet cache for fast reads."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        tmp_path = save_upload_to_temp(file, prefix="hourly_cache_")

        try:
            file_hash = compute_file_hash(tmp_path)
            config.DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            progress_messages = []

            # Delete old caches
            deleted = 0
            for old in config.DB_CACHE_DIR.glob("hourly_collection_cache_*.parquet"):
                try:
                    old.unlink()
                    deleted += 1
                    progress_messages.append({'type': 'delete', 'message': f'Deleted old cache: {old.name}'})
                except Exception:
                    pass
            if deleted:
                progress_messages.append({'type': 'cleanup', 'message': f'Cleared {deleted} old cache file(s)'})

            # Read Excel and convert to Parquet
            import time
            t_start = time.time()
            progress_messages.append({'type': 'cache', 'message': f'Caching Collection (hash: {file_hash})...'})

            # Try with usecols first for speed, fallback to all columns
            cols_to_use = ['ReverseTotal', 'Reverse Total', 'AccountID', 'Account ID',
                           'CollectionTotal', 'Collection Total']
            try:
                df = pd.read_excel(tmp_path, engine='calamine',
                                 usecols=lambda x: x in cols_to_use)
            except Exception:
                df = pd.read_excel(tmp_path, engine='calamine')

            cache_path = config.DB_CACHE_DIR / f"hourly_collection_cache_{file_hash}.parquet"
            df.to_parquet(cache_path, index=False)

            elapsed = time.time() - t_start
            original_size = tmp_path.stat().st_size
            cache_size = cache_path.stat().st_size

            msg = f"Cached Collection: {cache_path.name} ({elapsed:.1f}s, {original_size/1024/1024:.1f}MB -> {cache_size/1024/1024:.1f}MB)"
            logger.info(msg)
            progress_messages.append({'type': 'success', 'message': msg})

            return jsonify({
                'success': True,
                'cached': True,
                'hash': file_hash,
                'time': round(elapsed, 2),
                'originalSize': original_size,
                'cacheSize': cache_size,
                'message': f'Collection cached successfully in {elapsed:.1f}s',
                'progress': progress_messages
            })
        finally:
            tmp_path.unlink(missing_ok=True)

    except Exception as e:
        err = user_error(e, context='hourly-cache-collection')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@hourly_bp.route('/cache-collection-gdrive', methods=['POST'])
def cache_collection_gdrive():
    """Cache the GDrive-downloaded collection file to Parquet for fast reads."""
    try:
        gdrive_coll = config.GDRIVE_DOWNLOAD_DIR / 'hourly' / 'gdrive_collection_last.xlsx'
        if not gdrive_coll.exists():
            return jsonify({'error': 'No GDrive collection file found'}), 404

        file_hash = _get_file_hash(gdrive_coll)
        config.DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Delete old caches
        for old in config.DB_CACHE_DIR.glob("hourly_collection_cache_*.parquet"):
            try:
                old.unlink()
            except Exception:
                pass

        import time
        t_start = time.time()

        df = pd.read_excel(gdrive_coll, engine='calamine')
        cache_path = config.DB_CACHE_DIR / f"hourly_collection_cache_{file_hash}.parquet"
        df.to_parquet(cache_path, index=False)

        elapsed = time.time() - t_start
        original_size = gdrive_coll.stat().st_size
        cache_size = cache_path.stat().st_size

        logger.info(f"Cached GDrive collection: {cache_path.name} ({elapsed:.1f}s, {original_size/1024/1024:.1f}MB -> {cache_size/1024/1024:.1f}MB)")

        return jsonify({
            'success': True,
            'hash': file_hash,
            'time': round(elapsed, 2),
            'originalSize': original_size,
            'cacheSize': cache_size,
            'message': f'Collection cached in {elapsed:.1f}s',
        })

    except Exception as e:
        err = user_error(e, context='hourly-cache-collection-gdrive')
        return jsonify({'error': err['user_message']}), 500


# ── Backend files status ─────────────────────────────────────────────────

@hourly_bp.route('/backend-files-status', methods=['GET'])
def get_backend_files_status():
    try:
        config.BACKEND_DATA_DIR.mkdir(parents=True, exist_ok=True)

        status = {'eodOutput': None, 'eodOutputSource': None, 'eodOutputTimestamp': None,
                  'eodOutputCached': False, 'collectionCached': False}

        # Check BACKEND_DATA_DIR first
        for f in config.BACKEND_DATA_DIR.iterdir():
            if f.name.startswith('EOD_Output_') and not f.name.startswith('~$'):
                status['eodOutput'] = f.name

                if 'AUTOFLOW_' in f.name:
                    status['eodOutputSource'] = 'eod-auto'
                    # Parse timestamp from filename: EOD_Output_AUTOFLOW_dd-Mon-YYYY_HH-MM-SS.xlsx
                    try:
                        ts_part = f.stem.split('AUTOFLOW_')[1]  # e.g. "09-Feb-2026_18-30-45"
                        parsed = datetime.strptime(ts_part, "%d-%b-%Y_%H-%M-%S")
                        status['eodOutputTimestamp'] = parsed.strftime("%d-%b-%Y %I:%M:%S %p")
                    except (IndexError, ValueError):
                        mtime = f.stat().st_mtime
                        status['eodOutputTimestamp'] = datetime.fromtimestamp(mtime).strftime("%d-%b-%Y %I:%M:%S %p")
                else:
                    status['eodOutputSource'] = 'uploaded'
                    mtime = f.stat().st_mtime
                    status['eodOutputTimestamp'] = datetime.fromtimestamp(mtime).strftime("%d-%b-%Y %I:%M:%S %p")
                break

        # If not found, check ARCHIVE_DIR for auto-flow from EOD module
        if status['eodOutput'] is None:
            config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            for f in config.ARCHIVE_DIR.iterdir():
                if f.name.startswith('EOD_Output_') and not f.name.startswith('~$'):
                    status['eodOutput'] = f.name
                    status['eodOutputSource'] = 'eod-auto'
                    mtime = f.stat().st_mtime
                    status['eodOutputTimestamp'] = datetime.fromtimestamp(mtime).strftime("%d-%b-%Y %I:%M:%S %p")
                    break

        # Check HourlyDaily file status
        hd_name, hd_path, hd_ts, hd_expired = _check_hourly_daily_status()
        status['hourlyDailyFile'] = hd_name
        status['hourlyDailyPath'] = hd_path
        status['hourlyDailyTimestamp'] = hd_ts
        status['hourlyDailyExpired'] = hd_expired

        # Check Parquet cache status
        eod_cache, _ = _find_eod_cache()
        if eod_cache:
            status['eodOutputCached'] = True
        coll_cache, _ = _find_collection_cache()
        if coll_cache:
            status['collectionCached'] = True

        # Check GDrive collection cache (Card 2 source)
        gdrive_coll_path = config.GDRIVE_DOWNLOAD_DIR / 'hourly' / 'gdrive_collection_last.xlsx'
        if gdrive_coll_path.exists():
            status['gdriveCollectionCached'] = True
            status['gdriveCollectionName'] = 'gdrive_collection_last.xlsx'

        # Check if fast report exists
        fast_report = config.BACKEND_DATA_DIR / 'Hourly_Fast_Report_Latest.xlsx'
        status['fastReportAvailable'] = fast_report.exists()

        return jsonify(status)

    except Exception as e:
        err = user_error(e, context='hourly-backend-status')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Process: Pivot Collection + merge into EOD Output ────────────────────

@hourly_bp.route('/process', methods=['POST'])
def process():
    if not try_acquire_processing():
        # Reclaim the slot if the holder is stale (crashed/stuck), else give a
        # precise reason so the user isn't told a generic "busy".
        if process_jobs.reap_stale():
            release_processing()
        if not try_acquire_processing():
            act = process_jobs.active()
            if act and act['status'] == 'cancelling':
                return jsonify({
                    'error': 'Previous process is cancelling. Please wait a moment.',
                    'status': 'cancelling',
                }), 409
            return jsonify({
                'error': 'Server is busy processing another request. Please try again in a moment.',
                'status': 'busy',
            }), 503
    # Register this run so the frontend can poll status / request cancellation.
    job_id = process_jobs.start(request.form.get('processId'), 'hourly')
    try:
        import time as _time
        t_process_start = _time.time()
        process_jobs.checkpoint(job_id)
        cleanup_eod_tmp = False
        eod_path = None

        # 1. Resolve Collection source — GDrive cache or manual upload
        use_gdrive_collection = request.form.get('useGDriveCollection') == 'true'
        gdrive_coll_path = config.GDRIVE_DOWNLOAD_DIR / 'hourly' / 'gdrive_collection_last.xlsx'

        if use_gdrive_collection:
            if not gdrive_coll_path.exists():
                return jsonify({
                    'error': 'No cached Google Drive collection file found. Please scan and download again.'
                }), 400
            logger.info(f"Using GDrive-cached collection: {gdrive_coll_path}")

        # Get the selected date from frontend (dd-mm-yyyy format)
        selected_date = request.form.get('date')
        if not selected_date:
            selected_date = datetime.now().strftime('%d-%m-%Y')
        logger.info(f"Using date: {selected_date}")

        # Get optional time from form (for report title)
        sel_hour = request.form.get('hour')
        sel_minute = request.form.get('minute')
        sel_ampm = request.form.get('ampm')
        if sel_hour and sel_minute and sel_ampm:
            selected_time = f"{sel_hour}:{sel_minute.zfill(2)} {sel_ampm.upper()}"
        else:
            selected_time = datetime.now().strftime('%-I:%M %p')

        # 2. Find EOD Output file — manual upload takes priority
        eod_upload = request.files.get('eodOutput')
        if eod_upload and eod_upload.filename:
            eod_path = save_upload_to_temp(eod_upload, prefix="hourly_eod_")
            cleanup_eod_tmp = True
            source = 'upload'
        else:
            eod_path, source = _find_eod_output()
        if not eod_path:
            return jsonify({
                'error': 'No EOD Output file found. Please upload one or run EOD processing first.'
            }), 400
        logger.info(f"Using EOD Output: {eod_path} (source: {source})")

        # 3. Read Collection file with pandas (try Parquet cache first)
        cleanup_coll_tmp = False
        if use_gdrive_collection:
            coll_tmp = gdrive_coll_path
        elif 'file' in request.files and request.files['file'].filename:
            collection_file = request.files['file']
            coll_tmp = save_upload_to_temp(collection_file, prefix="hourly_coll_")
            cleanup_coll_tmp = True
        else:
            return jsonify({'error': 'No Collection file provided. Upload one or use Google Drive.'}), 400
        try:
            coll_hash = compute_file_hash(coll_tmp)
            coll_cache_path = config.DB_CACHE_DIR / f"hourly_collection_cache_{coll_hash}.parquet"

            if coll_cache_path.exists():
                collection_df = pd.read_parquet(coll_cache_path)
                logger.info(f"Collection loaded from Parquet cache ({coll_cache_path.name})")
            else:
                collection_df = pd.read_excel(coll_tmp, engine='calamine')
                logger.info(f"Collection loaded from Excel (no cache hit)")
        finally:
            if cleanup_coll_tmp:
                coll_tmp.unlink(missing_ok=True)
        logger.info(f"Collection file loaded: {len(collection_df)} rows")
        logger.info(f"Collection columns: {list(collection_df.columns)}")

        # 4. Filter where ReverseTotal == 0 (optional — skip if column absent)
        col_reverse = find_column(collection_df, 'ReverseTotal', 'Reverse Total')
        if col_reverse:
            filtered_df = collection_df[collection_df[col_reverse] == 0]
            logger.info(f"Filtered ({col_reverse} == 0): {len(filtered_df)} rows")
        else:
            filtered_df = collection_df
            logger.info("ReverseTotal column not found — skipping reverse filter, using all rows")

        # 4b. Apply date filtering to match EOD period (first of month to selected date)
        col_trxdate = find_column(filtered_df, 'Trxdate', 'Transaction Date')
        if col_trxdate:
            try:
                selected_date_dt = pd.to_datetime(selected_date, format='%d-%m-%Y')
                first_of_month = selected_date_dt.replace(day=1)

                # Parse transaction dates (handles Excel serial numbers + string formats)
                from services.eod_processor import parse_trxdate
                trxdate_parsed = parse_trxdate(filtered_df[col_trxdate])

                # Filter: transactions from first of month to selected date (inclusive)
                mask = (trxdate_parsed >= first_of_month) & (trxdate_parsed <= selected_date_dt)
                filtered_df = filtered_df[mask]

                logger.info(f"Applied date filter ({first_of_month.strftime('%d-%m-%Y')} to {selected_date}): {len(filtered_df)} rows")
            except Exception as date_err:
                logger.warning(f"Date filtering failed (non-fatal): {date_err}. Proceeding without date filter.")
        else:
            logger.warning(f"Trxdate column not found. Proceeding without date filtering.")

        # 5. Create pivot: AccountID as rows, Sum of CollectionTotal as values
        col_account_coll = find_column(filtered_df, 'AccountID', 'Account ID')
        if not col_account_coll:
            return jsonify({
                'error': f"Column 'AccountID' not found in Collection file. "
                         f"Available columns: {list(collection_df.columns)}"
            }), 400
        logger.info(f"Detected AccountID column (Collection): '{col_account_coll}'")

        col_coll_total = find_column(filtered_df, 'CollectionTotal', 'Collection Total')
        if not col_coll_total:
            return jsonify({
                'error': f"Column 'CollectionTotal' not found in Collection file. "
                         f"Available columns: {list(collection_df.columns)}"
            }), 400
        logger.info(f"Detected CollectionTotal column: '{col_coll_total}'")

        process_jobs.checkpoint(job_id)

        # Clean the collection AMOUNT to numeric WITHOUT forcing 0: strip
        # commas/currency/spaces so amounts stored as text still aggregate. Cells
        # that can't be parsed become NaN (excluded from the sum), never 0.
        raw_amt = filtered_df[col_coll_total]
        if pd.api.types.is_numeric_dtype(raw_amt):
            amt_numeric = pd.to_numeric(raw_amt, errors='coerce')
        else:
            amt_numeric = pd.to_numeric(
                raw_amt.astype(str).str.replace(r'[^0-9.\-]', '', regex=True).replace('', None),
                errors='coerce',
            )
            bad = int(raw_amt.notna().sum() - amt_numeric.notna().sum())
            if bad > 0:
                logger.warning(f"CollectionTotal had {bad} non-numeric cell(s) coerced to NaN (excluded, not zeroed)")

        # Aggregate by a NORMALISED join key (min_count=1 so an all-NaN group
        # stays NaN/blank rather than collapsing to a misleading 0).
        coll_keys = _norm_join_key(filtered_df[col_account_coll])
        pivot_series = amt_numeric.groupby(coll_keys).sum(min_count=1)
        logger.info(f"Pivot table created: {len(pivot_series)} unique AccountIDs")

        # 6. Read EOD Output file (try Parquet cache first)
        eod_hash = _get_file_hash(eod_path)
        eod_cache_path = config.DB_CACHE_DIR / f"hourly_eod_cache_{eod_hash}.parquet"

        if eod_cache_path.exists():
            eod_df = pd.read_parquet(eod_cache_path)  # Full read: EOD output columns all needed for hourly merge
            logger.info(f"EOD Output loaded from Parquet cache ({eod_cache_path.name})")
        else:
            eod_df = pd.read_excel(eod_path, engine='calamine')
            logger.info(f"EOD Output loaded from Excel (no cache hit)")
        logger.info(f"EOD Output loaded: {len(eod_df)} rows")
        logger.info(f"EOD Output columns: {list(eod_df.columns)}")

        # 7. Create new column name with the selected date
        new_col_name = f"Collection as on {selected_date}"

        # 8. Match Account ID and fill in Sum of CollectionTotal
        col_account_eod = find_column(eod_df, 'Account ID', 'AccountID')
        if not col_account_eod:
            return jsonify({
                'error': f"Column 'Account ID' not found in EOD Output file. "
                         f"Available columns: {list(eod_df.columns)}"
            }), 400
        logger.info(f"Detected AccountID column (EOD Output): '{col_account_eod}'")

        # Map onto EOD Output using the SAME normalised key on both sides.
        lookup = pivot_series.to_dict()
        eod_keys = _norm_join_key(eod_df[col_account_eod])
        eod_df[new_col_name] = eod_keys.map(lookup)
        matched_rows = int(eod_df[new_col_name].notna().sum())

        # ---- debug-safe summary (streams to Live Log; counts/totals only) ----
        uploaded_rows = int(len(collection_df))
        nonzero_uploaded = int((amt_numeric.fillna(0) != 0).sum())
        uploaded_total = float(amt_numeric.sum(skipna=True) or 0)
        matched_total = float(pd.to_numeric(eod_df[new_col_name], errors='coerce').sum(skipna=True) or 0)
        unmatched_rows = int(len(eod_df) - matched_rows)
        logger.info(
            f"HOURLY SUMMARY | uploaded rows: {uploaded_rows} | amount col: '{col_coll_total}' "
            f"| non-zero uploaded rows: {nonzero_uploaded} | uploaded total: {uploaded_total:,.2f}"
        )
        logger.info(
            f"HOURLY SUMMARY | matched rows: {matched_rows} | matched total: {matched_total:,.2f} "
            f"| unmatched rows: {unmatched_rows} | output collection total: {matched_total:,.2f}"
        )

        # ---- validation: non-zero uploaded but all-zero output => mapping failed
        if nonzero_uploaded > 0 and matched_total == 0:
            return jsonify({
                'error': 'Collection mapping failed: uploaded collection contains non-zero values but generated output is all zero.',
                'suggestion': "Check that the Collection 'AccountID' matches the EOD Output 'Account ID' (same IDs, no stray text/whitespace) and that the amount column holds numbers.",
            }), 422

        # 9. Add Remark and Remark2 columns
        col_installment = find_column(eod_df, 'Installment Amount')
        col_cumulative = find_column(eod_df, 'Cumulative Demand')
        col_dpd_last = find_column(eod_df, 'DPD Group - Last Month')
        for label, col in [
            ('Installment Amount', col_installment),
            ('Cumulative Demand', col_cumulative),
            ('DPD Group - Last Month', col_dpd_last),
        ]:
            if not col:
                return jsonify({
                    'error': f"Column '{label}' not found in EOD Output file. "
                             f"Available columns: {list(eod_df.columns)}"
                }), 400

        # Remark: Vectorized (replaces slow row-by-row apply)
        # If "DPD Group - Last Month" is "0 Days" -> Cumulative Demand - Collection
        # Otherwise -> Installment Amount - Collection
        import numpy as np
        has_collection = eod_df[new_col_name].notna()
        is_zero_days = eod_df[col_dpd_last] == '0 Days'

        eod_df['Remark'] = np.where(
            ~has_collection, None,
            np.where(is_zero_days,
                     eod_df[col_cumulative] - eod_df[new_col_name],
                     eod_df[col_installment] - eod_df[new_col_name])
        )

        # Remark2: Vectorized
        eod_df['Remark2'] = np.where(
            ~has_collection, 'Not Collected',
            np.where(eod_df['Remark'].astype(float) <= 0, 'Full Collected', 'Partially Collected')
        )
        logger.info("Added 'Remark' and 'Remark2' columns")

        # 10. Save as "Hourly Collection Report.xlsx" (xlsxwriter for speed)
        temp_dir = tempfile.mkdtemp(dir=str(config.TEMP_DIR))
        process_jobs.add_temp(job_id, temp_dir)
        output_filename = "Hourly Collection Report.xlsx"
        output_path = str(Path(temp_dir) / output_filename)

        import time as _time

        total_rows = len(eod_df)
        total_cols = len(eod_df.columns)
        t_write_start = _time.time()
        logger.info(f"Writing {total_rows:,} rows x {total_cols} cols to Excel...")

        # ── Fast pre-computed report (same as EOD daily report) ──
        # Must run BEFORE fillna('') which destroys numeric data. The Fast Report
        # is the file auto-downloaded to the user (see the send_file below).
        fast_report_path = config.BACKEND_DATA_DIR / 'Hourly_Fast_Report_Latest.xlsx'
        fast_report_ready = False
        try:
            from services.eod_processor import _compute_precomputed_sheets
            from services.daily_report_builder import build_daily_report

            # Build a precomp-ready copy with hourly collection in the 'Collection' column
            df_for_precomp = eod_df.copy()
            df_for_precomp['Collection'] = eod_df[new_col_name]

            # Recalculate Partial Amount (same logic as eod_processor / EOD generate-daily-hourly)
            reg_demand = pd.to_numeric(df_for_precomp.get('Regular Demand', 0), errors='coerce').fillna(0)
            collection = pd.to_numeric(df_for_precomp.get('Collection', 0), errors='coerce')
            difference = reg_demand - collection.fillna(0)

            df_for_precomp['Partial Amount'] = 'Not Collected'
            has_col = collection.notna()
            df_for_precomp.loc[has_col & (difference <= 0), 'Partial Amount'] = 'Full EMI Paid'
            df_for_precomp.loc[has_col & (difference > 0), 'Partial Amount'] = 'Partial Amount'

            # Recalculate installment - collected columns
            inst_amt = pd.to_numeric(df_for_precomp.get('Installment Amount', 0), errors='coerce').fillna(0)
            df_for_precomp['installment - collected amt'] = inst_amt - collection.fillna(0)
            df_for_precomp['installment - collected value'] = (
                df_for_precomp['installment - collected amt'] <= 0
            ).astype(int)

            # Derive target_date from the data
            h_target_date = None
            if 'Meeting Date' in df_for_precomp.columns:
                from services.eod_processor import parse_date_column
                meeting_dates = parse_date_column(df_for_precomp['Meeting Date'])
                max_date = meeting_dates.dropna().max()
                if pd.notna(max_date):
                    h_target_date = max_date
            if h_target_date is None:
                h_target_date = pd.Timestamp.now()

            has_officer = 'Emp ID' in df_for_precomp.columns
            # Anchor the On-Date sheet on the GENERATION date (the date the user
            # is generating for), not max(Meeting Date) which is month-end. This
            # makes the On-Date show today's demand with data instead of a future
            # month-end+1 date that has none. Only the On-Date columns shift;
            # FTOD/monthly/buckets still use h_target_date so OverAll is unchanged.
            try:
                h_ondate = pd.to_datetime(selected_date, format='%d-%m-%Y')
            except Exception:
                h_ondate = pd.Timestamp.now().normalize()
            # pnpa_always_active: the hourly report is intraday, so PNPA must keep
            # the Active-Loan filter (consistent with 1-30/31-60) even though
            # h_target_date lands on month-end and would otherwise drop it.
            precomp = _compute_precomputed_sheets(df_for_precomp, h_target_date,
                                                  ondate_next_date=h_ondate,
                                                  pnpa_always_active=True)

            # Per-employee data for the 'Employee Data' sheet (all products combined).
            # df_for_precomp['Collection'] is the hourly value — numbers stay
            # hourly-mode consistent with the rest of the report.
            employee_data = None
            try:
                from services.eod_processor import build_employee_report
                _emp_tmp = Path(temp_dir) / 'emp_for_report.xlsx'
                if build_employee_report(df_for_precomp, h_target_date, _emp_tmp) and _emp_tmp.exists():
                    _x = pd.read_excel(_emp_tmp, sheet_name=['IGL', 'FIG', 'VVY'])
                    _all = pd.concat([_x['IGL'], _x['FIG'], _x['VVY']], ignore_index=True)
                    _idc = ['Region', 'Division', 'Area', 'Branch', 'Emp ID']
                    _mc = [c for c in _all.columns if c not in _idc + ['Officer Name']]
                    _g = _all.groupby(_idc, as_index=False)[_mc].sum()
                    _onm = (_all[_all['Officer Name'].astype(str) != '']
                            .groupby('Emp ID')['Officer Name'].first().to_dict())
                    _g['Officer Name'] = _g['Emp ID'].map(_onm).fillna('')
                    employee_data = _g
            except Exception as _emp_err:
                logger.warning(f"Hourly report 'Employee Data' sheet skipped "
                               f"({type(_emp_err).__name__}: {_emp_err})")

            if precomp and '_precomp' in precomp:
                h_fmt = f"{selected_date} @ {selected_time}"
                build_daily_report(precomp['_precomp'], fast_report_path, h_target_date, has_officer,
                                   formatted_dt=h_fmt, hourly_mode=True, employee_data=employee_data,
                                   ondate_next_date=h_ondate)
                fast_report_ready = fast_report_path.exists()
                logger.info(f"Fast hourly report generated: {fast_report_path.name}")

            # Save the precomp-ready data as parquet for /generate-fast-report
            try:
                fast_cache_path = config.DB_CACHE_DIR / 'hourly_fast_cache.parquet'
                df_for_precomp.to_parquet(str(fast_cache_path), index=False)
                logger.info(f"Saved hourly fast cache: {fast_cache_path.name}")
            except Exception as cache_err:
                logger.warning(f"Could not save hourly fast cache: {cache_err}")

            del df_for_precomp
        except Exception as fast_err:
            logger.warning(f"Fast hourly report generation failed (non-fatal): {fast_err}")

        # Direct xlsxwriter with constant_memory for speed + low memory
        # NOTE: pandas to_excel + constant_memory silently drops data;
        # _write_excel_fast writes row-by-row which is safe.
        eod_df = eod_df.fillna('')
        from services.eod_processor import _write_excel_fast
        process_jobs.checkpoint(job_id)
        _write_excel_fast(eod_df, output_path)

        t_write = _time.time() - t_write_start
        logger.info(f"Saved output: {output_path} (Excel write: {t_write:.1f}s)")

        # Save a persistent copy to BACKEND_DATA_DIR for bundle access
        latest_output = config.BACKEND_DATA_DIR / 'Hourly_Collection_Report_Latest.xlsx'
        try:
            shutil.copy2(output_path, str(latest_output))
            logger.info(f"Saved latest output: {latest_output}")
        except Exception as cpy_err:
            logger.warning(f"Could not save latest output: {cpy_err}")

        # Auto-download is ALWAYS the generated HOURLY FAST REPORT — never the
        # detailed Hourly Collection Report, and never an uploaded input. If the
        # Fast Report wasn't produced this run, fail clearly rather than sending
        # the wrong file.
        if not (fast_report_ready and Path(fast_report_path).exists()):
            return jsonify({
                'error': 'Hourly Fast Report could not be generated for this run, so there is nothing to download.',
                'suggestion': 'Ensure the EOD Output has the required Region/Division/officer and demand columns, then retry.',
            }), 500

        # Filename rule: "Hourly Report - {selected_time}.xlsx" with ':' -> '-'
        # and any invalid filename characters stripped.
        safe_time = selected_time.replace(':', '-')
        safe_time = ''.join(c for c in safe_time if c not in '\\/*?:"<>|').strip()
        download_filename = f"Hourly Report - {safe_time}.xlsx"

        response = send_file(
            str(fast_report_path),
            as_attachment=True,
            download_name=download_filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        # Pass detected AccountID field name to frontend for VBA code injection
        response.headers['X-Account-ID-Field'] = col_account_eod
        response.headers['Access-Control-Expose-Headers'] = 'X-Account-ID-Field'
        t_total = _time.time() - t_process_start
        logger.info(f"Hourly Process Completed in {t_total:.2f} seconds")

        # Schedule cleanup of temp directory after response is sent
        @response.call_on_close
        def _cleanup_temp():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        return response

    except process_jobs.JobCancelled:
        logger.info("Hourly processing cancelled by user.")
        process_jobs.cleanup(job_id)
        return jsonify({
            'cancelled': True,
            'status': 'cancelled',
            'message': 'Processing cancelled by user.',
        }), 200
    except Exception as e:
        err = user_error(e, context='hourly-process')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500
    finally:
        process_jobs.finish(job_id)
        if cleanup_eod_tmp and eod_path:
            Path(eod_path).unlink(missing_ok=True)
        gc_checkpoint("hourly-request-complete")
        release_processing()


@hourly_bp.route('/save-to-downloads', methods=['POST'])
def save_to_downloads():
    """Save the latest generated Hourly Fast Report to ~/Downloads."""
    try:
        latest = config.BACKEND_DATA_DIR / 'Hourly_Fast_Report_Latest.xlsx'
        if not latest.exists():
            return jsonify({'success': False, 'message': 'No generated report found'}), 404

        dl_dir = Path.home() / 'Downloads'
        dl_dir.mkdir(parents=True, exist_ok=True)
        dest = dl_dir / 'Hourly Report.xlsx'

        # Dedup naming if file exists
        if dest.exists():
            i = 1
            while True:
                dest = dl_dir / f'Hourly Report ({i}).xlsx'
                if not dest.exists():
                    break
                i += 1

        shutil.copy2(str(latest), str(dest))
        logger.info(f"Saved to Downloads: {dest}")

        return jsonify({'success': True, 'path': str(dest), 'filename': dest.name})

    except Exception as e:
        err = user_error(e, context='hourly-save-downloads')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Fast Pre-computed Hourly Report ──────────────────────────────────────

@hourly_bp.route('/download-fast-report', methods=['GET'])
def download_fast_report():
    """Download the pre-computed fast hourly report."""
    report_file = config.BACKEND_DATA_DIR / 'Hourly_Fast_Report_Latest.xlsx'
    if not report_file.exists():
        return jsonify({'error': 'No fast hourly report available. Run hourly processing first.'}), 404
    date_str = request.args.get('date', '')
    dl_name = f'Hourly_Report_{date_str}.xlsx' if date_str else 'Hourly_Report.xlsx'
    return send_file(
        report_file,
        as_attachment=True,
        download_name=dl_name,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@hourly_bp.route('/generate-fast-report', methods=['POST'])
def generate_fast_report():
    """Generate the fast pre-computed hourly report from the latest Hourly Collection Report.

    This uses _compute_precomputed_sheets + build_daily_report (same as the
    EOD daily report) but operates on the hourly-merged data, producing
    OverAll / On-Date / Tomorrow / FY sheets instantly without VBA.
    """
    try:
        from services.eod_processor import _compute_precomputed_sheets
        from services.daily_report_builder import build_daily_report

        logger.info("HOURLY FAST REPORT: Starting on-demand generation")

        # Accept target_date from POST body
        target_date_str = None
        if request.is_json:
            target_date_str = (request.json or {}).get('targetDate')
        else:
            target_date_str = request.form.get('targetDate')

        target_date = None
        if target_date_str:
            try:
                target_date = pd.Timestamp(datetime.strptime(target_date_str, '%d-%m-%Y'))
                logger.info(f"HOURLY FAST REPORT: Using target_date: {target_date_str}")
            except ValueError:
                logger.warning(f"HOURLY FAST REPORT: Could not parse target_date '{target_date_str}'")

        # Load the hourly-merged output (with Collection already set to hourly values)
        df = None

        # Try the dedicated hourly fast cache first (has Collection = hourly values)
        fast_cache = config.DB_CACHE_DIR / 'hourly_fast_cache.parquet'
        if fast_cache.exists():
            try:
                df = pd.read_parquet(fast_cache)
                logger.info(f"HOURLY FAST REPORT: Loaded from hourly_fast_cache.parquet")
            except Exception as e:
                logger.warning(f"HOURLY FAST REPORT: Fast cache read failed: {e}")

        # Fallback: load from Excel and fix up Collection column
        if df is None:
            latest_xlsx = config.BACKEND_DATA_DIR / 'Hourly_Collection_Report_Latest.xlsx'
            if latest_xlsx.exists():
                try:
                    df = pd.read_excel(latest_xlsx, engine='calamine')
                except (ImportError, ValueError):
                    df = pd.read_excel(latest_xlsx)
                logger.info("HOURLY FAST REPORT: Loaded from Hourly_Collection_Report_Latest.xlsx")

                # The Excel file has hourly collection in "Collection as on ..." column
                # Copy it into 'Collection' and recalculate dependent columns
                if df is not None:
                    coll_cols = [c for c in df.columns if c.startswith('Collection as on')]
                    if coll_cols:
                        import numpy as np
                        df['Collection'] = df[coll_cols[0]]

                        reg_demand = pd.to_numeric(df.get('Regular Demand', 0), errors='coerce').fillna(0)
                        collection = pd.to_numeric(df.get('Collection', 0), errors='coerce')
                        difference = reg_demand - collection.fillna(0)

                        df['Partial Amount'] = 'Not Collected'
                        has_col = collection.notna()
                        df.loc[has_col & (difference <= 0), 'Partial Amount'] = 'Full EMI Paid'
                        df.loc[has_col & (difference > 0), 'Partial Amount'] = 'Partial Amount'

                        inst_amt = pd.to_numeric(df.get('Installment Amount', 0), errors='coerce').fillna(0)
                        df['installment - collected amt'] = inst_amt - collection.fillna(0)
                        df['installment - collected value'] = (
                            df['installment - collected amt'] <= 0
                        ).astype(int)

        if df is None or len(df) == 0:
            return jsonify({'error': 'No hourly data available. Run hourly processing first.'}), 404

        # Derive target_date from data if not provided
        if target_date is None and 'Meeting Date' in df.columns:
            try:
                from services.eod_processor import parse_date_column
                meeting_dates = parse_date_column(df['Meeting Date'])
                max_date = meeting_dates.dropna().max()
                if pd.notna(max_date):
                    target_date = max_date
            except (ValueError, TypeError):
                pass
        if target_date is None:
            target_date = pd.Timestamp.now()

        has_officer = 'Emp ID' in df.columns

        precomp = _compute_precomputed_sheets(df, target_date)
        if not precomp or '_precomp' not in precomp:
            return jsonify({'error': 'Pre-computation returned no data.'}), 500

        report_path = config.BACKEND_DATA_DIR / 'Hourly_Fast_Report_Latest.xlsx'
        sel_h = request.form.get('hour') if request.form else None
        sel_m = request.form.get('minute') if request.form else None
        sel_ap = request.form.get('ampm') if request.form else None
        if sel_h and sel_m and sel_ap:
            gen_time = f"{sel_h}:{sel_m.zfill(2)} {sel_ap.upper()}"
        else:
            gen_time = datetime.now().strftime('%-I:%M %p')
        h_fmt = f"{target_date.strftime('%d-%m-%Y')} @ {gen_time}"
        build_daily_report(precomp['_precomp'], report_path, target_date, has_officer,
                           formatted_dt=h_fmt, hourly_mode=True)
        logger.info(f"HOURLY FAST REPORT: Generated -> {report_path.name}")

        return jsonify({
            'success': True,
            'reportDate': target_date.strftime('%d-%m-%Y'),
            'message': 'Fast hourly report generated successfully.',
        })

    except Exception as e:
        err = user_error(e, context='hourly-generate-fast-report')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Get Meeting Dates from EOD Output ────────────────────────────────────

@hourly_bp.route('/get-eod-meeting-dates', methods=['GET'])
def get_eod_meeting_dates():
    try:
        eod_path, _ = _find_eod_output()
        if not eod_path:
            return jsonify({'error': 'No EOD Output file found.'}), 404

        # Try Parquet cache first
        eod_hash = _get_file_hash(eod_path)
        eod_cache_path = config.DB_CACHE_DIR / f"hourly_eod_cache_{eod_hash}.parquet"

        if eod_cache_path.exists():
            eod_df = pd.read_parquet(eod_cache_path)  # Full read: EOD output columns all needed for hourly merge
            logger.info(f"Meeting dates: loaded from Parquet cache")
        else:
            eod_df = pd.read_excel(eod_path, engine='calamine')
            logger.info(f"Meeting dates: loaded from Excel (no cache)")

        col_meeting = find_column(eod_df, 'Meeting Date')
        if not col_meeting:
            return jsonify({
                'error': f"'Meeting Date' column not found. Available: {list(eod_df.columns)}"
            }), 400

        # Get unique non-null Meeting Date values, convert to dd-mm-yyyy
        raw_dates = eod_df[col_meeting].dropna().unique()
        date_strings = set()
        for d in raw_dates:
            try:
                if hasattr(d, 'strftime'):
                    date_strings.add(d.strftime('%d-%m-%Y'))
                else:
                    parsed = pd.to_datetime(str(d), dayfirst=True)
                    date_strings.add(parsed.strftime('%d-%m-%Y'))
            except Exception:
                pass

        date_list = sorted(date_strings)
        logger.info(f"Meeting Dates found in EOD Output: {date_list}")
        return jsonify({'dates': date_list})

    except Exception as e:
        err = user_error(e, context='hourly-meeting-dates')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Delete expired Hourly Daily file ─────────────────────────────────────

@hourly_bp.route('/delete-hourly-daily', methods=['POST'])
def delete_hourly_daily():
    """Delete the HourlyDaily_* file from backend (called when expired past midnight)."""
    try:
        found = file_manager.find_file_by_pattern(config.BACKEND_DATA_DIR, 'HourlyDaily_*')
        if not found or found.name.startswith('~$'):
            return jsonify({'success': True, 'message': 'No file to delete'})

        try:
            found.unlink()
            logger.info(f"Deleted expired Hourly Daily file: {found.name}")
        except PermissionError:
            logger.warning(f"Cannot delete Hourly Daily file (locked): {found.name}")
            return jsonify({'error': 'File is locked (possibly open in Excel)'}), 423

        return jsonify({'success': True, 'deleted': found.name})

    except Exception as e:
        err = user_error(e, context='hourly-delete-daily')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Google Drive Integration ──────────────────────────────────────────────

@hourly_bp.route('/gdrive-config', methods=['GET'])
def hourly_gdrive_config_get():
    """Load saved GDrive folder URL (shared config with EOD module)."""
    try:
        cfg = load_gdrive_config(config.GDRIVE_CONFIG_PATH)
        return jsonify({'success': True, 'folder_url': cfg.get('folder_url', '')})
    except Exception as e:
        err = user_error(e, context='hourly-gdrive-config')
        return jsonify({'error': err['user_message']}), 500


@hourly_bp.route('/gdrive-scan-collection', methods=['POST'])
def hourly_gdrive_scan_collection():
    """Scan GDrive folder for files starting with 'collectionreport'."""
    try:
        data = request.get_json()
        folder_url = data.get('folder_url', '')
        folder_id = parse_folder_id(folder_url)
        if not folder_id:
            return jsonify({'success': False, 'message': 'Invalid Google Drive folder URL'}), 400

        all_files = list_folder_files_public(folder_id)
        collection_files = [
            f for f in all_files
            if f['name'].lower().startswith('collection')
        ]

        # Persist folder URL
        save_gdrive_config(config.GDRIVE_CONFIG_PATH, {'folder_url': folder_url})

        return jsonify({
            'success': True,
            'files': collection_files,
            'collection_files': collection_files,
            'total_files': len(all_files),
            'all_files': all_files,
        })
    except Exception as e:
        err = user_error(e, context='hourly-gdrive-scan')
        return jsonify({'success': False, 'message': err['user_message']}), 500


@hourly_bp.route('/bundle-list', methods=['GET'])
def hourly_bundle_list():
    """List local EOD_Bundle subfolders (~/Downloads/EOD_Bundle/) sorted newest-first."""
    try:
        bundle_root = Path.home() / 'Downloads' / 'EOD_Bundle'
        if not bundle_root.exists():
            bundle_root = Path.home() / 'Desktop' / 'EOD_Bundle'
        if not bundle_root.exists():
            return jsonify({'success': True, 'bundles': [], 'message': 'EOD_Bundle folder not found'})

        def _parse_bundle_sort_key(dirname):
            """Parse 'HHh.MMm.SSs on dd-mm-YYYY' → datetime for proper sorting."""
            try:
                # e.g. "10h.45m.33s on 15-03-2024"
                parts = dirname.split(' on ')
                if len(parts) == 2:
                    time_part = parts[0]  # "10h.45m.33s"
                    date_part = parts[1]  # "15-03-2024"
                    h = int(time_part.split('h')[0])
                    m = int(time_part.split('.')[1].replace('m', ''))
                    s = int(time_part.split('.')[2].replace('s', ''))
                    dd, mm, yyyy = date_part.split('-')
                    return datetime(int(yyyy), int(mm), int(dd), h, m, s)
            except Exception:
                pass
            return datetime.min  # unparseable folders go last

        bundles = []
        subdirs = [d for d in bundle_root.iterdir() if d.is_dir() and not d.name.startswith('.')]
        subdirs.sort(key=lambda d: _parse_bundle_sort_key(d.name), reverse=True)

        for d in subdirs:

            files = [
                f.name for f in d.iterdir()
                if f.is_file() and not f.name.startswith('~$') and not f.name.startswith('.')
            ]

            # Read target_date if present
            td_file = d / '.target_date'
            target_date = td_file.read_text().strip() if td_file.exists() else None

            # Classify files
            eod_output_file = None
            hourly_daily_file = None
            for fn in files:
                fn_lower = fn.lower()
                if 'regular demand' in fn_lower:
                    eod_output_file = fn
                elif 'hourly daily' in fn_lower:
                    hourly_daily_file = fn

            bundles.append({
                'name': d.name,
                'path': str(d),
                'files': files,
                'target_date': target_date,
                'eod_output_file': eod_output_file,
                'hourly_daily_file': hourly_daily_file,
            })

        return jsonify({'success': True, 'bundles': bundles})
    except Exception as e:
        err = user_error(e, context='hourly-bundle-list')
        return jsonify({'success': False, 'message': err['user_message']}), 500


@hourly_bp.route('/bundle-use', methods=['POST'])
def hourly_bundle_use():
    """Copy files from a local EOD_Bundle subfolder into the hourly pipeline."""
    try:
        data = request.get_json()
        bundle_path = data.get('bundle_path', '')
        use_eod_output = data.get('use_eod_output', False)
        use_hourly_daily = data.get('use_hourly_daily', False)
        eod_output_name = data.get('eod_output_name', '')
        hourly_daily_name = data.get('hourly_daily_name', '')

        bundle_dir = Path(bundle_path)
        if not bundle_dir.exists():
            return jsonify({'success': False, 'message': 'Bundle folder not found'}), 404

        result = {'success': True}

        # Copy EOD output file → BACKEND_DATA_DIR as EOD_Output_* (Card 1)
        if use_eod_output and eod_output_name:
            src = bundle_dir / eod_output_name
            if not src.exists():
                return jsonify({'success': False, 'message': f'{eod_output_name} not found in bundle'}), 404

            # Remove old EOD output files
            for old in config.BACKEND_DATA_DIR.glob('EOD_Output_*'):
                try:
                    old.unlink()
                except Exception:
                    pass
            # Also clear any stale Parquet cache for EOD
            for old in config.DB_CACHE_DIR.glob('hourly_eod_cache_*.parquet'):
                try:
                    old.unlink()
                except Exception:
                    pass

            saved_name = f'EOD_Output_{eod_output_name}'
            saved_path = config.BACKEND_DATA_DIR / saved_name
            shutil.copy2(str(src), str(saved_path))
            logger.info(f"Bundle EOD output copied: {src} -> {saved_path}")
            result['eod_output_saved'] = True
            result['eod_output_name'] = eod_output_name

            # Auto-cache as Parquet immediately
            try:
                file_hash = _get_file_hash(saved_path)
                cache_path = config.DB_CACHE_DIR / f"hourly_eod_cache_{file_hash}.parquet"
                eod_df = pd.read_excel(saved_path, engine='calamine')
                eod_df.to_parquet(cache_path, index=False)
                result['eod_output_cached'] = True
                logger.info(f"Auto-cached EOD Output: {cache_path.name}")
            except Exception as cache_err:
                logger.warning(f"Auto-cache EOD failed: {cache_err}")
                result['eod_output_cached'] = False

        # Copy hourly daily file → backend HourlyDaily_*
        if use_hourly_daily and hourly_daily_name:
            src = bundle_dir / hourly_daily_name
            if not src.exists():
                return jsonify({'success': False, 'message': f'{hourly_daily_name} not found in bundle'}), 404

            # Remove old HourlyDaily files
            for old in config.BACKEND_DATA_DIR.glob('HourlyDaily_*'):
                try:
                    old.unlink()
                except Exception:
                    pass
            saved_name = f'HourlyDaily_{hourly_daily_name}'
            saved_path = config.BACKEND_DATA_DIR / saved_name
            shutil.copy2(str(src), str(saved_path))
            vba_path = _make_short_vba_path(saved_path)
            logger.info(f"Bundle hourly daily copied: {src} -> {saved_path}")
            result['hourly_daily_path'] = vba_path
            result['hourly_daily_name'] = saved_name

        return jsonify(result)
    except Exception as e:
        err = user_error(e, context='hourly-bundle-use')
        return jsonify({'success': False, 'message': err['user_message']}), 500


@hourly_bp.route('/gdrive-download', methods=['POST'])
def hourly_gdrive_download():
    """Download a file from GDrive and save it for the hourly module."""
    try:
        data = request.get_json()
        file_id = data.get('file_id', '')
        file_name = data.get('file_name', '')
        target = data.get('target', '')  # 'collection' or 'hourly_daily'

        if not file_id or not file_name:
            return jsonify({'success': False, 'message': 'Missing file_id or file_name'}), 400

        gdrive_hourly_dir = config.GDRIVE_DOWNLOAD_DIR / 'hourly'
        gdrive_hourly_dir.mkdir(parents=True, exist_ok=True)

        dest = gdrive_hourly_dir / file_name
        downloaded = gdrive_download_file(file_id, dest)

        if target == 'collection':
            # Save as the cached GDrive collection file
            cache_path = gdrive_hourly_dir / 'gdrive_collection_last.xlsx'
            if cache_path.exists():
                cache_path.unlink()
            shutil.copy2(str(downloaded), str(cache_path))
            logger.info(f"GDrive collection cached: {cache_path}")

            # Auto-cache as Parquet immediately
            collection_cached = False
            try:
                for old in config.DB_CACHE_DIR.glob("hourly_collection_cache_*.parquet"):
                    try:
                        old.unlink()
                    except Exception:
                        pass
                file_hash = _get_file_hash(cache_path)
                pq_path = config.DB_CACHE_DIR / f"hourly_collection_cache_{file_hash}.parquet"
                df = pd.read_excel(cache_path, engine='calamine')
                df.to_parquet(pq_path, index=False)
                collection_cached = True
                logger.info(f"Auto-cached collection: {pq_path.name}")
            except Exception as cache_err:
                logger.warning(f"Auto-cache collection failed: {cache_err}")

            return jsonify({
                'success': True,
                'path': str(cache_path),
                'filename': file_name,
                'target': 'collection',
                'collection_cached': collection_cached,
            })

        elif target == 'hourly_daily':
            # Save as HourlyDaily file in backend (replaces existing)
            for old in config.BACKEND_DATA_DIR.glob('HourlyDaily_*'):
                try:
                    old.unlink()
                except Exception:
                    pass
            saved_name = f'HourlyDaily_{file_name}'
            saved_path = config.BACKEND_DATA_DIR / saved_name
            shutil.copy2(str(downloaded), str(saved_path))
            vba_path = _make_short_vba_path(saved_path)
            logger.info(f"GDrive hourly daily saved: {saved_path}")
            return jsonify({
                'success': True,
                'path': vba_path,
                'filename': saved_name,
                'target': 'hourly_daily',
            })

        else:
            return jsonify({
                'success': True,
                'path': str(downloaded),
                'filename': file_name,
            })

    except Exception as e:
        err = user_error(e, context='hourly-gdrive-download')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Hourly Bundle ────────────────────────────────────────────────────────

_HOURLY_BUNDLE_DIR_NAME = 'Hourly_Bundle'


def _get_hourly_bundle_root():
    downloads = Path.home() / 'Downloads'
    if not downloads.exists():
        downloads = Path.home() / 'Desktop'
    return downloads / _HOURLY_BUNDLE_DIR_NAME


def _get_hourly_latest_mtime():
    latest = config.BACKEND_DATA_DIR / 'Hourly_Collection_Report_Latest.xlsx'
    if latest.exists():
        return latest.stat().st_mtime
    return None


def _find_hourly_existing_save(mtime):
    marker = _get_hourly_bundle_root() / '.last_save_mtime'
    if marker.exists():
        try:
            lines = marker.read_text().strip().split('\n')
            saved_mtime = float(lines[0])
            saved_path = lines[1] if len(lines) > 1 else ''
            if abs(saved_mtime - mtime) < 0.01 and saved_path and Path(saved_path).exists():
                return saved_path
        except (ValueError, IndexError):
            pass
    return None


def _extract_vba_from_js(js_path, var_name):
    """Extract VBA code from a JS const by finding backtick delimiters."""
    content = Path(js_path).read_text(encoding='utf-8')
    # Find the const declaration
    marker = f'{var_name} = `'
    idx = content.find(marker)
    if idx == -1:
        return ''
    start = idx + len(marker)
    end = content.find('`;', start)
    if end == -1:
        end = content.rfind('`')
    if end <= start:
        return ''
    return content[start:end]


def _get_cleanup_vba(fy_label='FY_25-26'):
    """Generate VBA cleanup code with dynamic FY label."""
    return f"""\
    ' =========================================================
    ' CLEANUP: Delete all pivot tables and Sheet1
    ' =========================================================
    Dim cleanWs As Worksheet
    Dim cleanPvt As PivotTable
    Dim cleanSheets As Variant
    Dim csIdx As Long

    cleanSheets = Array("OverAll", "{fy_label}", "OverAll_On-Date", "{fy_label}_On-Date")

    Application.DisplayAlerts = False

    For csIdx = LBound(cleanSheets) To UBound(cleanSheets)
        Set cleanWs = Nothing
        On Error Resume Next
        Set cleanWs = ThisWorkbook.Sheets(cleanSheets(csIdx))
        On Error GoTo 0
        If Not cleanWs Is Nothing Then
            For Each cleanPvt In cleanWs.PivotTables
                cleanPvt.TableRange2.Clear
            Next cleanPvt
            cleanWs.Range("AG1:BZ300").Clear
        End If
    Next csIdx

    ' Delete Sheet1
    On Error Resume Next
    ThisWorkbook.Sheets("Sheet1").Delete
    On Error GoTo 0

    Application.DisplayAlerts = True"""


_CLEANUP_VBA = _get_cleanup_vba()


def _save_hourly_bundle(save_dir, formatted_datetime='', date_only='', account_id_field=''):
    """Copy latest Hourly output + HourlyDaily + VBA templates to a bundle folder.

    Args:
        formatted_datetime: e.g. "14-03-2026 @ 12:20 PM" for VBA injection
        date_only: e.g. "14-03-2026" for VBA injection
        account_id_field: detected AccountID field name
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    saved = []

    # 1. Copy Hourly Collection Report
    latest = config.BACKEND_DATA_DIR / 'Hourly_Collection_Report_Latest.xlsx'
    if latest.exists():
        shutil.copy2(str(latest), str(save_dir / 'Hourly Collection Report.xlsx'))
        saved.append('Hourly Collection Report.xlsx')

    # 2. Copy HourlyDaily file
    hd_file = None
    for f in config.BACKEND_DATA_DIR.glob('HourlyDaily_*'):
        if not f.name.startswith('~$'):
            hd_file = f
            break
    hd_bundle_name = None
    if hd_file:
        hd_bundle_name = hd_file.name.replace('HourlyDaily_', '')
        shutil.copy2(str(hd_file), str(save_dir / hd_bundle_name))
        saved.append(hd_bundle_name)

    # Local path for VBA injection (bundle's HourlyDaily file)
    local_hd_path = str(save_dir / hd_bundle_name) if hd_bundle_name else ''

    # 3. Extract VBA templates, inject date/time and paths
    vba_js = config.STATIC_DIR / 'hourly' / 'vba_code.js'

    for var_name, dst_name in [
        ('MERGE_VBA_CODE', 'VBA_Merge.txt'),
        ('DEMO_VBA_CODE', 'VBA_Demo.txt'),
        ('FINAL_VBA_CODE', 'VBA_Final.txt'),
    ]:
        vba_text = _extract_vba_from_js(vba_js, var_name)
        if vba_text:
            if '{{HOURLY_DAILY_PATH}}' in vba_text:
                vba_text = vba_text.replace('{{HOURLY_DAILY_PATH}}', local_hd_path)
            if formatted_datetime:
                vba_text = vba_text.replace('{{DEMO_DATE_TIME}}', formatted_datetime)
            if date_only:
                vba_text = vba_text.replace('{{DEMO_DATE_ONLY}}', date_only)
            if account_id_field:
                vba_text = vba_text.replace('{{ACCOUNT_ID_FIELD}}', account_id_field)
            else:
                vba_text = vba_text.replace('{{ACCOUNT_ID_FIELD}}', '')
            # Replace cleanup placeholder — empty for Merge, full cleanup for Demo/Final
            if '{{CLEANUP_CODE}}' in vba_text:
                if var_name in ('DEMO_VBA_CODE', 'FINAL_VBA_CODE'):
                    # Derive FY label from date_only (dd-mm-yyyy)
                    _cleanup_vba = _CLEANUP_VBA
                    if date_only:
                        try:
                            from datetime import datetime as _dt
                            from services.eod_processor import get_fy_label
                            _d = _dt.strptime(date_only, '%d-%m-%Y')
                            _cleanup_vba = _get_cleanup_vba(get_fy_label(_d))
                        except Exception:
                            pass
                    vba_text = vba_text.replace('{{CLEANUP_CODE}}', _cleanup_vba)
                else:
                    vba_text = vba_text.replace('{{CLEANUP_CODE}}', '')

            # Replace hardcoded FY_25-26 with dynamic FY label
            if date_only:
                try:
                    from datetime import datetime as _dt
                    from services.eod_processor import get_fy_label
                    _d = _dt.strptime(date_only, '%d-%m-%Y')
                    _fy = get_fy_label(_d)
                    if _fy != 'FY_25-26':
                        vba_text = vba_text.replace('FY_25-26', _fy)
                except Exception:
                    pass

            (save_dir / dst_name).write_text(vba_text, encoding='utf-8')
            saved.append(dst_name)

    # 4. Save target_date metadata
    if date_only:
        (save_dir / '.target_date').write_text(date_only)

    # 5. Write mtime marker for duplicate detection
    mtime = _get_hourly_latest_mtime()
    if mtime is not None:
        marker = _get_hourly_bundle_root() / '.last_save_mtime'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{mtime}\n{save_dir}")

    return saved, str(save_dir)


@hourly_bp.route('/save-bundle-to-server', methods=['POST'])
def hourly_save_bundle():
    """Save Hourly_Bundle to ~/Downloads/Hourly_Bundle/<timestamp>/."""
    try:
        data = request.get_json(force=True) if request.is_json else {}
        action = data.get('action')
        formatted_datetime = data.get('formatted_datetime', '')
        date_only = data.get('date_only', '')
        account_id_field = data.get('account_id_field', '')
        mtime = _get_hourly_latest_mtime()

        if mtime is None:
            return jsonify({'success': False, 'message': 'No processed output found. Run Processing first.'}), 400

        # Duplicate detection
        if action is None and mtime is not None:
            existing = _find_hourly_existing_save(mtime)
            if existing:
                return jsonify(
                    already_saved=True,
                    existing_path=existing,
                    existing_name=Path(existing).name,
                )

        # Auto-generate folder timestamp from current time
        now = datetime.now()
        timestamp = f"{now.strftime('%Hh.%Mm.%Ss')} on {now.strftime('%d-%m-%Y')}"

        if action == 'replace':
            existing = _find_hourly_existing_save(mtime) if mtime else None
            save_dir = existing if existing else str(_get_hourly_bundle_root() / timestamp)
        else:
            save_dir = str(_get_hourly_bundle_root() / timestamp)

        saved, path = _save_hourly_bundle(
            save_dir,
            formatted_datetime=formatted_datetime,
            date_only=date_only,
            account_id_field=account_id_field,
        )
        return jsonify(success=True, saved=saved, path=path)

    except Exception as e:
        err = user_error(e, context='hourly-save-bundle')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── WhatsApp Sender ────────────────────────────────────────────────────

@hourly_bp.route('/whatsapp-sender')
def whatsapp_sender():
    """Serve the WhatsApp sender page."""
    return send_from_directory(HOURLY_STATIC, 'whatsapp_sender.html')


@hourly_bp.route('/whatsapp-contacts', methods=['GET'])
def whatsapp_contacts_get():
    """Return the list of WhatsApp contacts."""
    csv_path = config.DATA_DIR / 'whatsapp_contacts.csv'
    names = []
    if csv_path.exists():
        import csv
        with open(csv_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                n = row.get('name', '').strip()
                if n:
                    names.append(n)
    return jsonify({'contacts': names})


@hourly_bp.route('/whatsapp-contacts', methods=['POST'])
def whatsapp_contacts_save():
    """Save the list of WhatsApp contacts."""
    data = request.get_json(silent=True) or {}
    names = data.get('contacts', [])
    csv_path = config.DATA_DIR / 'whatsapp_contacts.csv'
    import csv
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['name'])
        for n in names:
            n = n.strip()
            if n:
                writer.writerow([n])
    return jsonify({'success': True, 'contacts': names})


@hourly_bp.route('/whatsapp-open', methods=['POST'])
def whatsapp_open():
    """Open WhatsApp Web in Chromium (browser stays open)."""
    from services.whatsapp_sender import open_whatsapp
    result = open_whatsapp()
    return jsonify(result), 200 if result['success'] else 500


@hourly_bp.route('/whatsapp-send', methods=['POST'])
def whatsapp_send():
    """Send a file from a bundle via WhatsApp."""
    from services.whatsapp_sender import send_file_to_contact

    data = request.get_json(silent=True) or {}
    bundle_path = data.get('bundle_path', '')
    filename = data.get('filename', '')

    if not bundle_path or not filename:
        return jsonify({'success': False, 'error': 'Missing bundle_path or filename'}), 400

    file_path = Path(bundle_path) / filename
    if not file_path.exists():
        return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404

    result = send_file_to_contact(str(file_path))
    status_code = 200 if result['success'] else 500
    return jsonify(result), status_code


# ── Hourly VBA Runner ───────────────────────────────────────────────────

@hourly_bp.route('/vba-runner/bundles')
def hourly_vba_runner_bundles():
    """List all Hourly_Bundle folders sorted newest-first."""
    bundle_root = _get_hourly_bundle_root()
    if not bundle_root.exists():
        return jsonify({'bundles': []})

    def _parse_sort_key(dirname):
        try:
            parts = dirname.split(' on ')
            if len(parts) == 2:
                tp = parts[0]
                dp = parts[1]
                h = int(tp.split('h')[0])
                m = int(tp.split('.')[1].replace('m', ''))
                s = int(tp.split('.')[2].replace('s', ''))
                dd, mm, yyyy = dp.split('-')
                return datetime(int(yyyy), int(mm), int(dd), h, m, s)
        except Exception:
            pass
        return datetime.min

    subdirs = [d for d in bundle_root.iterdir() if d.is_dir() and not d.name.startswith('.')]
    subdirs.sort(key=lambda d: _parse_sort_key(d.name), reverse=True)

    bundles = []
    for d in subdirs:
        files = [
            f.name for f in d.iterdir()
            if f.is_file() and not f.name.startswith('~$') and not f.name.startswith('.')
        ]
        td_file = d / '.target_date'
        td = td_file.read_text().strip() if td_file.exists() else None

        # Find the hourly daily file in this bundle
        hd_file = None
        for fn in files:
            fn_lower = fn.lower()
            if 'hourly daily' in fn_lower or fn_lower.startswith('hourlydaily'):
                hd_file = fn
                break

        bundles.append({
            'name': d.name,
            'path': str(d),
            'files': sorted(files),
            'target_date': td,
            'hourly_daily_file': hd_file,
            'has_report': 'Hourly Collection Report.xlsx' in files,
            'has_merge_vba': 'VBA_Merge.txt' in files,
            'has_demo_vba': 'VBA_Demo.txt' in files,
            'has_final_vba': 'VBA_Final.txt' in files,
        })

    return jsonify({'bundles': bundles})


@hourly_bp.route('/vba-runner/run', methods=['POST'])
def hourly_vba_runner_run():
    """Run VBA macro on the Hourly Collection Report in a bundle.

    POST JSON: {bundle_path, script: "merge"|"demo"|"final"}
    VBA files in bundles already have date/time and paths injected from bundle save.
    """
    import platform as _plat

    data = request.get_json(force=True)
    bundle_path = Path(data.get('bundle_path', ''))
    script_type = data.get('script', 'final')

    if not bundle_path.exists() or not bundle_path.is_dir():
        return jsonify({'error': 'Bundle folder not found'}), 404

    report_xlsx = bundle_path / 'Hourly Collection Report.xlsx'
    if not report_xlsx.exists():
        return jsonify({'error': 'Hourly Collection Report.xlsx not found in bundle'}), 404

    # Pick VBA file
    vba_map = {'merge': 'VBA_Merge.txt', 'demo': 'VBA_Demo.txt', 'final': 'VBA_Final.txt'}
    vba_file = bundle_path / vba_map.get(script_type, 'VBA_Final.txt')
    if not vba_file.exists():
        return jsonify({'error': f'{vba_file.name} not found in bundle'}), 404

    # Macro name map
    macro_map = {'merge': 'CopySheets', 'demo': 'CreatePivotTable', 'final': 'FinalProcess'}
    macro_name = macro_map.get(script_type, 'FinalProcess')

    # VBA text is ready as-is (date/time/paths already injected during bundle save)
    vba_text = vba_file.read_text(encoding='utf-8')

    import tempfile
    vba_tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', delete=False, encoding='utf-8', dir=str(config.TEMP_DIR)
    )
    vba_tmp.write(vba_text)
    vba_tmp.close()
    vba_tmp_path = Path(vba_tmp.name)

    try:
        if _plat.system() == 'Windows':
            # Kill any existing/zombie Excel processes so COM gets a fresh instance
            import subprocess as _sp_check
            _sp_check.run(
                ['taskkill', '/F', '/IM', 'EXCEL.EXE'],
                capture_output=True, timeout=10,
            )
            import time as _tw
            _tw.sleep(1)
            return _hourly_vba_runner_windows(report_xlsx, vba_tmp_path, macro_name)
        else:
            return _hourly_vba_runner_mac(report_xlsx, vba_tmp_path, macro_name)
    finally:
        try:
            vba_tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _hourly_vba_runner_windows(xlsx_path, vba_file, macro_name):
    """Run VBA macro via VBScript (cscript.exe).

    Mirrors the proven EOD _vba_runner_windows approach:
    VBScript uses native Windows COM — no pywin32, no gen_py cache issues.
    """
    import subprocess as _sp
    import tempfile
    import time

    xlsx_str = str(xlsx_path.resolve())
    vba_str = str(vba_file.resolve())

    vbs_lines = [
        'On Error Resume Next',
        'Dim xlApp, wb, vbMod, fso, ts, vbaCode, modName',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        'Set xlApp = CreateObject("Excel.Application")',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: Could not start Excel"',
        '    WScript.Quit 1',
        'End If',
        'On Error GoTo 0',
        '',
        'xlApp.DisplayAlerts = False',
        'xlApp.Visible = True',
        'xlApp.AutomationSecurity = 1',  # msoAutomationSecurityLow — allow macros
        '',
        # Open workbook, save as .xlsm FIRST (xlsx can't run macros)
        'Dim origPath, xlsmPath',
        f'origPath = "{xlsx_str}"',
        'xlsmPath = Replace(origPath, ".xlsx", ".xlsm")',
        'If fso.FileExists(xlsmPath) Then fso.DeleteFile xlsmPath',
        '',
        'Set wb = xlApp.Workbooks.Open(origPath)',
        'If wb Is Nothing Then',
        '    WScript.StdErr.WriteLine "ERROR: Could not open workbook"',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'wb.SaveAs xlsmPath, 52',
        '',
        # Read VBA code (ASCII encoding)
        f'Set ts = fso.OpenTextFile("{vba_str}", 1, False, 0)',
        'vbaCode = ts.ReadAll',
        'ts.Close',
        '',
        # Inject VBA into the .xlsm workbook
        'On Error Resume Next',
        'Set vbMod = wb.VBProject.VBComponents.Add(1)',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: VBA access blocked"',
        '    xlApp.DisplayAlerts = True',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'Err.Clear',
        'vbMod.CodeModule.AddFromString vbaCode',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: Failed to inject VBA: " & Err.Description',
        '    xlApp.DisplayAlerts = True',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'On Error GoTo 0',
        '',
        # Run the macro
        f'xlApp.Run "{macro_name}"',
        '',
        # Save back as .xlsx and clean up
        'wb.VBProject.VBComponents.Remove vbMod',
        'wb.SaveAs origPath, 51',
        'If fso.FileExists(xlsmPath) Then fso.DeleteFile xlsmPath',
        '',
        'xlApp.DisplayAlerts = True',
        '',
        'WScript.StdOut.WriteLine "OK"',
    ]
    vbs_content = '\r\n'.join(vbs_lines) + '\r\n'

    tmp_vbs = None
    try:
        t0 = time.perf_counter()
        logger.info(f"VBA-RUNNER [Windows/VBS]: Launching cscript for {xlsx_path.name}")

        tmp_vbs = tempfile.NamedTemporaryFile(
            mode='w', suffix='.vbs', delete=False, encoding='utf-8'
        )
        tmp_vbs.write(vbs_content)
        tmp_vbs.close()

        result = _sp.run(
            ['cscript.exe', '//Nologo', tmp_vbs.name],
            capture_output=True, text=True, timeout=900,
        )

        logger.info(f"VBA-RUNNER [Windows/VBS] stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            logger.warning(f"VBA-RUNNER [Windows/VBS] stderr: {result.stderr.strip()}")

        elapsed = time.perf_counter() - t0
        output = result.stdout.strip()
        err_output = result.stderr.strip()

        if result.returncode == 0 and output == 'OK':
            logger.info(f"VBA-RUNNER [Windows/VBS]: Completed in {elapsed:.1f}s")
            return jsonify({
                'success': True,
                'output': f'Macro executed on {xlsx_path.name}',
                'elapsed': round(elapsed, 1),
                'message': f'{macro_name} completed successfully',
            })
        else:
            error_msg = output or err_output or 'Unknown error from cscript'
            if 'VBA access blocked' in error_msg:
                return jsonify({
                    'error': 'Excel blocked VBA access. Fix: Excel > File > Options > '
                             'Trust Center > Trust Center Settings > Macro Settings > '
                             'check "Trust access to the VBA project object model", then retry.'
                }), 403
            return jsonify({'error': error_msg}), 500

    except _sp.TimeoutExpired:
        logger.error("VBA-RUNNER [Windows/VBS]: Timed out after 15 minutes")
        return jsonify({
            'error': 'Excel automation timed out (15 min). The macro may still be running in Excel.'
        }), 504
    except Exception as e:
        logger.exception(f"VBA-RUNNER [Windows/VBS]: Failed: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if tmp_vbs:
            import os
            try:
                os.unlink(tmp_vbs.name)
            except OSError:
                pass


def _hourly_vba_runner_mac(xlsx_path, vba_file, macro_name):
    """Run VBA macro using AppleScript (macOS only).

    Mirrors the proven EOD _vba_runner_mac approach:
    separate small osascript calls + `run VB macro` command.
    """
    import subprocess as _sp
    import time as _time

    try:
        t0 = _time.perf_counter()
        logger.info(f"VBA-RUNNER [Mac]: Opening Excel and running VBA on {xlsx_path.name}")

        xlsx_str = str(xlsx_path.resolve())

        def _osa(script, label, timeout=30):
            r = _sp.run(['osascript', '-e', script],
                        capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                err = r.stderr.strip()
                logger.error(f"VBA-RUNNER [{label}]: {err}")
                raise RuntimeError(f'{label}: {err}')
            logger.info(f"VBA-RUNNER [{label}]: OK - {r.stdout.strip()}")
            return r.stdout.strip()

        # Step 1: Close all open workbooks
        _osa('''
tell application "Microsoft Excel"
    activate
    try
        close every workbook without saving
    end try
end tell
return "closed"
''', 'close-all')

        _time.sleep(1)

        # Step 2: Open the workbook
        _osa(f'''
tell application "Microsoft Excel"
    open "{xlsx_str}"
end tell
delay 5
tell application "Microsoft Excel"
    return name of active workbook
end tell
''', 'open-file', timeout=60)

        # Step 3: Copy VBA code to clipboard
        vba_code = vba_file.read_bytes()
        _sp.run(['pbcopy'], input=vba_code, timeout=10)
        logger.info("VBA-RUNNER [clipboard]: VBA code copied")

        # Step 4: Record a macro then stop (creates an empty module)
        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Record New Macro..." of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
delay 1.5
tell application "System Events"
    keystroke return
end tell
delay 1
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Stop Recording" of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
return "recorded"
''', 'record-macro')

        _time.sleep(1)

        # Step 5: Open VBE via Macros dialog → Edit
        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Macros..." of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
delay 2
tell application "System Events"
    tell process "Microsoft Excel"
        click button "Edit" of front window
    end tell
end tell
delay 2
return "vbe-open"
''', 'open-vbe-edit')

        # Re-copy VBA to clipboard (in case something overwrote it)
        _sp.run(['pbcopy'], input=vba_code, timeout=10)

        # Step 6: Select All + Paste in VBE (using menu items, not keyboard shortcuts)
        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Select All" of menu "Edit" of menu bar 1
        delay 0.5
        click menu item "Paste" of menu "Edit" of menu bar 1
        delay 5
    end tell
end tell
return "pasted"
''', 'paste-vba', timeout=30)

        # Step 7: Run the macro via AppleScript command (not F5 key)
        _osa(f'''
tell application "Microsoft Excel"
    activate
end tell
delay 1
tell application "Microsoft Excel"
    run VB macro "{macro_name}"
end tell
tell application "Microsoft Excel"
    save active workbook
end tell
return "done"
''', 'run-macro', timeout=900)

        elapsed = _time.perf_counter() - t0
        logger.info(f"VBA-RUNNER [Mac]: Macro completed in {elapsed:.1f}s")

        return jsonify({
            'success': True,
            'output': 'Macro executed on ' + xlsx_path.name,
            'elapsed': round(elapsed, 1),
            'message': f'{macro_name} completed successfully',
        })

    except _sp.TimeoutExpired:
        logger.error("VBA-RUNNER [Mac]: Timed out after 15 minutes")
        return jsonify({'error': 'Excel automation timed out (15 min). The macro may still be running in Excel.'}), 504
    except Exception as e:
        logger.exception(f"VBA-RUNNER [Mac]: Failed: {e}")
        return jsonify({'error': str(e)}), 500


# ── Health Check ─────────────────────────────────────────────────────────

@hourly_bp.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'module': 'hourly'})
