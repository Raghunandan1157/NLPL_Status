"""
Employee V3 Blueprint — Login-gated portal for individual employee performance.
Employees enter their Emp ID to see personal KPIs. "CEO" redirects to the full /employee/ module.
"""

import logging
import re
import threading
from datetime import datetime

import pandas as pd
from flask import Blueprint, jsonify, request, send_from_directory

import config
from services import employee_processor
from services.column_matcher import find_column

try:
    from services.employee_processor import get_cached_trend_point
except ImportError:
    get_cached_trend_point = None

emp_v3_bp = Blueprint('emp_v3', __name__)
STATIC_EMP_V3_DIR = config.STATIC_DIR / 'emp-v3'

# ── Lightweight employee cache (avoids full merge pipeline) ──────────
_emp_v3_cache = {'date': None, 'employees': [], 'id_set': set()}
_emp_v3_lock = threading.Lock()


def _validate_emp_id(emp_id):
    """Return True if emp_id is a non-empty alphanumeric string (allows _ and -)."""
    return bool(emp_id) and bool(re.match(r'^[A-Za-z0-9_-]+$', emp_id))


def _load_emp_list_fast_v3():
    """
    Read employee list directly from demand_cache.parquet (fast).
    Avoids the full merge pipeline that get_merged_dataframe() runs.
    """
    dates = employee_processor.get_available_dates()
    if not dates:
        return []

    latest = dates[-1]['date_iso']

    with _emp_v3_lock:
        # Return cached if same date
        if _emp_v3_cache['date'] == latest and _emp_v3_cache['employees']:
            return _emp_v3_cache['employees']

    # Read demand parquet directly (no merge needed)
    parts = latest.split('-')
    demand_path = config.BACKEND_MONTHLY_DIR / f"{parts[0]}-{parts[1]}" / 'demand_cache.parquet'
    if not demand_path.exists():
        return []

    try:
        df = pd.read_parquet(demand_path, columns=[
            'Emp ID', 'Emp Name', 'BranchName', 'Region',
        ])
    except Exception:
        df = pd.read_parquet(demand_path)

    # Detect columns
    emp_id_col = find_column(
        df, 'Emp ID', 'EmpID', 'Emp Id', 'Employee ID',
        'emp_id', 'EMPID', 'Emp_ID', 'EmployeeID',
    )
    if not emp_id_col:
        return []

    emp_name_col = find_column(
        df, 'Emp Name', 'EmpName', 'Employee Name',
        'emp_name', 'Emp_Name', 'EmployeeName',
    )
    branch_col = find_column(
        df, 'BranchName', 'Branch Name', 'Branch', 'Branchname',
    )

    # Group by emp_id
    grouped = df.groupby(df[emp_id_col].astype(str).str.strip())
    employees = []
    id_set = set()

    for eid, grp in grouped:
        eid = str(eid).strip()
        if not eid:
            continue
        name = ''
        if emp_name_col and emp_name_col in grp.columns:
            name = str(grp[emp_name_col].iloc[0] or '').strip()
        branch = ''
        if branch_col and branch_col in grp.columns:
            branch = str(grp[branch_col].iloc[0] or '').strip()

        employees.append({
            'emp_id': eid,
            'emp_name': name,
            'branch': branch,
            'account_count': len(grp),
        })
        id_set.add(eid)

    employees.sort(key=lambda e: e['emp_id'])

    with _emp_v3_lock:
        # Cache it
        _emp_v3_cache['date'] = latest
        _emp_v3_cache['employees'] = employees
        _emp_v3_cache['id_set'] = id_set

    logging.info("Emp V3: loaded %d employees from demand parquet", len(employees))
    return employees


# ── Static files (index) ────────────────────────────────────────────

@emp_v3_bp.route('/')
def index():
    return send_from_directory(str(STATIC_EMP_V3_DIR), 'index.html')


# ── API: Login / validate emp_id ─────────────────────────────────────

