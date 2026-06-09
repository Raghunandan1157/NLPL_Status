"""
Step 1 - Combined Python Script for Regular Demand Vs Collection (OPTIMIZED)
=============================================================================
Optimizations:
  - Single read, single write (no intermediate Excel saves)
  - Pandas only (no openpyxl for row-level ops)
  - xlsxwriter engine (faster than openpyxl)
  - Memory-efficient lookups with pandas merge/map

NOTE: Migrated from EOD/Step - 1 - Combined.py into Unified services.
      Path references updated to use config module.
"""

import gc
import time
import logging
from pathlib import Path
import pandas as pd

import config
from services.excel_reader import compute_file_hash, smart_read_excel
from services.memory_manager import gc_checkpoint
from services.column_matcher import find_column


def parse_date_column(series):
    """Universal date column converter. Handles:
    - Already datetime → passthrough
    - Excel serial numbers (int/float like 46027) → convert from 1899-12-30 epoch
    - String dates in common formats (dd-mm-yyyy, mm/dd/yy, mm/dd/yyyy, yyyy-mm-dd)

    Returns a pandas Series of datetime64.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    if pd.api.types.is_numeric_dtype(series):
        logging.info("Detected numeric date column - converting from Excel serial date")
        excel_epoch = pd.Timestamp('1899-12-30')
        return excel_epoch + pd.to_timedelta(series, unit='D')
    # String dates — try multiple formats
    result = pd.to_datetime(series, format='%d-%m-%Y', errors='coerce')
    for fmt in ['%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S']:
        if not (result.isna() & series.notna()).any():
            break
        partial = pd.to_datetime(series, format=fmt, errors='coerce')
        result = result.where(result.notna(), partial)
    # Final fallback: let pandas infer (dayfirst=True for dd-mm-yyyy ambiguous dates)
    if (result.isna() & series.notna()).any():
        partial = pd.to_datetime(series, dayfirst=True, errors='coerce')
        result = result.where(result.notna(), partial)
    nat_count = result.isna().sum()
    if nat_count > 0:
        logging.warning(
            f"parse_date_column: {nat_count} NaT values after parsing "
            f"(out of {len(series)} total). Sample unparseable: "
            f"{series[result.isna()].dropna().head(5).tolist()}"
        )
    return result


# Backward-compatible alias
parse_trxdate = parse_date_column


def derive_fy_bounds(target_date):
    """
    Derive the Indian Financial Year (FY) start and end dates from a target date.

    Indian FY runs from April 1 to March 31. For example:
      - Any date in April 2025 through March 2026 falls in FY 2025-26
        (start: 2025-04-01, end: 2026-03-31)

    Args:
        target_date: A date or Timestamp to derive the FY from.
                     If None, falls back to the current date.

    Returns:
        tuple: (fy_start, fy_end) as pd.Timestamp objects.
    """
    if target_date is None:
        target_date = pd.Timestamp.now()
    else:
        target_date = pd.Timestamp(target_date)

    if target_date.month >= 4:
        fy_start_year = target_date.year
    else:
        fy_start_year = target_date.year - 1

    fy_start = pd.Timestamp(f'{fy_start_year}-04-01')
    fy_end = pd.Timestamp(f'{fy_start_year + 1}-03-31')
    return (fy_start, fy_end)


def get_fy_label(target_date):
    """Return Indian Financial Year label like 'FY_25-26' for the given date.

    Derives the FY dynamically: Apr-Dec → FY starts this calendar year,
    Jan-Mar → FY started the previous calendar year.
    If target_date is None, uses today's date.
    """
    fy_start, fy_end = derive_fy_bounds(target_date)
    start_yy = fy_start.year % 100
    end_yy = fy_end.year % 100
    return f'FY_{start_yy:02d}-{end_yy:02d}'


# Use config paths instead of hardcoded relative paths
backend_data_dir = config.BACKEND_DATA_DIR
cache_dir = config.DB_CACHE_DIR


def get_file_hash(file_path):
    """Get MD5 hash of file for cache invalidation (only first 1MB for speed).

    Delegates to ``compute_file_hash`` from ``services.excel_reader`` which
    reads from disk in chunks, avoiding loading the entire file into memory.
    """
    return compute_file_hash(file_path)


def insert_backup_column(df, col_name, values, desired_index=80):
    # Insert at CC (1-based 81 -> 0-based 80) when possible; otherwise append.
    if col_name in df.columns:
        df[col_name] = values
        return
    insert_at = desired_index if desired_index <= len(df.columns) else len(df.columns)
    df.insert(insert_at, col_name, values)


def extract_dpd_buckets(df, column_name):
    """
    Extract unique DPD bucket values from a DataFrame and map to standard names.

    This handles cases where PAR files have prefixed values like "1: 1-30" or "2: 31-60"
    instead of plain "1-30" or "31-60".

    Args:
        df: DataFrame containing the DPD column
        column_name: Name of the DPD bucket column

    Returns:
        dict: Mapping of standard bucket names to actual values found in the data
              e.g., {"1-30": "1: 1-30", "31-60": "2: 31-60", ...}
    """
    if column_name not in df.columns:
        logging.warning(f"Column '{column_name}' not found in DataFrame")
        return {}

    unique_values = df[column_name].dropna().unique().tolist()
    bucket_mapping = {}

    # Standard bucket patterns to look for
    patterns = [
        ("1-30", "1-30"),
        ("31-60", "31-60"),
        ("61-90", "61-90"),
        ("91-120", "91-120"),
        ("121-180", "121-180"),
        ("181-365", "181-365"),
        (">365", ">365"),
        ("365+", "365+"),  # Alternative format
    ]

    for standard_name, pattern in patterns:
        for val in unique_values:
            val_str = str(val)
            if pattern in val_str:
                bucket_mapping[standard_name] = val_str
                logging.debug(f"Mapped bucket '{standard_name}' -> '{val_str}'")
                break

    logging.info(f"Extracted {len(bucket_mapping)} DPD bucket mappings")
    return bucket_mapping


# Headers to add
headers_to_add = ["Collection", "Collection Date", "DPD Group", "Partial Amount", "DPD Group - Last Month", "Loan Status - Last Month", "installment - collected amt", "installment - collected value"]

# Allow-list for DPD column names that may be interpolated into SQL
ALLOWED_DPD_COLUMNS = frozenset([
    'Days Group', 'Days group', 'DaysGroup', 'Daysgroup',
    'DPD Group', 'DPD Days', 'DPDDays'
])


COLUMN_ALIASES = {
    'Trxdate': ['Trxdate', 'Trx Date', 'Transaction Date', 'TransactionDate', 'TRXDATE', 'Trx_Date', 'Transaction_Date'],
    'AccountID': ['AccountID', 'Account ID', 'Account_Id', 'AccountId'],
    'CollectionTotal': ['CollectionTotal', 'Collection Total', 'Collection_Total'],
    'ReverseTotal': ['ReverseTotal', 'Reverse Total', 'Reverse_Total'],
}

_DEMAND_COL_ALIASES = {
    'Account ID': ['Account ID', 'AccountID', 'Account_Id', 'AccountId'],
    'No of Regular Demand': ['No of Regular Demand', 'No. of Regular Demand', 'NoOfRegularDemand', 'No. Of Regular Demand'],
    'Meeting Date': ['Meeting Date', 'Meeting_Date', 'MeetingDate'],
    'Product Name': ['Product Name', 'Product_Name', 'ProductName', 'Product'],
    'Loan Date': ['Loan Date', 'Loan_Date', 'LoanDate'],
    'Regular Demand': ['Regular Demand', 'Regular_Demand', 'RegularDemand'],
    'Installment Amount': ['Installment Amount', 'Installment_Amount', 'InstallmentAmount'],
    'Cumulative Demand': ['Cumulative Demand', 'Cumulative_Demand', 'CumulativeDemand'],
    'No of Cumulative': ['No of Cumulative', 'No. of Cumulative', 'NoOfCumulative', 'No. Of Cumulative'],
    'installment - collected value': ['installment - collected value', 'installment-collected value', 'installment_collected_value'],
    'installment - collected amt': ['installment - collected amt', 'installment-collected amt', 'installment_collected_amt'],
    'Partial Amount': ['Partial Amount', 'Partial_Amount', 'PartialAmount'],
}


_REQUIRED_COLS = {
    'collection': ['AccountID', 'CollectionTotal', 'ReverseTotal', 'Trxdate'],
    'demand': ['Account ID', 'No of Regular Demand', 'Meeting Date', 'Product Name', 'Loan Date', 'Regular Demand'],
    'par': ['AccountID'],
}


def _validate_required_columns(df, label):
    """Raise ValueError with a clear message if required columns are missing.

    Lists the missing columns and the actual columns found in the file so
    the user knows exactly what to rename. Detects summary-style reports
    and warns the user they need the raw transaction dump instead.
    """
    required = _REQUIRED_COLS.get(label, [])
    missing = [c for c in required if c not in df.columns]
    if not missing:
        return
    actual = ', '.join(str(c) for c in df.columns)

    # Detect summary report patterns (region/division/branch-wise aggregated reports)
    actual_lower = actual.lower()
    summary_markers = ['region name', 'division name', 'branch name', 'emp id name',
                       'on date demand', 'regular demand vs collection', '1-30 dpd',
                       '31-60 dpd', 'pnpa', 'collection %', 'ftod']
    if any(m in actual_lower for m in summary_markers):
        raise ValueError(
            f"{label.upper()} file appears to be a SUMMARY/REPORT (region/branch/employee-wise aggregated data). "
            f"The EOD processor needs the RAW TRANSACTION DUMP with columns: {', '.join(required)}. "
            f"Found columns in your file: {actual}. "
            f"Please download the raw collection transaction file (account-level rows) from your core banking system."
        )

    raise ValueError(
        f"{label.upper()} file missing required column(s): {', '.join(missing)}. "
        f"Found columns: {actual}. "
        f"Please rename the columns in your Excel file to match the expected names."
    )


def _normalize_columns(df, label):
    """Normalize known column aliases to their canonical names.

    Collection files often use 'Transaction Date' instead of 'Trxdate',
    'Account ID' instead of 'AccountID', etc. This renames them so the
    downstream DuckDB and Pandas paths always see the canonical names.
    """
    if label in ('collection', 'coll'):
        aliases = COLUMN_ALIASES
    elif label == 'demand':
        aliases = _DEMAND_COL_ALIASES
    else:
        aliases = {
            k: v for k, v in COLUMN_ALIASES.items() if k in ('AccountID',)
        }
    renames = {}
    for canonical, candidates in aliases.items():
        if canonical in df.columns:
            continue
        found = find_column(df, *candidates)
        if found and found != canonical:
            renames[found] = canonical

    # Fallback: for Trxdate, if still missing, look for any column containing "date"
    # (but exclude "Collection Date" which is an output column name).
    if label in ('collection', 'coll') and 'Trxdate' not in df.columns and 'Trxdate' not in renames.values():
        for col in df.columns:
            if 'date' in str(col).lower() and str(col).lower() != 'collection date':
                renames[col] = 'Trxdate'
                logging.info(f"Column normalization fallback: '{col}' -> 'Trxdate'")
                break

    if renames:
        logging.info(f"Column normalization for {label}: {renames}")
        df.rename(columns=renames, inplace=True)
    _validate_required_columns(df, label)
    return df


def _ensure_parquet_cache(file_path, label, usecols=None):
    """Ensure a Parquet cache exists for the given file. Returns the Parquet path.

    If the Parquet cache already exists (based on file hash), returns immediately.
    Otherwise reads the Excel file, writes to Parquet, frees the DataFrame, and
    returns the cache path.
    """
    file_path = Path(file_path)
    file_hash = compute_file_hash(file_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = cache_dir / f"daily_{label}_cache_{file_hash}.parquet"

    if parquet_path.exists():
        logging.info(f"DuckDB-first path: registered {label} Parquet directly (no pandas intermediate)")
        return parquet_path

    logging.info(f"Creating Parquet cache for {label} (hash: {file_hash})")
    if usecols is not None:
        try:
            df = smart_read_excel(str(file_path), usecols=usecols)
        except (ValueError, KeyError):
            df = smart_read_excel(str(file_path))
    else:
        df = smart_read_excel(str(file_path))
    # Normalize column aliases (e.g. 'Transaction Date' → 'Trxdate')
    df = _normalize_columns(df, label)
    # Cast object columns to str to avoid ArrowTypeError on mixed-type columns (e.g. GroupName)
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].astype(str)
    df.to_parquet(parquet_path, index=False)
    del df
    gc_checkpoint("parquet-cache-conversion")
    logging.info(f"Cached {label} to: cache/{parquet_path.name}")
    return parquet_path


def _safe_fillna(df):
    """Fill NaN/NA values with '' for STRING columns only.

    Numeric columns (int, float) are left untouched — the XLSX writer handles
    NaN natively by writing blank cells.  Converting numeric columns to object
    dtype destroys type information, causing numbers to appear as text in Excel
    and breaking pivot tables.

    Only object/string columns get NaN→'' replacement.
    """
    for col in df.columns:
        if pd.api.types.is_integer_dtype(df[col]) or pd.api.types.is_float_dtype(df[col]):
            continue  # leave numeric columns as-is; writer handles NaN
        mask = df[col].isna()
        if not mask.any():
            continue
        df.loc[mask, col] = ''
    return df


def _detect_dpd_column(parquet_path):
    """Detect the DPD column name from Parquet schema without loading data.

    Tries pyarrow first (zero-copy schema read), falls back to DuckDB DESCRIBE.
    Returns the first column name matching ALLOWED_DPD_COLUMNS.
    """
    import duckdb as _ddb

    try:
        import pyarrow.parquet as pq
        col_names = pq.read_schema(parquet_path).names
    except Exception:
        rows = _ddb.execute(
            "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?))",
            [str(parquet_path)]
        ).fetchall()
        col_names = [r[0] for r in rows]

    for name in col_names:
        if name in ALLOWED_DPD_COLUMNS:
            return name
    raise ValueError(f"No recognized DPD column found in {parquet_path}. Available: {col_names}")


def _resolve_demand_source(con, demand_file, force_uploaded=False):
    """Determine whether to use Demand_Master table or uploaded demand file.

    Returns a SQL fragment: either 'Demand_Master' (table name) or a
    read_parquet('path') expression for use in the FROM clause.

    When force_uploaded=True, always use the uploaded file (e.g. for month-end
    processing where the DB may have a different month's demand).
    """
    if not force_uploaded:
        try:
            count = con.execute("SELECT COUNT(*) FROM Demand_Master").fetchone()[0]
            if count > 0:
                logging.info(f"Using Demand_Master from DB ({count} rows)")
                return "Demand_Master"
        except Exception:
            pass

    logging.info(f"Using uploaded Demand file: {demand_file}")
    demand_parquet = _ensure_parquet_cache(demand_file, "demand")
    return f"read_parquet('{demand_parquet}')"


def _is_month_end_date(target_date, force_regular_rules=False):
    """Return True when target_date is the last calendar day of its month.

    Month-end triggers the month-end report rules (PNPA drops the
    "Loan Status - Last Month = Active Loan" filter, Regular Collection
    excludes both 1-30 and 31-60). When force_regular_rules is True the
    detection collapses to False so the caller computes demand with the
    regular daily-report rules regardless of the date.
    """
    if force_regular_rules:
        return False
    import calendar
    return target_date.day == calendar.monthrange(target_date.year, target_date.month)[1]


def _compute_precomputed_sheets(df, target_date, force_regular_rules=False, ondate_next_date=None,
                                pnpa_always_active=False):
    """Pre-compute all aggregations needed for district/branch sheet generation.

    Returns a dict of {'_precomp': DataFrame} with one row per
    (filter_type, filter_value, group_value, scope, product) combination.
    """
    import numpy as np

    t0 = time.perf_counter()
    logging.info("PRECOMP: Starting pre-computation of aggregation sheets")

    # Parse Meeting Date for comparisons
    meeting_dt = parse_date_column(df['Meeting Date'])
    meeting_dt_clean = meeting_dt.dt.normalize()
    target_date_clean = pd.Timestamp(target_date).normalize()

    # Date masks
    first_of_month_clean = target_date_clean.replace(day=1)
    ftod_mask = (meeting_dt_clean >= first_of_month_clean) & (meeting_dt_clean <= target_date_clean)

    # Safety: if ftod_mask matches nothing, target_date month doesn't match data.
    # Try each month present in the data (most rows wins) to find the right one.
    if ftod_mask.sum() == 0 and meeting_dt_clean.notna().any():
        valid = meeting_dt_clean.dropna()
        month_counts = valid.dt.to_period('M').value_counts()
        if len(month_counts) > 0:
            best_period = month_counts.idxmax()
            import calendar
            last_day = calendar.monthrange(best_period.year, best_period.month)[1]
            corrected = best_period.to_timestamp().to_pydatetime().replace(day=last_day)
            logging.warning(
                f"PRECOMP: target_date {target_date.strftime('%Y-%m-%d')} "
                f"matched 0 Meeting Date rows — auto-correcting to "
                f"{corrected.strftime('%Y-%m-%d')} (month with most data: {month_counts.iloc[0]} rows)"
            )
            target_date = corrected
            target_date_clean = pd.Timestamp(target_date).normalize()
            first_of_month_clean = target_date_clean.replace(day=1)
            ftod_mask = (meeting_dt_clean >= first_of_month_clean) & (meeting_dt_clean <= target_date_clean)
            logging.info(f"PRECOMP: After correction, ftod_mask matches {ftod_mask.sum()} rows")

    target_date_mask = meeting_dt_clean == target_date_clean

    # Next business day. Default = target_date + 1 (unchanged for EOD/daily).
    # For the HOURLY report, ondate_next_date pins the On-Date to the generation
    # date (today) instead of max(Meeting Date) which is month-end — otherwise the
    # On-Date sheet shows month-end+1 (a future date with no data).
    if ondate_next_date is not None:
        next_day_clean = pd.Timestamp(ondate_next_date).normalize()
    else:
        next_day_clean = target_date_clean + pd.Timedelta(days=1)
    next_day_mask = meeting_dt_clean == next_day_clean

    # Day after next (next_day + 1) — feeds the OverAll On-Date sheet's right block.
    next_day2_clean = next_day_clean + pd.Timedelta(days=1)
    next_day2_mask = meeting_dt_clean == next_day2_clean

    # DPD Group classification
    dpd = df['DPD Group'].fillna('').astype(str)
    is_130 = dpd.str.contains('1-30', na=False)

    # DPD Days column — falls back to DPD Group if DPD Days absent
    dpd_days = df.get('DPD Days', df.get('DPD Group', pd.Series('', index=df.index))).fillna('').astype(str)
    # PNPA uses Last Month DPD (consistent with 1-30 and 31-60 buckets)
    dpd_last_raw = df.get('DPD Group - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
    is_61_90 = dpd_last_raw.str.contains('61-90', na=False)

    # Last Month columns
    dpd_last = df.get('DPD Group - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
    status_last = df.get('Loan Status - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
    is_last_130 = dpd_last.str.contains('1-30', na=False)
    is_last_3160 = dpd_last.str.contains('31-60', na=False)
    is_last_active = status_last == 'Active Loan'
    is_last_npa = status_last == 'NPA'
    dpd_last_not_0days = dpd_last != '0 Days'

    # DPD Group blank detection (for NPA closure)
    dpd_is_blank = (dpd == '') | (dpd == '(blank)') | dpd.isna()
    dpd_not_blank = ~dpd_is_blank

    # Product column
    product_col = df['Product Name'].fillna('').astype(str)

    # Numeric columns - ensure numeric
    reg_demand = pd.to_numeric(df.get('Regular Demand', 0), errors='coerce').fillna(0)
    collection = pd.to_numeric(df.get('Collection', 0), errors='coerce').fillna(0)
    no_reg_demand = pd.to_numeric(df.get('No of Regular Demand', 0), errors='coerce').fillna(0)
    no_cumulative = pd.to_numeric(df.get('No of Cumulative', 0), errors='coerce').fillna(0)
    cumulative_demand = pd.to_numeric(df.get('Cumulative Demand', 0), errors='coerce').fillna(0)
    inst_coll_val = pd.to_numeric(df.get('installment - collected value', 0), errors='coerce').fillna(0)

    # FY flag
    loan_date_flag = pd.to_numeric(df.get('Loan Date', 0), errors='coerce').fillna(0)

    # On-date base mask: No of Regular Demand = 1 AND Regular Demand != 0
    od_base = (no_reg_demand == 1) & (reg_demand != 0)

    # Partial Amount column
    partial_amt = df.get('Partial Amount', pd.Series('', index=df.index)).fillna('').astype(str)

    # Officer Name for EmpID rows
    officer_name_col = df.get('Officer Name', pd.Series('', index=df.index)).fillna('').astype(str)

    # Pre-compute all metric source columns to avoid repeated masking
    # Regular (FTOD, exclude 1-30 for collection)
    # Demand: SUM No of Regular Demand where FTOD (DPD Days=All, DPD Group=All)
    # Collection: COUNT of Collection where FTOD & DPD Group excludes 1-30
    # Amount Demand: SUM Regular Demand where FTOD
    # Amount Collection: SUM Collection where FTOD & DPD Group excludes 1-30

    def _text_col(name, fallback=''):
        value = df.get(name, fallback)
        if isinstance(value, pd.Series):
            return value.astype('object').where(value.notna(), '').astype(str).str.strip()
        return pd.Series(fallback, index=df.index).astype(str).str.strip()

    products = ['ALL', 'IGL', 'FIG', 'VVY']
    scopes = ['OA', 'FY']

    # For each row, compute metric values that will be aggregated
    # We'll build a working DataFrame with all needed metric columns
    w = pd.DataFrame(index=df.index)
    w['_ALL'] = 'ALL'  # Dummy column for unfiltered OverAll-level groupings
    # Region: merge AP + Telangana at region level only
    raw_region = _text_col('Region')
    w['Region'] = raw_region.replace({'ANDRA PRADESH': 'AP & TELANGANA', 'TELANGANA': 'AP & TELANGANA'})
    w['Division'] = _text_col('Division')
    w['Area'] = _text_col('Area') if 'Area' in df.columns else _text_col('District')
    w['BranchName'] = _text_col('BranchName')
    w['Emp ID'] = _text_col('Emp ID')
    w['Officer Name'] = officer_name_col.astype('object').where(officer_name_col.notna(), '').astype(str).str.strip()
    w['Product Name'] = product_col.astype('object').where(product_col.notna(), '').astype(str).str.strip()
    w['Loan Date'] = loan_date_flag

    # ---- COUNT-based metrics ----
    # Regular demand (FTOD, all DPD)
    w['reg_demand'] = np.where(ftod_mask, no_reg_demand, 0)
    # Total portfolio demand (unfiltered by FTOD - used in Branch+Officer section)
    w['reg_demand_total'] = no_reg_demand
    # Regular collection: Sum(No of Regular Demand) where FTOD & DPD Group exclusion
    # VBA uses Sum of no_reg with DPD Group page filter (NOT Count of Collection)
    # Regular (vba_template.js): excludes DPD Group containing "1-30"
    # Month-end (vba_template_month_end.js): excludes "1-30" AND "31-60"
    has_collection = df['Collection'].notna() if 'Collection' in df.columns else pd.Series(False, index=df.index)
    is_130_group = dpd.str.contains('1-30', case=False, na=False)
    is_3160_group = dpd.str.contains('31-60', case=False, na=False)
    _is_month_end = _is_month_end_date(target_date, force_regular_rules)
    if _is_month_end:
        # Month-end: exclude both 1-30 and 31-60 from Regular Collection
        w['reg_collection'] = np.where(ftod_mask & ~is_130_group & ~is_3160_group, no_reg_demand, 0)
    else:
        # Regular: exclude only 1-30 from Regular Collection
        w['reg_collection'] = np.where(ftod_mask & ~is_130_group, no_reg_demand, 0)

    # Hourly VBA-matching collection: DPD Days="0 Days", DPD Group="1: 1-30",
    # Partial Amount="Full EMI Paid" (= Remark2 "Full Collected").
    # NO Meeting Date / FTOD filter — hourly VBA has no date filter on collections.
    # VBA Column D filter: DPD Days="0 Days" AND DPD Group="1: 1-30" (two separate fields).
    # DPD Days = current DPD status (from PAR), DPD Group = DPD bucket classification.
    # When DPD Days column exists separately, use both filters.
    # When DPD Days is absent (pandas EOD path), it falls back to DPD Group — making
    # "0 Days" ∩ "1-30" impossible. In that case, use DPD Group="1: 1-30" alone
    # (accounts in 1-30 bucket that fully paid = the VBA intent).
    dpd_days_val = df.get('DPD Days', df.get('DPD Group', pd.Series('', index=df.index))).fillna('').astype(str)
    is_0days = dpd_days_val == '0 Days'
    is_full_paid = partial_amt == 'Full EMI Paid'
    # reg_collection_display: DPD Group="1: 1-30" & has_collection, using no_reg_demand
    # (matches hourly VBA Column D which counts collected accounts in 1-30 bucket)
    w['reg_collection_display'] = np.where(is_130 & has_collection, no_reg_demand, 0)

    # 1-30 bucket
    # Demand: DPD Group-Last Month=1-30, Loan Status-Last Month=Active Loan (unchanged)
    mask_130_base = is_last_130 & is_last_active
    w['dem_130'] = np.where(mask_130_base, 1, 0)
    # Collection: VBA uses base mask + installment-collected value = 1
    # NO FTOD filter, NO current DPD Days filter (matches VBA GenerateBucket130)
    is_inst_full = inst_coll_val == 1
    w['col_130'] = np.where(mask_130_base & is_inst_full, 1, 0)

    # 31-60 bucket
    mask_3160_base = is_last_3160 & is_last_active
    w['dem_3160'] = np.where(mask_3160_base, 1, 0)
    # Collection: VBA uses base mask + installment-collected value = 1 (no FTOD)
    w['col_3160'] = np.where(mask_3160_base & is_inst_full, 1, 0)

    # PNPA (61-90)
    # Month-end (vba_template_month_end.js): no "Loan Status - Last Month" filter
    # Regular dates (vba_template.js): filter "Loan Status - Last Month" = "Active Loan"
    # The HOURLY report is intraday (not a month-end report), but its target_date
    # is max(Meeting Date) = month-end, which would wrongly drop the active filter
    # and make PNPA inconsistent with the 1-30/31-60 buckets (which always keep it).
    # pnpa_always_active forces the active filter so PNPA matches the other buckets.
    if _is_month_end and not pnpa_always_active:
        mask_pnpa_base = is_61_90
    else:
        mask_pnpa_base = is_61_90 & is_last_active
    w['pnpa_demand'] = np.where(mask_pnpa_base, 1, 0)
    # Collection: VBA uses DPD Days=61-90 base + installment-collected value = 1 (no FTOD)
    w['pnpa_collection'] = np.where(mask_pnpa_base & is_inst_full, 1, 0)

    # NPA (Loan Status-Last Month=NPA, DPD Group-Last Month excludes 0 Days)
    mask_npa_base = is_last_npa & dpd_last_not_0days
    w['npa_cases'] = np.where(mask_npa_base, 1, 0)
    # NPA Activation: VBA filters Loan Status-Last Month="NPA",
    # DPD Group-Last Month != "0 Days", DPD Group != blank, has Collection.
    w['npa_act_acc'] = np.where(mask_npa_base & dpd_not_blank & has_collection, 1, 0)
    w['npa_act_amt'] = np.where(mask_npa_base & dpd_not_blank & has_collection, collection, 0)
    # Closure (kept for backward compatibility but not displayed in main report)
    w['npa_clo_acc'] = np.where(mask_npa_base & dpd_is_blank & has_collection, 1, 0)
    w['npa_clo_amt'] = np.where(mask_npa_base & dpd_is_blank, collection, 0)

    # ---- HOURLY VBA collection columns ----
    # The hourly VBA (vba_code.js SyncAllColumns) uses:
    #   Data field: Count of "Collection as on" column (non-null rows)
    #   Filter: Remark2="Full Collected" (for D/H/L/P columns)
    #   CURRENT DPD Days for bucket classification
    # VBA clears Remark2 filter for NPA columns (T/U).
    remark2 = df.get('Remark2', pd.Series('', index=df.index)).fillna('').astype(str)
    is_remark2_full = remark2 == 'Full Collected'

    # Hourly Regular Collection (Col D): DPD Group="1: 1-30" AND has hourly Collection.
    # Uses SUM of no_reg_demand (not count of 1s) to match VBA pivot COUNT behavior
    # where some accounts contribute >1 to demand count.
    # No Remark2/Full filter — the VBA counts all collected accounts in the 1-30 bucket.
    w['hourly_reg_collection'] = np.where(is_130 & has_collection, no_reg_demand, 0)

    # Hourly 1-30 (Col H): same base as dem_130 (Last Month DPD=1-30 + Active) + Remark2=Full Collected
    w['hourly_col_130'] = np.where(mask_130_base & is_remark2_full & has_collection, 1, 0)

    # Hourly 31-60 (Col L): same base as dem_3160 (Last Month DPD=31-60 + Active) + Remark2=Full Collected
    w['hourly_col_3160'] = np.where(mask_3160_base & is_remark2_full & has_collection, 1, 0)

    # Hourly PNPA (Col P): same base as pnpa_demand (Last Month DPD=61-90 + Active) + Remark2=Full Collected
    w['hourly_pnpa_collection'] = np.where(mask_pnpa_base & is_remark2_full & has_collection, 1, 0)

    # Hourly NPA (Col T/U): Loan Status-Last Month="NPA", Remark2 cleared,
    # Count/Sum of Collection. No DPD or Remark2 restrictions.
    w['npa_hourly_acc'] = np.where(is_last_npa & has_collection, 1, 0)
    w['npa_hourly_amt'] = np.where(is_last_npa & has_collection, collection, 0)

    # On-Date Demand (Meeting Date = target, No of Regular Demand=1, Regular Demand!=0)
    w['od_demand'] = np.where(target_date_mask & od_base, 1, 0)
    # On-Date Collection (same + Partial Amount = "Full EMI Paid")
    w['od_collection'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), 1, 0)
    # Next day demand
    w['od_demand_next'] = np.where(next_day_mask & od_base, 1, 0)
    # Next day collection (same + Partial Amount = "Full EMI Paid"). Mirrors
    # od_collection but for next_day_mask — feeds the On-Date sheet COLLECTION.
    w['od_collection_next'] = np.where(next_day_mask & od_base & (partial_amt == 'Full EMI Paid'), 1, 0)
    # Day-after-next (target_date + 2) demand/collection — TOMORROW column of the
    # OverAll On-Date sheet. Additive; nothing else reads these.
    w['od_demand_next2'] = np.where(next_day2_mask & od_base, 1, 0)
    w['od_collection_next2'] = np.where(next_day2_mask & od_base & (partial_amt == 'Full EMI Paid'), 1, 0)

    # ---- AMOUNT-based metrics ----
    # Regular Amount (FTOD)
    w['reg_demand_amt'] = np.where(ftod_mask, reg_demand, 0)
    if _is_month_end:
        w['reg_collection_amt'] = np.where(ftod_mask & ~is_130_group & ~is_3160_group, collection, 0)
    else:
        w['reg_collection_amt'] = np.where(ftod_mask & ~is_130_group, collection, 0)

    # 1-30 Amount (VBA applies FTOD to demand amounts, no FTOD on collection)
    w['dem_130_amt'] = np.where(ftod_mask & mask_130_base, cumulative_demand, 0)
    w['col_130_amt'] = np.where(mask_130_base & is_inst_full, collection, 0)

    # 31-60 Amount (FTOD on demand)
    w['dem_3160_amt'] = np.where(ftod_mask & mask_3160_base, cumulative_demand, 0)
    w['col_3160_amt'] = np.where(mask_3160_base & is_inst_full, collection, 0)

    # PNPA Amount (FTOD on demand)
    w['pnpa_demand_amt'] = np.where(ftod_mask & mask_pnpa_base, cumulative_demand, 0)
    w['pnpa_collection_amt'] = np.where(mask_pnpa_base & is_inst_full, collection, 0)

    # On-Date Amount
    w['od_demand_amt'] = np.where(target_date_mask & od_base, reg_demand, 0)
    w['od_collection_amt'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), collection, 0)

    # Total portfolio demand amount (unfiltered - used in Branch+Officer section)
    w['reg_demand_total_amt'] = reg_demand

    # ---- FY current-month bucket metrics ----
    # The FY (Financial-Year) sheets cover newly-disbursed loans, which have no
    # prior-month delinquency history — so the normal last-month bucket masks
    # (is_last_130 etc.) are always empty for them. For FY scope we therefore
    # recompute the 1-30 / 31-60 / PNPA / NPA buckets on the CURRENT month
    # (DPD Group + CurrentLoan Status) so the sheet shows real movement.
    # These columns are aggregated only for scope == 'FY' (see _metric_cols_fy);
    # the OverAll/OA sheets keep the last-month basis unchanged.
    cur_status = _text_col('CurrentLoan Status')
    is_cur_active = cur_status == 'Active Loan'
    is_cur_npa = cur_status == 'NPA'
    is_cur_6190 = dpd.str.contains('61-90', case=False, na=False)
    dpd_cur_not_0 = dpd_not_blank & (dpd != '0 Days')

    cur_mask_130 = is_130_group & is_cur_active
    cur_mask_3160 = is_3160_group & is_cur_active
    cur_mask_pnpa = is_cur_6190 if _is_month_end else (is_cur_6190 & is_cur_active)
    cur_mask_npa = is_cur_npa & dpd_cur_not_0

    w['dem_130_fy'] = np.where(cur_mask_130, 1, 0)
    w['col_130_fy'] = np.where(cur_mask_130 & is_inst_full, 1, 0)
    w['dem_3160_fy'] = np.where(cur_mask_3160, 1, 0)
    w['col_3160_fy'] = np.where(cur_mask_3160 & is_inst_full, 1, 0)
    w['pnpa_demand_fy'] = np.where(cur_mask_pnpa, 1, 0)
    w['pnpa_collection_fy'] = np.where(cur_mask_pnpa & is_inst_full, 1, 0)
    # HOURLY collection on the current-month basis — without these the hourly FY
    # sheet shows 0 bucket collection (the last-month hourly_* columns are empty
    # for newly-disbursed FY loans). Mirrors hourly_col_130 = mask & Full Collected.
    w['hourly_col_130_fy'] = np.where(cur_mask_130 & is_remark2_full & has_collection, 1, 0)
    w['hourly_col_3160_fy'] = np.where(cur_mask_3160 & is_remark2_full & has_collection, 1, 0)
    w['hourly_pnpa_collection_fy'] = np.where(cur_mask_pnpa & is_remark2_full & has_collection, 1, 0)
    w['npa_cases_fy'] = np.where(cur_mask_npa, 1, 0)
    w['npa_act_acc_fy'] = np.where(cur_mask_npa & dpd_not_blank & has_collection, 1, 0)
    w['npa_act_amt_fy'] = np.where(cur_mask_npa & dpd_not_blank & has_collection, collection, 0)
    w['npa_clo_acc_fy'] = np.where(cur_mask_npa & dpd_is_blank & has_collection, 1, 0)
    w['npa_clo_amt_fy'] = np.where(cur_mask_npa & dpd_is_blank, collection, 0)
    # amount versions (FTOD on demand, mirroring the last-month amount metrics)
    w['dem_130_amt_fy'] = np.where(ftod_mask & cur_mask_130, cumulative_demand, 0)
    w['col_130_amt_fy'] = np.where(cur_mask_130 & is_inst_full, collection, 0)
    w['dem_3160_amt_fy'] = np.where(ftod_mask & cur_mask_3160, cumulative_demand, 0)
    w['col_3160_amt_fy'] = np.where(cur_mask_3160 & is_inst_full, collection, 0)
    w['pnpa_demand_amt_fy'] = np.where(ftod_mask & cur_mask_pnpa, cumulative_demand, 0)
    w['pnpa_collection_amt_fy'] = np.where(cur_mask_pnpa & is_inst_full, collection, 0)

    # Define the metric columns to aggregate
    # Count-based metrics (cols 6-21 in VBA's _precomp reader)
    count_metric_cols = [
        'reg_demand', 'reg_collection', 'reg_collection_display',
        'dem_130', 'col_130', 'dem_3160', 'col_3160',
        'pnpa_demand', 'pnpa_collection',
        'npa_cases', 'npa_act_acc', 'npa_act_amt', 'npa_clo_acc', 'npa_clo_amt',
        'hourly_reg_collection',
        'hourly_col_130', 'hourly_col_3160', 'hourly_pnpa_collection',
        'npa_hourly_acc', 'npa_hourly_amt',
        'od_demand', 'od_collection', 'od_demand_next', 'od_collection_next',
        'od_demand_next2', 'od_collection_next2',
        'reg_demand_total',
    ]
    # Amount-based metrics (cols 23+ in VBA, after officer_name at col 22)
    amount_metric_cols = [
        'reg_demand_amt', 'reg_collection_amt',
        'dem_130_amt', 'col_130_amt', 'dem_3160_amt', 'col_3160_amt',
        'pnpa_demand_amt', 'pnpa_collection_amt',
        'od_demand_amt', 'od_collection_amt',
        'reg_demand_total_amt',
    ]
    metric_cols = count_metric_cols + amount_metric_cols

    # For FY scope, swap the delinquency-bucket metrics to their current-month
    # (_fy) counterparts. Same column order, so the aggregated frame is renamed
    # back to the standard names — the report builder is unchanged.
    _fy_bucket_swap = {
        'dem_130': 'dem_130_fy', 'col_130': 'col_130_fy',
        'dem_3160': 'dem_3160_fy', 'col_3160': 'col_3160_fy',
        'pnpa_demand': 'pnpa_demand_fy', 'pnpa_collection': 'pnpa_collection_fy',
        'hourly_col_130': 'hourly_col_130_fy', 'hourly_col_3160': 'hourly_col_3160_fy',
        'hourly_pnpa_collection': 'hourly_pnpa_collection_fy',
        'npa_cases': 'npa_cases_fy',
        'npa_act_acc': 'npa_act_acc_fy', 'npa_act_amt': 'npa_act_amt_fy',
        'npa_clo_acc': 'npa_clo_acc_fy', 'npa_clo_amt': 'npa_clo_amt_fy',
        'dem_130_amt': 'dem_130_amt_fy', 'col_130_amt': 'col_130_amt_fy',
        'dem_3160_amt': 'dem_3160_amt_fy', 'col_3160_amt': 'col_3160_amt_fy',
        'pnpa_demand_amt': 'pnpa_demand_amt_fy', 'pnpa_collection_amt': 'pnpa_collection_amt_fy',
    }
    _metric_cols_fy = [_fy_bucket_swap.get(c, c) for c in metric_cols]

    # Pre-compute the 8 (scope × product) subsets once — reused across all 12 filter_types
    _scope_base = {
        'OA': w,
        'FY': w[w['Loan Date'] == 1],
    }
    _subset_cache = {}
    for _sc in scopes:
        _sb = _scope_base[_sc]
        for _pr in products:
            _subset_cache[(_sc, _pr)] = _sb if _pr == 'ALL' else _sb[_sb['Product Name'] == _pr]

    results = []
    gt_rows = []

    # Group level definitions: (filter_type, filter_col, group_col, include_officer_name)
    # filter_type must be unique per grouping level so VBA can select the right rows.
    group_levels = [
        # OverAll sheets: unfiltered (_ALL) groupings for main summary + FY + product sections
        ('All_Region', '_ALL', 'Region', False),
        ('All_Division', '_ALL', 'Division', False),
        ('All_Area', '_ALL', 'Area', False),
        ('All_Branch', '_ALL', 'BranchName', False),
        ('All_EmpID', '_ALL', 'Emp ID', True),
        # Region sheets: drill into Division, Area, Branch
        ('Region_Division', 'Region', 'Division', False),
        ('Region_Area', 'Region', 'Area', False),
        ('Region_Branch', 'Region', 'BranchName', False),
        # Division sheets: drill into Area, Branch
        ('Division_Area', 'Division', 'Area', False),
        ('Division_Branch', 'Division', 'BranchName', False),
        # Area sheets: drill into Branch (was District)
        ('Area', 'Area', 'BranchName', False),
        # Branch sheets + officer assembly
        ('BranchName', 'BranchName', 'Emp ID', True),
    ]

    # Pre-build officer name lookup: Emp ID -> most common Officer Name
    officer_lookup = (
        w[w['Officer Name'] != '']
        .groupby('Emp ID')['Officer Name']
        .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0])
        .to_dict()
    )

    for filter_type, filter_col, group_col, include_officer in group_levels:
        for scope in scopes:
            scope_mask = pd.Series(True, index=w.index)
            if scope == 'FY':
                scope_mask = w['Loan Date'] == 1

            for product in products:
                prod_mask = pd.Series(True, index=w.index)
                if product != 'ALL':
                    prod_mask = w['Product Name'] == product

                base_mask = scope_mask & prod_mask
                subset = w[base_mask]

                if len(subset) == 0:
                    continue

                # Group by filter_col + group_col, sum metrics. FY scope uses the
                # current-month bucket columns, then renames them back to the
                # standard metric names so downstream code is unchanged.
                agg_cols = _metric_cols_fy if scope == 'FY' else metric_cols
                grouped = subset.groupby([filter_col, group_col])[agg_cols].sum()

                if len(grouped) == 0:
                    continue

                if scope == 'FY':
                    grouped.columns = metric_cols

                grouped = grouped.reset_index()

                # Add Grand Total per filter_value
                for fv, grp_df in grouped.groupby(filter_col):
                    # Build batch rows
                    batch = grp_df.copy()
                    batch['filter_type'] = filter_type
                    batch['filter_value'] = str(fv).strip()
                    batch['group_value'] = batch[group_col].astype(str).str.strip()
                    batch['scope'] = scope
                    batch['product'] = product
                    if include_officer:
                        batch['officer_name'] = batch[group_col].map(
                            lambda eid: officer_lookup.get(eid, '')
                        )
                    else:
                        batch['officer_name'] = ''

                    # Column order: 5 keys + 16 count metrics + officer_name + 8 amount metrics
                    # VBA reads A:V (22 cols): cols 1-5=keys, 6-21=count metrics, 22=officer_name
                    out_cols = ['filter_type', 'filter_value', 'group_value', 'scope',
                                'product'] + count_metric_cols + ['officer_name'] + amount_metric_cols
                    results.append(batch[out_cols])

                    # Grand Total row
                    totals = grp_df[metric_cols].sum()
                    gt = {
                        'filter_type': filter_type,
                        'filter_value': str(fv).strip(),
                        'group_value': 'Grand Total',
                        'scope': scope,
                        'product': product,
                        'officer_name': '',
                    }
                    for mc in metric_cols:
                        gt[mc] = totals[mc]
                    results.append(pd.DataFrame([gt]))

    if not results:
        logging.warning("PRECOMP: No results generated")
        return {}

    pc_df = pd.concat(results, ignore_index=True)
    logging.info(f"PRECOMP: Generated {len(pc_df)} rows in {time.perf_counter() - t0:.2f}s")
    return {'_precomp': pc_df}


def _write_excel_fast(df, output_file, precomputed_sheets=None):
    """Write XLSX by generating XML directly into a ZipFile.

    Bypasses xlsxwriter entirely — no Cell objects, no type validation,
    no dimension tracking. Just f-strings streamed into deflated ZIP.
    ~3-4x faster than xlsxwriter for large datasets (268K+ rows).
    """
    import zipfile
    import time as _t
    from xml.sax.saxutils import escape as _xesc
    import numpy as np

    t0 = _t.perf_counter()

    # ── Column letter cache (A..Z, AA..AZ, ...) ──
    def _col_letter(idx):
        s = ''
        idx += 1
        while idx > 0:
            idx, rem = divmod(idx - 1, 26)
            s = chr(65 + rem) + s
        return s

    # ── Shared strings table (built incrementally) ──
    _ss_map = {}
    _ss_list = []
    def _ss(val):
        try:
            return _ss_map[val]
        except KeyError:
            i = len(_ss_list)
            _ss_map[val] = i
            _ss_list.append(val)
            return i

    # ── Pre-detect object columns that are actually numeric ──
    # Some columns (e.g. Center ID, Collection) contain numeric values stored
    # as strings due to mixed types or fillna.  Detect these by sampling and
    # attempting float conversion.  If >50% of non-empty values are numeric,
    # treat the column as "coerce to number when possible".
    def _detect_numeric_object_cols(sdf):
        """Return set of column indices that are object dtype but mostly numeric."""
        coerce_cols = set()
        for c in range(len(sdf.columns)):
            if sdf.iloc[:, c].dtype.kind not in ('O', 'S', 'U'):
                continue  # already numeric or date
            # Sample up to 200 non-empty values
            sample = sdf.iloc[:min(1000, len(sdf)), c].dropna()
            sample = sample[sample != '']
            if len(sample) == 0:
                continue
            sample = sample.head(200)
            num_ok = 0
            for v in sample:
                if isinstance(v, (int, float, np.integer, np.floating)):
                    num_ok += 1
                elif isinstance(v, str):
                    try:
                        float(v)
                        num_ok += 1
                    except (ValueError, TypeError):
                        pass
            if num_ok > len(sample) * 0.5:
                coerce_cols.add(c)
        return coerce_cols

    # ── Sheets to write ──
    all_sheets = [('Sheet1', df, False)]
    if precomputed_sheets:
        for name, pc_df in precomputed_sheets.items():
            all_sheets.append((name, pc_df, True))
    n_sheets = len(all_sheets)

    with zipfile.ZipFile(str(output_file), 'w', zipfile.ZIP_DEFLATED) as zf:

        # ── Stream each sheet's XML ──
        for si, (sname, sdf, hidden) in enumerate(all_sheets):
            cols = sdf.columns.tolist()
            n_cols = len(cols)
            n_rows = len(sdf)
            cl = [_col_letter(c) for c in range(n_cols)]

            # Classify columns, extract raw data once
            is_num = []       # True = write as number always
            coerce_num = _detect_numeric_object_cols(sdf)  # set of col indices to coerce
            col_data = []
            for c in range(n_cols):
                s = sdf.iloc[:, c]
                if s.dtype.kind in ('i', 'f'):
                    is_num.append(True)
                    col_data.append(s.values)          # numpy array
                else:
                    is_num.append(False)
                    col_data.append(s.tolist())         # python list
            if coerce_num:
                coerced_names = [cols[c] for c in coerce_num]
                logging.info(f"    auto-coerce numeric-as-text cols: {coerced_names}")

            # Pre-compute row-number strings (avoids 47× int→str per row)
            rn_strs = [str(r + 2) for r in range(n_rows)]

            with zf.open(f'xl/worksheets/sheet{si+1}.xml', 'w') as f:
                f.write(
                    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    b'<sheetData>'
                )

                # Header row
                hdr = ''.join(
                    f'<c r="{cl[c]}1" t="s"><v>{_ss(str(cols[c]))}</v></c>'
                    for c in range(n_cols)
                )
                f.write(f'<row r="1">{hdr}</row>\n'.encode())

                # Data rows — batched encode+write every 500 rows
                BATCH = 500
                batch = []
                for r in range(n_rows):
                    rn = rn_strs[r]
                    cells = []
                    for c in range(n_cols):
                        ref = cl[c] + rn
                        if is_num[c]:
                            v = col_data[c][r]
                            if pd.isna(v):
                                cells.append(f'<c r="{ref}"/>')
                            else:
                                cells.append(f'<c r="{ref}"><v>{"%.16G" % v}</v></c>')
                        else:
                            v = col_data[c][r]
                            if v is None or (isinstance(v, str) and v == '') or pd.isna(v):
                                cells.append(f'<c r="{ref}"/>')
                            elif isinstance(v, (int, float, np.integer, np.floating)):
                                cells.append(f'<c r="{ref}"><v>{"%.16G" % v}</v></c>')
                            elif c in coerce_num and isinstance(v, str):
                                try:
                                    nv = float(v)
                                    if nv != nv:  # "nan" string → float NaN → blank
                                        cells.append(f'<c r="{ref}"/>')
                                    else:
                                        cells.append(f'<c r="{ref}"><v>{"%.16G" % nv}</v></c>')
                                except (ValueError, TypeError):
                                    cells.append(f'<c r="{ref}" t="s"><v>{_ss(v)}</v></c>')
                            else:
                                cells.append(f'<c r="{ref}" t="s"><v>{_ss(str(v))}</v></c>')
                    batch.append(f'<row r="{rn}">{"".join(cells)}</row>\n')
                    if len(batch) >= BATCH:
                        f.write(''.join(batch).encode())
                        batch = []
                if batch:
                    f.write(''.join(batch).encode())

                f.write(b'</sheetData></worksheet>')

            if si == 0:
                logging.info(f"    main sheet xml: {_t.perf_counter() - t0:.2f}s")

        # ── Shared strings XML ──
        t_ss = _t.perf_counter()
        with zf.open('xl/sharedStrings.xml', 'w') as f:
            uc = len(_ss_list)
            f.write(
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                f'count="{uc}" uniqueCount="{uc}">'.encode()
            )
            # Batch shared strings too
            ss_batch = []
            for s in _ss_list:
                ss_batch.append(f'<si><t>{_xesc(s)}</t></si>')
                if len(ss_batch) >= 2000:
                    f.write(''.join(ss_batch).encode())
                    ss_batch = []
            if ss_batch:
                f.write(''.join(ss_batch).encode())
            f.write(b'</sst>')
        logging.info(f"    shared strings: {_t.perf_counter() - t_ss:.2f}s ({uc} unique)")

        # ── Boilerplate XML ──
        # Content types
        ct_sheets = ''.join(
            f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(n_sheets)
        )
        zf.writestr('[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f'{ct_sheets}'
            '<Override PartName="/xl/sharedStrings.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        )

        # Root rels
        zf.writestr('_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '</Relationships>'
        )

        # Workbook
        wb_parts = []
        for i, (name, _, hidden) in enumerate(all_sheets):
            state = ' state="hidden"' if hidden else ''
            wb_parts.append(
                f'<sheet name="{_xesc(name)}" sheetId="{i+1}" r:id="rId{i+1}"{state}/>'
            )
        wb_sheets = ''.join(wb_parts)
        zf.writestr('xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{wb_sheets}</sheets></workbook>'
        )

        # Workbook rels
        wb_rels = ''.join(
            f'<Relationship Id="rId{i+1}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i+1}.xml"/>'
            for i in range(n_sheets)
        )
        zf.writestr('xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{wb_rels}'
            f'<Relationship Id="rId{n_sheets+1}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            f'Target="sharedStrings.xml"/>'
            f'<Relationship Id="rId{n_sheets+2}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            f'Target="styles.xml"/>'
            '</Relationships>'
        )

        # Minimal styles
        zf.writestr('xl/styles.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="1"><xf/></cellXfs>'
            '</styleSheet>'
        )

    logging.info(f"    total xlsx: {_t.perf_counter() - t0:.2f}s")


def _check_disk_space():
    """Log a warning if disk space is critically low (< 10% free)."""
    try:
        from services.cache_manager import get_disk_free_pct
        free_pct = get_disk_free_pct()
        if free_pct is not None and free_pct < 10:
            logging.warning(
                "LOW DISK SPACE: %.1f%% free. Processing may fail if "
                "disk fills up during Excel write.", free_pct
            )
    except Exception:
        pass


def _build_report_from_precomp(precomputed, output_file, target_date, df, sheets_dir=None):
    """Generate the formatted report Excel from precomputed data (replaces VBA).

    Returns the report file path on success, None on failure.
    """
    if not precomputed or '_precomp' not in precomputed or target_date is None:
        return None
    try:
        from services.report_builder import build_report
        report_path = Path(output_file).with_name(
            Path(output_file).stem + '_report.xlsx'
        )
        has_officer = 'Emp ID' in df.columns

        # Build per-employee data for the report's 'Employee Data' sheet
        # (sheet 2, all products combined). Reuses build_employee_report so
        # the numbers match the Employee Report. ('IL Reports' sheet 3 is
        # built inside build_report from the precomputed data.)
        employee_data = None
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as _td:
                _tmp = Path(_td) / 'emp_for_report.xlsx'
                if build_employee_report(df, target_date, _tmp) and _tmp.exists():
                    _x = pd.read_excel(_tmp, sheet_name=['IGL', 'FIG', 'VVY'])
                    _all = pd.concat([_x['IGL'], _x['FIG'], _x['VVY']],
                                     ignore_index=True)
                    _idc = ['Region', 'Division', 'Area', 'Branch', 'Emp ID']
                    _mc = [c for c in _all.columns
                           if c not in _idc + ['Officer Name']]
                    _g = _all.groupby(_idc, as_index=False)[_mc].sum()
                    _onm = (_all[_all['Officer Name'].astype(str) != '']
                            .groupby('Emp ID')['Officer Name'].first().to_dict())
                    _g['Officer Name'] = _g['Emp ID'].map(_onm).fillna('')
                    employee_data = _g
        except Exception as _emp_err:
            logging.warning(
                f"Report Employee Data sheet skipped "
                f"({type(_emp_err).__name__}: {_emp_err})"
            )

        build_report(precomputed['_precomp'], report_path, target_date, has_officer,
                     sheets_dir=sheets_dir, employee_data=employee_data)
        return report_path
    except Exception as e:
        logging.warning(f"Report generation failed ({type(e).__name__}: {e}), skipping")
        return None


def _build_report_with_excel_fallback(precomputed, output_file, target_date, df, sheets_dir=None, force_regular_rules=False):
    """Build the formatted report, retrying from the written XLSX if in-memory precomp failed."""
    report_path = _build_report_from_precomp(precomputed, output_file, target_date, df, sheets_dir=sheets_dir)
    if report_path or target_date is None:
        return report_path

    try:
        logging.info("Report fallback: rebuilding precomp from written EOD output")
        output_df = pd.read_excel(output_file, sheet_name='Sheet1')
        fallback_precomputed = _compute_precomputed_sheets(
            output_df,
            target_date,
            force_regular_rules=force_regular_rules,
        )
        return _build_report_from_precomp(
            fallback_precomputed,
            output_file,
            target_date,
            output_df,
            sheets_dir=sheets_dir,
        )
    except Exception as e:
        logging.warning(f"Report fallback failed ({type(e).__name__}: {e}), skipping")
        return None


def build_employee_report(df, target_date, output_path, par_file=None, force_regular_rules=False):
    """Build a raw employee-level report with 3 sheets (IGL, FIG, VVY).

    Each sheet is a flat table: Region, District, Branch, Emp ID, Officer Name,
    plus all B+O metrics (Regular, 1-30, 31-60, PNPA, NPA, On-Date) — counts and amounts.
    No formatting, no colours — just raw data.

    Returns the output path on success, None on failure.
    """
    import numpy as np

    t0 = time.perf_counter()
    logging.info("EMPLOYEE REPORT: Starting employee report generation")

    try:
        # --- Same mask/metric logic as _compute_precomputed_sheets ---
        meeting_dt = parse_date_column(df['Meeting Date'])
        meeting_dt_clean = meeting_dt.dt.normalize()
        target_date_clean = pd.Timestamp(target_date).normalize()
        first_of_month_clean = target_date_clean.replace(day=1)
        ftod_mask = (meeting_dt_clean >= first_of_month_clean) & (meeting_dt_clean <= target_date_clean)

        # Safety: if ftod_mask matches nothing, target_date month doesn't match data.
        # Try each month present in the data (most rows wins) to find the right one.
        if ftod_mask.sum() == 0 and meeting_dt_clean.notna().any():
            # Group by year-month, pick the month with most rows
            valid = meeting_dt_clean.dropna()
            month_counts = valid.dt.to_period('M').value_counts()
            if len(month_counts) > 0:
                best_period = month_counts.idxmax()
                # Use last day of that month
                corrected = best_period.to_timestamp('M').to_pydatetime()  # last day of month
                # Actually get last day: go to next month minus 1 day
                import calendar
                last_day = calendar.monthrange(best_period.year, best_period.month)[1]
                corrected = corrected.replace(day=last_day)
                logging.warning(
                    f"EMPLOYEE REPORT: target_date {target_date.strftime('%Y-%m-%d')} "
                    f"matched 0 Meeting Date rows — auto-correcting to "
                    f"{corrected.strftime('%Y-%m-%d')} (month with most data: {month_counts.iloc[0]} rows)"
                )
                target_date = corrected
                target_date_clean = pd.Timestamp(target_date).normalize()
                first_of_month_clean = target_date_clean.replace(day=1)
                ftod_mask = (meeting_dt_clean >= first_of_month_clean) & (meeting_dt_clean <= target_date_clean)
                logging.info(f"EMPLOYEE REPORT: After correction, ftod_mask matches {ftod_mask.sum()} rows")

        target_date_mask = meeting_dt_clean == target_date_clean
        next_day_clean = target_date_clean + pd.Timedelta(days=1)
        next_day_mask = meeting_dt_clean == next_day_clean

        # Find DPD Group column dynamically
        _dpd_col = 'DPD Group'
        if _dpd_col not in df.columns:
            for _cand in list(ALLOWED_DPD_COLUMNS) + ['Due Days']:
                if _cand in df.columns:
                    _dpd_col = _cand
                    break
        dpd = df.get(_dpd_col, pd.Series('', index=df.index)).fillna('').astype(str)
        is_130 = dpd.str.contains('1-30', na=False)
        dpd_days = df.get('DPD Days', df.get('DPD Group', pd.Series('', index=df.index))).fillna('').astype(str)

        dpd_last = df.get('DPD Group - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
        status_last = df.get('Loan Status - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
        is_61_90 = dpd_last.str.contains('61-90', na=False)
        is_last_130 = dpd_last.str.contains('1-30', na=False)
        is_last_3160 = dpd_last.str.contains('31-60', na=False)
        is_last_active = status_last == 'Active Loan'
        is_last_npa = status_last == 'NPA'
        dpd_last_not_0days = dpd_last != '0 Days'
        dpd_is_blank = (dpd == '') | (dpd == '(blank)') | dpd.isna()
        dpd_not_blank = ~dpd_is_blank

        has_collection = df['Collection'].notna() if 'Collection' in df.columns else pd.Series(False, index=df.index)

        # Product Name — derive from PAR if missing in demand
        if 'Product Name' in df.columns:
            product_col = df['Product Name'].fillna('').astype(str)
        else:
            product_col = pd.Series('IGL', index=df.index)
            if par_file is not None:
                try:
                    _pp = pd.read_excel(par_file, engine='calamine',
                        usecols=lambda c: c.strip() in ['AccountID','ProductID','Product Name','Product'])
                    _pp.columns = _pp.columns.str.strip()
                    if 'Product Name' not in _pp.columns and 'Product' in _pp.columns:
                        _pp['Product Name'] = _pp['Product']
                    if 'Product Name' not in _pp.columns and 'ProductID' in _pp.columns:
                        pid = _pp['ProductID'].fillna('').astype(str)
                        _pp['Product Name'] = pid.apply(lambda x: 'FIG' if (x.startswith('6') or 'FIG' in x.upper()) else 'IGL')
                    acct_col = 'Account ID' if 'Account ID' in df.columns else 'AccountID'
                    if acct_col in df.columns and 'AccountID' in _pp.columns:
                        m = dict(zip(_pp['AccountID'].astype(str), _pp['Product Name'].astype(str)))
                        product_col = df[acct_col].astype(str).map(m).fillna('IGL')
                        product_col = product_col.apply(lambda x: x if x in ('IGL','FIG') else 'VVY')
                        logging.info(f"EMPLOYEE REPORT: Product from PAR — IGL={(product_col=='IGL').sum()}, FIG={(product_col=='FIG').sum()}, VVY={(product_col=='VVY').sum()}")
                    del _pp
                except Exception as e:
                    logging.warning(f"EMPLOYEE REPORT: Product derive failed: {e}")

        reg_demand = pd.to_numeric(df.get('Regular Demand', 0), errors='coerce').fillna(0)
        collection = pd.to_numeric(df.get('Collection', 0), errors='coerce').fillna(0)
        no_reg_demand = pd.to_numeric(df.get('No of Regular Demand', 0), errors='coerce').fillna(0)
        no_cumulative = pd.to_numeric(df.get('No of Cumulative', 0), errors='coerce').fillna(0)
        cumulative_demand = pd.to_numeric(df.get('Cumulative Demand', 0), errors='coerce').fillna(0)
        inst_coll_val = pd.to_numeric(df.get('installment - collected value', 0), errors='coerce').fillna(0)
        od_base = (no_reg_demand == 1) & (reg_demand != 0)
        partial_amt = df.get('Partial Amount', pd.Series('', index=df.index)).fillna('').astype(str)

        # Build working frame — handle column name variants (Area vs District, Emp ID vs OfficerID)
        def _text_col(name, fallback=''):
            value = df.get(name, fallback)
            if isinstance(value, pd.Series):
                return value.astype('object').where(value.notna(), '').astype(str).str.strip()
            return pd.Series(fallback, index=df.index).astype(str).str.strip()

        w = pd.DataFrame(index=df.index)
        w['Region'] = _text_col('Region')
        w['Division'] = _text_col('Division')
        w['Area'] = _text_col('Area') if 'Area' in df.columns else _text_col('District')
        w['BranchName'] = _text_col('BranchName')
        w['Emp ID'] = _text_col('Emp ID') if 'Emp ID' in df.columns else _text_col('OfficerID')
        w['Officer Name'] = _text_col('Officer Name')
        w['Product Name'] = product_col.astype('object').where(product_col.notna(), '').astype(str).str.strip()

        # Count metrics
        mask_130_base = is_last_130 & is_last_active
        mask_3160_base = is_last_3160 & is_last_active
        mask_npa_base = is_last_npa & dpd_last_not_0days

        # Regular collection: Sum(No of Regular Demand) with DPD Group exclusion
        # Regular: exclude "1-30". Month-end: exclude "1-30" AND "31-60"
        _is_month_end = _is_month_end_date(target_date, force_regular_rules)
        # PNPA: month-end drops "Loan Status - Last Month" filter (matches vba_template_month_end.js)
        if _is_month_end:
            mask_pnpa_base = is_61_90
        else:
            mask_pnpa_base = is_61_90 & is_last_active
        _is_130 = dpd.str.contains('1-30', case=False, na=False)
        _is_3160 = dpd.str.contains('31-60', case=False, na=False)

        w['Regular Demand'] = np.where(ftod_mask, no_reg_demand, 0)
        if _is_month_end:
            w['Regular Collection'] = np.where(ftod_mask & ~_is_130 & ~_is_3160, no_reg_demand, 0)
        else:
            w['Regular Collection'] = np.where(ftod_mask & ~_is_130, no_reg_demand, 0)
        w['1-30 Demand'] = np.where(mask_130_base, 1, 0)
        w['1-30 Collection'] = np.where(mask_130_base & (inst_coll_val == 1), 1, 0)
        w['31-60 Demand'] = np.where(mask_3160_base, 1, 0)
        w['31-60 Collection'] = np.where(mask_3160_base & (inst_coll_val == 1), 1, 0)
        w['PNPA Demand'] = np.where(mask_pnpa_base, 1, 0)
        w['PNPA Collection'] = np.where(mask_pnpa_base & (inst_coll_val == 1), 1, 0)
        w['1-90 Demand'] = w['1-30 Demand'] + w['31-60 Demand'] + w['PNPA Demand']
        w['1-90 Collection'] = w['1-30 Collection'] + w['31-60 Collection'] + w['PNPA Collection']
        w['NPA Cases'] = np.where(mask_npa_base, 1, 0)
        w['NPA Act Acc'] = np.where(mask_npa_base & dpd_not_blank & has_collection, 1, 0)
        w['NPA Act Amt'] = np.where(mask_npa_base & dpd_not_blank, collection, 0)
        w['NPA Clo Acc'] = np.where(mask_npa_base & dpd_is_blank & has_collection, 1, 0)
        w['NPA Clo Amt'] = np.where(mask_npa_base & dpd_is_blank, collection, 0)
        w['On-Date Demand'] = np.where(target_date_mask & od_base, 1, 0)
        w['On-Date Collection'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), 1, 0)

        # Amount metrics
        w['Regular Demand Amt'] = np.where(ftod_mask, reg_demand, 0)
        if _is_month_end:
            w['Regular Collection Amt'] = np.where(ftod_mask & ~_is_130 & ~_is_3160, collection, 0)
        else:
            w['Regular Collection Amt'] = np.where(ftod_mask & ~_is_130, collection, 0)
        w['1-30 Demand Amt'] = np.where(mask_130_base, cumulative_demand, 0)
        w['1-30 Collection Amt'] = np.where(mask_130_base & (inst_coll_val == 1), collection, 0)
        w['31-60 Demand Amt'] = np.where(mask_3160_base, cumulative_demand, 0)
        w['31-60 Collection Amt'] = np.where(mask_3160_base & (inst_coll_val == 1), collection, 0)
        w['PNPA Demand Amt'] = np.where(mask_pnpa_base, cumulative_demand, 0)
        w['PNPA Collection Amt'] = np.where(mask_pnpa_base & (inst_coll_val == 1), collection, 0)
        w['1-90 Demand Amt'] = w['1-30 Demand Amt'] + w['31-60 Demand Amt'] + w['PNPA Demand Amt']
        w['1-90 Collection Amt'] = w['1-30 Collection Amt'] + w['31-60 Collection Amt'] + w['PNPA Collection Amt']
        w['On-Date Demand Amt'] = np.where(target_date_mask & od_base, reg_demand, 0)
        w['On-Date Collection Amt'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), collection, 0)

        # FY flag from Loan Date (1 = loan originated in current FY, 0 = older)
        loan_date_flag = pd.to_numeric(df.get('Loan Date', 0), errors='coerce').fillna(0)
        w['_fy_flag'] = loan_date_flag

        group_cols = ['Region', 'Division', 'Area', 'BranchName', 'Emp ID']
        metric_cols = [c for c in w.columns if c not in ('Region', 'Division', 'Area', 'BranchName', 'Emp ID', 'Officer Name', 'Product Name', '_fy_flag')]

        # Officer name lookup: Emp ID -> most common name
        officer_lookup = (
            w[w['Officer Name'] != '']
            .groupby('Emp ID')['Officer Name']
            .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0])
            .to_dict()
        )

        products = ['IGL', 'FIG', 'VVY']

        pos_cols = ['Regular_POS', 'SMA0_POS', 'SMA1_POS', 'PNPA_POS', 'NPA_POS', 'Total_POS']

        # ── Employee POS via AccountID linkage ────────────────────────
        emp_pos = None  # DataFrame: EmpID -> POS buckets (across all products)
        if par_file is not None:
            try:
                from services.excel_reader import smart_read_excel
                par_emp = smart_read_excel(par_file)
                par_emp.columns = par_emp.columns.str.strip()
                if 'AccountID' in par_emp.columns and 'PrincipalOS' in par_emp.columns:
                    acct_col = 'Account ID' if 'Account ID' in df.columns else 'AccountID'
                    acct_to_emp = dict(zip(df[acct_col].astype(str), df.get('Emp ID', pd.Series('', index=df.index)).astype(str)))
                    par_emp['_emp'] = par_emp['AccountID'].astype(str).map(acct_to_emp)
                    par_emp['_pos'] = pd.to_numeric(par_emp['PrincipalOS'], errors='coerce').fillna(0)

                    # Find DPD col
                    dpd_c = None
                    for c in ['DPD Days', 'Days Group', 'Days group', 'DaysGroup']:
                        if c in par_emp.columns:
                            dpd_c = c; break
                    if dpd_c:
                        bmap = {
                            '0 Days':'Regular_POS','0days':'Regular_POS',
                            '1: 1-30':'SMA0_POS','1-30':'SMA0_POS',
                            '2: 31-60':'SMA1_POS','31-60':'SMA1_POS',
                            '3: 61-90':'PNPA_POS','61-90':'PNPA_POS',
                            '4: 91-120':'NPA_POS','91-120':'NPA_POS',
                            '5: 121-180':'NPA_POS','121-180':'NPA_POS','121-150':'NPA_POS','151-180':'NPA_POS',
                            '6: 181-365':'NPA_POS','181-365':'NPA_POS','181-210':'NPA_POS','211-250':'NPA_POS','251-365':'NPA_POS',
                            '7: >365 Days':'NPA_POS','>365 Days':'NPA_POS','>365':'NPA_POS','>365days':'NPA_POS',
                        }
                        par_emp['_bucket'] = par_emp[dpd_c].fillna('').astype(str).map(bmap).fillna('')
                        valid = par_emp[(par_emp['_emp'].notna()) & (par_emp['_emp'] != '') & (par_emp['_bucket'] != '')]
                        emp_pos = valid.pivot_table(values='_pos', index='_emp', columns='_bucket', aggfunc='sum', fill_value=0)
                        for c in pos_cols:
                            if c not in emp_pos.columns: emp_pos[c] = 0
                        emp_pos['Total_POS'] = emp_pos[['Regular_POS','SMA0_POS','SMA1_POS','PNPA_POS','NPA_POS']].sum(axis=1)
                        logging.info(f"EMPLOYEE REPORT: Employee POS mapped for {len(emp_pos)} employees ({valid['_pos'].sum()/10000000:.2f} Cr)")
                del par_emp
            except Exception as e:
                logging.warning(f"EMPLOYEE REPORT: Employee POS mapping failed: {e}")

        def _write_product_sheet(writer, subset, sheet_name):
            if len(subset) == 0:
                empty = pd.DataFrame(columns=['Region', 'Division', 'Area', 'Branch', 'Emp ID', 'Officer Name'] + metric_cols)
                empty.to_excel(writer, sheet_name=sheet_name, index=False)
                return 0
            agg = subset.groupby(group_cols)[metric_cols].sum().reset_index()
            agg['Officer Name'] = agg['Emp ID'].map(lambda eid: officer_lookup.get(eid, ''))
            agg = agg.rename(columns={'BranchName': 'Branch'})
            col_order = ['Region', 'Division', 'Area', 'Branch', 'Emp ID', 'Officer Name'] + metric_cols
            agg = agg[col_order]
            agg = agg[agg['Emp ID'] != '']

            # Add employee-level POS from AccountID linkage
            if emp_pos is not None:
                for c in pos_cols:
                    agg[c] = agg['Emp ID'].map(emp_pos[c].to_dict() if c in emp_pos.columns else {}).fillna(0).astype(int)

            agg.to_excel(writer, sheet_name=sheet_name, index=False)
            return len(agg)

        fy_account_count = (w['_fy_flag'] == 1).sum()
        logging.info(f"EMPLOYEE REPORT: FY 25-26 accounts = {fy_account_count} / {len(w)}")

        # ── POS (PrincipalOS) from PAR file ──────────────────────────
        # Aggregate by Region+Division+Area+BranchName (all products) so every
        # account in the PAR is included regardless of product type.
        pos_data = None  # DataFrame indexed by (Region, Division, Area, BranchName)
        if par_file is not None:
            try:
                from services.excel_reader import smart_read_excel
                par_df = smart_read_excel(par_file)
                par_df.columns = par_df.columns.str.strip()
                # Normalize column name variants across months
                col_renames = {}
                if 'District Name' in par_df.columns and 'Area' not in par_df.columns:
                    col_renames['District Name'] = 'Area'
                if 'District' in par_df.columns and 'Area' not in par_df.columns:
                    col_renames['District'] = 'Area'
                if 'Product' in par_df.columns and 'Product Name' not in par_df.columns:
                    col_renames['Product'] = 'Product Name'
                if col_renames:
                    par_df = par_df.rename(columns=col_renames)

                # If no Product Name at all, derive from ProductID
                if 'Product Name' not in par_df.columns and 'ProductID' in par_df.columns:
                    pid = par_df['ProductID'].fillna('').astype(str)
                    par_df['Product Name'] = pid.apply(
                        lambda x: 'FIG' if (x.startswith('6') or 'FIG' in x.upper()) else 'IGL'
                    )

                logging.info(f"EMPLOYEE REPORT: Reading PAR for POS — {len(par_df)} rows")

                dpd_col_par = None
                for cand in ['DPD Days', 'Days Group', 'Days group', 'DaysGroup']:
                    if cand in par_df.columns:
                        dpd_col_par = cand
                        break

                if dpd_col_par and 'PrincipalOS' in par_df.columns:
                    par_dpd = par_df[dpd_col_par].fillna('').astype(str)
                    pos_bucket_map = {
                        '0 Days': 'Regular_POS', '0days': 'Regular_POS',
                        '1: 1-30': 'SMA0_POS', '1-30': 'SMA0_POS',
                        '2: 31-60': 'SMA1_POS', '31-60': 'SMA1_POS',
                        '3: 61-90': 'PNPA_POS', '61-90': 'PNPA_POS',
                        '4: 91-120': 'NPA_POS', '91-120': 'NPA_POS',
                        '5: 121-180': 'NPA_POS', '121-180': 'NPA_POS', '121-150': 'NPA_POS', '151-180': 'NPA_POS',
                        '6: 181-365': 'NPA_POS', '181-365': 'NPA_POS', '181-210': 'NPA_POS', '211-250': 'NPA_POS', '251-365': 'NPA_POS',
                        '7: >365 Days': 'NPA_POS', '>365 Days': 'NPA_POS', '>365': 'NPA_POS', '>365days': 'NPA_POS', '>120': 'NPA_POS',
                    }
                    par_df['_pos_bucket'] = par_dpd.map(pos_bucket_map).fillna('')
                    unmapped = par_dpd[par_df['_pos_bucket'] == ''].unique()
                    if len(unmapped) > 0 and not (len(unmapped) == 1 and unmapped[0] == ''):
                        logging.warning(f"EMPLOYEE REPORT: Unmapped DPD values for POS: {unmapped.tolist()}")
                    par_df['_pos_val'] = pd.to_numeric(par_df['PrincipalOS'], errors='coerce').fillna(0)

                    # Normalize geography strings for matching
                    for geo in ['Region', 'Division', 'Area', 'BranchName']:
                        if geo in par_df.columns:
                            par_df[geo] = par_df[geo].fillna('').astype(str).str.strip()

                    # Fix Area: use demand's BranchName->Area mapping
                    # Handles missing Area column and name mismatches
                    if 'BranchName' in par_df.columns and 'BranchName' in df.columns:
                        dem_branch_area = dict(zip(
                            df['BranchName'].fillna('').astype(str).str.strip(),
                            df.get('Area', df.get('District', pd.Series('', index=df.index))).fillna('').astype(str).str.strip()
                        ))
                        dem_branch_div = dict(zip(
                            df['BranchName'].fillna('').astype(str).str.strip(),
                            df.get('Division', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
                        ))
                        corrected_area = par_df['BranchName'].map(dem_branch_area)
                        corrected_div = par_df['BranchName'].map(dem_branch_div)
                        if 'Area' not in par_df.columns:
                            par_df['Area'] = corrected_area.fillna('')
                            logging.info(f"EMPLOYEE REPORT: Added Area from demand ({corrected_area.notna().sum()} mapped)")
                        else:
                            changed = (corrected_area.notna()) & (corrected_area != par_df['Area'])
                            if changed.any():
                                logging.info(f"EMPLOYEE REPORT: Corrected {changed.sum()} PAR area names")
                            par_df.loc[corrected_area.notna(), 'Area'] = corrected_area[corrected_area.notna()]
                        if 'Division' not in par_df.columns:
                            par_df['Division'] = corrected_div.fillna('')

                    # Normalize Product Name: only IGL, FIG, IL are main products
                    if 'Product Name' in par_df.columns:
                        # "IGL & FIG" -> resolve using ProductID column
                        # ProductID starting with '6' or containing 'FIG' = FIG, rest = IGL
                        combo_mask = par_df['Product Name'] == 'IGL & FIG'
                        if combo_mask.any() and 'ProductID' in par_df.columns:
                            pid = par_df.loc[combo_mask, 'ProductID'].fillna('').astype(str)
                            is_fig = pid.str.startswith('6') | pid.str.upper().str.contains('FIG')
                            par_df.loc[combo_mask & is_fig.reindex(par_df.index, fill_value=False), 'Product Name'] = 'FIG'
                            par_df.loc[combo_mask & ~is_fig.reindex(par_df.index, fill_value=True), 'Product Name'] = 'IGL'
                            fig_count = is_fig.sum()
                            logging.info(f"EMPLOYEE REPORT: Resolved {combo_mask.sum()} 'IGL & FIG' rows -> {fig_count} FIG, {combo_mask.sum()-fig_count} IGL (via ProductID)")

                        main_products = {'IGL', 'FIG'}
                        par_df['Product Name'] = par_df['Product Name'].apply(
                            lambda x: x if x in main_products else 'IL'
                        )

                    par_valid = par_df[par_df['_pos_bucket'] != ''].copy()

                    # Pivot: Region+Division+Area+BranchName+Product -> POS buckets
                    geo_cols = ['Region', 'Division', 'Area', 'BranchName']
                    prod_col = 'Product Name'
                    if all(c in par_valid.columns for c in geo_cols) and prod_col in par_valid.columns:
                        par_valid[prod_col] = par_valid[prod_col].fillna('').astype(str)
                        # Map VVY -> VVY (kept as-is; rename happens at sheet level)
                        idx_cols = geo_cols + [prod_col]
                        pos_pivot = par_valid.pivot_table(
                            values='_pos_val',
                            index=idx_cols,
                            columns='_pos_bucket',
                            aggfunc='sum',
                            fill_value=0,
                        )
                        for col in ['Regular_POS', 'SMA0_POS', 'SMA1_POS', 'PNPA_POS', 'NPA_POS']:
                            if col not in pos_pivot.columns:
                                pos_pivot[col] = 0
                        pos_pivot['Total_POS'] = pos_pivot[['Regular_POS', 'SMA0_POS', 'SMA1_POS', 'PNPA_POS', 'NPA_POS']].sum(axis=1)
                        pos_data = pos_pivot
                        total_pos = pos_pivot['Total_POS'].sum()
                        logging.info(f"EMPLOYEE REPORT: POS by branch+product — {len(pos_data)} combos, total={total_pos:,.0f} ({total_pos/10000000:.2f} Cr)")
                    del par_df, par_valid
                else:
                    logging.warning(f"EMPLOYEE REPORT: PAR missing columns for POS")
            except Exception as pos_err:
                logging.warning(f"EMPLOYEE REPORT: POS computation failed: {pos_err}")

        pos_cols = ['Regular_POS', 'SMA0_POS', 'SMA1_POS', 'PNPA_POS', 'NPA_POS', 'Total_POS']

        with pd.ExcelWriter(str(output_path), engine='xlsxwriter') as writer:
            # OverAll sheets (all accounts)
            for product in products:
                subset = w[w['Product Name'] == product] if product != 'ALL' else w
                n = _write_product_sheet(writer, subset, product)
                logging.info(f"EMPLOYEE REPORT: {product} sheet — {n} employees")

            # FY sheets (only accounts where Loan Date is in current FY)
            w_fy = w[w['_fy_flag'] == 1]
            for product in products:
                subset = w_fy[w_fy['Product Name'] == product] if product != 'ALL' else w_fy
                fy_sheet = f"{product}_FY"
                n = _write_product_sheet(writer, subset, fy_sheet)
                logging.info(f"EMPLOYEE REPORT: {fy_sheet} sheet — {n} employees")

            # POS sheet — branch-level PrincipalOS from PAR, per product
            if pos_data is not None:
                pos_out = pos_data.reset_index()
                pos_out = pos_out[['Region', 'Division', 'Area', 'BranchName', 'Product Name'] + pos_cols]
                pos_out.to_excel(writer, sheet_name='POS', index=False)
                logging.info(f"EMPLOYEE REPORT: POS sheet — {len(pos_out)} rows, total={pos_out['Total_POS'].sum()/10000000:.2f} Cr")

            # EMP_POS sheet — employee-level POS from AccountID linkage
            if emp_pos is not None and len(emp_pos) > 0:
                emp_pos_out = emp_pos.reset_index().rename(columns={'_emp': 'Emp ID'})
                emp_pos_out = emp_pos_out[['Emp ID'] + pos_cols]
                emp_pos_out.to_excel(writer, sheet_name='EMP_POS', index=False)
                logging.info(f"EMPLOYEE REPORT: EMP_POS sheet — {len(emp_pos_out)} employees, total={emp_pos_out['Total_POS'].sum()/10000000:.2f} Cr")

        elapsed = time.perf_counter() - t0
        logging.info(f"EMPLOYEE REPORT: Generated in {elapsed:.2f}s -> {output_path}")
        return output_path

    except Exception as e:
        logging.warning(f"EMPLOYEE REPORT: Failed ({type(e).__name__}: {e})")
        return None


def build_employee_report_with_accounts(df, target_date, output_path, force_regular_rules=False):
    """Build an account-level employee report with 3 sheets (IGL, FIG, VVY).

    Same metrics as build_employee_report but NOT aggregated — each row is an
    individual account with: Region, District, Branch, Emp ID, Officer Name,
    Account ID, plus all B+O metric columns.  Raw data, no formatting.

    Returns the output path on success, None on failure.
    """
    import numpy as np

    t0 = time.perf_counter()
    logging.info("EMPLOYEE REPORT (ACCOUNTS): Starting account-level employee report generation")

    try:
        # --- Same mask/metric logic as build_employee_report ---
        meeting_dt = parse_date_column(df['Meeting Date'])
        meeting_dt_clean = meeting_dt.dt.normalize()
        target_date_clean = pd.Timestamp(target_date).normalize()
        first_of_month_clean = target_date_clean.replace(day=1)
        ftod_mask = (meeting_dt_clean >= first_of_month_clean) & (meeting_dt_clean <= target_date_clean)
        target_date_mask = meeting_dt_clean == target_date_clean

        # Find DPD Group column dynamically
        _dpd_col = 'DPD Group'
        if _dpd_col not in df.columns:
            for _cand in list(ALLOWED_DPD_COLUMNS) + ['Due Days']:
                if _cand in df.columns:
                    _dpd_col = _cand
                    break
        dpd = df.get(_dpd_col, pd.Series('', index=df.index)).fillna('').astype(str)
        is_130 = dpd.str.contains('1-30', na=False)
        dpd_days = df.get('DPD Days', df.get('DPD Group', pd.Series('', index=df.index))).fillna('').astype(str)

        dpd_last = df.get('DPD Group - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
        status_last = df.get('Loan Status - Last Month', pd.Series('', index=df.index)).fillna('').astype(str)
        is_61_90 = dpd_last.str.contains('61-90', na=False)
        is_last_130 = dpd_last.str.contains('1-30', na=False)
        is_last_3160 = dpd_last.str.contains('31-60', na=False)
        is_last_active = status_last == 'Active Loan'
        is_last_npa = status_last == 'NPA'
        dpd_last_not_0days = dpd_last != '0 Days'
        dpd_is_blank = (dpd == '') | (dpd == '(blank)') | dpd.isna()
        dpd_not_blank = ~dpd_is_blank

        has_collection = df['Collection'].notna() if 'Collection' in df.columns else pd.Series(False, index=df.index)
        product_col = df['Product Name'].fillna('').astype(str)

        reg_demand = pd.to_numeric(df.get('Regular Demand', 0), errors='coerce').fillna(0)
        collection = pd.to_numeric(df.get('Collection', 0), errors='coerce').fillna(0)
        no_reg_demand = pd.to_numeric(df.get('No of Regular Demand', 0), errors='coerce').fillna(0)
        no_cumulative = pd.to_numeric(df.get('No of Cumulative', 0), errors='coerce').fillna(0)
        cumulative_demand = pd.to_numeric(df.get('Cumulative Demand', 0), errors='coerce').fillna(0)
        inst_coll_val = pd.to_numeric(df.get('installment - collected value', 0), errors='coerce').fillna(0)
        od_base = (no_reg_demand == 1) & (reg_demand != 0)
        partial_amt = df.get('Partial Amount', pd.Series('', index=df.index)).fillna('').astype(str)

        # Build working frame — with Account ID (NOT aggregated)
        w = pd.DataFrame(index=df.index)
        w['Region'] = df.get('Region', '').astype(str).str.strip()
        w['Division'] = df.get('Division', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
        w['Area'] = df.get('Area', df.get('District', pd.Series('', index=df.index))).fillna('').astype(str).str.strip()
        w['BranchName'] = df.get('BranchName', '').astype(str).str.strip()
        w['Emp ID'] = df.get('Emp ID', '').astype(str).str.strip()
        w['Officer Name'] = df.get('Officer Name', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()

        # Detect Account ID column
        acct_col = None
        for candidate in ['Account ID', 'AccountID', 'Account_ID', 'account_id']:
            if candidate in df.columns:
                acct_col = candidate
                break
        if acct_col:
            w['Account ID'] = df[acct_col].astype(str).str.strip()
        else:
            w['Account ID'] = ''

        w['Product Name'] = product_col

        # Count metrics (same as build_employee_report)
        mask_130_base = is_last_130 & is_last_active
        mask_3160_base = is_last_3160 & is_last_active
        mask_npa_base = is_last_npa & dpd_last_not_0days

        # Regular collection: Sum(No of Regular Demand) with DPD Group exclusion
        # Regular: exclude "1-30". Month-end: exclude "1-30" AND "31-60"
        _is_month_end = _is_month_end_date(target_date, force_regular_rules)
        # PNPA: month-end drops "Loan Status - Last Month" filter (matches vba_template_month_end.js)
        if _is_month_end:
            mask_pnpa_base = is_61_90
        else:
            mask_pnpa_base = is_61_90 & is_last_active
        _is_130 = dpd.str.contains('1-30', case=False, na=False)
        _is_3160 = dpd.str.contains('31-60', case=False, na=False)

        w['Regular Demand'] = np.where(ftod_mask, no_reg_demand, 0)
        if _is_month_end:
            w['Regular Collection'] = np.where(ftod_mask & ~_is_130 & ~_is_3160, no_reg_demand, 0)
        else:
            w['Regular Collection'] = np.where(ftod_mask & ~_is_130, no_reg_demand, 0)
        w['1-30 Demand'] = np.where(mask_130_base, 1, 0)
        w['1-30 Collection'] = np.where(mask_130_base & (inst_coll_val == 1), 1, 0)
        w['31-60 Demand'] = np.where(mask_3160_base, 1, 0)
        w['31-60 Collection'] = np.where(mask_3160_base & (inst_coll_val == 1), 1, 0)
        w['PNPA Demand'] = np.where(mask_pnpa_base, 1, 0)
        w['PNPA Collection'] = np.where(mask_pnpa_base & (inst_coll_val == 1), 1, 0)
        w['1-90 Demand'] = w['1-30 Demand'] + w['31-60 Demand'] + w['PNPA Demand']
        w['1-90 Collection'] = w['1-30 Collection'] + w['31-60 Collection'] + w['PNPA Collection']
        w['NPA Cases'] = np.where(mask_npa_base, 1, 0)
        w['NPA Act Acc'] = np.where(mask_npa_base & dpd_not_blank & has_collection, 1, 0)
        w['NPA Act Amt'] = np.where(mask_npa_base & dpd_not_blank, collection, 0)
        w['NPA Clo Acc'] = np.where(mask_npa_base & dpd_is_blank & has_collection, 1, 0)
        w['NPA Clo Amt'] = np.where(mask_npa_base & dpd_is_blank, collection, 0)
        w['On-Date Demand'] = np.where(target_date_mask & od_base, 1, 0)
        w['On-Date Collection'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), 1, 0)

        # Amount metrics
        w['Regular Demand Amt'] = np.where(ftod_mask, reg_demand, 0)
        if _is_month_end:
            w['Regular Collection Amt'] = np.where(ftod_mask & ~_is_130 & ~_is_3160, collection, 0)
        else:
            w['Regular Collection Amt'] = np.where(ftod_mask & ~_is_130, collection, 0)
        w['1-30 Demand Amt'] = np.where(mask_130_base, cumulative_demand, 0)
        w['1-30 Collection Amt'] = np.where(mask_130_base & (inst_coll_val == 1), collection, 0)
        w['31-60 Demand Amt'] = np.where(mask_3160_base, cumulative_demand, 0)
        w['31-60 Collection Amt'] = np.where(mask_3160_base & (inst_coll_val == 1), collection, 0)
        w['PNPA Demand Amt'] = np.where(mask_pnpa_base, cumulative_demand, 0)
        w['PNPA Collection Amt'] = np.where(mask_pnpa_base & (inst_coll_val == 1), collection, 0)
        w['1-90 Demand Amt'] = w['1-30 Demand Amt'] + w['31-60 Demand Amt'] + w['PNPA Demand Amt']
        w['1-90 Collection Amt'] = w['1-30 Collection Amt'] + w['31-60 Collection Amt'] + w['PNPA Collection Amt']
        w['On-Date Demand Amt'] = np.where(target_date_mask & od_base, reg_demand, 0)
        w['On-Date Collection Amt'] = np.where(target_date_mask & od_base & (partial_amt == 'Full EMI Paid'), collection, 0)

        id_cols = ['Region', 'Division', 'Area', 'BranchName', 'Emp ID', 'Officer Name', 'Account ID']
        metric_cols = [c for c in w.columns if c not in id_cols + ['Product Name']]

        products = ['IGL', 'FIG', 'VVY']
        with pd.ExcelWriter(str(output_path), engine='xlsxwriter') as writer:
            for product in products:
                subset = w[w['Product Name'] == product]
                if len(subset) == 0:
                    empty = pd.DataFrame(columns=['Region', 'Division', 'Area', 'Branch', 'Emp ID', 'Officer Name', 'Account ID'] + metric_cols)
                    empty.to_excel(writer, sheet_name=product, index=False)
                    continue

                out = subset[id_cols + metric_cols].copy()
                out = out.rename(columns={'BranchName': 'Branch'})
                col_order = ['Region', 'Division', 'Area', 'Branch', 'Emp ID', 'Officer Name', 'Account ID'] + metric_cols
                out = out[col_order]

                out.to_excel(writer, sheet_name=product, index=False)
                logging.info(f"EMPLOYEE REPORT (ACCOUNTS): {product} sheet -- {len(out)} rows")

        elapsed = time.perf_counter() - t0
        logging.info(f"EMPLOYEE REPORT (ACCOUNTS): Generated in {elapsed:.2f}s -> {output_path}")
        return output_path

    except Exception as e:
        logging.warning(f"EMPLOYEE REPORT (ACCOUNTS): Failed ({type(e).__name__}: {e})")
        return None


def process_files(demand_file, collection_file, par_file, output_file, auto_fix_sheets=False, db_manager=None, target_date=None, sheets_dir=None, skip_output=False, force_demand_file=False, force_regular_rules=False):
    """Process EOD files. Tries DuckDB-first path, falls back to pandas on failure.

    Args:
        skip_output: If True, skip Excel write, precomp, and report generation.
                     Returns (df_result, None). Used by Quick Report which only
                     needs the DataFrame and builds its own output.
        force_demand_file: If True, always use the uploaded demand file instead of
                          DB Demand_Master. Used by Quick Month-End when processing
                          a different month's data.
    """
    _check_disk_space()

    # Fallback: if no target_date provided, default to today
    if target_date is None:
        from datetime import datetime as _dt
        target_date = _dt.now()
        logging.info(f"No target_date provided, defaulting to today: {target_date.strftime('%d-%m-%Y')}")
    else:
        logging.info(f"Target Date: {target_date.strftime('%d-%m-%Y')}")

    if db_manager:
        try:
            logging.info("EOD processing: attempting DuckDB-first path")
            result = process_files_duckdb(db_manager, demand_file, collection_file, par_file, output_file, target_date=target_date, sheets_dir=sheets_dir, skip_output=skip_output, force_demand_file=force_demand_file, force_regular_rules=force_regular_rules)
            logging.info("EOD processing: DuckDB path completed successfully")
            return result
        except Exception as e:
            logging.warning(f"DuckDB path failed ({type(e).__name__}: {e}), falling back to pandas path")
            logging.info(
                "Switching to standard processing mode. "
                "This may use slightly more memory but is fully compatible."
            )

    return process_files_pandas(demand_file, collection_file, par_file, output_file, auto_fix_sheets, target_date=target_date, sheets_dir=sheets_dir, skip_output=skip_output, force_regular_rules=force_regular_rules)

def process_files_duckdb(db_manager, demand_file, collection_file, par_file, output_file, target_date=None, sheets_dir=None, skip_output=False, force_demand_file=False, force_regular_rules=False):
    t0 = time.perf_counter()
    con = db_manager.get_connection()

    # 1. Register Daily Files via direct Parquet registration (no pandas intermediate)
    logging.info("STEP 1: Registering Daily Files into DB (DuckDB-first -- no pandas intermediates)")

    COLLECTION_COLS = ['AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal']

    t_read = time.perf_counter()
    coll_parquet = _ensure_parquet_cache(collection_file, "collection", COLLECTION_COLS)
    # Note: CREATE VIEW does not support parameterized queries in DuckDB,
    # so we use f-string with escaped path. These are local file paths, not user input.
    coll_path_escaped = str(coll_parquet).replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW daily_collection AS SELECT * FROM read_parquet('{coll_path_escaped}')")
    logging.info(f"  Collection registered: {time.perf_counter() - t_read:.2f}s")

    # Calculate date range for filtering
    first_of_month = None
    if target_date:
        first_of_month = target_date.replace(day=1)
        logging.info(f"Collection date filter: {first_of_month.strftime('%d-%m-%Y')} to {target_date.strftime('%d-%m-%Y')}")

    t_read = time.perf_counter()
    par_parquet = _ensure_parquet_cache(par_file, "par")
    par_path_escaped = str(par_parquet).replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW daily_par AS SELECT * FROM read_parquet('{par_path_escaped}')")
    logging.info(f"  PAR registered: {time.perf_counter() - t_read:.2f}s")

    # Demand: DB table or uploaded file (via Parquet)
    demand_source = _resolve_demand_source(con, demand_file, force_uploaded=force_demand_file)

    # Check if Last_Month_PAR exists in DB, otherwise try to load from BACKEND_DATA
    has_last_month = False
    try:
        count = con.execute("SELECT count(*) FROM Last_Month_PAR").fetchone()[0]
        if count > 0:
            has_last_month = True
            logging.info(f"Using Last_Month_PAR from DB ({count} rows)")
    except Exception:
        pass

    if not has_last_month:
        # Try to read from BACKEND_DATA folder (like original Pandas code)
        last_month_files = list(backend_data_dir.glob("Last_Month*.xlsx"))
        if last_month_files:
            try:
                t_read = time.perf_counter()
                logging.info(f"Loading Last Month from file: {last_month_files[0].name}")
                # Use smart_read_excel with selective columns for optimization
                df_last = smart_read_excel(last_month_files[0], usecols=['AccountID', 'DPD Days', 'LoanStatus'])
                con.register('Last_Month_PAR', df_last)
                has_last_month = True
                logging.info(f"  Last Month PAR read: {time.perf_counter() - t_read:.2f}s ({len(df_last)} rows)")
            except Exception as e:
                logging.warning(f"Could not load Last Month PAR: {e}")

    gc_checkpoint("eod-duckdb-file-registration")

    # 2. Execute Main Query
    logging.info("STEP 2: Executing SQL Join")

    # Detect DPD column from Parquet schema (no data loading)
    days_group_col = _detect_dpd_column(par_parquet)
    logging.info(f"  DPD column detected from schema: '{days_group_col}'")

    # Dynamic Query Building
    select_clauses = [
        "d.*",
        "c.Collection_Sum as Collection",
        "strftime(c.Latest_Date, '%d-%m-%Y') as \"Collection Date\"",
        "p.Par_DPD_Group as \"DPD Group\"",
        "p.Par_DPD_Days as \"DPD Days\"",
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
        "LEFT JOIN Collection_Agg c ON CAST(d.\"Account ID\" AS VARCHAR) = CAST(c.AccountID AS VARCHAR)",
        "LEFT JOIN PAR_Mapped p ON CAST(d.\"Account ID\" AS VARCHAR) = CAST(p.AccountID AS VARCHAR)"
    ]
    # Build date filter clause for Collection
    date_filter_clause = ""
    if target_date and first_of_month:
        date_filter_clause = f"""
          AND Trxdate >= '{first_of_month.strftime('%Y-%m-%d')}'
          AND Trxdate <= '{target_date.strftime('%Y-%m-%d')}'"""

    # Trxdate may be stored as string (e.g. "2/5/26") in Parquet. Coerce to
    # TIMESTAMP in a sub-CTE so date filtering and MAX() work correctly.
    ctes = [
        f"""Collection_Filtered AS (
            SELECT AccountID, CollectionTotal, ReverseTotal, Trxdate
            FROM daily_collection
            WHERE (ReverseTotal = 0 OR ReverseTotal IS NULL OR TRIM(CAST(ReverseTotal AS VARCHAR)) = '')
        )""",
        f"""Collection_Dated AS (
            SELECT
                AccountID,
                TRY_CAST(CollectionTotal AS DOUBLE) as CollectionTotal,
                CASE
                    WHEN typeof(Trxdate) IN ('TIMESTAMP', 'DATE') THEN CAST(Trxdate AS TIMESTAMP)
                    WHEN typeof(Trxdate) IN ('INTEGER', 'BIGINT', 'DOUBLE', 'FLOAT')
                        THEN CAST('1899-12-30' AS TIMESTAMP) + INTERVAL (CAST(Trxdate AS INTEGER)) DAY
                    ELSE COALESCE(
                        TRY_STRPTIME(CAST(Trxdate AS VARCHAR), '%m/%d/%y'),
                        TRY_STRPTIME(CAST(Trxdate AS VARCHAR), '%m/%d/%Y'),
                        TRY_STRPTIME(CAST(Trxdate AS VARCHAR), '%d-%m-%Y'),
                        TRY_CAST(Trxdate AS TIMESTAMP)
                    )
                END AS Trxdate
            FROM Collection_Filtered
        )""",
        f"""Collection_Agg AS (
            SELECT
                AccountID,
                SUM(CollectionTotal) as Collection_Sum,
                MAX(Trxdate) as Latest_Date
            FROM Collection_Dated
            WHERE 1=1{date_filter_clause}
            GROUP BY AccountID
        )""",
        f"""PAR_Mapped AS (
            SELECT AccountID, "{days_group_col}" as Par_DPD_Group,
                   CASE
                       WHEN "DPD Days" IS NOT NULL THEN CAST("DPD Days" AS VARCHAR)
                       ELSE CAST("{days_group_col}" AS VARCHAR)
                   END as Par_DPD_Days
            FROM daily_par
        )"""
    ]

    if has_last_month:
        ctes.append("""Legacy_Data AS (
            SELECT AccountID, "DPD Days", LoanStatus
            FROM Last_Month_PAR
        )""")
        select_clauses.append("lm.\"DPD Days\" as \"DPD Group - Last Month\"")
        select_clauses.append("lm.LoanStatus as \"Loan Status - Last Month\"")
        joins.append("LEFT JOIN Legacy_Data lm ON CAST(d.\"Account ID\" AS VARCHAR) = CAST(lm.AccountID AS VARCHAR)")
    else:
        select_clauses.append("NULL as \"DPD Group - Last Month\"")
        select_clauses.append("NULL as \"Loan Status - Last Month\"")

    final_query = "WITH " + ",\n".join(ctes) + "\nSELECT \n" + ",\n".join(select_clauses) + "\n" + "\n".join(joins)

    t_query = time.perf_counter()
    logging.info("Executing Final Query...")
    df_result = con.execute(final_query).df()
    logging.info(f"  SQL Query: {time.perf_counter() - t_query:.2f}s")
    logging.info("DuckDB merge complete -- no intermediate pandas DataFrames loaded")

    logging.info(f"SQL Query completed. Result shape: {df_result.shape}")
    gc_checkpoint("eod-duckdb-sql-join")

    # 3. Save to Excel
    logging.info("STEP 3: Saving to Excel")
    # Format 'Meeting Date' as Short Date (dd-mm-yyyy) if exists
    if 'Meeting Date' in df_result.columns:
        logging.info(f"Meeting Date dtype: {df_result['Meeting Date'].dtype}")
        logging.info(f"Meeting Date sample (first 3): {df_result['Meeting Date'].head(3).tolist()}")

        col = df_result['Meeting Date']

        if pd.api.types.is_numeric_dtype(col):
            logging.info("Detected numeric Meeting Date - converting from Excel serial date")
            excel_epoch = pd.Timestamp('1899-12-30')
            valid_mask = (col > 0) & (col < 100000)
            df_result.loc[valid_mask, 'Meeting Date'] = (excel_epoch + pd.to_timedelta(col[valid_mask], unit='D')).dt.strftime('%d-%m-%Y')
            df_result.loc[~valid_mask, 'Meeting Date'] = ''
        else:
            temp_dates = pd.to_datetime(col, errors='coerce', dayfirst=True)
            valid_count = temp_dates.notna().sum()
            logging.info(f"Meeting Date valid dates: {valid_count} / {len(temp_dates)}")

            if valid_count > 0:
                df_result['Meeting Date'] = temp_dates.dt.strftime('%d-%m-%Y').fillna('')
            else:
                logging.warning("No valid Meeting Dates found - keeping original values")

    # Convert 'Loan Date' to FY flag
    if 'Loan Date' in df_result.columns:
        logging.info(f"Loan Date dtype: {df_result['Loan Date'].dtype}")
        logging.info(f"Loan Date sample (first 3): {df_result['Loan Date'].head(3).tolist()}")

        col = df_result['Loan Date']
        backup_col_name = "Loan Date (Original)"
        fy_start, fy_end = derive_fy_bounds(target_date)

        if pd.api.types.is_numeric_dtype(col):
            logging.info("Detected numeric Loan Date - converting from Excel serial date")
            excel_epoch = pd.Timestamp('1899-12-30')
            valid_mask = (col > 0) & (col < 100000)
            temp_dates = pd.Series(pd.NaT, index=df_result.index)
            temp_dates.loc[valid_mask] = excel_epoch + pd.to_timedelta(col[valid_mask], unit='D')
            backup_series = temp_dates.dt.strftime('%d-%m-%Y').fillna('')
            insert_backup_column(df_result, backup_col_name, backup_series)
            fy_flag = temp_dates.between(fy_start, fy_end).fillna(False).astype(int)
            df_result['Loan Date'] = fy_flag
        else:
            temp_dates = pd.to_datetime(col, errors='coerce', dayfirst=True)
            valid_count = temp_dates.notna().sum()
            logging.info(f"Loan Date valid dates: {valid_count} / {len(temp_dates)}")

            backup_series = temp_dates.dt.strftime('%d-%m-%Y').fillna('')
            insert_backup_column(df_result, backup_col_name, backup_series)
            fy_flag = temp_dates.between(fy_start, fy_end).fillna(False).astype(int)
            df_result['Loan Date'] = fy_flag


    gc_checkpoint("eod-duckdb-post-processing")

    # When skip_output=True (Quick Report), skip Excel write + precomp + report
    # The caller only needs df_result and will build its own output.
    if skip_output:
        elapsed = time.perf_counter() - t0
        logging.info(f"Process Completed in {elapsed:.2f} seconds (skip_output=True, skipped Excel write)")
        return df_result, None

    # Excel writing - bulk write via pandas to_excel (much faster than cell-by-cell)
    total_rows = len(df_result)
    total_cols = len(df_result.columns)
    t_write = time.perf_counter()
    logging.info(f"STEP 4: Writing {total_rows:,} rows x {total_cols} cols to Excel...")

    # Pre-compute aggregation sheets BEFORE fillna (fillna replaces NaN with ''
    # which breaks Collection notna() checks used for count metrics)
    precomputed = None
    if target_date is not None:
        try:
            precomputed = _compute_precomputed_sheets(df_result, target_date, force_regular_rules=force_regular_rules)
        except Exception as e:
            logging.warning(f"Pre-computation failed ({type(e).__name__}: {e}), continuing without precomp sheets")

    # Type-safe fillna for DuckDB nullable integer columns, then bulk write
    df_result = _safe_fillna(df_result)

    _write_excel_fast(df_result, output_file, precomputed_sheets=precomputed)

    logging.info(f"  Excel write: {time.perf_counter() - t_write:.2f}s")

    # Generate formatted report Excel (replaces VBA macro)
    report_path = _build_report_with_excel_fallback(
        precomputed,
        output_file,
        target_date,
        df_result,
        sheets_dir=sheets_dir,
        force_regular_rules=force_regular_rules,
    )

    gc_checkpoint("eod-duckdb-excel-write")

    elapsed = time.perf_counter() - t0
    logging.info(f"Process Completed in {elapsed:.2f} seconds")

    return df_result, report_path


def process_files_pandas(demand_file, collection_file, par_file, output_file, auto_fix_sheets=False, target_date=None, sheets_dir=None, skip_output=False, force_regular_rules=False):
    t0 = time.perf_counter()

    logging.info("Starting Legacy Pandas Processing")

    # [Original Step 1] - Read files
    logging.info("STEP 1: Reading input files (Pandas path)")
    df_collection = smart_read_excel(collection_file)
    # Normalize column aliases (e.g. 'Transaction Date' → 'Trxdate')
    df_collection = _normalize_columns(df_collection, 'collection')
    # Parse Trxdate - handle Excel serial numbers, string dates, etc.
    df_collection['Trxdate'] = parse_trxdate(df_collection['Trxdate'])
    df_collection = df_collection.sort_values('Trxdate', ascending=False)

    # [Original Step 2]
    # Filter: ReverseTotal = 0 OR NULL OR blank
    df_filtered = df_collection[
        (df_collection['ReverseTotal'] == 0) |
        (df_collection['ReverseTotal'].isna()) |
        (df_collection['ReverseTotal'].astype(str).str.strip() == '')
    ]

    # Apply Trxdate date range filter if target_date is provided
    if target_date:
        first_of_month = target_date.replace(day=1)
        logging.info(f"Collection date filter: {first_of_month.strftime('%d-%m-%Y')} to {target_date.strftime('%d-%m-%Y')}")
        df_filtered = df_filtered[
            (df_filtered['Trxdate'] >= first_of_month) &
            (df_filtered['Trxdate'] <= target_date)
        ]
        logging.info(f"Filtered collection rows: {len(df_filtered)}")
    pivot_table = df_filtered.groupby('AccountID', as_index=False)['CollectionTotal'].sum()
    date_lookup = df_collection.drop_duplicates('AccountID', keep='first')[['AccountID', 'Trxdate']]

    # [Original Step 3] - Read demand & merge
    logging.info("STEP 2: Merging Demand + Collection + PAR")
    df_main = smart_read_excel(demand_file)
    df_main = _normalize_columns(df_main, 'demand')
    gc_checkpoint("eod-pandas-file-read")

    # [Original Step 3.5]
    last_month_files = list(backend_data_dir.glob("Last_Month*.xlsx"))
    if last_month_files:
        try:
            # IMPORTANT: the account-level PAR data (AccountID/DPD Days/LoanStatus)
            # may not be on the first sheet — some Last Month files carry a pivot
            # summary on sheet 0 (branch names), with the real data on 'Sheet1'.
            # sheet_name=0 therefore reads the wrong sheet and the last-month
            # columns silently drop, blanking every DPD bucket in the EOD Report.
            # smart_read_excel prefers 'Sheet1' and auto-detects the header row.
            df_last = smart_read_excel(last_month_files[0], usecols=['AccountID', 'DPD Days', 'LoanStatus'])
            dpd_lookup = dict(zip(df_last['AccountID'], df_last['DPD Days']))
            status_lookup = dict(zip(df_last['AccountID'], df_last['LoanStatus']))
            df_main['DPD Group - Last Month'] = df_main['Account ID'].map(dpd_lookup)
            df_main['Loan Status - Last Month'] = df_main['Account ID'].map(status_lookup)
            _matched_lm = int(df_main['DPD Group - Last Month'].notna().sum())
            logging.info(f"Last Month PAR loaded ({len(df_last)} rows); "
                         f"DPD-Last-Month matched {_matched_lm} of {len(df_main)} accounts")
        except (ValueError, KeyError, FileNotFoundError) as e:
            logging.warning(f"Could not load Last Month PAR: {e}")

    # [Original Step 4]
    collection_lookup = dict(zip(pivot_table['AccountID'], pivot_table['CollectionTotal']))
    date_lookup_dict = dict(zip(date_lookup['AccountID'], date_lookup['Trxdate']))

    df_main['Collection'] = df_main['Account ID'].map(collection_lookup)
    df_main['Collection Date'] = df_main['Account ID'].map(date_lookup_dict)
    df_main['Collection Date'] = pd.to_datetime(df_main['Collection Date']).dt.strftime('%d-%m-%Y')

    # [Original Step 4.5]
    # Difference
    df_main['Regular Demand'] = pd.to_numeric(df_main['Regular Demand'], errors='coerce').fillna(0)
    df_main['Collection'] = pd.to_numeric(df_main['Collection'], errors='coerce')
    difference = df_main['Regular Demand'] - df_main['Collection'].fillna(0)

    df_main['Partial Amount'] = "Not Collected"
    has_col = df_main['Collection'].notna()
    df_main.loc[has_col & (difference <= 0), 'Partial Amount'] = "Full EMI Paid"
    df_main.loc[has_col & (difference > 0), 'Partial Amount'] = "Partial Amount"

    # [Step 4.6] - Installment vs Collection columns
    df_main['Installment Amount'] = pd.to_numeric(df_main['Installment Amount'], errors='coerce').fillna(0)
    df_main['installment - collected amt'] = df_main['Installment Amount'] - df_main['Collection'].fillna(0)
    df_main['installment - collected value'] = (df_main['installment - collected amt'] <= 0).astype(int)

    # [Original Step 5]
    df_par = smart_read_excel(par_file)
    # Find DPD col
    days_group_col = None
    possible_names = ['Days Group', 'Days group', 'DaysGroup', 'Daysgroup', 'DPD Group', 'DPD Days', 'DPDDays']
    for name in possible_names:
        if name in df_par.columns:
            days_group_col = name
            break
    if days_group_col:
        dpd_lookup = dict(zip(df_par['AccountID'], df_par[days_group_col]))
        df_main['DPD Group'] = df_main['Account ID'].map(dpd_lookup)

    # Free intermediate DataFrames no longer needed
    del df_collection, df_filtered, pivot_table, date_lookup, df_par
    gc_checkpoint("eod-pandas-merge")

    # Convert 'Loan Date' to FY flag
    logging.info("STEP 3: Processing dates, FY flags, DPD mapping")
    if 'Loan Date' in df_main.columns:
        logging.info(f"Loan Date dtype: {df_main['Loan Date'].dtype}")
        logging.info(f"Loan Date sample (first 3): {df_main['Loan Date'].head(3).tolist()}")

        col = df_main['Loan Date']
        backup_col_name = "Loan Date (Original)"
        fy_start, fy_end = derive_fy_bounds(target_date)

        if pd.api.types.is_numeric_dtype(col):
            logging.info("Detected numeric Loan Date - converting from Excel serial date")
            excel_epoch = pd.Timestamp('1899-12-30')
            valid_mask = (col > 0) & (col < 100000)
            temp_dates = pd.Series(pd.NaT, index=df_main.index)
            temp_dates.loc[valid_mask] = excel_epoch + pd.to_timedelta(col[valid_mask], unit='D')
            backup_series = temp_dates.dt.strftime('%d-%m-%Y').fillna('')
            insert_backup_column(df_main, backup_col_name, backup_series)
            fy_flag = temp_dates.between(fy_start, fy_end).fillna(False).astype(int)
            df_main['Loan Date'] = fy_flag
        else:
            temp_dates = pd.to_datetime(col, errors='coerce', dayfirst=True)
            valid_count = temp_dates.notna().sum()
            logging.info(f"Loan Date valid dates: {valid_count} / {len(temp_dates)}")

            backup_series = temp_dates.dt.strftime('%d-%m-%Y').fillna('')
            insert_backup_column(df_main, backup_col_name, backup_series)
            fy_flag = temp_dates.between(fy_start, fy_end).fillna(False).astype(int)
            df_main['Loan Date'] = fy_flag

    gc_checkpoint("eod-pandas-post-processing")

    if skip_output:
        logging.info("Legacy Done (skip_output=True, skipped Excel write)")
        return df_main, None

    # Pre-compute aggregation sheets BEFORE fillna (fillna replaces NaN with ''
    # which breaks Collection notna() checks used for count metrics)
    precomputed = None
    if target_date is not None:
        try:
            precomputed = _compute_precomputed_sheets(df_main, target_date, force_regular_rules=force_regular_rules)
        except Exception as e:
            logging.warning(f"Pre-computation failed ({type(e).__name__}: {e}), continuing without precomp sheets")

    # [Original Step 6]
    logging.info("STEP 4: Writing Excel output + building report")
    df_main = _safe_fillna(df_main)

    _write_excel_fast(df_main, output_file, precomputed_sheets=precomputed)

    # Generate formatted report Excel (replaces VBA macro)
    report_path = _build_report_with_excel_fallback(
        precomputed,
        output_file,
        target_date,
        df_main,
        sheets_dir=sheets_dir,
        force_regular_rules=force_regular_rules,
    )

    gc_checkpoint("eod-pandas-excel-write")
    logging.info("Legacy Done")

    return df_main, report_path
