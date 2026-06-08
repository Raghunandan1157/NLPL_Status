"""
Instant Report Blueprint
========================
Provides endpoints to upload PAR + Collection, run EOD pipeline,
compute pivot summaries, and return JSON for frontend image rendering.

Reuses: DBManager, eod_processor, file_manager, column_matcher
"""

from flask import Blueprint, send_from_directory, jsonify, request
from pathlib import Path
import tempfile
import logging
import time
import io
import hashlib
import pandas as pd
import duckdb

import config
from services.db_manager import get_db_manager
from services.instant_processor import compute_instant_report
from services.instant_cache import (
    save_date_cache, list_cached_dates, load_date_cache,
    save_date_report, load_date_report, load_multi_date_reports, extract_entity_data,
    get_hierarchy_from_parquet,
)
from services import eod_processor as processor
from services.excel_reader import compute_file_hash, save_upload_to_temp
from services.memory_manager import try_acquire_processing, release_processing, gc_checkpoint
from services.error_handler import user_error

try:
    from services.employee_processor import invalidate_merged_df_cache
except ImportError:
    invalidate_merged_df_cache = lambda date_str=None: None

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
instant_bp = Blueprint('instant', __name__)

# ---------------------------------------------------------------------------
# Paths from config
# ---------------------------------------------------------------------------
STATIC_INSTANT_DIR = config.STATIC_DIR / 'instant'
BACKEND_DATA_DIR = config.BACKEND_DATA_DIR
BACKEND_MONTHLY_DIR = config.BACKEND_MONTHLY_DIR
DB_CACHE_DIR = config.DB_CACHE_DIR

# ---------------------------------------------------------------------------
# DB Manager - shared singleton (single DuckDB connection for all blueprints)
# ---------------------------------------------------------------------------
db_manager = get_db_manager()


# ---------------------------------------------------------------------------
# Helper: resolve month-specific backend directory
# ---------------------------------------------------------------------------

def _get_monthly_backend_dir(target_date_str):
    """
    Given a target date string (DD-MM-YYYY or YYYY-MM-DD), return the monthly backend dir.
    Always returns the month-specific folder — never falls back to global BACKEND_DATA_DIR.
    Each month has its own independent Demand + Last Month PAR.
    """
    import re
    # Try DD-MM-YYYY first
    m = re.match(r'(\d{2})-(\d{2})-(\d{4})', target_date_str)
    if m:
        year, month = m.group(3), m.group(2)
    else:
        # Try YYYY-MM-DD
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', target_date_str)
        if m:
            year, month = m.group(1), m.group(2)
        else:
            # Can't parse date — use current month
            from datetime import datetime
            now = datetime.now()
            year, month = str(now.year), str(now.month).zfill(2)

    monthly_dir = BACKEND_MONTHLY_DIR / f"{year}-{month}"
    monthly_dir.mkdir(parents=True, exist_ok=True)
    return monthly_dir


# ---------------------------------------------------------------------------
# Helpers: Parquet caching for monthly backend files
# ---------------------------------------------------------------------------

def _create_monthly_parquet_cache(excel_path, file_type):
    """Convert uploaded Excel to Parquet cache in the same directory for fast reads."""
    cache_name = 'demand_cache.parquet' if file_type == 'demand' else 'last_month_par_cache.parquet'
    cache_path = excel_path.parent / cache_name
    try:
        t0 = time.time()
        if file_type == 'demand':
            try:
                df = pd.read_excel(excel_path, sheet_name=0, engine='calamine')
            except Exception:
                df = pd.read_excel(excel_path, sheet_name=0)
            for col in df.columns:
                if df[col].dtype == 'object':
                    df[col] = df[col].astype(str).replace('nan', '').replace('None', '')
        else:
            try:
                df = pd.read_excel(excel_path, sheet_name=0, usecols=['AccountID', 'DPD Days', 'LoanStatus'], engine='calamine')
            except ValueError:
                try:
                    df = pd.read_excel(excel_path, sheet_name=0, engine='calamine')
                except Exception:
                    df = pd.read_excel(excel_path, sheet_name=0)
            except Exception:
                try:
                    df = pd.read_excel(excel_path, sheet_name=0, usecols=['AccountID', 'DPD Days', 'LoanStatus'])
                except ValueError:
                    df = pd.read_excel(excel_path, sheet_name=0)
            if 'AccountID' in df.columns:
                df['AccountID'] = df['AccountID'].astype(str).str.strip()
                df = df[~df['AccountID'].str.contains('\ufffd|nan|None', na=True, case=False)]
        df.to_parquet(cache_path, index=False)
        elapsed = time.time() - t0
        logging.info(f"Created parquet cache: {cache_path.name} ({len(df)} rows, {elapsed:.1f}s)")
        return cache_path, len(df)
    except Exception as e:
        logging.error(f"Failed to create parquet cache: {e}")
        return None, 0


def _load_demand_fallback(backend_dir, con):
    """Load Demand Master from backend dir (parquet preferred), register in DuckDB. Returns source name or None."""
    cache_path = backend_dir / 'demand_cache.parquet'
    excel_files = list(backend_dir.glob("Demand_Sheet_Master_*"))
    if cache_path.exists() and (not excel_files or cache_path.stat().st_mtime >= excel_files[0].stat().st_mtime):
        logging.info(f"Loading demand from parquet cache: {cache_path}")
        df = pd.read_parquet(cache_path)  # Full read: returning cached data for DuckDB registration
        con.register('raw_demand_upload', df)
        return 'raw_demand_upload'
    if excel_files:
        logging.info(f"Loading demand from Excel: {excel_files[0].name}")
        df = processor.smart_read_excel(excel_files[0])
        con.register('raw_demand_upload', df)
        return 'raw_demand_upload'
    return None


def _load_last_month_fallback(backend_dir, con):
    """Load Last Month PAR from backend dir (parquet preferred), register in DuckDB. Returns True if loaded."""
    cache_path = backend_dir / 'last_month_par_cache.parquet'
    excel_files = list(backend_dir.glob("Last_Month*.xlsx"))
    if cache_path.exists() and (not excel_files or cache_path.stat().st_mtime >= excel_files[0].stat().st_mtime):
        logging.info(f"Loading Last Month PAR from parquet cache: {cache_path}")
        df = pd.read_parquet(cache_path)  # Full read: returning cached data for DuckDB registration
        con.register('Last_Month_PAR', df)
        return True
    if excel_files:
        try:
            df = processor.smart_read_excel(excel_files[0], usecols=['AccountID', 'DPD Days', 'LoanStatus'])
            con.register('Last_Month_PAR', df)
            return True
        except (ValueError, KeyError, Exception) as e:
            logging.warning(f"Could not load Last Month PAR from Excel: {e}")
    return False


# ---------------------------------------------------------------------------
# Helper: ensure cached reports are fresh (regenerate if demand data changed)
# ---------------------------------------------------------------------------