@emp_v3_bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    emp_id = str(data.get('emp_id', '')).strip()

    if not emp_id:
        return jsonify({'error': 'Emp ID is required'}), 400

    # CEO shortcut
    if emp_id.upper() == 'CEO':
        return jsonify({'role': 'ceo'})

    if not _validate_emp_id(emp_id):
        return jsonify({'error': 'Invalid Emp ID format'}), 400

    # Validate emp_id exists (uses fast parquet read, not full merge)
    try:
        employees = _load_emp_list_fast_v3()
        if not employees:
            return jsonify({'error': 'No data available yet'}), 404

        with _emp_v3_lock:
            found = emp_id in _emp_v3_cache['id_set']
        if not found:
            return jsonify({'error': f'Employee "{emp_id}" not found'}), 404

        return jsonify({'role': 'employee', 'emp_id': emp_id})

    except Exception as e:
        logging.error(f"Emp V3 login error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── API: Employee list (for autocomplete dropdown) ───────────────────

@emp_v3_bp.route('/api/employees')
def api_employees():
    try:
        employees = _load_emp_list_fast_v3()
        return jsonify({'employees': employees})
    except Exception as e:
        logging.error(f"Emp V3 employees error: {e}")
        return jsonify({'error': 'Internal server error', 'employees': []}), 500


# ── API: My Performance (individual) ─────────────────────────────────

@emp_v3_bp.route('/api/my-performance')
def api_my_performance():
    emp_id = request.args.get('emp_id', '').strip()
    date_str = request.args.get('date', '').strip()

    if not emp_id:
        return jsonify({'error': 'emp_id parameter required'}), 400
    if not _validate_emp_id(emp_id):
        return jsonify({'error': 'Invalid emp_id format'}), 400
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        # Merged DF cache: populated by get_merged_dataframe
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)

        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404

        perf = employee_processor.compute_employee_performance(
            df, emp_id, target_date, emp_cols
        )
        accounts = employee_processor.compute_employee_accounts(df, emp_id, emp_cols)

        return jsonify({'performance': perf, 'accounts': accounts})

    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Emp V3 my-performance error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── API: Status / data availability ──────────────────────────────────

@emp_v3_bp.route('/api/status')
def api_status():
    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({
                'available': False,
                'message': 'No cached dates with demand data',
                'dates': []
            })

        # Use fast check — just see if employee list loaded
        employees = _load_emp_list_fast_v3()
        has_emp = len(employees) > 0

        return jsonify({
            'available': has_emp,
            'dates': dates,
            'message': 'Employee data available' if has_emp else 'Emp ID column not found in data'
        })
    except Exception as e:
        logging.error(f"Emp V3 status error: {e}")
        return jsonify({'available': False, 'error': 'Internal server error', 'dates': []}), 500


# ── API: Collection Trend (all dates) ────────────────────────────────

