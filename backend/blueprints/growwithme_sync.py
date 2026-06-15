"""
GrowWithMe Sync Blueprint (Phase 2 — local MySQL API)
=====================================================
Pushes EOD daily, Quick hourly, Disbursement and Portfolio data into the
``growwithme-local`` Node/Express API (now deployed on AWS EC2, MariaDB
``Growwithme_NEWDB``; base URL in GROWWITHME_API_URL).

The growwithme-local ``/sync`` endpoints expect rows **already exploded** into
DPD buckets + NPA actions:

  POST {GROWWITHME_API_URL}/api/collection/sync     (EOD daily — grain 2)
  POST {GROWWITHME_API_URL}/api/hourly/sync         (Quick hourly — grain 1)
  POST {GROWWITHME_API_URL}/api/disbursement/sync   (Disbursement — monthly)
  POST {GROWWITHME_API_URL}/api/portfolio/sync      (Portfolio POS — monthly)

This blueprint reuses the shared report parsers (``blueprints.report_parsers``)
and transforms each flat row into the bucketed shape using the GrowwithmeDB ids:

  dpd_bucket : 1=regular 2=1_30 3=31_60 4=pnpa 5=on_date (6=61_90 derived, skipped)
  npa_action : 1=activation 2=closure
  product_type: 1=IGL 2=FIG 3=IL

Config (env, no engine-config change needed):
  GROWWITHME_API_URL    base URL of the Node API (default http://localhost:4000)
  GROWWITHME_API_TOKEN  optional `Authorization: Token <t>`; the /sync push
                        endpoints are intentionally open, so this is usually blank.

NOTE on semantics (differs from the Supabase path):
  The growwithme-local /sync endpoints are INSERT-only (no delete-by-date), so
  re-running a sync ADDS rows rather than overriding. Callers decide when to push.
  Disbursement is stored monthly (db_month = first-of-month), so daily rows are
  aggregated up to the month here.
"""

import os
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
import requests as http_requests

import config

# Reuse the exact parsers the Supabase sync uses, so AWS / Supabase / GrowwithmeDB
# all ingest identical numbers from the same report files.
from blueprints.report_parsers import (
    _employee_report_path,
    _parse_report,
    _quick_report_path,
    _parse_quick_report,
    _norm,
    _num,
    _DISB_PRODUCT_TYPE_ID,
)
from openpyxl import load_workbook

logger = logging.getLogger(__name__)
growwithme_bp = Blueprint('growwithme_sync', __name__)

GROWWITHME_API_URL = (os.environ.get('GROWWITHME_API_URL') or 'http://localhost:4000').rstrip('/')
GROWWITHME_API_TOKEN = os.environ.get('GROWWITHME_API_TOKEN', '')

# Flat metric column -> (dpd_bucket_id). Each bucket pulls four metric columns:
# (demand_count, demand_amt, collection_count, collection_amt).
_DPD_MAP = [
    (1, 'regular_demand',  'regular_demand_amt',  'regular_collection',  'regular_collection_amt'),
    (2, 'demand_1_30',     'demand_1_30_amt',     'collection_1_30',     'collection_1_30_amt'),
    (3, 'demand_31_60',    'demand_31_60_amt',    'collection_31_60',    'collection_31_60_amt'),
    (4, 'pnpa_demand',     'pnpa_demand_amt',     'pnpa_collection',     'pnpa_collection_amt'),
    (5, 'on_date_demand',  'on_date_demand_amt',  'on_date_collection',  'on_date_collection_amt'),
]
# npa_action_id -> (accounts column, amount column).
_NPA_MAP = [
    (1, 'npa_act_acc', 'npa_act_amt'),  # activation
    (2, 'npa_clo_acc', 'npa_clo_amt'),  # closure
]

# Reuse a connection-pooled session — daily syncs POST one row per employee.
_session = http_requests.Session()


def _headers():
    h = {'Content-Type': 'application/json'}
    if GROWWITHME_API_TOKEN:
        h['Authorization'] = f'Token {GROWWITHME_API_TOKEN}'
    return h


def _post(path, payload):
    """POST to the growwithme-local API. Returns (ok, result_or_errmsg)."""
    url = f'{GROWWITHME_API_URL}{path}'
    try:
        resp = _session.post(url, json=payload, headers=_headers(), timeout=60)
    except http_requests.exceptions.RequestException as e:
        return False, f'growwithme-api not reachable: {e}'
    if resp.status_code not in (200, 201, 204):
        return False, f'{path} failed ({resp.status_code}): {(resp.text or "")[:300]}'
    try:
        return True, resp.json()
    except ValueError:
        return True, None