def _ensure_reports_fresh(date_list):
    """
    Check each cached report.json against its month's demand/last_month parquet.
    If the backend data is newer than the report, regenerate the report.
    Returns dict of {date_str: report_data} with all reports fresh.
    """
    from datetime import datetime as dt
    reports = {}

    if not db_manager:
        # Can't regenerate without DB, fall back to cached
        return load_multi_date_reports(date_list)

    con = db_manager.get_connection()

    for date_str in date_list:
        report_path = config.INSTANT_HISTORY_DIR / date_str / 'report.json'
        if not report_path.exists():
            continue

        # Check if report is stale (monthly backend data is newer)
        backend_dir = _get_monthly_backend_dir(date_str)
        demand_cache = backend_dir / 'demand_cache.parquet'
        lm_cache = backend_dir / 'last_month_par_cache.parquet'

        report_mtime = report_path.stat().st_mtime
        needs_regen = False
        if demand_cache.exists() and demand_cache.stat().st_mtime > report_mtime:
            needs_regen = True
        if lm_cache.exists() and lm_cache.stat().st_mtime > report_mtime:
            needs_regen = True

        if not needs_regen:
            # Report is fresh, load from cache
            try:
                import json
                with open(report_path, 'r') as f:
                    reports[date_str] = json.load(f)
            except Exception:
                pass
            continue

        # Report is stale — regenerate from cached PAR+Collection + current month's demand
        try:
            logging.info(f"Regenerating stale report for {date_str}")
            target_date = dt.strptime(date_str, '%Y-%m-%d')

            df_par, df_collection, cached_meta = load_date_cache(date_str)

            from services.eod_processor import parse_trxdate
            df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])

            con.register('daily_par', df_par)
            con.register('daily_collection', df_collection)

            # Load demand from monthly folder
            demand_source = _load_demand_fallback(backend_dir, con)
            if not demand_source:
                logging.warning(f"No demand for {date_str}, skipping regen")
                # Load old report as fallback
                try:
                    import json
                    with open(report_path, 'r') as f:
                        reports[date_str] = json.load(f)
                except Exception:
                    pass
                continue

            has_last_month = _load_last_month_fallback(backend_dir, con)

            df_result = _run_merge_pipeline(
                con, df_par, df_collection, target_date, demand_source, has_last_month
            )

            report_data = compute_instant_report(df_result, target_date=target_date)
            report_data['metadata']['source'] = 'cache'
            report_data['metadata']['cache_date'] = date_str
            report_data['metadata']['regenerated'] = True

            save_date_report(date_str, report_data)
            reports[date_str] = report_data
            logging.info(f"Regenerated report for {date_str}")

        except Exception as e:
            logging.warning(f"Failed to regenerate {date_str}: {e}")
            # Fall back to old cached report
            try:
                import json
                with open(report_path, 'r') as f:
                    reports[date_str] = json.load(f)
            except Exception:
                pass

    return reports


# ---------------------------------------------------------------------------
# Helper: shared SQL merge pipeline
# ---------------------------------------------------------------------------

def _run_merge_pipeline(con, df_par, df_collection, target_date, demand_source, has_last_month):
    """
    Build and execute the SQL merge query that joins PAR, Collection, Demand,
    and optionally Last Month PAR.  Used by both /process and /generate-from-cache.

    Returns:
        df_result  - merged DataFrame ready for compute_instant_report()
    """
    first_of_month = None
    if target_date:
        first_of_month = target_date.replace(day=1)

    # Detect DPD column name
    days_group_col = 'DPD Group'
    ALLOWED_DPD_COLUMNS = processor.ALLOWED_DPD_COLUMNS
    for col in df_par.columns:
        if col in ALLOWED_DPD_COLUMNS:
            days_group_col = col
            break

    if days_group_col not in ALLOWED_DPD_COLUMNS:
        raise ValueError(f"DPD column '{days_group_col}' not recognized")

    date_filter_clause = ""
    if target_date and first_of_month:
        date_filter_clause = f"""
          AND Trxdate >= '{first_of_month.strftime('%Y-%m-%d')}'
          AND Trxdate <= '{target_date.strftime('%Y-%m-%d')}'"""

    ctes = [
        f"""Collection_Agg AS (
            SELECT AccountID, SUM(CollectionTotal) as Collection_Sum, MAX(Trxdate) as Latest_Date
            FROM daily_collection
            WHERE (ReverseTotal = 0 OR ReverseTotal IS NULL OR TRIM(CAST(ReverseTotal AS VARCHAR)) = ''){date_filter_clause}
            GROUP BY AccountID
        )""",
        f"""PAR_Mapped AS (
            SELECT AccountID, "{days_group_col}" as Par_DPD_Group
            FROM daily_par
        )"""
    ]

    select_clauses = [
        "d.*",
        "c.Collection_Sum as Collection",
        "strftime(c.Latest_Date, '%d-%m-%Y') as \"Collection Date\"",
        "p.Par_DPD_Group as \"DPD Group\"",
        """CASE
            WHEN c.Collection_Sum IS NULL THEN 'Not Collected'
            WHEN (TRY_CAST(d."Regular Demand" AS DOUBLE) - c.Collection_Sum) <= 0 THEN 'Full EMI Paid'
            ELSE 'Partial Amount'
        END as "Partial Amount" """,
        """(TRY_CAST(d."Installment Amount" AS DOUBLE) - COALESCE(c.Collection_Sum, 0)) as "installment - collected amt" """,
        """CASE
            WHEN (TRY_CAST(d."Installment Amount" AS DOUBLE) - COALESCE(c.Collection_Sum, 0)) <= 0 THEN 1
            ELSE 0
        END as "installment - collected value" """
    ]

    joins = [
        f"FROM {demand_source} d",
        "LEFT JOIN Collection_Agg c ON d.\"Account ID\" = c.AccountID",
        "LEFT JOIN PAR_Mapped p ON d.\"Account ID\" = p.AccountID"
    ]

    if has_last_month:
        ctes.append("""Legacy_Data AS (
            SELECT AccountID, "DPD Days", LoanStatus
            FROM Last_Month_PAR
        )""")
        select_clauses.append("lm.\"DPD Days\" as \"DPD Group - Last Month\"")
        select_clauses.append("lm.LoanStatus as \"Loan Status - Last Month\"")
        joins.append("LEFT JOIN Legacy_Data lm ON d.\"Account ID\" = lm.AccountID")
    else:
        select_clauses.append("NULL as \"DPD Group - Last Month\"")
        select_clauses.append("NULL as \"Loan Status - Last Month\"")

    final_query = "WITH " + ",\n".join(ctes) + "\nSELECT \n" + ",\n".join(select_clauses) + "\n" + "\n".join(joins)

    df_result = con.execute(final_query).df()
    return df_result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@instant_bp.route('/')
def index():
    """Serve Instant Report index.html."""
    return send_from_directory(str(STATIC_INSTANT_DIR), 'index.html')