@emp_v3_bp.route('/api/collection-trend')
def api_collection_trend():
    emp_id = request.args.get('emp_id', '').strip()
    if not emp_id:
        return jsonify({'error': 'emp_id parameter required'}), 400
    if not _validate_emp_id(emp_id):
        return jsonify({'error': 'Invalid emp_id format'}), 400

    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({'trend': []})

        trend = []
        for date_info in dates:
            date_str = date_info['date_iso']
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
                # Merged DF cache: populated by get_merged_dataframe
                df = employee_processor.get_merged_dataframe(date_str)
                emp_cols = employee_processor.detect_employee_columns(df)
                if not emp_cols['emp_id']:
                    continue

                point = employee_processor.get_cached_trend_point(
                    emp_id, date_str, target_date, df, emp_cols
                )
                trend.append({
                    'date_iso': date_str,
                    'date_display': date_info.get('date_display', date_str),
                    **point,
                })
            except Exception as e:
                logging.warning(f"Emp V3 trend: skipping date {date_str}: {e}")
                continue

        return jsonify({'trend': trend})

    except Exception as e:
        logging.error(f"Emp V3 collection-trend error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── Mobile adapter helper ─────────────────────────────────────────────

def _reshape_for_mobile(perf, accounts_data, date_str):
    """Reshape compute_employee_performance + accounts output into Flutter format."""
    emp = perf.get('employee', {})
    kpis = perf.get('kpis', {})

    title_map = {
        'Regular Demand vs Collection': 'Regular Demand',
        '1-30 DPD Bucket': 'DPD 1-30',
        '31-60 DPD Bucket': 'DPD 31-60',
        'PNPA (61-90 DPD)': 'PNPA',
        'NPA': 'NPA',
    }

    def _extract_stats(stats, is_npa=False):
        if is_npa:
            demand = stats.get('demand', 0)
            collection = stats.get('activation_account', 0) + stats.get('closure_account', 0)
            pct = round(collection / demand * 100, 2) if demand > 0 else 0
            return demand, collection, pct
        return stats.get('demand', 0), stats.get('collection', 0), stats.get('collection_pct', 0)

    sections = []
    for s in perf.get('sections', []):
        raw_title = s.get('title', '')
        is_npa = 'NPA' == raw_title

        o_demand, o_collection, o_pct = _extract_stats(s.get('overall', {}), is_npa)
        f_demand, f_collection, f_pct = _extract_stats(s.get('fy', {}), is_npa)

        products = []
        for p in s.get('products', []):
            # Overall stats (from 'overall' sub-dict if present, else from top-level keys)
            p_overall = p.get('overall', p)
            p_o_demand, p_o_collection, p_o_pct = _extract_stats(p_overall, is_npa)
            # FY stats (from 'fy' sub-dict if present, else zeros)
            p_fy = p.get('fy', {})
            p_f_demand, p_f_collection, p_f_pct = _extract_stats(p_fy, is_npa)
            products.append({
                'product': p.get('name', ''),
                'overall_demand': p_o_demand,
                'overall_collection': p_o_collection,
                'overall_pct': p_o_pct,
                'fy_demand': p_f_demand,
                'fy_collection': p_f_collection,
                'fy_pct': p_f_pct,
            })

        sections.append({
            'title': title_map.get(raw_title, raw_title),
            'overall_demand': o_demand,
            'overall_collection': o_collection,
            'overall_pct': o_pct,
            'fy_demand': f_demand,
            'fy_collection': f_collection,
            'fy_pct': f_pct,
            'products': products,
        })

    # Assign each account to a section using the same business logic
    # as compute_employee_performance:
    #   Regular Demand: all accounts (dpd_group current)
    #   DPD 1-30: dpd_group_lm contains '1-30', active status
    #   DPD 31-60: dpd_group_lm contains '31-60', active status
    #   PNPA: dpd_days contains '61-90', active status
    #   NPA: loan_status contains 'npa'
    section_accounts = {
        'Regular Demand': [],
        'DPD 1-30': [],
        'DPD 31-60': [],
        'PNPA': [],
        'NPA': [],
    }

    for a in accounts_data.get('accounts', []):
        acct = {
            'account_id': a.get('account_id', ''),
            'product': a.get('product', ''),
            'demand': a.get('demand', 0),
            'collection': a.get('collection', 0),
            'dpd_group': a.get('dpd_group', ''),
            'loan_status': a.get('loan_status', ''),
        }
        dpd_lm = a.get('dpd_group_lm', '').lower()
        dpd_days = a.get('dpd_days', '').lower()
        loan_status = a.get('loan_status', '').lower()
        is_active = 'active' in loan_status

        # Assign to sections (account can appear in multiple sections)
        # Regular: accounts with demand > 0
        if a.get('demand', 0) > 0:
            section_accounts['Regular Demand'].append(acct)

        if '1-30' in dpd_lm and is_active:
            section_accounts['DPD 1-30'].append(acct)
        if '31-60' in dpd_lm and is_active:
            section_accounts['DPD 31-60'].append(acct)
        if '61-90' in dpd_days and is_active:
            section_accounts['PNPA'].append(acct)
        if 'npa' in loan_status:
            section_accounts['NPA'].append(acct)

    # Attach accounts to each section
    for s in sections:
        s['accounts'] = section_accounts.get(s['title'], [])

    return {
        'kpi': {
            'emp_id': emp.get('emp_id', ''),
            'emp_name': emp.get('emp_name', ''),
            'date': date_str,
            'region': emp.get('region', ''),
            'area': emp.get('area', ''),
            'branch': emp.get('branch', ''),
            'demand': kpis.get('demand', 0),
            'collection': kpis.get('collection', 0),
            'collection_pct': kpis.get('collection_pct', 0),
            'ftod': kpis.get('ftod', 0),
            'npa_count': kpis.get('npa_count', 0),
        },
        'sections': sections,
    }


# ── Mobile API: Status ────────────────────────────────────────────────

@emp_v3_bp.route('/api/mobile/status')
def api_mobile_status():
    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({
                'available': False,
                'message': 'No cached dates with demand data',
                'dates': []
            })

        employees = _load_emp_list_fast_v3()
        has_emp = len(employees) > 0

        return jsonify({
            'available': has_emp,
            'dates': dates,
            'message': 'Employee data available' if has_emp else 'Emp ID column not found in data'
        })
    except Exception as e:
        logging.error(f"Mobile status error: {e}")
        return jsonify({'available': False, 'error': 'Internal server error', 'dates': []}), 500


# ── Mobile API: My Performance (reshaped for Flutter) ─────────────────

@emp_v3_bp.route('/api/mobile/my-performance')
def api_mobile_my_performance():
    emp_id = request.args.get('emp_id', '').strip()
    date_str = request.args.get('date', '').strip()

    if not emp_id:
        return jsonify({'error': 'emp_id parameter required'}), 400
    if not _validate_emp_id(emp_id):
        return jsonify({'error': 'Invalid emp_id format'}), 400
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        # Merged DF cache: populated by get_merged_dataframe
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)

        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404

        perf = employee_processor.compute_employee_performance(
            df, emp_id, target_date, emp_cols
        )
        accounts = employee_processor.compute_employee_accounts(df, emp_id, emp_cols)

        result = _reshape_for_mobile(perf, accounts, date_str)
        return jsonify(result)

    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Mobile my-performance error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── Mobile API: Collection Trend ──────────────────────────────────────

