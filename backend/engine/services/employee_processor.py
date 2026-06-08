"""
Employee Performance Analytics Engine
======================================
Core analytics engine for the Employee Performance module.
Provides employee-level pivot summaries, leaderboards, and
detailed breakdowns mirroring the VBA section structure.

Uses the same merge pipeline pattern as blueprints/instant.py
and the same DuckDB GROUP BY analytics as services/instant_processor.py.
"""

import re
import math
import logging
import threading
import time
from datetime import datetime

import pandas as pd
import duckdb

import config
from services.column_matcher import find_column
from services.instant_cache import list_cached_dates, load_date_cache
from services import eod_processor as processor
from services.hardware_profile import MERGED_DF_MAX_ENTRIES as _HP_MERGED_DF_MAX_ENTRIES

# Graceful import of memory_manager (may not be available in all environments)
try:
    from services.memory_manager import is_memory_pressure
except ImportError:
    def is_memory_pressure():
        return False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merged DataFrame Cache (CPU-01)
# ---------------------------------------------------------------------------
_merged_df_cache = {}                # {date_str: (df, timestamp)}
_merged_df_lock = threading.Lock()
_MERGED_DF_TTL = 300                 # 5 minutes
# Auto-tuned from hardware profile (low=3, medium=5, high=10)
_MERGED_DF_MAX_ENTRIES = _HP_MERGED_DF_MAX_ENTRIES


# ---------------------------------------------------------------------------
# Trend Point Cache (CPU-02)
# ---------------------------------------------------------------------------
_trend_cache = {}                    # {(emp_id, date_str): (result_dict, timestamp)}
_trend_cache_lock = threading.Lock()
_TREND_TTL = 300                     # Same TTL as merged DF


# ---------------------------------------------------------------------------
# Column Detection
# ---------------------------------------------------------------------------

def detect_employee_columns(df):
    """
    Fuzzy-match employee-related columns in the DataFrame.

    Returns:
        dict with keys 'emp_id' and 'emp_name', each either the resolved
        column name or None if not found.
    """
    emp_id = find_column(
        df, 'Emp ID', 'EmpID', 'Emp Id', 'Employee ID',
        'emp_id', 'EMPID', 'Emp_ID', 'EmployeeID',
    )
    emp_name = find_column(
        df, 'Emp Name', 'EmpName', 'Employee Name',
        'emp_name', 'Emp_Name', 'EmployeeName',
    )
    return {'emp_id': emp_id, 'emp_name': emp_name}


# ---------------------------------------------------------------------------
# Shared Column Resolution Helper
# ---------------------------------------------------------------------------

def _resolve_columns(df):
    """
    Resolve all analytics column names from the merged DataFrame.
    Returns a dict of logical_name -> actual_column_name.
    """
    col_region = find_column(df, 'Region', 'RegionName', 'Region Name') or 'Region'
    col_area = find_column(df, 'Area', 'AreaName', 'Area Name', 'District', 'DistrictName', 'District Name') or 'Area'
    col_branch = find_column(df, 'BranchName', 'Branch Name', 'Branch', 'Branchname') or 'BranchName'
    col_demand_count = find_column(df, 'No of Regular Demand', 'No. of Regular Demand', 'NoOfRegularDemand') or 'No of Regular Demand'
    col_cumulative = find_column(df, 'No of Cumulative', 'No. of Cumulative', 'NoOfCumulative') or 'No of Cumulative'
    col_dpd_group = find_column(df, 'DPD Group', 'DPDGroup') or 'DPD Group'
    col_dpd_last_month = find_column(df, 'DPD Group - Last Month', 'DPD Group - last Month') or 'DPD Group - Last Month'
    col_loan_status = find_column(df, 'Loan Status - Last Month', 'LoanStatus - Last Month') or 'Loan Status - Last Month'
    col_inst_collected = find_column(df, 'installment - collected value', 'Installment - Collected Value') or 'installment - collected value'
    col_dpd_days = find_column(df, 'DPD Days', 'DPDDays', 'DPD days')
    if not col_dpd_days:
        col_dpd_days = col_dpd_group
    col_collection = find_column(df, 'Collection', 'Collection_Sum') or 'Collection'
    col_meeting_date = find_column(df, 'Meeting Date', 'MeetingDate', 'Meeting date') or 'Meeting Date'
    col_product = find_column(df, 'Product Name', 'ProductName', 'Product', 'Product name') or 'Product Name'
    col_account_id = find_column(df, 'Account ID', 'AccountID', 'Account_ID') or 'Account ID'
    col_loan_date = find_column(df, 'Loan Date', 'LoanDate', 'Loan_Date') or 'Loan Date'
    col_inst_amount = find_column(df, 'Installment Amount', 'InstallmentAmount') or 'Installment Amount'
    col_regular_demand = find_column(df, 'Regular Demand', 'RegularDemand') or 'Regular Demand'

    return {
        'region': col_region,
        'area': col_area,
        'branch': col_branch,
        'demand_count': col_demand_count,
        'cumulative': col_cumulative,
        'dpd_group': col_dpd_group,
        'dpd_last_month': col_dpd_last_month,
        'loan_status': col_loan_status,
        'inst_collected': col_inst_collected,
        'dpd_days': col_dpd_days,
        'collection': col_collection,
        'meeting_date': col_meeting_date,
        'product': col_product,
        'account_id': col_account_id,
        'loan_date': col_loan_date,
        'inst_amount': col_inst_amount,
        'regular_demand': col_regular_demand,
    }


# ---------------------------------------------------------------------------
# Meeting Date Filtering (mirrored from instant_processor.py lines 40-95)
# ---------------------------------------------------------------------------