@instant_bp.route('/backend-status', methods=['GET'])
def backend_status():
    """Check if Demand Master + Last Month PAR exist in backend."""
    try:
        BACKEND_DATA_DIR.mkdir(exist_ok=True)

        status = {
            'demandMaster': None,
            'lastMonthPar': None,
            'dbAvailable': db_manager is not None,
            'demandInDb': False,
            'lastMonthInDb': False
        }

        # Check file system
        for f in BACKEND_DATA_DIR.iterdir():
            if f.name.startswith('Demand_Sheet_Master_'):
                status['demandMaster'] = f.name
            elif f.name.startswith('Last_Month_PAR_'):
                status['lastMonthPar'] = f.name

        # Check database
        if db_manager:
            con = db_manager.get_connection()
            try:
                count = con.execute("SELECT count(*) FROM Demand_Master").fetchone()[0]
                if count > 0:
                    status['demandInDb'] = True
                    status['demandRowCount'] = count
            except (duckdb.CatalogException, duckdb.Error):
                pass
            try:
                count = con.execute("SELECT count(*) FROM Last_Month_PAR").fetchone()[0]
                if count > 0:
                    status['lastMonthInDb'] = True
                    status['lastMonthRowCount'] = count
            except (duckdb.CatalogException, duckdb.Error):
                pass

        # Monthly backend data info
        monthly_months = []
        BACKEND_MONTHLY_DIR.mkdir(exist_ok=True)
        for folder in sorted(BACKEND_MONTHLY_DIR.iterdir(), reverse=True):
            if folder.is_dir() and len(folder.name) == 7:
                month_info = {'month': folder.name, 'files': []}
                for f in folder.iterdir():
                    if f.is_file():
                        month_info['files'].append(f.name)
                monthly_months.append(month_info)
        status['monthlyData'] = monthly_months

        return jsonify(status)

    except Exception as e:
        err = user_error(e, context='instant-backend-status')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/cache-file', methods=['POST'])
def cache_file():
    """Pre-cache uploaded Excel as Parquet on file drop."""
    try:
        file_type = request.form.get('type')
        if file_type not in ['par', 'collection']:
            return jsonify({'error': 'Invalid file type'}), 400

        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        tmp_path = save_upload_to_temp(file, prefix=f"instant_cache_{file_type}_")

        try:
            # Calculate hash from disk
            file_hash = compute_file_hash(tmp_path)

            DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            if file_type == 'par':
                cache_pattern = "daily_par_cache_*.parquet"
                cache_path = DB_CACHE_DIR / f"daily_par_cache_{file_hash}.parquet"
                cols_to_use = ['AccountID', 'Days Group', 'Days group', 'DaysGroup', 'DPD Group', 'DPD Days', 'DPDDays']
            else:
                cache_pattern = "daily_collection_cache_*.parquet"
                cache_path = DB_CACHE_DIR / f"daily_collection_cache_{file_hash}.parquet"
                cols_to_use = ['AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal']

            # Check if cache already exists
            if cache_path.exists():
                return jsonify({
                    'success': True,
                    'cached': True,
                    'hash': file_hash,
                    'message': f'{file_type.upper()} already cached',
                    'alreadyCached': True
                })

            # Delete old caches for this type
            for old_cache in DB_CACHE_DIR.glob(cache_pattern):
                try:
                    old_cache.unlink()
                except Exception:
                    pass

            t_start = time.time()

            try:
                df = pd.read_excel(tmp_path, usecols=lambda x: x in cols_to_use, engine='calamine')
            except (ValueError, KeyError):
                try:
                    df = pd.read_excel(tmp_path, engine='calamine')
                except Exception:
                    df = pd.read_excel(tmp_path)
            except Exception:
                try:
                    df = pd.read_excel(tmp_path, usecols=lambda x: x in cols_to_use)
                except (ValueError, KeyError):
                    df = pd.read_excel(tmp_path)

            df.to_parquet(cache_path, index=False)
            elapsed = time.time() - t_start

            return jsonify({
                'success': True,
                'cached': True,
                'hash': file_hash,
                'time': round(elapsed, 2),
                'message': f'{file_type.upper()} cached in {elapsed:.1f}s'
            })
        finally:
            tmp_path.unlink(missing_ok=True)

    except Exception as e:
        err = user_error(e, context='instant-cache-file')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/history-dates', methods=['GET'])
def history_dates():
    """Return list of dates that have cached instant report data."""
    try:
        dates = list_cached_dates()
        return jsonify({'dates': dates})
    except Exception as e:
        err = user_error(e, context='instant-history-dates')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/nlpl-dates', methods=['GET'])
def nlpl_dates():
    """Return NLPL (grand total) data across all cached dates."""
    try:
        cached = list_cached_dates()
        if not cached:
            return jsonify({'error': 'No cached dates'}), 404

        date_list = [d['date_iso'] for d in cached]
        reports = _ensure_reports_fresh(date_list)

        if not reports:
            return jsonify({'error': 'No cached reports found'}), 404

        result_dates = []
        for date_str in sorted(reports.keys()):
            entity_data = extract_entity_data(reports[date_str], 'NLPL', 'Region')
            if entity_data:
                parts = date_str.split('-')
                date_display = f"{parts[2]}-{parts[1]}-{parts[0]}"
                result_dates.append({
                    'date_iso': date_str,
                    'date_display': date_display,
                    'sections': entity_data,
                })

        if not result_dates:
            return jsonify({'error': 'No NLPL data found'}), 404

        return jsonify({
            'entity_name': 'NLPL',
            'entity_level': 'NLPL',
            'dates': result_dates,
            'total_dates': len(result_dates),
        })
    except Exception as e:
        err = user_error(e, context='instant-nlpl-dates')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/entity-hierarchy', methods=['GET'])
def entity_hierarchy():
    """Return the entity hierarchy tree from the Demand Master."""
    try:
        if not db_manager:
            return jsonify({'error': 'Database not available'}), 500

        con = db_manager.get_connection()

        # Query hierarchy from Demand Master
        # Use Area with fallback to District for legacy data files
        try:
            # Detect available columns
            cols_info = con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'Demand_Master'").fetchall()
            col_names = {c[0] for c in cols_info}

            has_division = 'Division' in col_names
            area_col_name = 'Area' if 'Area' in col_names else 'District'

            select_parts = ["TRIM(Region) as region"]
            where_parts = ["Region IS NOT NULL", "BranchName IS NOT NULL"]

            if has_division:
                select_parts.append("TRIM(Division) as division")
                where_parts.append("Division IS NOT NULL")
            else:
                select_parts.append("NULL as division")

            select_parts.append(f"TRIM({area_col_name}) as area")
            where_parts.append(f"{area_col_name} IS NOT NULL")

            select_parts.append("TRIM(BranchName) as branch")

            sql = f"""
                SELECT DISTINCT {', '.join(select_parts)}
                FROM Demand_Master
                WHERE {' AND '.join(where_parts)}
                ORDER BY region, division, area, branch
            """
            rows = con.execute(sql).fetchall()
        except (duckdb.CatalogException, duckdb.Error):
            return jsonify({'error': 'Demand Master not loaded'}), 404

        regions = sorted(set(r[0] for r in rows if r[0]))
        region_to_divisions = {}
        division_to_areas = {}
        area_to_branches = {}

        for region, division, area, branch in rows:
            if not region:
                continue
            if division:
                region_to_divisions.setdefault(region, set()).add(division)
                if area:
                    division_to_areas.setdefault(division, set()).add(area)
            if area and branch:
                area_to_branches.setdefault(area, set()).add(branch)

        # Convert sets to sorted lists
        region_to_divisions = {k: sorted(v) for k, v in region_to_divisions.items()}
        division_to_areas = {k: sorted(v) for k, v in division_to_areas.items()}
        area_to_branches = {k: sorted(v) for k, v in area_to_branches.items()}

        return jsonify({
            'regions': regions,
            'region_to_divisions': region_to_divisions,
            'division_to_areas': division_to_areas,
            'area_to_branches': area_to_branches,
        })
    except Exception as e:
        err = user_error(e, context='instant-entity-hierarchy')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/generate-from-cache', methods=['POST'])