def _explode(rec, period_date=None, period_hour=None):
    """Turn one flat 25-metric row into one growwithme /sync batch row.

    The whole-scope override lives on the API side, so the batch carries the
    exploded employee rows; date/hour identify the scope being replaced.
    """
    dpd = [
        {
            'bucket_id': bk,
            'demand_count': rec.get(dc, 0),
            'demand_amt': rec.get(da, 0),
            'collection_count': rec.get(cc, 0),
            'collection_amt': rec.get(ca, 0),
        }
        for bk, dc, da, cc, ca in _DPD_MAP
    ]
    npa = [
        {'action_id': aid, 'accounts': rec.get(acc, 0), 'amount': rec.get(amt, 0)}
        for aid, acc, amt in _NPA_MAP
    ]
    # Quick hourly rows carry the combined product id 0 (no product split); send
    # NULL rather than 0 since GrowwithmeDB product_type has no id 0.
    pt = rec.get('product_type_id')
    row = {
        'emp_id': rec['emp_id'],
        'product_type_id': pt if pt else None,
        'npa_cases': rec.get('npa_cases', 0),
        'dpd': dpd,
        'npa': npa,
    }
    if period_date is not None:
        row['period_date'] = period_date
    if period_hour is not None:
        row['period_hour'] = period_hour
    return row


def _push_batch(path, payload):
    """POST a single batch to `path`. Returns (response_dict, http_status)."""
    ok, res = _post(path, payload)
    if not ok:
        status = 502 if 'not reachable' in str(res) else 502
        return {'success': False, 'message': res, 'inserted': 0}, status
    res = res or {}
    inserted = int(res.get('inserted') or 0)
    skipped = int(res.get('skipped') or 0)
    msg = f'{inserted} rows synced to growwithme-local'
    if skipped:
        msg += f' · {skipped} skipped (employee not in GrowwithmeDB)'
    return {'success': True, 'inserted': inserted, 'skipped': skipped, 'message': msg}, 200


@growwithme_bp.route('/ping', methods=['GET'])
def ping():
    """Read-only reachability check — hits the open GET /api index."""
    try:
        resp = _session.get(f'{GROWWITHME_API_URL}/api', headers=_headers(), timeout=15)
    except http_requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'url': GROWWITHME_API_URL,
                        'reachable': False, 'message': f'growwithme-api not reachable: {e}'}), 502
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    return jsonify({
        'success': resp.status_code == 200,
        'url': GROWWITHME_API_URL,
        'reachable': True,
        'httpStatus': resp.status_code,
        'database': body.get('database'),
        'message': 'growwithme-local API reachable.' if resp.status_code == 200
                   else f'Reachable, unexpected response (HTTP {resp.status_code}).',
    }), 200


@growwithme_bp.route('/sync-daily', methods=['POST'])
def sync_daily():
    """Push the latest EOD Employee Report into GrowwithmeDB (collection grain 2).

    JSON body: {"date": "YYYY-MM-DD"}. One POST /api/collection/sync per employee
    row (the Node endpoint inserts a single employee-period at a time).
    NOTE: insert-only — re-running for the same date appends new rows.
    """
    data = request.get_json(silent=True) or {}
    date = (data.get('date') or '').strip()
    if not date:
        return jsonify({'success': False, 'message': 'date is required (YYYY-MM-DD)'}), 400

    path = _employee_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No EOD Employee Report found. Run EOD processing first.'}), 404
    try:
        rows = _parse_report(path)
    except Exception as e:
        logger.warning(f'GrowwithmeDB daily sync: report parse failed: {e}')
        return jsonify({'success': False, 'message': f'Report parse failed: {e}'}), 500
    if not rows:
        return jsonify({'success': False, 'message': 'No employee rows found in report.'}), 400

    batch = {'period_date': date, 'rows': [_explode(r) for r in rows]}
    body, status = _push_batch('/api/collection/sync', batch)
    body.setdefault('date', date)
    if body.get('success'):
        logger.info(f"GrowwithmeDB daily sync: {body['inserted']} rows for {date}")
    return jsonify(body), status