@emp_v3_bp.route('/api/mobile/collection-trend')
def api_mobile_collection_trend():
    emp_id = request.args.get('emp_id', '').strip()
    if not emp_id:
        return jsonify({'error': 'emp_id parameter required'}), 400
    if not _validate_emp_id(emp_id):
        return jsonify({'error': 'Invalid emp_id format'}), 400

    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({'trend': []})

        trend = []
        for date_info in dates:
            date_str = date_info['date_iso']
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
                # Merged DF cache: populated by get_merged_dataframe
                df = employee_processor.get_merged_dataframe(date_str)
                emp_cols = employee_processor.detect_employee_columns(df)
                if not emp_cols['emp_id']:
                    continue

                point = employee_processor.get_cached_trend_point(
                    emp_id, date_str, target_date, df, emp_cols
                )
                trend.append({
                    'date_iso': date_str,
                    'date_display': date_info.get('date_display', date_str),
                    **point,
                })
            except Exception as e:
                logging.warning(f"Mobile trend: skipping date {date_str}: {e}")
                continue

        return jsonify({'trend': trend})

    except Exception as e:
        logging.error(f"Mobile collection-trend error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── Mobile API: Account Detail (all columns) ──────────────────────────

@emp_v3_bp.route('/api/mobile/account-detail')
def api_mobile_account_detail():
    account_id = request.args.get('account_id', '').strip()
    date_str = request.args.get('date', '').strip()

    if not account_id:
        return jsonify({'error': 'account_id parameter required'}), 400
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400

    try:
        # Merged DF cache: populated by get_merged_dataframe
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)

        detail = employee_processor.get_account_detail(df, account_id, emp_cols)
        if detail is None:
            return jsonify({'error': f'Account "{account_id}" not found'}), 404

        return jsonify({'account': detail})

    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Mobile account-detail error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ── Mobile API: Accounts list (for customer login) ─────────────────