def generate_from_cache():
    """Re-generate an instant report from previously cached PAR + Collection data."""
    try:
        from datetime import datetime

        t0 = time.perf_counter()

        body = request.get_json(silent=True) or {}
        date_str = body.get('date')
        if not date_str:
            return jsonify({'error': 'Missing "date" field (YYYY-MM-DD)'}), 400

        # Validate date format
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        # Load cached data
        df_par, df_collection, cached_meta = load_date_cache(date_str)

        # Parse Trxdate (handles Excel serial numbers + string formats)
        from services.eod_processor import parse_trxdate
        df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])

        if not db_manager:
            return jsonify({'error': 'Database not available'}), 500

        con = db_manager.get_connection()

        # Register cached DataFrames in DuckDB
        con.register('daily_par', df_par)
        con.register('daily_collection', df_collection)

        # ── Resolve monthly backend dir ──────────────────────
        backend_dir = _get_monthly_backend_dir(date_str)

        # ── Demand Master: from monthly folder only ──────────
        demand_source = _load_demand_fallback(backend_dir, con)
        if not demand_source:
            return jsonify({'error': 'No Demand Sheet uploaded for this month. Upload via Report History.'}), 400

        # ── Last Month PAR: from monthly folder only ─────────
        has_last_month = _load_last_month_fallback(backend_dir, con)

        # ── Run shared merge pipeline ────────────────────────
        t_query = time.perf_counter()
        df_result = _run_merge_pipeline(
            con, df_par, df_collection, target_date, demand_source, has_last_month
        )
        logging.info(f"Instant (cache): SQL query {time.perf_counter() - t_query:.2f}s, shape={df_result.shape}")

        # ── Compute pivot summaries ──────────────────────────
        report_data = compute_instant_report(df_result, target_date=target_date)

        report_data['metadata']['source'] = 'cache'
        report_data['metadata']['cache_date'] = date_str

        # Save report.json alongside parquets for fast cross-date retrieval
        save_date_report(date_str, report_data)

        elapsed = time.perf_counter() - t0
        report_data['metadata']['processing_time'] = round(elapsed, 3)
        logging.info(f"Instant Report (from cache) completed in {elapsed:.2f}s")

        return jsonify(report_data)

    except FileNotFoundError as e:
        logging.warning(f"Cache not found: {e}")
        return jsonify({'error': 'Cached data not found for the requested date.', 'suggestion': 'Run the report again to regenerate the cache.'}), 404
    except ValueError as e:
        logging.warning(f"Invalid value: {e}")
        return jsonify({'error': 'Invalid input provided.', 'suggestion': 'Check the date format and try again.'}), 400
    except Exception as e:
        err = user_error(e, context='instant-generate-from-cache')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/cross-date-entity', methods=['POST'])
def cross_date_entity():
    """Get a specific entity's performance metrics across all cached dates."""
    try:
        body = request.get_json(silent=True) or {}
        entity_name = body.get('entity_name', '').strip()
        entity_level = body.get('entity_level', 'Region')

        if not entity_name:
            return jsonify({'error': 'Missing entity_name'}), 400

        if entity_level not in ('Region', 'Division', 'Area', 'Branch', 'NLPL'):
            return jsonify({'error': 'entity_level must be Region, Division, Area, Branch, or NLPL'}), 400

        # NLPL means grand total, extract from Region-level tables
        if entity_level == 'NLPL':
            entity_level = 'Region'

        # Get all cached dates
        cached_dates = list_cached_dates()
        if not cached_dates:
            return jsonify({'error': 'No cached dates available'}), 404

        date_list = [d['date_iso'] for d in cached_dates]

        # Load reports, regenerating any that are stale (demand/last_month changed)
        reports = _ensure_reports_fresh(date_list)

        if not reports:
            return jsonify({'error': 'No cached reports found. Generate reports first to build cache.'}), 404

        # Extract entity data from each date's report
        result_dates = []
        has_any_data = False
        for date_str in sorted(reports.keys()):
            entity_data = extract_entity_data(reports[date_str], entity_name, entity_level)
            if entity_data:
                has_any_data = True
            parts = date_str.split('-')
            date_display = f"{parts[2]}-{parts[1]}-{parts[0]}"
            result_dates.append({
                'date_iso': date_str,
                'date_display': date_display,
                'sections': entity_data if entity_data else {},
            })

        if not has_any_data:
            return jsonify({
                'error': f'Entity "{entity_name}" ({entity_level}) not found in any cached report'
            }), 404

        return jsonify({
            'entity_name': entity_name,
            'entity_level': entity_level,
            'dates': result_dates,
            'total_dates': len(result_dates),
        })

    except Exception as e:
        err = user_error(e, context='instant-cross-date')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/delete-cache/<date_str>', methods=['DELETE'])
def delete_cache(date_str):
    """Delete a specific date's cached data."""
    import shutil
    from datetime import datetime as dt
    try:
        # Validate date format
        dt.strptime(date_str, '%Y-%m-%d')
        cache_folder = config.INSTANT_HISTORY_DIR / date_str
        if not cache_folder.exists():
            return jsonify({'error': f'No cache found for {date_str}'}), 404
        shutil.rmtree(cache_folder)
        logging.info(f"Instant cache: Deleted {date_str}")
        return jsonify({'success': True, 'deleted': date_str})
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        err = user_error(e, context='instant-delete-cache')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/delete-all-cache', methods=['DELETE'])
def delete_all_cache():
    """Delete ALL cached date folders."""
    import shutil
    try:
        history_dir = config.INSTANT_HISTORY_DIR
        if not history_dir.exists():
            return jsonify({'success': True, 'deleted': 0})

        deleted = []
        errors = []
        for folder in sorted(history_dir.iterdir()):
            if folder.is_dir():
                try:
                    shutil.rmtree(folder)
                    deleted.append(folder.name)
                except Exception as e:
                    errors.append({'date': folder.name, 'error': str(e)})

        logging.info(f"Instant cache: Deleted all ({len(deleted)} folders)")
        return jsonify({
            'success': True,
            'deleted': len(deleted),
            'deleted_dates': deleted,
            'errors': errors
        })
    except Exception as e:
        err = user_error(e, context='instant-delete-all-cache')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/bulk-process', methods=['POST'])