def _filter_by_meeting_date(df, target_date, col_meeting_date):
    """
    Pre-filter DataFrame by Meeting Date range (first of month to target date).
    Handles datetime, Excel serial, and string date formats.

    Returns:
        Filtered DataFrame (or original if filtering is not possible).
    """
    if not target_date or col_meeting_date not in df.columns:
        if target_date:
            logger.warning(
                "Employee: '%s' column not found in DataFrame columns: %s... skipping date filter",
                col_meeting_date, list(df.columns[:10]),
            )
        return df

    first_of_month = target_date.replace(day=1)
    raw_vals = df[col_meeting_date]
    logger.info(
        "Employee: Meeting Date column '%s' dtype=%s, non-null=%d/%d",
        col_meeting_date, raw_vals.dtype, raw_vals.notna().sum(), len(raw_vals),
    )

    meeting_dates = pd.Series([pd.NaT] * len(df), index=df.index)

    # Strategy 1: Already datetime
    if pd.api.types.is_datetime64_any_dtype(raw_vals):
        meeting_dates = raw_vals
        logger.info("Employee: Meeting Date already datetime")

    # Strategy 2: Numeric (Excel serial dates)
    elif pd.api.types.is_numeric_dtype(raw_vals):
        meeting_dates = pd.to_datetime(raw_vals, unit='D', origin='1899-12-30', errors='coerce')
        valid_count = meeting_dates.notna().sum()
        logger.info(
            "Employee: Meeting Date parsed as Excel serial (dtype=%s): %d/%d valid",
            raw_vals.dtype, valid_count, len(df),
        )

    else:
        # Strategy 3: String - dayfirst=True
        parsed = pd.to_datetime(raw_vals, dayfirst=True, errors='coerce')
        valid_count = parsed.notna().sum()
        if valid_count > 0:
            meeting_dates = parsed
            logger.info("Employee: Meeting Date parsed with dayfirst=True: %d/%d valid", valid_count, len(df))
        else:
            # Strategy 4: default parser
            parsed = pd.to_datetime(raw_vals, errors='coerce')
            valid_count = parsed.notna().sum()
            if valid_count > 0:
                meeting_dates = parsed
                logger.info("Employee: Meeting Date parsed with default: %d/%d valid", valid_count, len(df))

    valid_total = meeting_dates.notna().sum()
    if valid_total > 0:
        mask = (meeting_dates >= pd.Timestamp(first_of_month)) & (meeting_dates <= pd.Timestamp(target_date))
        df_dated = df[mask].copy()
        logger.info(
            "Employee: Meeting Date filter %s to %s: %d/%d rows",
            first_of_month.strftime('%d-%m-%Y'), target_date.strftime('%d-%m-%Y'),
            len(df_dated), len(df),
        )
        return df_dated

    logger.warning("Employee: Could not parse any Meeting Date values, using all data")
    return df


# ---------------------------------------------------------------------------
# Data Loading: Merged DataFrame from Instant Cache (with TTL+LRU cache)
# ---------------------------------------------------------------------------

def get_merged_dataframe(date_str):
    """
    Return merged DataFrame for a date, using an in-memory TTL cache.

    On cache HIT (valid, non-expired entry): returns the cached DataFrame.
    On cache MISS: delegates to _compute_merged_dataframe, stores result
    in cache (unless memory pressure is detected), and returns it.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'

    Returns:
        Merged pandas DataFrame.

    Raises:
        FileNotFoundError: if cache or demand data is missing
        ValueError: on invalid date format or data issues
    """
    now = time.monotonic()

    # Check cache under lock (fast path)
    with _merged_df_lock:
        if date_str in _merged_df_cache:
            df, ts = _merged_df_cache[date_str]
            if now - ts < _MERGED_DF_TTL:
                logger.info("Employee: cache HIT for %s", date_str)
                return df
            else:
                # TTL expired -- remove stale entry
                del _merged_df_cache[date_str]

    # Cache miss -- compute outside the lock (never hold lock during I/O)
    df = _compute_merged_dataframe(date_str)

    # Store in cache if memory budget allows
    if is_memory_pressure():
        logger.warning("Employee: skipping cache store for %s -- memory pressure", date_str)
    else:
        with _merged_df_lock:
            # Evict oldest if at capacity
            if len(_merged_df_cache) >= _MERGED_DF_MAX_ENTRIES:
                oldest_key = min(_merged_df_cache, key=lambda k: _merged_df_cache[k][1])
                del _merged_df_cache[oldest_key]
                logger.info("Employee: cache EVICT oldest key %s", oldest_key)
            _merged_df_cache[date_str] = (df, time.monotonic())
            logger.info("Employee: cache STORE for %s (entries: %d)", date_str, len(_merged_df_cache))

    return df


def invalidate_merged_df_cache(date_str=None):
    """
    Invalidate cached merged DataFrames and trend cache.

    Args:
        date_str: If provided, remove only that date's entry.
                  If None, clear the entire cache.
    """
    with _merged_df_lock:
        if date_str is not None:
            removed = _merged_df_cache.pop(date_str, None)
            if removed:
                logger.info("Employee: cache INVALIDATE for %s", date_str)
            else:
                logger.debug("Employee: cache INVALIDATE for %s (was not cached)", date_str)
        else:
            count = len(_merged_df_cache)
            _merged_df_cache.clear()
            logger.info("Employee: cache INVALIDATE ALL (%d entries cleared)", count)

    # Also clear trend cache -- it depends on merged DF data
    with _trend_cache_lock:
        if date_str is not None:
            keys_to_remove = [k for k in _trend_cache if k[1] == date_str]
            for k in keys_to_remove:
                del _trend_cache[k]
            if keys_to_remove:
                logger.info("Employee: trend cache INVALIDATE %d entries for %s", len(keys_to_remove), date_str)
        else:
            count = len(_trend_cache)
            _trend_cache.clear()
            logger.info("Employee: trend cache INVALIDATE ALL (%d entries cleared)", count)


def get_cached_trend_point(emp_id, date_str, target_date, df, emp_cols):
    """
    Return cached collection trend KPIs for one employee+date pair.

    On cache HIT: returns the small cached dict.
    On cache MISS: calls compute_employee_performance, extracts KPIs,
    caches the result dict, and returns it.

    Args:
        emp_id: Employee ID string
        date_str: ISO date string 'YYYY-MM-DD'
        target_date: datetime object for the target date
        df: Merged DataFrame (already loaded, possibly from merged DF cache)
        emp_cols: Dict from detect_employee_columns

    Returns:
        dict with keys: collection_pct, demand, collection
    """
    key = (emp_id, date_str)
    now = time.monotonic()

    with _trend_cache_lock:
        if key in _trend_cache:
            val, ts = _trend_cache[key]
            if now - ts < _TREND_TTL:
                logger.debug("Employee: trend cache HIT for %s/%s", emp_id, date_str)
                return val
            else:
                del _trend_cache[key]

    # Miss -- compute
    perf = compute_employee_performance(df, emp_id, target_date, emp_cols)
    kpis = perf.get('kpis', {})
    result = {
        'collection_pct': kpis.get('collection_pct', 0),
        'demand': kpis.get('demand', 0),
        'collection': kpis.get('collection', 0),
    }

    with _trend_cache_lock:
        _trend_cache[key] = (result, time.monotonic())
        logger.debug("Employee: trend cache STORE for %s/%s", emp_id, date_str)

    return result


