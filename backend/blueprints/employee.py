"""
Employee Performance Blueprint — API endpoints for employee-level analytics.
All data comes from Instant Report cached data (read-only).
"""

import logging

import pandas as pd
from flask import Blueprint, jsonify, request, send_from_directory

import config
from services import instant_cache
from services import employee_processor

employee_bp = Blueprint('employee', __name__)
STATIC_EMPLOYEE_DIR = config.STATIC_DIR / 'employee'


# ── Static files (index) ────────────────────────────────────────────

@employee_bp.route('/')
def index():
    return send_from_directory(str(STATIC_EMPLOYEE_DIR), 'index.html')


# ── API: Status / data availability ─────────────────────────────────

@employee_bp.route('/api/status')
def api_status():
    try:
        dates = employee_processor.get_available_dates()
        if not dates:
            return jsonify({
                'available': False,
                'message': 'No cached dates with demand data',
                'dates': []
            })

        # Load the latest date to check for Emp ID column
        latest = dates[-1]['date_iso']
        df = employee_processor.get_merged_dataframe(latest)
        emp_cols = employee_processor.detect_employee_columns(df)

        return jsonify({
            'available': emp_cols['emp_id'] is not None,
            'emp_id_column': emp_cols['emp_id'],
            'emp_name_column': emp_cols['emp_name'],
            'dates': dates,
            'message': (
                'Employee data available'
                if emp_cols['emp_id']
                else 'Emp ID column not found in data'
            )
        })
    except Exception as e:
        logging.error(f"Employee status error: {e}")
        return jsonify({'available': False, 'error': str(e), 'dates': []}), 500


# ── API: Employee list ───────────────────────────────────────────────

@employee_bp.route('/api/employees')
def api_employees():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)
        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404
        employees = employee_processor.compute_employee_list(df, emp_cols)
        return jsonify({'employees': employees})
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Employee list error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Leaderboard (paginated, sortable, filterable) ───────────────

@employee_bp.route('/api/leaderboard')
def api_leaderboard():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400

    sort_by = request.args.get('sort', 'collection_pct')
    sort_order = request.args.get('order', 'desc')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '')
    region = request.args.get('region', '')
    district = request.args.get('district', '')
    branch = request.args.get('branch', '')

    try:
        from datetime import datetime
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)
        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404

        result = employee_processor.compute_employee_leaderboard(
            df, target_date, emp_cols,
            sort_by=sort_by, sort_order=sort_order,
            page=page, per_page=per_page,
            search=search, region=region, district=district, branch=branch
        )
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Employee leaderboard error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Individual employee performance ─────────────────────────────

@employee_bp.route('/api/employee/<emp_id>')
def api_employee(emp_id):
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        from datetime import datetime
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)
        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404
        result = employee_processor.compute_employee_performance(
            df, emp_id, target_date, emp_cols
        )
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Employee performance error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Employee account-level detail ───────────────────────────────

@employee_bp.route('/api/employee/<emp_id>/accounts')
def api_employee_accounts(emp_id):
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        df = employee_processor.get_merged_dataframe(date_str)
        emp_cols = employee_processor.detect_employee_columns(df)
        if not emp_cols['emp_id']:
            return jsonify({'error': 'Emp ID column not found'}), 404
        result = employee_processor.compute_employee_accounts(df, emp_id, emp_cols)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Employee accounts error: {e}")
        return jsonify({'error': str(e)}), 500


# ── Static file catch-all (MUST be the last route) ──────────────────
# This route handles all remaining GET requests for static assets
# (JS, CSS, images, etc.) under the /employee/ prefix.
# It must be defined after all /api/* routes so it does not shadow them.

@employee_bp.route('/<path:filename>', methods=['GET'])
def serve_static(filename):
    file_path = STATIC_EMPLOYEE_DIR / filename
    if not file_path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(str(STATIC_EMPLOYEE_DIR), filename)