def bulk_process():
    """
    Accept multiple PAR files + 1 Collection file, run the EOD pipeline
    for each date, cache results, and return a summary.

    Form data:
        par_files    – one or more PAR Excel files (multipart)
        collection   – single Collection Excel file (multipart)
    """
    if not try_acquire_processing():
        return jsonify({
            'error': 'Server is busy processing another request. Please try again in a moment.'
        }), 503
    from datetime import datetime
    import re

    total_t0 = time.perf_counter()
    processed = []
    failed = []

    # ── Validate inputs ─────────────────────────────────────────────
    par_files = request.files.getlist('par_files')
    if not par_files:
        release_processing()
        return jsonify({'error': 'No PAR files provided (field: par_files)'}), 400

    use_existing = request.form.get('use_existing_collection') == 'true'
    has_collection_file = 'collection' in request.files and request.files['collection'].filename

    if not has_collection_file and not use_existing:
        release_processing()
        return jsonify({'error': 'No Collection file provided (field: collection)'}), 400

    coll_hash = ''
    coll_original_filename = ''

    # ── Read Collection ONCE ────────────────────────────────────────
    coll_tmp = None  # Track for cleanup
    if has_collection_file:
        collection_file = request.files['collection']
        coll_original_filename = collection_file.filename or ''
        logging.info(f"Bulk-process: received {len(par_files)} PAR file(s) + 1 Collection (uploaded)")
        try:
            coll_tmp = save_upload_to_temp(collection_file, prefix="bulk_coll_")
            coll_hash = compute_file_hash(coll_tmp)

            COLLECTION_COLS = ['AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal']
            try:
                df_collection = processor.smart_read_excel(
                    coll_tmp, usecols=COLLECTION_COLS
                )
            except (ValueError, KeyError):
                df_collection = processor.smart_read_excel(coll_tmp)

            from services.eod_processor import parse_trxdate
            df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
            logging.info(f"Bulk-process: Collection read OK – {len(df_collection)} rows")
        except Exception as e:
            logging.error(f"Bulk-process: Failed to read Collection file: {e}")
            if coll_tmp is not None:
                coll_tmp.unlink(missing_ok=True)
            release_processing()
            return jsonify({'error': f'Failed to read Collection file: {e}'}), 400
    else:
        # Use existing collection from latest cached date
        logging.info(f"Bulk-process: received {len(par_files)} PAR file(s) + existing collection")
        try:
            cached = list_cached_dates()
            if not cached:
                release_processing()
                return jsonify({'error': 'No existing cached collection found'}), 400
            latest_date = cached[-1]['date_iso']
            _, df_collection, cached_meta = load_date_cache(latest_date)
            coll_original_filename = cached_meta.get('collection_original_filename', f'cached:{latest_date}')
            coll_hash = cached_meta.get('collection_file_hash', 'cached')

            from services.eod_processor import parse_trxdate
            df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
            logging.info(f"Bulk-process: Existing collection loaded from {latest_date} – {len(df_collection)} rows")
        except Exception as e:
            logging.error(f"Bulk-process: Failed to load existing collection: {e}")
            release_processing()
            return jsonify({'error': f'Failed to load existing collection: {e}'}), 400

    # ── DB connection ────────────────────────────────────────────────
    if not db_manager:
        release_processing()
        return jsonify({'error': 'Database not available'}), 500

    con = db_manager.get_connection()

    # ── Parse dates from PAR filenames & sort ───────────────────────
    par_entries = []
    for pf in par_files:
        filename = pf.filename or ''
        date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', filename)
        if not date_match:
            failed.append({
                'date': None,
                'filename': filename,
                'error': 'No date found in filename (expected DD-MM-YYYY pattern)',
            })
            continue
        try:
            target_date = datetime(
                int(date_match.group(3)),
                int(date_match.group(2)),
                int(date_match.group(1)),
            )
        except ValueError as ve:
            failed.append({
                'date': date_match.group(0),
                'filename': filename,
                'error': f'Invalid date in filename: {ve}',
            })
            continue

        # Save to temp file so we can sort before processing
        par_tmp = save_upload_to_temp(pf, prefix=f"bulk_par_{len(par_entries)}_")
        par_entries.append({
            'filename': filename,
            'target_date': target_date,
            'path': par_tmp,
        })

    # Sort by date ascending
    par_entries.sort(key=lambda e: e['target_date'])

    # ── Process each PAR date ───────────────────────────────────────
    PAR_COLS = ['AccountID', 'Days Group', 'Days group', 'DaysGroup',
                'DPD Group', 'DPD Days', 'DPDDays']

    for entry in par_entries:
        t_start = time.perf_counter()
        target_date = entry['target_date']
        date_display = target_date.strftime('%d-%m-%Y')
        date_iso = target_date.strftime('%Y-%m-%d')

        try:
            # Resolve monthly backend dir for this date
            backend_dir = _get_monthly_backend_dir(date_display)

            # Read PAR from temp file
            try:
                df_par = processor.smart_read_excel(
                    entry['path'],
                    usecols=lambda x: x in PAR_COLS,
                )
            except (ValueError, KeyError):
                df_par = processor.smart_read_excel(entry['path'])

            # PAR file hash from disk
            par_hash = compute_file_hash(entry['path'])

            # Register DataFrames
            con.register('daily_par', df_par)
            con.register('daily_collection', df_collection)

            # Demand Master: from monthly folder only
            demand_source = _load_demand_fallback(backend_dir, con)
            if not demand_source:
                raise Exception('No Demand Sheet uploaded for this month.')

            # Last Month PAR: from monthly folder only
            has_last_month = _load_last_month_fallback(backend_dir, con)

            # Merge pipeline
            df_result = _run_merge_pipeline(
                con, df_par, df_collection, target_date, demand_source, has_last_month
            )

            # Compute report
            report_data = compute_instant_report(df_result, target_date=target_date)

            # Save cache
            metadata_dict = {
                'par_original_filename': entry['filename'],
                'collection_original_filename': coll_original_filename,
                'par_rows': len(df_par),
                'collection_rows': len(df_collection),
                'par_file_hash': par_hash,
                'collection_file_hash': coll_hash,
            }
            save_date_cache(date_iso, df_par, df_collection, metadata_dict, report_data=report_data)
            invalidate_merged_df_cache(date_iso)

            elapsed = time.perf_counter() - t_start
            processed.append({
                'date': date_display,
                'date_iso': date_iso,
                'status': 'success',
                'processing_time': round(elapsed, 2),
            })
            logging.info(f"Bulk-process: {date_display} OK in {elapsed:.2f}s")

        except Exception as exc:
            elapsed = time.perf_counter() - t_start
            failed.append({
                'date': date_display,
                'filename': entry['filename'],
                'error': str(exc),
            })
            logging.error(f"Bulk-process: {date_display} FAILED – {exc}")
        finally:
            gc_checkpoint(f"instant-bulk-iteration-{date_display}")

    # Clean up all temp files
    for entry in par_entries:
        try:
            entry['path'].unlink(missing_ok=True)
        except Exception:
            pass
    if coll_tmp is not None:
        coll_tmp.unlink(missing_ok=True)

    total_time = round(time.perf_counter() - total_t0, 2)
    logging.info(
        f"Bulk-process complete: {len(processed)} succeeded, "
        f"{len(failed)} failed, total {total_time}s"
    )

    gc_checkpoint("instant-bulk-request-complete")
    release_processing()

    return jsonify({
        'success': len(failed) == 0,
        'total_dates': len(processed) + len(failed),
        'processed': processed,
        'failed': failed,
        'total_time': total_time,
    })