@growwithme_bp.route('/sync-hourly', methods=['POST'])
def sync_hourly():
    """Push the latest Quick Report into GrowwithmeDB (collection grain 1).

    JSON body (optional): {"date": "YYYY-MM-DD", "period_hour": <0-23>}.
    The Quick Report is an intra-day snapshot with no hour column, so the hour
    defaults to the current local hour (overridable). One POST /api/hourly/sync
    per employee row.
    """
    data = request.get_json(silent=True) or {}
    date = (data.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    raw_hour = data.get('period_hour')
    period_hour = int(raw_hour) if raw_hour is not None else datetime.now().hour

    path = _quick_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No Quick Report found. Run Quick Hourly processing first.'}), 404
    try:
        rows = _parse_quick_report(path)
    except Exception as e:
        logger.warning(f'GrowwithmeDB hourly sync: report parse failed: {e}')
        return jsonify({'success': False, 'message': f'Quick Report parse failed: {e}'}), 500
    if not rows:
        return jsonify({'success': False, 'message': 'No employee rows found in Quick Report.'}), 400

    batch = {'rows': [_explode(r, period_date=date, period_hour=period_hour) for r in rows]}
    body, status = _push_batch('/api/hourly/sync', batch)
    body.update(date=date, period_hour=period_hour)
    if body.get('success'):
        logger.info(f"GrowwithmeDB hourly sync: {body['inserted']} rows for {date} h{period_hour}")
    return jsonify(body), status


# ── Portfolio (POS) sync ──────────────────────────────────────────────
# Portfolio is NOT in the daily EOD report — it comes from the Month-End
# Employee Report's `POS` sheet (branch+product PrincipalOS, computed from PAR).
# That sheet's grain matches GrowwithmeDB's portfolio_period (branch+product+month)
# exactly, so we read it directly and push one row per (branch, product).
#
# The growwithme-local /api/portfolio/sync endpoint does a whole-month override
# (delete the month, then insert), so every call replaces the month.

# Product (sheet/Product Name) -> GrowwithmeDB product_type_id. VVY == IL.
_PORTFOLIO_PT_ID = {'IGL': 1, 'FIG': 2, 'IL': 3, 'VVY': 3}

# Three-letter month label -> month number, for {"month":"MAR","year":2026}.
_MONTH_NUM = {m: f'{i:02d}' for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], start=1)}

# POS column header -> growwithme pos bucket key (sent to /api/portfolio/sync).
_POS_HEADER_KEY = [
    ('regular_pos', 'regular'),
    ('sma0_pos', 'sma0'),
    ('sma1_pos', 'sma1'),
    ('pnpa_pos', 'pnpa'),
    ('npa_pos', 'npa'),
    ('total_pos', 'total'),
]


def _month_end_report_path():
    """Locate the latest Month-End Employee Report (has the POS sheet).

    Falls back to the EOD Employee Report only if it happens to carry a POS
    sheet (it normally does not).
    """
    for name in ('Quick_Month_End_Employee_Latest.xlsx', 'Employee_Report_Latest.xlsx'):
        p = config.BACKEND_DATA_DIR / name
        if p.exists():
            return p
    return None