def _compute_merged_dataframe(date_str):
    """
    Load and merge PAR + Collection + Demand + Last Month PAR for a given date.

    This is the expensive inner implementation that loads parquets, creates a
    DuckDB connection, registers DataFrames, runs the merge SQL, and returns
    the result. Called by get_merged_dataframe on cache MISS.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'

    Returns:
        Merged pandas DataFrame containing all demand master columns plus
        Collection, DPD Group, DPD Group - Last Month, Loan Status - Last Month, etc.

    Raises:
        FileNotFoundError: if cache or demand data is missing
        ValueError: on invalid date format or data issues
    """
    # Parse date
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if not m:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD.")
    year, month, day = m.group(1), m.group(2), m.group(3)
    target_date = datetime(int(year), int(month), int(day))
    first_of_month = target_date.replace(day=1)

    # Load cached PAR + Collection
    df_par, df_collection, metadata = load_date_cache(date_str)
    logger.info("Employee: Loaded cache for %s (PAR=%d, Collection=%d)", date_str, len(df_par), len(df_collection))

    # Load demand master from monthly backend
    backend_dir = config.BACKEND_MONTHLY_DIR / f"{year}-{month}"
    demand_cache = backend_dir / 'demand_cache.parquet'
    if not demand_cache.exists():
        raise FileNotFoundError(f"No demand data for {year}-{month}")
    try:
        df_demand = pd.read_parquet(demand_cache, columns=[
            'Account ID', 'Emp ID', 'Emp Name', 'BranchName', 'Region',
            'Regular Demand', 'Installment Amount', 'LoanStatus', 'Loan Date',
        ])
    except Exception:
        df_demand = pd.read_parquet(demand_cache)
    logger.info("Employee: Loaded demand master (%d rows) from %s", len(df_demand), demand_cache)

    # Load last month PAR if available
    lm_cache = backend_dir / 'last_month_par_cache.parquet'
    has_last_month = lm_cache.exists()
    df_last_month = None
    if has_last_month:
        try:
            df_last_month = pd.read_parquet(lm_cache, columns=['AccountID', 'DPD Days', 'LoanStatus'])
        except Exception:
            df_last_month = pd.read_parquet(lm_cache)
        logger.info("Employee: Loaded last month PAR (%d rows)", len(df_last_month))

    # Parse collection Trxdate if needed
    from services.eod_processor import parse_trxdate
    df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])

    # Detect DPD column from PAR
    ALLOWED_DPD_COLUMNS = processor.ALLOWED_DPD_COLUMNS
    days_group_col = 'DPD Group'
    for col in df_par.columns:
        if col in ALLOWED_DPD_COLUMNS:
            days_group_col = col
            break

    # Date filter clause for collection aggregation
    date_filter_clause = ""
    date_filter_clause = f"""
      AND Trxdate >= '{first_of_month.strftime('%Y-%m-%d')}'
      AND Trxdate <= '{target_date.strftime('%Y-%m-%d')}'"""

    # Build CTEs
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
        )""",
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
        END as "installment - collected value" """,
    ]

    joins = [
        "FROM raw_demand_upload d",
        "LEFT JOIN Collection_Agg c ON d.\"Account ID\" = c.AccountID",
        "LEFT JOIN PAR_Mapped p ON d.\"Account ID\" = p.AccountID",
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

    final_query = (
        "WITH " + ",\n".join(ctes) + "\nSELECT \n"
        + ",\n".join(select_clauses) + "\n" + "\n".join(joins)
    )

    # Execute in standalone DuckDB connection
    con = duckdb.connect()
    try:
        con.register('daily_par', df_par)
        con.register('daily_collection', df_collection)
        con.register('raw_demand_upload', df_demand)
        if has_last_month:
            con.register('Last_Month_PAR', df_last_month)

        df_result = con.execute(final_query).df()
        logger.info("Employee: Merge pipeline complete, result shape=%s", df_result.shape)
        return df_result
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Available Dates (with demand data)
# ---------------------------------------------------------------------------

def get_available_dates():
    """
    Return list of cached dates that also have demand data available
    in the monthly backend directory.

    Returns:
        List of dicts with 'date_iso', 'date_display', 'has_report', etc.
    """
    dates = list_cached_dates()
    result = []
    for d in dates:
        date_str = d['date_iso']
        parts = date_str.split('-')
        backend_dir = config.BACKEND_MONTHLY_DIR / f"{parts[0]}-{parts[1]}"
        if (backend_dir / 'demand_cache.parquet').exists():
            result.append(d)
    return result


# ---------------------------------------------------------------------------
# Employee List (for autocomplete / search)
# ---------------------------------------------------------------------------

def compute_employee_list(df, emp_cols):
    """
    Return all unique employees with account counts for search autocomplete.

    Args:
        df: Merged DataFrame
        emp_cols: dict from detect_employee_columns()

    Returns:
        List of dicts: [{'emp_id': ..., 'emp_name': ..., 'region': ...,
                         'area': ..., 'branch': ..., 'account_count': ...}, ...]
    """
    col_emp_id = emp_cols.get('emp_id')
    col_emp_name = emp_cols.get('emp_name')

    if not col_emp_id:
        logger.warning("Employee: No Emp ID column found, cannot compute employee list")
        return []

    logger.debug("compute_employee_list: using cached merged DF (%d rows)", len(df))
    cols = _resolve_columns(df)

    con = duckdb.connect()
    try:
        con.register('emp_data', df)

        # Build SELECT with available columns
        name_expr = f'MAX("{col_emp_name}")' if col_emp_name and col_emp_name in df.columns else "NULL"
        region_expr = f'MAX("{cols["region"]}")' if cols['region'] in df.columns else "NULL"
        area_expr = f'MAX("{cols["area"]}")' if cols['area'] in df.columns else "NULL"
        branch_expr = f'MAX("{cols["branch"]}")' if cols['branch'] in df.columns else "NULL"

        sql = f"""
            SELECT
                CAST("{col_emp_id}" AS VARCHAR) as emp_id,
                {name_expr} as emp_name,
                {region_expr} as region,
                {area_expr} as area,
                {branch_expr} as branch,
                COUNT(*) as account_count
            FROM emp_data
            WHERE "{col_emp_id}" IS NOT NULL
              AND TRIM(CAST("{col_emp_id}" AS VARCHAR)) != ''
            GROUP BY CAST("{col_emp_id}" AS VARCHAR)
            ORDER BY emp_id
        """

        rows = con.execute(sql).fetchall()
        result = []
        for r in rows:
            result.append({
                'emp_id': str(r[0]).strip(),
                'emp_name': str(r[1] or '').strip(),
                'region': str(r[2] or '').strip(),
                'area': str(r[3] or '').strip(),
                'branch': str(r[4] or '').strip(),
                'account_count': int(r[5]),
            })
        logger.info("Employee: Found %d unique employees", len(result))
        return result
    except Exception as e:
        logger.error("Employee: compute_employee_list error: %s", e)
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Employee Leaderboard (ranked, paginated, filtered)
# ---------------------------------------------------------------------------

def compute_employee_leaderboard(
    df,
    target_date,
    emp_cols,
    sort_by='collection_pct',
    sort_order='desc',
    page=1,
    per_page=50,
    search='',
    region='',
    district='',
    branch='',
):
    """
    Rank ALL employees with server-side pagination and filtering.

    Args:
        df: Merged DataFrame (already date-filtered or raw)
        target_date: datetime object for meeting date filtering
        emp_cols: dict from detect_employee_columns()
        sort_by: column to sort by (collection_pct, demand, collection, ftod, npa_count, emp_name)
        sort_order: 'asc' or 'desc'
        page: page number (1-based)
        per_page: results per page
        search: free-text search on emp_id or emp_name
        region: filter by region name
        district: filter by area name (kept as 'district' param for API compatibility)
        branch: filter by branch name

    Returns:
        dict with 'employees', 'total', 'page', 'per_page', 'total_pages', 'summary'
    """
    col_emp_id = emp_cols.get('emp_id')
    col_emp_name = emp_cols.get('emp_name')

    if not col_emp_id:
        logger.warning("Employee: No Emp ID column found, cannot compute leaderboard")
        return {
            'employees': [], 'total': 0, 'page': page, 'per_page': per_page,
            'total_pages': 0, 'summary': {},
        }

    logger.debug("compute_employee_leaderboard: using cached merged DF (%d rows)", len(df))
    cols = _resolve_columns(df)

    # Pre-filter by meeting date in Python (handles multiple date formats)
    df_dated = _filter_by_meeting_date(df, target_date, cols['meeting_date'])

    con = duckdb.connect()
    try:
        con.register('emp_data', df_dated)

        # Build expressions
        name_expr = f'MAX("{col_emp_name}")' if col_emp_name and col_emp_name in df_dated.columns else "NULL"
        region_expr = f'MAX("{cols["region"]}")' if cols['region'] in df_dated.columns else "NULL"
        area_expr = f'MAX("{cols["area"]}")' if cols['area'] in df_dated.columns else "NULL"
        branch_expr = f'MAX("{cols["branch"]}")' if cols['branch'] in df_dated.columns else "NULL"

        # Use MODE() for most common branch (employee across multiple branches)
        # DuckDB supports mode() aggregate
        branch_mode_expr = f'MODE("{cols["branch"]}")' if cols['branch'] in df_dated.columns else "NULL"

        # WHERE clause for emp_id not null
        where_clauses = [
            f'"{col_emp_id}" IS NOT NULL',
            f"TRIM(CAST(\"{col_emp_id}\" AS VARCHAR)) != ''",
        ]

        # Search filter
        if search:
            search_escaped = search.replace("'", "''")
            search_parts = [f"LOWER(CAST(\"{col_emp_id}\" AS VARCHAR)) LIKE '%{search_escaped.lower()}%'"]
            if col_emp_name and col_emp_name in df_dated.columns:
                search_parts.append(f"LOWER(CAST(\"{col_emp_name}\" AS VARCHAR)) LIKE '%{search_escaped.lower()}%'")
            where_clauses.append(f"({' OR '.join(search_parts)})")

        # Region / Area / Branch filters
        if region and cols['region'] in df_dated.columns:
            region_escaped = region.replace("'", "''")
            where_clauses.append(f"TRIM(CAST(\"{cols['region']}\" AS VARCHAR)) = '{region_escaped}'")
        if district and cols['area'] in df_dated.columns:
            area_escaped = district.replace("'", "''")
            where_clauses.append(f"TRIM(CAST(\"{cols['area']}\" AS VARCHAR)) = '{area_escaped}'")
        if branch and cols['branch'] in df_dated.columns:
            branch_escaped = branch.replace("'", "''")
            where_clauses.append(f"TRIM(CAST(\"{cols['branch']}\" AS VARCHAR)) = '{branch_escaped}'")

        where_sql = " AND ".join(where_clauses)

        # Demand = SUM of "No of Regular Demand"
        # Collection = count where DPD Group NOT LIKE '%1-30%'
        # (same pattern as instant_processor Section 1)
        demand_col = cols['demand_count']
        dpd_col = cols['dpd_group']
        dpd_lm_col = cols['dpd_last_month']
        loan_status_col = cols['loan_status']

        # Core aggregation CTE
        agg_sql = f"""
            WITH emp_agg AS (
                SELECT
                    CAST("{col_emp_id}" AS VARCHAR) as emp_id,
                    {name_expr} as emp_name,
                    {region_expr} as region,
                    {area_expr} as area,
                    {branch_mode_expr} as branch,
                    COALESCE(SUM(TRY_CAST("{demand_col}" AS BIGINT)), 0) as demand,
                    COALESCE(SUM(
                        CASE WHEN CAST(COALESCE("{dpd_col}", '') AS VARCHAR) NOT LIKE '%1-30%'
                        THEN TRY_CAST("{demand_col}" AS BIGINT) ELSE 0 END
                    ), 0) as collection,
                    COALESCE(SUM(
                        CASE WHEN CAST(COALESCE("{dpd_lm_col}", '') AS VARCHAR) LIKE '%1-30%'
                             AND LOWER(COALESCE(CAST("{loan_status_col}" AS VARCHAR), '')) LIKE '%active%'
                        THEN 1 ELSE 0 END
                    ), 0) as dpd_1_30,
                    COALESCE(SUM(
                        CASE WHEN LOWER(COALESCE(CAST("{loan_status_col}" AS VARCHAR), '')) LIKE '%npa%'
                        THEN 1 ELSE 0 END
                    ), 0) as npa_count
                FROM emp_data
                WHERE {where_sql}
                GROUP BY CAST("{col_emp_id}" AS VARCHAR)
            )
            SELECT
                emp_id, emp_name, region, area, branch,
                demand, collection,
                (demand - collection) as ftod,
                CASE WHEN demand > 0 THEN ROUND(CAST(collection AS DOUBLE) / demand * 100, 2) ELSE 0 END as collection_pct,
                dpd_1_30, npa_count
            FROM emp_agg
        """

        # Count total matching employees
        count_sql = f"SELECT COUNT(*) FROM ({agg_sql}) sub"
        total = con.execute(count_sql).fetchone()[0]

        if total == 0:
            return {
                'employees': [], 'total': 0, 'page': page, 'per_page': per_page,
                'total_pages': 0, 'summary': {
                    'total_employees': 0, 'avg_collection_pct': 0,
                    'best_employee': None, 'worst_employee': None,
                },
            }

        # Summary stats
        summary_sql = f"""
            SELECT
                COUNT(*) as total_employees,
                ROUND(AVG(collection_pct), 2) as avg_collection_pct,
                MAX(collection_pct) as max_pct,
                MIN(collection_pct) as min_pct
            FROM ({agg_sql}) sub
        """
        summary_row = con.execute(summary_sql).fetchone()

        # Best and worst employees
        best_sql = f"""
            SELECT emp_id, emp_name, collection_pct
            FROM ({agg_sql}) sub
            ORDER BY collection_pct DESC, demand DESC
            LIMIT 1
        """
        best_row = con.execute(best_sql).fetchone()

        worst_sql = f"""
            SELECT emp_id, emp_name, collection_pct
            FROM ({agg_sql}) sub
            WHERE demand > 0
            ORDER BY collection_pct ASC, demand DESC
            LIMIT 1
        """
        worst_row = con.execute(worst_sql).fetchone()

        # Validate sort_by
        valid_sorts = {
            'collection_pct', 'demand', 'collection', 'ftod',
            'npa_count', 'dpd_1_30', 'emp_id', 'emp_name',
        }
        if sort_by not in valid_sorts:
            sort_by = 'collection_pct'
        order_dir = 'DESC' if sort_order.lower() == 'desc' else 'ASC'

        # Secondary sort for stability
        secondary_sort = ', demand DESC' if sort_by != 'demand' else ', collection_pct DESC'

        # Paginated + ranked results
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        ranked_sql = f"""
            SELECT
                ROW_NUMBER() OVER (ORDER BY {sort_by} {order_dir}{secondary_sort}) as rank,
                emp_id, emp_name, region, area, branch,
                demand, collection, ftod, collection_pct,
                dpd_1_30, npa_count
            FROM ({agg_sql}) sub
            ORDER BY {sort_by} {order_dir}{secondary_sort}
            LIMIT {per_page} OFFSET {offset}
        """
        rows = con.execute(ranked_sql).fetchall()

        employees = []
        for r in rows:
            employees.append({
                'rank': int(r[0]),
                'emp_id': str(r[1]).strip(),
                'emp_name': str(r[2] or '').strip(),
                'region': str(r[3] or '').strip(),
                'area': str(r[4] or '').strip(),
                'branch': str(r[5] or '').strip(),
                'demand': int(r[6]),
                'collection': int(r[7]),
                'ftod': int(r[8]),
                'collection_pct': float(r[9]),
                'dpd_1_30': int(r[10]),
                'npa_count': int(r[11]),
            })

        summary = {
            'total_employees': int(summary_row[0]),
            'avg_collection_pct': float(summary_row[1] or 0),
            'best_employee': {
                'emp_id': str(best_row[0]).strip(),
                'emp_name': str(best_row[1] or '').strip(),
                'collection_pct': float(best_row[2]),
            } if best_row else None,
            'worst_employee': {
                'emp_id': str(worst_row[0]).strip(),
                'emp_name': str(worst_row[1] or '').strip(),
                'collection_pct': float(worst_row[2]),
            } if worst_row else None,
        }

        return {
            'employees': employees,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'summary': summary,
        }

    except Exception as e:
        logger.error("Employee: compute_employee_leaderboard error: %s", e)
        return {
            'employees': [], 'total': 0, 'page': page, 'per_page': per_page,
            'total_pages': 0, 'summary': {},
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Employee Performance (VBA-style sections for one employee)
# ---------------------------------------------------------------------------

def compute_employee_performance(df, emp_id, target_date, emp_cols):
    """
    Compute detailed VBA-style performance sections for a single employee.

    Sections mirror compute_instant_report() but filtered to one employee:
      - Regular Demand vs Collection (Overall + FY)
      - 1-30 DPD Bucket (Overall + FY)
      - 31-60 DPD Bucket (Overall + FY)
      - PNPA 61-90 (Overall + FY)
      - NPA (Overall + FY)
      - Product-wise breakdown (IGL, FIG, IL/VVY)

    Args:
        df: Merged DataFrame
        emp_id: Employee ID string
        target_date: datetime object for meeting date filtering
        emp_cols: dict from detect_employee_columns()

    Returns:
        dict with 'employee', 'kpis', 'sections'
    """
    col_emp_id = emp_cols.get('emp_id')
    col_emp_name = emp_cols.get('emp_name')

    if not col_emp_id:
        raise ValueError("No Emp ID column found in data")

    logger.debug("compute_employee_performance: using cached merged DF (%d rows)", len(df))
    cols = _resolve_columns(df)

    # Pre-filter by meeting date
    df_dated = _filter_by_meeting_date(df, target_date, cols['meeting_date'])

    # Filter to this employee
    emp_id_str = str(emp_id).strip()
    emp_mask = df_dated[col_emp_id].astype(str).str.strip() == emp_id_str
    df_emp = df_dated[emp_mask].copy()

    if len(df_emp) == 0:
        raise ValueError(f"Employee '{emp_id}' not found in data")

    logger.info("Employee: Computing performance for %s (%d accounts)", emp_id_str, len(df_emp))

    # Extract employee info
    emp_name = ''
    if col_emp_name and col_emp_name in df_emp.columns:
        emp_name = str(df_emp[col_emp_name].iloc[0] or '').strip()
    emp_region = str(df_emp[cols['region']].iloc[0] or '').strip() if cols['region'] in df_emp.columns else ''
    emp_area = str(df_emp[cols['area']].iloc[0] or '').strip() if cols['area'] in df_emp.columns else ''
    emp_branch = str(df_emp[cols['branch']].iloc[0] or '').strip() if cols['branch'] in df_emp.columns else ''

    employee_info = {
        'emp_id': emp_id_str,
        'emp_name': emp_name,
        'region': emp_region,
        'area': emp_area,
        'branch': emp_branch,
    }

    con = duckdb.connect()
    try:
        con.register('emp_all', df_emp)

        # Determine FY subset (Loan Date == 1 means current FY loan)
        loan_date_col = cols['loan_date']
        has_loan_date = loan_date_col in df_emp.columns

        if has_loan_date:
            try:
                fy_mask = df_emp[loan_date_col].astype(str).str.strip() == '1'
                df_emp_fy = df_emp[fy_mask].copy()
                con.register('emp_fy', df_emp_fy)
                logger.info("Employee: FY filter: %d/%d accounts", len(df_emp_fy), len(df_emp))
            except Exception as e:
                logger.warning("Employee: Could not filter by Loan Date: %s", e)
                df_emp_fy = pd.DataFrame(columns=df_emp.columns)
                con.register('emp_fy', df_emp_fy)
                has_loan_date = False
        else:
            df_emp_fy = pd.DataFrame(columns=df_emp.columns)
            con.register('emp_fy', df_emp_fy)

        # Column references
        demand_col = cols['demand_count']
        dpd_col = cols['dpd_group']
        dpd_lm_col = cols['dpd_last_month']
        loan_status_col = cols['loan_status']
        cumulative_col = cols['cumulative']
        inst_collected_col = cols['inst_collected']
        dpd_days_col = cols['dpd_days']
        collection_col = cols['collection']
        product_col = cols['product']

        # ── Helper: compute section stats from a table name ────────
        def _regular_stats(tbl):
            """Regular Demand vs Collection stats."""
            try:
                row = con.execute(f"""
                    SELECT
                        COALESCE(SUM(TRY_CAST("{demand_col}" AS BIGINT)), 0) as demand,
                        COALESCE(SUM(CASE WHEN CAST(COALESCE("{dpd_col}",'') AS VARCHAR) NOT LIKE '%1-30%'
                            THEN TRY_CAST("{demand_col}" AS BIGINT) ELSE 0 END), 0) as collection
                    FROM {tbl}
                """).fetchone()
                d, c = int(row[0]), int(row[1])
                return {'demand': d, 'collection': c, 'ftod': d - c,
                        'collection_pct': round(c / d * 100, 2) if d else 0}
            except Exception as e:
                logger.warning("Employee: _regular_stats(%s) error: %s", tbl, e)
                return {'demand': 0, 'collection': 0, 'ftod': 0, 'collection_pct': 0}

        def _bucket_stats(tbl, filter_col, pattern):
            """DPD bucket stats (1-30, 31-60, PNPA)."""
            try:
                row = con.execute(f"""
                    SELECT
                        COALESCE(SUM(TRY_CAST("{cumulative_col}" AS BIGINT)), 0) as demand,
                        COALESCE(SUM(CASE WHEN TRY_CAST("{inst_collected_col}" AS INT)=1 THEN 1 ELSE 0 END), 0) as collection
                    FROM {tbl}
                    WHERE CAST(COALESCE("{filter_col}",'') AS VARCHAR) LIKE '%{pattern}%'
                      AND LOWER(COALESCE(CAST("{loan_status_col}" AS VARCHAR),'')) LIKE '%active%'
                """).fetchone()
                d, c = int(row[0]), int(row[1])
                return {'demand': d, 'collection': c, 'balance': d - c,
                        'collection_pct': round(c / d * 100, 2) if d else 0}
            except Exception as e:
                logger.warning("Employee: _bucket_stats(%s, %s) error: %s", tbl, pattern, e)
                return {'demand': 0, 'collection': 0, 'balance': 0, 'collection_pct': 0}

        def _npa_stats(tbl):
            """NPA stats."""
            npa_where = f"""WHERE LOWER(COALESCE(CAST("{loan_status_col}" AS VARCHAR),'')) LIKE '%npa%'
                AND CAST(COALESCE("{dpd_lm_col}",'') AS VARCHAR) NOT LIKE '%0 Days%'
                AND TRIM(CAST(COALESCE("{dpd_lm_col}",'') AS VARCHAR)) != ''"""
            try:
                row = con.execute(f"""
                    SELECT
                        COALESCE(SUM(TRY_CAST("{cumulative_col}" AS BIGINT)), 0) as demand,
                        COALESCE(SUM(CASE WHEN "{dpd_col}" IS NOT NULL AND TRIM(CAST("{dpd_col}" AS VARCHAR))!=''
                            AND "{collection_col}" IS NOT NULL THEN 1 ELSE 0 END), 0) as activation_account,
                        COALESCE(SUM(CASE WHEN "{dpd_col}" IS NOT NULL AND TRIM(CAST("{dpd_col}" AS VARCHAR))!=''
                            THEN COALESCE(TRY_CAST("{collection_col}" AS DOUBLE),0) ELSE 0 END), 0) as activation_amount,
                        COALESCE(SUM(CASE WHEN ("{dpd_col}" IS NULL OR TRIM(CAST("{dpd_col}" AS VARCHAR))='')
                            AND "{collection_col}" IS NOT NULL THEN 1 ELSE 0 END), 0) as closure_account,
                        COALESCE(SUM(CASE WHEN "{dpd_col}" IS NULL OR TRIM(CAST("{dpd_col}" AS VARCHAR))=''
                            THEN COALESCE(TRY_CAST("{collection_col}" AS DOUBLE),0) ELSE 0 END), 0) as closure_amount
                    FROM {tbl} {npa_where}
                """).fetchone()
                return {
                    'demand': int(row[0]),
                    'activation_account': int(row[1]),
                    'activation_amount': round(float(row[2])),
                    'closure_account': int(row[3]),
                    'closure_amount': round(float(row[4])),
                }
            except Exception as e:
                logger.warning("Employee: _npa_stats(%s) error: %s", tbl, e)
                return {
                    'demand': 0, 'activation_account': 0, 'activation_amount': 0,
                    'closure_account': 0, 'closure_amount': 0,
                }

        def _product_stats(tbl, product_filter_val, stat_func, *stat_args):
            """Product-filtered stats using a given stat function."""
            # Filter by product in Python, register as temp table
            prod_tbl_name = f'{tbl}_prod'
            try:
                # Get the original DataFrame for this table
                src_df = df_emp if tbl == 'emp_all' else df_emp_fy
                if product_col in src_df.columns:
                    prod_mask = src_df[product_col].astype(str).str.strip() == product_filter_val
                    df_prod = src_df[prod_mask].copy()
                else:
                    df_prod = pd.DataFrame(columns=src_df.columns)
                con.register(prod_tbl_name, df_prod)
                return stat_func(prod_tbl_name, *stat_args)
            except Exception as e:
                logger.warning("Employee: _product_stats error: %s", e)
                if stat_func == _regular_stats:
                    return {'demand': 0, 'collection': 0, 'ftod': 0, 'collection_pct': 0}
                elif stat_func == _npa_stats:
                    return {'demand': 0, 'activation_account': 0, 'activation_amount': 0,
                            'closure_account': 0, 'closure_amount': 0}
                else:
                    return {'demand': 0, 'collection': 0, 'balance': 0, 'collection_pct': 0}

        # ── KPIs ──────────────────────────────────────────────────
        kpi_stats = _regular_stats('emp_all')
        npa_overall = _npa_stats('emp_all')
        kpis = {
            'demand': kpi_stats['demand'],
            'collection': kpi_stats['collection'],
            'collection_pct': kpi_stats['collection_pct'],
            'ftod': kpi_stats['ftod'],
            'npa_count': npa_overall['demand'],
        }

        # Product mapping: display_name -> data_value
        products = [('IGL', 'IGL'), ('FIG', 'FIG'), ('IL', 'VVY')]

        # ── Sections ──────────────────────────────────────────────
        sections = []

        # Section 1: Regular Demand vs Collection
        s1_overall = _regular_stats('emp_all')
        s1_fy = _regular_stats('emp_fy') if has_loan_date else {'demand': 0, 'collection': 0, 'ftod': 0, 'collection_pct': 0}
        s1_products = []
        for display_name, filter_val in products:
            p_overall = _product_stats('emp_all', filter_val, _regular_stats)
            p_fy = _product_stats('emp_fy', filter_val, _regular_stats) if has_loan_date else {'demand': 0, 'collection': 0, 'ftod': 0, 'collection_pct': 0}
            s1_products.append({'name': display_name, 'overall': p_overall, 'fy': p_fy, **p_overall})

        sections.append({
            'title': 'Regular Demand vs Collection',
            'overall': s1_overall,
            'fy': s1_fy,
            'products': s1_products,
        })

        # Sections 2-4: DPD Buckets
        bucket_defs = [
            ('1-30 DPD Bucket', dpd_lm_col, '1-30'),
            ('31-60 DPD Bucket', dpd_lm_col, '31-60'),
            ('PNPA (61-90 DPD)', dpd_days_col, '61-90'),
        ]
        for title, filter_col, pattern in bucket_defs:
            s_overall = _bucket_stats('emp_all', filter_col, pattern)
            s_fy = _bucket_stats('emp_fy', filter_col, pattern) if has_loan_date else {'demand': 0, 'collection': 0, 'balance': 0, 'collection_pct': 0}
            s_products = []
            for display_name, filter_val in products:
                p_overall = _product_stats('emp_all', filter_val, _bucket_stats, filter_col, pattern)
                p_fy = _product_stats('emp_fy', filter_val, _bucket_stats, filter_col, pattern) if has_loan_date else {'demand': 0, 'collection': 0, 'balance': 0, 'collection_pct': 0}
                s_products.append({'name': display_name, 'overall': p_overall, 'fy': p_fy, **p_overall})

            sections.append({
                'title': title,
                'overall': s_overall,
                'fy': s_fy,
                'products': s_products,
            })

        # Section 5: NPA
        s5_overall = _npa_stats('emp_all')
        s5_fy = _npa_stats('emp_fy') if has_loan_date else {
            'demand': 0, 'activation_account': 0, 'activation_amount': 0,
            'closure_account': 0, 'closure_amount': 0,
        }
        s5_products = []
        for display_name, filter_val in products:
            p_overall = _product_stats('emp_all', filter_val, _npa_stats)
            p_fy = _product_stats('emp_fy', filter_val, _npa_stats) if has_loan_date else {'demand': 0, 'activation_account': 0, 'activation_amount': 0, 'closure_account': 0, 'closure_amount': 0}
            s5_products.append({'name': display_name, 'overall': p_overall, 'fy': p_fy, **p_overall})

        sections.append({
            'title': 'NPA',
            'overall': s5_overall,
            'fy': s5_fy,
            'products': s5_products,
        })

        return {
            'employee': employee_info,
            'kpis': kpis,
            'sections': sections,
        }

    except Exception as e:
        logger.error("Employee: compute_employee_performance error: %s", e)
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Employee Accounts (raw account-level detail)
# ---------------------------------------------------------------------------

def compute_employee_accounts(df, emp_id, emp_cols):
    """
    Return raw account-level detail for one employee.

    Args:
        df: Merged DataFrame
        emp_id: Employee ID string
        emp_cols: dict from detect_employee_columns()

    Returns:
        dict with 'accounts' (list of account dicts) and 'total' (int)
    """
    col_emp_id = emp_cols.get('emp_id')

    if not col_emp_id:
        raise ValueError("No Emp ID column found in data")

    logger.debug("compute_employee_accounts: using cached merged DF (%d rows)", len(df))
    cols = _resolve_columns(df)

    emp_id_str = str(emp_id).strip()
    emp_mask = df[col_emp_id].astype(str).str.strip() == emp_id_str
    df_emp = df[emp_mask].copy()

    if len(df_emp) == 0:
        return {'accounts': [], 'total': 0}

    con = duckdb.connect()
    try:
        con.register('emp_accounts', df_emp)

        account_id_col = cols['account_id']
        product_col = cols['product']
        demand_col = cols['demand_count']
        dpd_col = cols['dpd_group']
        loan_status_col = cols['loan_status']
        collection_col = cols['collection']
        regular_demand_col = cols['regular_demand']
        inst_amount_col = cols['inst_amount']

        # Build SELECT dynamically based on available columns
        select_parts = []

        # Account ID
        if account_id_col in df_emp.columns:
            select_parts.append(f'CAST("{account_id_col}" AS VARCHAR) as account_id')
        else:
            select_parts.append("NULL as account_id")

        # Product
        if product_col in df_emp.columns:
            select_parts.append(f'CAST("{product_col}" AS VARCHAR) as product')
        else:
            select_parts.append("NULL as product")

        # Demand (Regular Demand value)
        if regular_demand_col in df_emp.columns:
            select_parts.append(f'TRY_CAST("{regular_demand_col}" AS DOUBLE) as demand')
        elif demand_col in df_emp.columns:
            select_parts.append(f'TRY_CAST("{demand_col}" AS BIGINT) as demand')
        else:
            select_parts.append("0 as demand")

        # Collection
        if collection_col in df_emp.columns:
            select_parts.append(f'TRY_CAST("{collection_col}" AS DOUBLE) as collection')
        else:
            select_parts.append("0 as collection")

        # DPD Group
        if dpd_col in df_emp.columns:
            select_parts.append(f'CAST(COALESCE("{dpd_col}", \'\') AS VARCHAR) as dpd_group')
        else:
            select_parts.append("'' as dpd_group")

        # Loan Status
        if loan_status_col in df_emp.columns:
            select_parts.append(f'CAST(COALESCE("{loan_status_col}", \'\') AS VARCHAR) as loan_status')
        else:
            select_parts.append("'' as loan_status")

        # Installment Amount
        if inst_amount_col in df_emp.columns:
            select_parts.append(f'TRY_CAST("{inst_amount_col}" AS DOUBLE) as installment_amount')
        else:
            select_parts.append("0 as installment_amount")

        # DPD Group - Last Month (needed for section mapping)
        dpd_lm_col = cols['dpd_last_month']
        if dpd_lm_col in df_emp.columns:
            select_parts.append(f'CAST(COALESCE("{dpd_lm_col}", \'\') AS VARCHAR) as dpd_group_lm')
        else:
            select_parts.append("'' as dpd_group_lm")

        # DPD Days (needed for PNPA section mapping)
        dpd_days_col = cols['dpd_days']
        if dpd_days_col in df_emp.columns:
            select_parts.append(f'CAST(COALESCE("{dpd_days_col}", \'\') AS VARCHAR) as dpd_days')
        else:
            select_parts.append("'' as dpd_days")

        sql = f"""
            SELECT {', '.join(select_parts)}
            FROM emp_accounts
            ORDER BY account_id
        """

        rows = con.execute(sql).fetchall()
        accounts = []
        for r in rows:
            accounts.append({
                'account_id': str(r[0] or '').strip(),
                'product': str(r[1] or '').strip(),
                'demand': round(float(r[2] or 0), 2),
                'collection': round(float(r[3] or 0), 2),
                'dpd_group': str(r[4] or '').strip(),
                'loan_status': str(r[5] or '').strip(),
                'installment_amount': round(float(r[6] or 0), 2),
                'dpd_group_lm': str(r[7] or '').strip(),
                'dpd_days': str(r[8] or '').strip(),
            })

        logger.info("Employee: Found %d accounts for %s", len(accounts), emp_id_str)
        return {'accounts': accounts, 'total': len(accounts)}

    except Exception as e:
        logger.error("Employee: compute_employee_accounts error: %s", e)
        return {'accounts': [], 'total': 0}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Account Detail (all columns for a single account)
# ---------------------------------------------------------------------------

def get_account_detail(df, account_id, emp_cols):
    """
    Return ALL columns for a single account as a flat dict.

    Args:
        df: Merged DataFrame
        account_id: Account ID string
        emp_cols: dict from detect_employee_columns()

    Returns:
        dict with all column values for the account, or None if not found
    """
    cols = _resolve_columns(df)
    account_id_col = cols['account_id']

    if account_id_col not in df.columns:
        logger.warning("Account detail: Account ID column '%s' not found", account_id_col)
        return None

    account_id_str = str(account_id).strip()
    mask = df[account_id_col].astype(str).str.strip() == account_id_str
    df_acct = df[mask]

    if len(df_acct) == 0:
        logger.warning("Account detail: Account '%s' not found", account_id_str)
        return None

    row = df_acct.iloc[0]
    result = {}
    for col_name in df.columns:
        val = row[col_name]
        # Convert to JSON-safe types
        if pd.isna(val):
            result[col_name] = None
        elif hasattr(val, 'isoformat'):
            result[col_name] = val.isoformat()
        elif isinstance(val, (int, float)):
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                result[col_name] = None
            else:
                result[col_name] = val
        else:
            result[col_name] = str(val).strip()

    # Add computed fields
    regular_demand = 0
    collection = 0
    try:
        regular_demand = float(row.get(cols['regular_demand'], 0) or 0)
    except (TypeError, ValueError):
        pass
    try:
        collection = float(row.get(cols['collection'], 0) or 0)
    except (TypeError, ValueError):
        pass

    if regular_demand > 0:
        result['_collection_pct'] = round(collection / regular_demand * 100, 2)
        result['_gap'] = round(regular_demand - collection, 2)
    else:
        result['_collection_pct'] = 0.0
        result['_gap'] = 0.0

    logger.info("Account detail: Returned %d fields for account '%s'", len(result), account_id_str)
    return result