@instant_bp.route('/process', methods=['POST'])
def process():
    """
    Accept PAR + Collection files, run EOD pipeline (DuckDB path),
    compute pivot summaries, return JSON.
    """
    if not try_acquire_processing():
        return jsonify({
            'error': 'Server is busy processing another request. Please try again in a moment.'
        }), 503
    try:
        t0 = time.perf_counter()

        if 'par' not in request.files or 'collection' not in request.files:
            return jsonify({'error': 'Missing PAR or Collection files'}), 400

        par = request.files['par']
        collection = request.files['collection']

        # Get target date: from form data, or auto-detect from collection filename
        from datetime import datetime
        import re
        target_date_str = request.form.get('targetDate')
        target_date = None
        if target_date_str:
            try:
                target_date = datetime.strptime(target_date_str, '%d-%m-%Y')
            except ValueError:
                pass

        # Auto-detect from collection filename if not explicitly provided
        if not target_date:
            coll_filename = collection.filename or ''
            date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', coll_filename)
            if date_match:
                try:
                    target_date = datetime(
                        int(date_match.group(3)),
                        int(date_match.group(2)),
                        int(date_match.group(1))
                    )
                    logging.info(f"Instant: Auto-detected target date from collection filename: {target_date.strftime('%d-%m-%Y')}")
                except ValueError:
                    pass

        # Fallback: detect from PAR filename
        if not target_date:
            par_filename = par.filename or ''
            date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', par_filename)
            if date_match:
                try:
                    target_date = datetime(
                        int(date_match.group(3)),
                        int(date_match.group(2)),
                        int(date_match.group(1))
                    )
                    logging.info(f"Instant: Auto-detected target date from PAR filename: {target_date.strftime('%d-%m-%Y')}")
                except ValueError:
                    pass

        if target_date:
            logging.info(f"Instant: Using target date: {target_date.strftime('%d-%m-%Y')}")
        else:
            logging.warning("Instant: No target date detected from filenames or form data")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            par_path = temp_path / "par.xlsx"
            collection_path = temp_path / "collection.xlsx"
            output_path = temp_path / "output.xlsx"

            par.save(par_path)
            collection.save(collection_path)

            # Resolve monthly backend dir
            backend_date_str = target_date.strftime('%d-%m-%Y') if target_date else (target_date_str or '')
            backend_dir = _get_monthly_backend_dir(backend_date_str)

            # Check monthly demand availability (parquet cache or Excel)
            demand_cache = backend_dir / 'demand_cache.parquet'
            demand_files = list(backend_dir.glob("Demand_Sheet_Master_*"))
            if not demand_cache.exists() and not demand_files:
                return jsonify({'error': 'No Demand Sheet uploaded for this month. Upload via Report History.'}), 400

            if not db_manager:
                return jsonify({'error': 'Database not available'}), 500

            # Run the EOD pipeline to get the merged DataFrame
            # We call process_files_duckdb directly and intercept df_result
            logging.info("Instant Report: Starting pipeline...")

            con = db_manager.get_connection()

            # ── Read Collection ──────────────────────────────────
            t_read = time.perf_counter()
            COLLECTION_COLS = ['AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal']

            coll_hash = processor.get_file_hash(collection_path)
            DB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            coll_cache = DB_CACHE_DIR / f"daily_collection_cache_{coll_hash}.parquet"

            if coll_cache.exists():
                df_collection = pd.read_parquet(coll_cache)  # Full read: feeds into EOD processing pipeline
            else:
                df_collection = processor.smart_read_excel(collection_path, usecols=COLLECTION_COLS)
                df_collection.to_parquet(coll_cache, index=False)

            from services.eod_processor import parse_trxdate
            df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
            con.register('daily_collection', df_collection)
            logging.info(f"Instant: Collection read {time.perf_counter() - t_read:.2f}s")

            # ── Read PAR ─────────────────────────────────────────
            t_read = time.perf_counter()
            PAR_COLS = ['AccountID', 'Days Group', 'Days group', 'DaysGroup', 'DPD Group', 'DPD Days', 'DPDDays']

            par_hash = processor.get_file_hash(par_path)
            par_cache = DB_CACHE_DIR / f"daily_par_cache_{par_hash}.parquet"

            if par_cache.exists():
                df_par = pd.read_parquet(par_cache)  # Full read: feeds into EOD processing pipeline
            else:
                try:
                    df_par = processor.smart_read_excel(par_path, usecols=lambda x: x in PAR_COLS)
                except (ValueError, KeyError):
                    df_par = processor.smart_read_excel(par_path)
                df_par.to_parquet(par_cache, index=False)

            con.register('daily_par', df_par)
            logging.info(f"Instant: PAR read {time.perf_counter() - t_read:.2f}s")

            # ── Demand Master: from monthly folder only ───────────
            demand_source = _load_demand_fallback(backend_dir, con)
            if not demand_source:
                return jsonify({'error': 'No Demand Sheet uploaded for this month.'}), 400

            # ── Last Month PAR: from monthly folder only ─────────
            has_last_month = _load_last_month_fallback(backend_dir, con)

            # ── Build and execute SQL (shared helper) ─────────────
            t_query = time.perf_counter()
            df_result = _run_merge_pipeline(
                con, df_par, df_collection, target_date, demand_source, has_last_month
            )
            logging.info(f"Instant: SQL query {time.perf_counter() - t_query:.2f}s, shape={df_result.shape}")

            # ── Compute pivot summaries ──────────────────────────
            t_pivot = time.perf_counter()
            report_data = compute_instant_report(df_result, target_date=target_date)
            logging.info(f"Instant: Pivot computation {time.perf_counter() - t_pivot:.2f}s")

            # ── Save date cache on success ───────────────────────
            if target_date is not None:
                try:
                    date_str = target_date.strftime('%Y-%m-%d')
                    cache_metadata = {
                        'par_original_filename': par.filename or '',
                        'collection_original_filename': collection.filename or '',
                        'par_rows': len(df_par),
                        'collection_rows': len(df_collection),
                        'par_file_hash': par_hash,
                        'collection_file_hash': coll_hash,
                    }
                    saved = save_date_cache(date_str, df_par, df_collection, cache_metadata, report_data=report_data)
                    if saved:
                        report_data['metadata']['date_cached'] = date_str
                    invalidate_merged_df_cache(date_str)
                except Exception as cache_err:
                    logging.warning(f"Instant: Failed to save date cache: {cache_err}")

            elapsed = time.perf_counter() - t0
            report_data['metadata']['processing_time'] = round(elapsed, 3)
            logging.info(f"Instant Report completed in {elapsed:.2f}s")

            return jsonify(report_data)

    except Exception as e:
        err = user_error(e, context='instant-process')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500
    finally:
        gc_checkpoint("instant-request-complete")
        release_processing()