def _parse_pos_sheet(path):
    """Parse the report's `POS` sheet into branch+product POS rows.

    Sheet columns: Region, Division, Area, BranchName, Product Name,
    Regular_POS, SMA0_POS, SMA1_POS, PNPA_POS, NPA_POS, Total_POS.
    Returns [{branch, product_type_id, pos:{regular,sma0,sma1,pnpa,npa,total}}, ...].
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if 'POS' not in wb.sheetnames:
            raise ValueError("No 'POS' sheet in the report — generate a Month-End report first.")
        rows = list(wb['POS'].iter_rows(values_only=True))
        if not rows:
            return []
        hdr = {_norm(h): i for i, h in enumerate(rows[0]) if h is not None}
        b_i = hdr.get('branchname')
        p_i = hdr.get('product name')
        if b_i is None or p_i is None:
            raise ValueError("POS sheet missing BranchName / Product Name columns.")
        pos_idx = [(hdr.get(h), key) for h, key in _POS_HEADER_KEY]

        out = []
        for row in rows[1:]:
            if not row or len(row) <= b_i:
                continue
            branch = str(row[b_i]).strip() if row[b_i] is not None else ''
            prod = str(row[p_i]).strip().upper() if (p_i < len(row) and row[p_i] is not None) else ''
            if not branch or not prod:
                continue
            pt = _PORTFOLIO_PT_ID.get(prod)
            if pt is None:
                logger.info(f"GrowwithmeDB portfolio sync: skipping unknown product '{prod}'")
                continue
            pos = {key: (_num(row[i]) if (i is not None and i < len(row)) else 0) for i, key in pos_idx}
            out.append({'branch': branch, 'product_type_id': pt, 'pos': pos})
        return out
    finally:
        wb.close()


def _resolve_period_month(data):
    """Resolve period_month ('YYYY-MM-01') from the request body.

    Accepts {"period_month":"YYYY-MM[-DD]"} directly, or {"month":"MAR","year":2026}.
    Returns the normalised string, or None if it can't be resolved.
    """
    pm = (data.get('period_month') or '').strip()
    if pm:
        import re
        mo = re.match(r'^(\d{4})-(\d{2})', pm)
        if mo:
            return f'{mo.group(1)}-{mo.group(2)}-01'
    label = (data.get('month') or '').strip().upper()[:3]
    year = data.get('year')
    if label in _MONTH_NUM and year:
        return f'{int(year)}-{_MONTH_NUM[label]}-01'
    return None


@growwithme_bp.route('/sync-portfolio', methods=['POST'])
def sync_portfolio():
    """Push the Month-End report's POS sheet into GrowwithmeDB.portfolio_* (monthly).

    JSON body: {"period_month":"YYYY-MM-01"}  (or {"month":"MAR","year":2026}).
    Whole-month override — re-running for the same month replaces it.
    """
    data = request.get_json(silent=True) or {}
    period_month = _resolve_period_month(data)
    if not period_month:
        return jsonify({'success': False,
                        'message': 'period_month required (YYYY-MM-01), or month+year (e.g. "MAR" + 2026).'}), 400

    path = _month_end_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No Month-End Employee Report found. Generate one first.'}), 404
    try:
        rows = _parse_pos_sheet(path)
    except Exception as e:
        logger.warning(f'GrowwithmeDB portfolio sync: POS parse failed: {e}')
        return jsonify({'success': False, 'message': f'POS parse failed: {e}'}), 500
    if not rows:
        return jsonify({'success': False, 'message': 'No POS rows found in the report.'}), 400

    batch = {'period_month': period_month, 'rows': rows}
    body, status = _push_batch('/api/portfolio/sync', batch)
    body.update(period_month=period_month, branch_products=len(rows))
    if body.get('success'):
        logger.info(f"GrowwithmeDB portfolio sync: {body['inserted']} branch×product rows for {period_month}")
    return jsonify(body), status


@growwithme_bp.route('/sync-disbursement', methods=['POST'])
def sync_disbursement():
    """Push a disbursement file into GrowwithmeDB.disbursement (monthly grain).

    Form fields:
      - file:  disbursement CSV/XLSX
      - dates: comma-separated YYYY-MM-DD to keep. Empty/missing -> all dates.
    Daily rows are aggregated to the month (db_month = first-of-month) and sent
    as a single batched POST /api/disbursement/sync {rows:[...]}.
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    if not f.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Expected .csv / .xlsx file'}), 400

    raw_dates = (request.form.get('dates') or '').strip()
    keep_dates = {d.strip() for d in raw_dates.split(',') if d.strip()} or None

    from blueprints.disbursement import _parse_file, _aggregate, _save_upload
    tmp = _save_upload(f, f.filename)
    try:
        parsed = _parse_file(tmp, f.filename)
        agg, _dates = _aggregate(parsed, keep_dates=keep_dates)
        if not agg:
            return jsonify({'success': False, 'message': (
                'No Active disbursement rows match the selected dates.' if keep_dates
                else 'No Active disbursement rows found in file.')}), 400

        # Roll daily aggregates up to the month.
        months = {}
        for (iso, branch, emp_id, prod), v in agg.items():
            db_month = iso[:7] + '-01'
            key = (db_month, branch, emp_id or None, prod)
            m = months.setdefault(key, {'cnt': 0, 'amt': 0.0})
            m['cnt'] += v['cnt']
            m['amt'] += v['amt']

        rows = [
            {
                'branch': branch,
                'emp_id': emp_id,
                'product_type_id': _DISB_PRODUCT_TYPE_ID.get(prod, 1),
                'db_month': db_month,
                'disb_count': m['cnt'],
                'disb_amount': round(m['amt'], 2),
            }
            for (db_month, branch, emp_id, prod), m in months.items()
        ]

        ok, res = _post('/api/disbursement/sync', {'rows': rows})
        if not ok:
            logger.warning(f'GrowwithmeDB disbursement sync failed: {res}')
            return jsonify({'success': False, 'message': res}), 502
        inserted = int((res or {}).get('count') or 0)
        logger.info(f'GrowwithmeDB disbursement sync: {inserted} monthly rows pushed')
        return jsonify({
            'success': True,
            'inserted': inserted,
            'message': f'{inserted} monthly rows synced to growwithme-local (disbursement)',
        })
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