@emp_v3_bp.route('/api/mobile/accounts')
def api_mobile_accounts():
    """List all accounts with basic info for customer login."""
    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({'accounts': [], 'error': 'No data available'}), 200

        latest = dates[-1]['date_iso']
        # Merged DF cache: populated by get_merged_dataframe
        df = employee_processor.get_merged_dataframe(latest)
        emp_cols = employee_processor.detect_employee_columns(df)

        account_col = find_column(df, 'Account ID', 'AccountID', 'Account_ID')
        client_col = find_column(df, 'Client Name', 'ClientName', 'Client_Name', 'Customer Name')
        product_col = find_column(df, 'Product Name', 'ProductName', 'Product', 'Product name')
        branch_col = find_column(df, 'BranchName', 'Branch Name', 'Branch', 'Branchname')

        if not account_col:
            return jsonify({'accounts': [], 'error': 'Account column not found'}), 200

        accounts = []
        seen = set()
        for _, row in df.iterrows():
            aid = str(row.get(account_col, '')).strip()
            if not aid or aid in seen:
                continue
            seen.add(aid)
            accounts.append({
                'account_id': aid,
                'client_name': str(row.get(client_col, '')).strip() if client_col else '',
                'product': str(row.get(product_col, '')).strip() if product_col else '',
                'branch': str(row.get(branch_col, '')).strip() if branch_col else '',
            })

        accounts.sort(key=lambda x: x['account_id'])
        return jsonify({'accounts': accounts, 'count': len(accounts)})

    except Exception as e:
        logging.error(f"Mobile accounts list error: {e}")
        return jsonify({'accounts': [], 'error': 'Internal server error'}), 500


# ── Mobile API: All Employee KPIs (bulk) ─────────────────────────────