# ---------------------------------------------------------------------------
# Monthly Backend Data Management
# ---------------------------------------------------------------------------

@instant_bp.route('/monthly-backend-status', methods=['GET'])
def monthly_backend_status():
    """List all months and their backend files."""
    try:
        BACKEND_MONTHLY_DIR.mkdir(exist_ok=True)
        months = []
        for folder in sorted(BACKEND_MONTHLY_DIR.iterdir(), reverse=True):
            if folder.is_dir() and len(folder.name) == 7:  # YYYY-MM format
                month_info = {
                    'month': folder.name,
                    'demand_sheet': None,
                    'last_month_par': None
                }
                demand_cache = folder / 'demand_cache.parquet'
                lm_cache = folder / 'last_month_par_cache.parquet'
                # Read metadata for original filenames
                import json
                meta = {}
                meta_path = folder / 'metadata.json'
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                    except Exception:
                        meta = {}
                # Check parquet caches (primary) and Excel files (legacy)
                if demand_cache.exists():
                    month_info['demand_sheet'] = {
                        'name': meta.get('demand_original', 'Demand Sheet'),
                        'size': demand_cache.stat().st_size,
                        'modified': demand_cache.stat().st_mtime,
                        'cached': True
                    }
                if lm_cache.exists():
                    month_info['last_month_par'] = {
                        'name': meta.get('last_month_original', 'Last Month PAR'),
                        'size': lm_cache.stat().st_size,
                        'modified': lm_cache.stat().st_mtime,
                        'cached': True
                    }
                # Also check for legacy Excel files (from older uploads)
                for f in folder.iterdir():
                    if f.is_file():
                        if f.name.startswith('Demand_Sheet_Master') and not month_info.get('demand_sheet'):
                            month_info['demand_sheet'] = {
                                'name': f.name,
                                'size': f.stat().st_size,
                                'modified': f.stat().st_mtime,
                                'cached': False
                            }
                        elif f.name.startswith('Last_Month') and not month_info.get('last_month_par'):
                            month_info['last_month_par'] = {
                                'name': f.name,
                                'size': f.stat().st_size,
                                'modified': f.stat().st_mtime,
                                'cached': False
                            }
                months.append(month_info)
        return jsonify({'success': True, 'months': months})
    except Exception as e:
        err = user_error(e, context='instant-monthly-status')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/monthly-backend-upload', methods=['POST'])
def monthly_backend_upload():
    """Upload a demand sheet or last month PAR for a specific month."""
    try:
        month = request.form.get('month')  # YYYY-MM
        file_type = request.form.get('type')  # demand or last_month
        file = request.files.get('file')

        if not month or not file_type or not file:
            return jsonify({'error': 'Missing month, type, or file'}), 400

        import re
        if not re.match(r'^\d{4}-\d{2}$', month):
            return jsonify({'error': 'Invalid month format. Use YYYY-MM'}), 400

        if file_type not in ('demand', 'last_month'):
            return jsonify({'error': 'Type must be demand or last_month'}), 400

        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'error': 'Only Excel files (.xlsx, .xls) accepted'}), 400

        month_dir = BACKEND_MONTHLY_DIR / month
        month_dir.mkdir(parents=True, exist_ok=True)

        original_name = file.filename
        cache_name = 'demand_cache.parquet' if file_type == 'demand' else 'last_month_par_cache.parquet'

        # Remove old Excel files of the same type (cleanup from older versions)
        prefix = 'Demand_Sheet_Master' if file_type == 'demand' else 'Last_Month'
        for existing in month_dir.glob(f"{prefix}*"):
            existing.unlink()
        # Remove old parquet cache
        old_cache = month_dir / cache_name
        if old_cache.exists():
            old_cache.unlink()

        # Save to temp, read Excel from disk, convert to parquet
        upload_tmp = save_upload_to_temp(file, prefix=f"monthly_{file_type}_")
        try:
            t0 = time.time()

            if file_type == 'demand':
                try:
                    df = pd.read_excel(upload_tmp, sheet_name=0, engine='calamine')
                except Exception:
                    df = pd.read_excel(upload_tmp, sheet_name=0)
                for col in df.columns:
                    if df[col].dtype == 'object':
                        df[col] = df[col].astype(str).replace('nan', '').replace('None', '')
            else:
                try:
                    df = pd.read_excel(upload_tmp, sheet_name=0,
                                       usecols=['AccountID', 'DPD Days', 'LoanStatus'], engine='calamine')
                except ValueError:
                    try:
                        df = pd.read_excel(upload_tmp, sheet_name=0, engine='calamine')
                    except Exception:
                        df = pd.read_excel(upload_tmp, sheet_name=0)
                except Exception:
                    try:
                        df = pd.read_excel(upload_tmp, sheet_name=0,
                                           usecols=['AccountID', 'DPD Days', 'LoanStatus'])
                    except ValueError:
                        df = pd.read_excel(upload_tmp, sheet_name=0)
                if 'AccountID' in df.columns:
                    df['AccountID'] = df['AccountID'].astype(str).str.strip()
                    df = df[~df['AccountID'].str.contains('\ufffd|nan|None', na=True, case=False)]

            cache_path = month_dir / cache_name
            df.to_parquet(cache_path, index=False)
            elapsed = time.time() - t0
            cache_rows = len(df)

            # Save original filename in metadata.json
            import json
            meta_path = month_dir / 'metadata.json'
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except Exception:
                    meta = {}
            meta_key = 'demand_original' if file_type == 'demand' else 'last_month_original'
            meta[meta_key] = original_name
            meta_path.write_text(json.dumps(meta))

            logging.info(f"Monthly backend: {original_name} → {cache_name} ({cache_rows} rows, {elapsed:.1f}s)")
            return jsonify({
                'success': True,
                'month': month,
                'type': file_type,
                'filename': original_name,
                'original_filename': original_name,
                'size': cache_path.stat().st_size,
                'cached': True,
                'cache_rows': cache_rows
            })
        finally:
            upload_tmp.unlink(missing_ok=True)
    except Exception as e:
        err = user_error(e, context='instant-monthly-upload')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/monthly-backend-delete', methods=['DELETE'])
