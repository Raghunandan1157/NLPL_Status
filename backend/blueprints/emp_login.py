"""
Employee Login Blueprint — Login-gated portal for individual employee performance.
Employees enter their Emp ID to see personal KPIs. "CEO" redirects to the full /employee/ module.
"""

import logging
from datetime import datetime

import pandas as pd
from flask import Blueprint, jsonify, request, send_from_directory

import config
from services import employee_processor
from services.column_matcher import find_column

emp_login_bp = Blueprint('emp_login', __name__)
STATIC_EMP_LOGIN_DIR = config.STATIC_DIR / 'emp-login'

# ── Lightweight employee cache (avoids full merge pipeline) ──────────
_emp_cache = {'date': None, 'employees': [], 'id_set': set()}


def _load_emp_list_fast():
    """
    Read employee list directly from demand_cache.parquet (fast).
    Avoids the full merge pipeline that get_merged_dataframe() runs.
    """
    dates = employee_processor.get_available_dates()
    if not dates:
        return []

    latest = dates[-1]['date_iso']

    # Return cached if same date
    if _emp_cache['date'] == latest and _emp_cache['employees']:
        return _emp_cache['employees']

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

    # Cache it
    _emp_cache['date'] = latest
    _emp_cache['employees'] = employees
    _emp_cache['id_set'] = id_set

    logging.info("Emp login: loaded %d employees from demand parquet", len(employees))
    return employees


# ── Static files (index) ────────────────────────────────────────────

@emp_login_bp.route('/')
def index():
    return send_from_directory(str(STATIC_EMP_LOGIN_DIR), 'index.html')


# ── API: Login / validate emp_id ─────────────────────────────────────

@emp_login_bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    emp_id = str(data.get('emp_id', '')).strip()

    if not emp_id:
        return jsonify({'error': 'Emp ID is required'}), 400

    # CEO shortcut
    if emp_id.upper() == 'CEO':
        return jsonify({'role': 'ceo'})

    # Validate emp_id exists (uses fast parquet read, not full merge)
    try:
        employees = _load_emp_list_fast()
        if not employees:
            return jsonify({'error': 'No data available yet'}), 404

        if emp_id not in _emp_cache['id_set']:
            return jsonify({'error': f'Employee "{emp_id}" not found'}), 404

        return jsonify({'role': 'employee', 'emp_id': emp_id})

    except Exception as e:
        logging.error(f"Emp login error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Employee list (for autocomplete dropdown) ───────────────────

@emp_login_bp.route('/api/employees')
def api_employees():
    try:
        employees = _load_emp_list_fast()
        return jsonify({'employees': employees})
    except Exception as e:
        logging.error(f"Emp login employees error: {e}")
        return jsonify({'employees': []}), 500


# ── API: My Performance (individual) ─────────────────────────────────

@emp_login_bp.route('/api/my-performance')
def api_my_performance():
    emp_id = request.args.get('emp_id', '').strip()
    date_str = request.args.get('date', '').strip()

    if not emp_id:
        return jsonify({'error': 'emp_id parameter required'}), 400
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
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
        logging.error(f"Emp my-performance error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Status / data availability ──────────────────────────────────

@emp_login_bp.route('/api/status')
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
        employees = _load_emp_list_fast()
        has_emp = len(employees) > 0

        return jsonify({
            'available': has_emp,
            'dates': dates,
            'message': 'Employee data available' if has_emp else 'Emp ID column not found in data'
        })
    except Exception as e:
        logging.error(f"Emp login status error: {e}")
        return jsonify({'available': False, 'error': str(e), 'dates': []}), 500


# ── Static file catch-all (MUST be the last route) ──────────────────

@emp_login_bp.route('/<path:filename>', methods=['GET'])
def serve_static(filename):
    file_path = STATIC_EMP_LOGIN_DIR / filename
    if not file_path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(str(STATIC_EMP_LOGIN_DIR), filename)