@emp_v3_bp.route('/api/mobile/all-kpis')
def api_mobile_all_kpis():
    """Return KPI summary for ALL employees in one call (used by management dashboard)."""
    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({'error': 'No data available', 'kpis': []}), 200

        latest = dates[-1]['date_iso']
        target_date = datetime.strptime(latest, '%Y-%m-%d')
        # Merged DF cache: populated by get_merged_dataframe
        df = employee_processor.get_merged_dataframe(latest)
        emp_cols = employee_processor.detect_employee_columns(df)

        col_emp_id = emp_cols.get('emp_id')
        col_emp_name = emp_cols.get('emp_name')
        if not col_emp_id:
            return jsonify({'error': 'Emp ID column not found', 'kpis': []}), 200

        region_col = find_column(df, 'Region', 'RegionName', 'Region Name')
        area_col = find_column(df, 'Area', 'AreaName', 'Area Name', 'District', 'DistrictName', 'District Name')
        branch_col = find_column(df, 'BranchName', 'Branch Name', 'Branch', 'Branchname')
        # Use the SAME column as compute_employee_performance: "No of Regular Demand" (0/1 count)
        demand_count_col = find_column(df, 'No of Regular Demand', 'No. of Regular Demand', 'NoOfRegularDemand')
        dpd_group_col = find_column(df, 'DPD Group', 'DPDGroup', 'dpd_group')
        cumulative_col = find_column(df, 'Cumulative Demand', 'Cumulative', 'CumulativeDemand')
        loan_status_col = find_column(df, 'Loan Status', 'LoanStatus', 'loan_status')
        dpd_lm_col = find_column(df, 'DPD Group - Last Month', 'DPD Group LM', 'DPDGroupLastMonth')

        if not demand_count_col:
            return jsonify({'error': 'Demand count column not found', 'kpis': []}), 200

        # Filter by meeting date like compute_employee_performance does
        meeting_col = find_column(df, 'Meeting Date', 'MeetingDate', 'meeting_date')
        if meeting_col and meeting_col in df.columns:
            df_copy = df.copy()
            df_copy[meeting_col] = pd.to_datetime(df_copy[meeting_col], errors='coerce')
            df_dated = df_copy[df_copy[meeting_col] <= target_date]
        else:
            df_dated = df

        # Pre-cast demand_count to numeric for fast groupby ops
        df_dated[demand_count_col] = pd.to_numeric(df_dated[demand_count_col], errors='coerce').fillna(0).astype(int)

        # Flag for "collected" rows: demand > 0 AND dpd_group does NOT contain '1-30'
        # This matches _regular_stats logic: collection = demand where dpd NOT LIKE '%1-30%'
        if dpd_group_col and dpd_group_col in df_dated.columns:
            dpd_str = df_dated[dpd_group_col].astype(str).fillna('')
            df_dated['_collected'] = (df_dated[demand_count_col] > 0) & (~dpd_str.str.contains('1-30', na=False))
        else:
            df_dated['_collected'] = df_dated[demand_count_col] > 0

        # Group by employee
        grouped = df_dated.groupby(df_dated[col_emp_id].astype(str).str.strip())
        kpis = []

        for eid, grp in grouped:
            eid = str(eid).strip()
            if not eid:
                continue

            name = ''
            if col_emp_name and col_emp_name in grp.columns:
                name = str(grp[col_emp_name].iloc[0] or '').strip()
            region = str(grp[region_col].iloc[0] or '').strip() if region_col and region_col in grp.columns else ''
            area = str(grp[area_col].iloc[0] or '').strip() if area_col and area_col in grp.columns else ''
            branch = str(grp[branch_col].iloc[0] or '').strip() if branch_col and branch_col in grp.columns else ''

            # demand = SUM of "No of Regular Demand" (same as _regular_stats)
            demand = int(grp[demand_count_col].sum())
            # collection = SUM of "No of Regular Demand" WHERE DPD Group NOT LIKE '%1-30%'
            collection = int((grp[demand_count_col] * grp['_collected'].astype(int)).sum())
            coll_pct = round(collection / demand * 100, 2) if demand > 0 else 0
            # ftod = demand - collection (same as _regular_stats)
            ftod = demand - collection

            # NPA count: count of rows with cumulative demand where loan status contains 'npa'
            npa_count = 0
            if loan_status_col and loan_status_col in grp.columns:
                try:
                    npa_mask = grp[loan_status_col].astype(str).str.lower().str.contains('npa', na=False)
                    if dpd_lm_col and dpd_lm_col in grp.columns:
                        dpd_lm_str = grp[dpd_lm_col].astype(str).str.strip()
                        npa_mask = npa_mask & (~dpd_lm_str.str.contains('0 Days', na=False)) & (dpd_lm_str != '')
                    if cumulative_col and cumulative_col in grp.columns:
                        npa_count = int(pd.to_numeric(grp.loc[npa_mask, cumulative_col], errors='coerce').fillna(0).sum())
                    else:
                        npa_count = int(npa_mask.sum())
                except Exception:
                    pass

            kpis.append({
                'emp_id': eid,
                'emp_name': name,
                'region': region,
                'area': area,
                'branch': branch,
                'demand': demand,
                'collection': collection,
                'collection_pct': coll_pct,
                'ftod': ftod,
                'npa_count': npa_count,
            })

        kpis.sort(key=lambda x: x['emp_id'])
        logging.info("Mobile all-kpis: returned %d employees", len(kpis))
        return jsonify({'date': latest, 'kpis': kpis, 'count': len(kpis)})

    except Exception as e:
        logging.error(f"Mobile all-kpis error: {e}")
        return jsonify({'error': 'Internal server error', 'kpis': []}), 500


# ── Static file catch-all (MUST be the last route) ──────────────────

@emp_v3_bp.route('/<path:filename>', methods=['GET'])
def serve_static(filename):
    file_path = STATIC_EMP_V3_DIR / filename
    if not file_path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(str(STATIC_EMP_V3_DIR), filename)