def monthly_backend_delete():
    """Delete a specific file from a month folder."""
    try:
        month = request.args.get('month')  # YYYY-MM
        file_type = request.args.get('type')  # demand or last_month

        if not month or not file_type:
            return jsonify({'error': 'Missing month or type'}), 400

        month_dir = BACKEND_MONTHLY_DIR / month
        if not month_dir.exists():
            return jsonify({'error': f'No data found for {month}'}), 404

        prefix = 'Demand_Sheet_Master' if file_type == 'demand' else 'Last_Month'
        cache_name = 'demand_cache.parquet' if file_type == 'demand' else 'last_month_par_cache.parquet'
        deleted = False
        # Delete legacy Excel files
        for f in month_dir.glob(f"{prefix}*"):
            f.unlink()
            deleted = True
            logging.info(f"Monthly backend: Deleted {f.name} from {month}")
        # Delete parquet cache
        cache_file = month_dir / cache_name
        if cache_file.exists():
            cache_file.unlink()
            deleted = True
            logging.info(f"Monthly backend: Deleted {cache_name} from {month}")
        # Clean up metadata
        import json
        meta_path = month_dir / 'metadata.json'
        if meta_path.exists() and deleted:
            try:
                meta = json.loads(meta_path.read_text())
                meta_key = 'demand_original' if file_type == 'demand' else 'last_month_original'
                meta.pop(meta_key, None)
                if meta:
                    meta_path.write_text(json.dumps(meta))
                else:
                    meta_path.unlink()
            except Exception:
                pass

        # Remove empty month folder
        if not any(month_dir.iterdir()):
            month_dir.rmdir()

        if deleted:
            return jsonify({'success': True, 'month': month, 'type': file_type})
        else:
            return jsonify({'error': f'No {file_type} file found for {month}'}), 404
    except Exception as e:
        err = user_error(e, context='instant-monthly-delete')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


@instant_bp.route('/bulk-process-single', methods=['POST'])
def bulk_process_single():
    """
    Process a single PAR file for bulk Kanban workflow.
    Accepts: par_file (single file), collection (optional), use_existing_collection (flag)
    The collection/demand/last_month are loaded fresh each call.
    """
    try:
        from datetime import datetime
        import re

        t0 = time.perf_counter()

        par_file = request.files.get('par_file')
        if not par_file:
            return jsonify({'error': 'No PAR file provided'}), 400

        collection_file = request.files.get('collection')
        use_existing = request.form.get('use_existing_collection') == 'true'

        # Extract date from filename
        date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', par_file.filename)
        if not date_match:
            return jsonify({'error': 'No date found in filename: ' + par_file.filename, 'filename': par_file.filename}), 400

        dd, mm, yyyy = date_match.group(1), date_match.group(2), date_match.group(3)
        date_display = f"{dd}-{mm}-{yyyy}"
        date_iso = f"{yyyy}-{mm}-{dd}"

        try:
            target_date = datetime(int(yyyy), int(mm), int(dd))
        except ValueError as ve:
            return jsonify({'error': f'Invalid date in filename: {ve}', 'filename': par_file.filename}), 400

        if not db_manager:
            return jsonify({'error': 'Database not available'}), 500

        con = db_manager.get_connection()

        # Read PAR from temp file
        PAR_COLS = ['AccountID', 'Days Group', 'Days group', 'DaysGroup',
                    'DPD Group', 'DPD Days', 'DPDDays']
        par_tmp = save_upload_to_temp(par_file, prefix="single_par_")
        try:
            try:
                df_par = processor.smart_read_excel(
                    par_tmp,
                    usecols=lambda x: x in PAR_COLS,
                )
            except (ValueError, KeyError):
                df_par = processor.smart_read_excel(par_tmp)

            par_hash = compute_file_hash(par_tmp)
        finally:
            par_tmp.unlink(missing_ok=True)

        con.register('daily_par', df_par)

        # Read Collection
        df_collection = None
        coll_original_filename = None
        coll_hash = ''

        if collection_file and collection_file.filename:
            coll_original_filename = collection_file.filename
            COLLECTION_COLS = ['AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal']
            single_coll_tmp = save_upload_to_temp(collection_file, prefix="single_coll_")
            try:
                coll_hash = compute_file_hash(single_coll_tmp)

                try:
                    df_collection = processor.smart_read_excel(
                        single_coll_tmp, usecols=COLLECTION_COLS
                    )
                except (ValueError, KeyError):
                    df_collection = processor.smart_read_excel(single_coll_tmp)
            finally:
                single_coll_tmp.unlink(missing_ok=True)

            from services.eod_processor import parse_trxdate
            df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
        elif use_existing:
            cached = list_cached_dates()
            if cached:
                latest_date = cached[-1]['date_iso']
                try:
                    _, df_collection, cached_meta = load_date_cache(latest_date)
                    coll_original_filename = cached_meta.get('collection_original_filename', f'cached:{latest_date}')
                    coll_hash = cached_meta.get('collection_file_hash', 'cached')
                    from services.eod_processor import parse_trxdate
                    df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
                except Exception:
                    df_collection = None

            if df_collection is None:
                return jsonify({'error': 'No existing collection found in cache'}), 400
        else:
            return jsonify({'error': 'No collection file provided'}), 400

        con.register('daily_collection', df_collection)

        # Resolve monthly backend dir
        backend_dir = _get_monthly_backend_dir(date_display)

        # Demand Master: from monthly folder only
        demand_source = _load_demand_fallback(backend_dir, con)
        if not demand_source:
            return jsonify({'error': 'No Demand Sheet uploaded for ' + date_display + '. Upload via Report History.'}), 400

        # Last Month PAR: from monthly folder only
        has_last_month = _load_last_month_fallback(backend_dir, con)

        # Run pipeline
        df_result = _run_merge_pipeline(
            con, df_par, df_collection, target_date, demand_source, has_last_month
        )

        # Compute report
        report_data = compute_instant_report(df_result, target_date=target_date)

        # Save cache
        metadata_dict = {
            'par_original_filename': par_file.filename,
            'collection_original_filename': coll_original_filename,
            'par_rows': len(df_par),
            'collection_rows': len(df_collection) if df_collection is not None else 0,
            'par_file_hash': par_hash,
            'collection_file_hash': coll_hash,
        }
        save_date_cache(date_iso, df_par, df_collection, metadata_dict, report_data=report_data)
        invalidate_merged_df_cache(date_iso)

        elapsed = round(time.perf_counter() - t0, 2)

        return jsonify({
            'success': True,
            'date': date_display,
            'date_iso': date_iso,
            'filename': par_file.filename,
            'processing_time': elapsed
        })

    except Exception as e:
        fname = 'unknown'
        try:
            pf = request.files.get('par_file')
            if pf:
                fname = pf.filename
        except Exception:
            pass
        err = user_error(e, context='instant-bulk-process')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
            'filename': fname
        }), 500


# ---------------------------------------------------------------------------
# Catch-all: serve static files (CSS, JS, etc.)
# MUST be defined AFTER all API routes to prevent it from shadowing them.
# A POST/DELETE/etc. to an API path like /bulk-process would otherwise match
# this <path:filename> pattern first, and because it only allows GET, Flask
# would return a 405 Method Not Allowed HTML page instead of reaching the
# correct API route handler.
# ---------------------------------------------------------------------------
@instant_bp.route('/<path:filename>', methods=['GET'])
def serve_static(filename):
    """Serve Instant Report static files (CSS, JS)."""
    file_path = STATIC_INSTANT_DIR / filename
    if not file_path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(str(STATIC_INSTANT_DIR), filename)
