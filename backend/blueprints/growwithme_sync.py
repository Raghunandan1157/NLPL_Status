"""
GrowWithMe Sync Blueprint (Phase 2 — local MySQL API)
=====================================================
Pushes EOD daily, Quick hourly, Disbursement and Portfolio data into the
``growwithme-local`` Node/Express API (now deployed on AWS EC2, MariaDB
``Growwithme_NEWDB``; base URL in GROWWITHME_API_URL).

The growwithme-local ``/sync`` endpoints expect rows **already exploded** into
DPD buckets + NPA actions:

  POST {GROWWITHME_API_URL}/api/collection/sync         (EOD daily — grain 2)
  POST {GROWWITHME_API_URL}/api/hourly/sync             (Quick hourly — grain 1)
  POST {GROWWITHME_API_URL}/api/disbursement/sync       (Disbursement — monthly)
  POST {GROWWITHME_API_URL}/api/disbursement/sync-daily (Disbursement — per-day)
  POST {GROWWITHME_API_URL}/api/portfolio/sync          (Portfolio POS — monthly)
  POST {GROWWITHME_API_URL}/api/portfolio/sync-accounts (Portfolio Total Account)

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
  The growwithme-local /sync endpoints now do a whole-scope OVERRIDE on the API
  side (collection per-date, hourly full-snapshot, disbursement per-month,
  disbursement/sync-daily per-date, portfolio per-month), so re-running a sync
  REPLACES that scope rather than appending. Disbursement is pushed at BOTH
  grains: monthly (db_month = first-of-month) for the Disbursement tab and daily
  (disb_date) for its Daily tab — from the same per-day aggregate. Portfolio
  pushes POS amounts and, when the POS sheet carries an account column, Total
  Account counts (pos_status 'total_acc').
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
    _safe_num,
    _DISB_PRODUCT_TYPE_ID,
)
from openpyxl import load_workbook
import re

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


# ── Input helpers (support both "sync the latest generated report" and "upload
#    a custom file") ─────────────────────────────────────────────────────────
def _param(name):
    """Read a field from the multipart form (file-upload requests) or JSON body."""
    if request.form and name in request.form:
        return (request.form.get(name) or '').strip()
    data = request.get_json(silent=True) or {}
    return str(data.get(name) or '').strip()


def _uploaded_file():
    """If the request carries an uploaded 'file', save it to a temp path and return
    (path, True). Otherwise (None, False). The caller deletes the temp path when the
    second value is True. Raises ValueError for a non-Excel upload."""
    f = request.files.get('file')
    if not f or not f.filename:
        return None, False
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        raise ValueError('Expected an Excel (.xlsx) file')
    import tempfile
    suffix = os.path.splitext(f.filename)[1] or '.xlsx'
    fd, tmp = tempfile.mkstemp(prefix='gwm_upload_', suffix=suffix)
    os.close(fd)
    f.save(tmp)
    return tmp, True


def _cleanup(path, is_temp):
    if is_temp and path:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Daily Collection Report (raw) → daily collection sync ─────────────────
# The daily sync normally ingests a generated EOD Employee Report (per-product
# IGL/FIG/VVY sheets, via _parse_report). A raw "Daily Collection Report" instead
# carries one combined per-employee row in an 'Employee Data' sheet — same bucket
# column layout the hourly parser (_parse_employee_data_sheet) already handles, but
# count-only (no rupee amounts, like the hourly grain) and with the employee CODE
# in a different column than the label 'EMP ID' (that column can hold a name).
_EMP_CODE_RE = re.compile(r'^[A-Za-z]{1,3}\d{3,}$')  # e.g. NL10838


def _has_employee_data_sheet(path):
    """True if the workbook has an 'Employee Data' sheet (a raw Daily Collection
    Report) rather than the per-product EOD Employee Report sheets."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return 'Employee Data' in wb.sheetnames
        finally:
            wb.close()
    except Exception:
        return False


def _parse_daily_collection(path):
    """Parse a raw Daily Collection Report's 'Employee Data' sheet into the same row
    dicts _parse_report returns ({emp_id, product_type_id, <25 metrics>}), so it can
    feed the daily collection sync. Combined across products (product_type_id=None);
    amounts are 0 (this report is count-only, matching the hourly grain).

    The employee CODE column is detected by content (values like NL10838), because
    in this report the column literally headed 'EMP ID' can hold the officer NAME
    while the code sits under 'EMP Name'. Bucket columns match the hourly layout:
    Regular D/C = 7/8, 1-30 = 11/12, 31-60 = 15/16, PNPA = 19/20, NPA cases = 27,
    activation acc/amt = 28/29, closure acc/amt = 30/31.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb['Employee Data']
        rows = list(ws.iter_rows(values_only=True))
        # Header row = the one that contains an 'EMP ID' cell.
        hdr_idx = -1
        for r, row in enumerate(rows):
            if row and any(c is not None and str(c).strip().upper() == 'EMP ID' for c in row):
                hdr_idx = r
                break
        if hdr_idx < 0:
            raise ValueError("Daily Collection Report 'Employee Data' sheet has no 'EMP ID' header.")

        data = rows[hdr_idx + 1:]
        # Candidate emp columns: headers mentioning 'emp'. Pick the one whose values
        # best match the employee-code pattern (NL#####).
        header = rows[hdr_idx]
        candidates = [i for i, c in enumerate(header) if c is not None and 'emp' in str(c).strip().lower()]
        if not candidates:
            candidates = list(range(min(4, len(header))))

        def _code_hits(ci):
            hits = 0
            for row in data[:40]:
                if row and ci < len(row) and row[ci] is not None and _EMP_CODE_RE.match(str(row[ci]).strip()):
                    hits += 1
            return hits

        emp_col = max(candidates, key=_code_hits)
        if _code_hits(emp_col) == 0:
            # No code-like column found — fall back to the literal 'EMP ID' column.
            emp_col = next((i for i, c in enumerate(header)
                            if c is not None and str(c).strip().upper() == 'EMP ID'), candidates[0])

        def g(row, i):
            return _safe_num(row[i]) if i < len(row) else 0

        out = []
        for row in data:
            if not row or emp_col >= len(row) or row[emp_col] is None:
                continue
            emp = str(row[emp_col]).strip()
            if not emp or emp.upper() in ('EMP ID', 'GRAND TOTAL', 'TOTAL'):
                continue
            out.append({
                'emp_id': emp,
                'product_type_id': None,  # combined report — no product split
                'regular_demand': g(row, 7), 'regular_collection': g(row, 8),
                'demand_1_30': g(row, 11), 'collection_1_30': g(row, 12),
                'demand_31_60': g(row, 15), 'collection_31_60': g(row, 16),
                'pnpa_demand': g(row, 19), 'pnpa_collection': g(row, 20),
                'npa_cases': g(row, 27),
                'npa_act_acc': g(row, 28), 'npa_act_amt': g(row, 29),
                'npa_clo_acc': g(row, 30), 'npa_clo_amt': g(row, 31),
                'on_date_demand': 0, 'on_date_collection': 0,
                'regular_demand_amt': 0, 'regular_collection_amt': 0,
                'demand_1_30_amt': 0, 'collection_1_30_amt': 0,
                'demand_31_60_amt': 0, 'collection_31_60_amt': 0,
                'pnpa_demand_amt': 0, 'pnpa_collection_amt': 0,
                'on_date_demand_amt': 0, 'on_date_collection_amt': 0,
            })
        return out
    finally:
        wb.close()


# ── Product-split daily source (preferred) ────────────────────────────────
# The raw Daily Collection Report's FIRST sheet ('OverAll') carries BRANCH +
# OFFICER blocks split by product — titled 'BRANCH + OFFICER NAME WISE
# COLLECTION REPORT (OverAll - IGL / - FIG / - IL)'. Parsing those recovers the
# product_type_id the combined 'Employee Data' sheet drops (it hardcodes None),
# so the frontend's IGL/FIG/IL tabs populate instead of going blank. Count-only
# (amounts 0), same daily grain + whole-date override as _parse_daily_collection.
_OVERALL_PRODUCT_BLOCKS = [('(OVERALL - IGL)', 1), ('(OVERALL - FIG)', 2), ('(OVERALL - IL)', 3)]
# OverAll officer-block column indices (0-based within the row tuple).
_OA = {
    'name': 1, 'on_date_demand': 2, 'on_date_collection': 3,
    'regular_demand': 5, 'regular_collection': 6,
    'demand_1_30': 9, 'collection_1_30': 10,
    'demand_31_60': 13, 'collection_31_60': 14,
    'pnpa_demand': 17, 'pnpa_collection': 18,
    'npa_cases': 25, 'npa_act_acc': 26, 'npa_act_amt': 27,
    'npa_clo_acc': 28, 'npa_clo_amt': 29,
}


def _overall_block_title_rows(rows):
    """Map product_type_id -> 0-based index (into `rows`) of each product block's
    'BRANCH + OFFICER NAME WISE ... (OverAll - IGL/FIG/IL)' title, by scanning
    column A. The combined '(OverAll)' block (no product suffix) is ignored."""
    found = {}
    for ri, row in enumerate(rows):
        a = row[0] if row else None
        if a is None:
            continue
        s = str(a).strip().upper()
        if not s.startswith('BRANCH + OFFICER'):
            continue
        for suffix, pt in _OVERALL_PRODUCT_BLOCKS:
            if s.endswith(suffix):
                found[pt] = ri
    return found


def _has_overall_product_blocks(path):
    """True if the workbook's 'OverAll' sheet carries all three product-split
    officer blocks (IGL/FIG/IL) — the preferred, product-aware daily source."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            if 'OverAll' not in wb.sheetnames:
                return False
            rows = list(wb['OverAll'].iter_rows(values_only=True))
            return len(_overall_block_title_rows(rows)) == 3
        finally:
            wb.close()
    except Exception:
        return False


def _officer_code(label):
    """Extract the officer code (e.g. NL10838) from an 'OFFICER NAME - NL#####'
    block label, or None for area/branch subtotal + header rows."""
    if label is None:
        return None
    tail = re.split(r'[-–]', str(label))[-1].strip()
    return tail if _EMP_CODE_RE.match(tail) else None


def _parse_daily_collection_products(path):
    """Parse the raw Daily Collection Report's 'OverAll' sheet product officer
    blocks into flat metric rows carrying product_type_id (1 IGL / 2 FIG / 3 IL),
    so _explode feeds the daily collection sync WITH the product split. Only
    officer rows (label ends in an NL##### code) are kept; area/branch subtotal
    and header rows are skipped. Count-only (amounts 0), like the hourly grain."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb['OverAll'].iter_rows(values_only=True))
    finally:
        wb.close()
    titles = _overall_block_title_rows(rows)
    if not titles:
        raise ValueError("Daily Collection Report 'OverAll' sheet has no product blocks.")
    # Each block runs from its title to the next block title (in sheet order).
    starts = sorted(titles.values())
    end_of = {s: (starts[i + 1] if i + 1 < len(starts) else len(rows))
              for i, s in enumerate(starts)}

    def g(row, key):
        i = _OA[key]
        return _safe_num(row[i]) if i < len(row) else 0

    out = []
    for pt, start in titles.items():
        for ri in range(start + 1, end_of[start]):
            row = rows[ri]
            if not row:
                continue
            code = _officer_code(row[_OA['name']] if _OA['name'] < len(row) else None)
            if not code:
                continue
            out.append({
                'emp_id': code, 'product_type_id': pt,
                'regular_demand': g(row, 'regular_demand'), 'regular_collection': g(row, 'regular_collection'),
                'demand_1_30': g(row, 'demand_1_30'), 'collection_1_30': g(row, 'collection_1_30'),
                'demand_31_60': g(row, 'demand_31_60'), 'collection_31_60': g(row, 'collection_31_60'),
                'pnpa_demand': g(row, 'pnpa_demand'), 'pnpa_collection': g(row, 'pnpa_collection'),
                'on_date_demand': g(row, 'on_date_demand'), 'on_date_collection': g(row, 'on_date_collection'),
                'npa_cases': g(row, 'npa_cases'),
                'npa_act_acc': g(row, 'npa_act_acc'), 'npa_act_amt': g(row, 'npa_act_amt'),
                'npa_clo_acc': g(row, 'npa_clo_acc'), 'npa_clo_amt': g(row, 'npa_clo_amt'),
                'regular_demand_amt': 0, 'regular_collection_amt': 0,
                'demand_1_30_amt': 0, 'collection_1_30_amt': 0,
                'demand_31_60_amt': 0, 'collection_31_60_amt': 0,
                'pnpa_demand_amt': 0, 'pnpa_collection_amt': 0,
                'on_date_demand_amt': 0, 'on_date_collection_amt': 0,
            })
    return out


@growwithme_bp.route('/sync-daily', methods=['POST'])
def sync_daily():
    """Push an EOD Employee Report into GrowwithmeDB (collection grain 2).

    Body: {"date": "YYYY-MM-DD"} as JSON, or multipart form with the same `date`
    field plus an optional `file` (an Employee Report .xlsx). With a file, that
    file is parsed; without one, the latest generated report is used.
    NOTE: insert-only — re-running for the same date appends new rows.
    """
    date = _param('date')
    if not date:
        return jsonify({'success': False, 'message': 'date is required (YYYY-MM-DD)'}), 400

    try:
        up, is_temp = _uploaded_file()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    path = up or _employee_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No EOD Employee Report found. Run EOD processing first, or upload one.'}), 404
    try:
        if _has_overall_product_blocks(path):
            # Raw Daily Collection Report — parse its 'OverAll' sheet product
            # officer blocks (IGL/FIG/IL) so rows carry product_type_id and the
            # frontend's product tabs populate. Preferred over the combined
            # 'Employee Data' sheet, which drops the product split.
            logger.info('GrowwithmeDB daily sync: parsing raw Daily Collection Report (OverAll product blocks).')
            rows = _parse_daily_collection_products(path)
        elif _has_employee_data_sheet(path):
            # Fallback: older report with only the combined 'Employee Data' sheet
            # (count-only, product-combined). Same daily grain, whole-date override.
            logger.info('GrowwithmeDB daily sync: parsing raw Daily Collection Report (Employee Data sheet, product-combined).')
            rows = _parse_daily_collection(path)
        else:
            rows = _parse_report(path)
    except Exception as e:
        logger.warning(f'GrowwithmeDB daily sync: report parse failed: {e}')
        return jsonify({'success': False, 'message': f'Report parse failed: {e}'}), 500
    finally:
        _cleanup(up, is_temp)
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

    Body (JSON or multipart): optional `date` (YYYY-MM-DD) + `period_hour` (0-23),
    plus an optional `file` (a Quick Report .xlsx). With a file, that file is
    parsed; without one, the latest generated Quick Report is used. The Quick
    Report has no hour column, so the hour defaults to the current local hour.
    """
    date = _param('date') or datetime.now().strftime('%Y-%m-%d')
    raw_hour = _param('period_hour')
    period_hour = int(raw_hour) if raw_hour else datetime.now().hour

    try:
        up, is_temp = _uploaded_file()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    path = up or _quick_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No hourly report found. Run Hourly/Quick processing first, or upload one.'}), 404
    try:
        rows = _parse_quick_report(path)
    except Exception as e:
        logger.warning(f'GrowwithmeDB hourly sync: report parse failed: {e}')
        return jsonify({'success': False, 'message': f'Quick Report parse failed: {e}'}), 500
    finally:
        _cleanup(up, is_temp)
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

# Raw-PAR fallback: map the PAR's "DPD Days" bucket text -> growwithme pos key,
# mirroring the Month-End engine's POS derivation (eod_processor.build_employee_report).
# Keys are matched lower-cased/stripped. 61-90 = PNPA; everything 91+ = NPA.
_PAR_DPD_POS = {
    '0 days': 'regular', '0days': 'regular', '0': 'regular',
    '1: 1-30': 'sma0', '1-30': 'sma0',
    '2: 31-60': 'sma1', '31-60': 'sma1',
    '3: 61-90': 'pnpa', '61-90': 'pnpa',
    '4: 91-120': 'npa', '91-120': 'npa',
    '5: 121-180': 'npa', '121-180': 'npa', '121-150': 'npa', '151-180': 'npa',
    '6: 181-365': 'npa', '181-365': 'npa', '181-210': 'npa', '211-250': 'npa', '251-365': 'npa',
    '7: >365 days': 'npa', '>365 days': 'npa', '>365': 'npa', '>365days': 'npa', '>120': 'npa',
}


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
    Regular_POS, SMA0_POS, SMA1_POS, PNPA_POS, NPA_POS, Total_POS, and (when the
    report carries it) a Total_Account / No_of_Account column.
    Returns [{branch, product_type_id, pos:{regular,sma0,sma1,pnpa,npa,total},
              acc: <int|None>}, ...]. `acc` is None when the sheet has no
    account-count column (then the Total Account push is skipped).
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
        # Account-count column for the "Total Account" card. Optional — match the
        # first header that mentions 'account' but is NOT a *_POS amount column.
        acc_i = next((i for h, i in hdr.items() if 'account' in h and 'pos' not in h), None)
        if acc_i is None:
            logger.info("GrowwithmeDB portfolio sync: no account-count column in POS sheet — "
                        "Total Account push will be skipped.")

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
            acc = _num(row[acc_i]) if (acc_i is not None and acc_i < len(row)) else None
            out.append({'branch': branch, 'product_type_id': pt, 'pos': pos, 'acc': acc})
        return out
    finally:
        wb.close()


# Demand-bucket account-count columns that sum to the "Total Account" figure —
# matching the live site's derivation: regular_demand + 1-30 + 31-60 + pnpa_demand
# + npa_cases. (db_col, normalised header) — these are COUNT columns (not _amt).
_ACC_FIELDS = [
    ('regular_demand', 'regular demand'),
    ('demand_1_30',    '1-30 demand'),
    ('demand_31_60',   '31-60 demand'),
    ('pnpa_demand',    'pnpa demand'),
    ('npa_cases',      'npa cases'),
]


def _parse_demand_accounts(path):
    """Derive per branch×product ACCOUNT COUNTS for the "Total Account" card the
    same way the live site does: SUM(regular_demand + 1-30 + 31-60 + pnpa_demand +
    npa_cases) of the account-count columns, aggregated from the report's per-product
    EOD sheets (IGL/FIG/VVY) up to branch×product.

    Returns {(BRANCH_UPPER, product_type_id): acc_int}. Empty when the report has no
    such sheets/columns (caller then falls back to a POS-sheet account column).
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    acc = {}
    try:
        for sheet_name in wb.sheetnames:
            if sheet_name.endswith('_FY') or sheet_name in ('POS', 'EMP_POS'):
                continue
            pt = _PORTFOLIO_PT_ID.get(sheet_name.strip().upper())  # IGL/FIG/IL/VVY -> id
            if pt is None:
                continue
            rows = list(wb[sheet_name].iter_rows(values_only=True))
            if not rows:
                continue
            hdr = {}
            for i, h in enumerate(_norm(x) for x in rows[0]):
                if h and h not in hdr:
                    hdr[h] = i
            b_i = next((hdr[c] for c in ('branchname', 'branch name', 'branch') if c in hdr), None)
            col_idx = [hdr.get(txt) for _, txt in _ACC_FIELDS]
            if b_i is None or all(c is None for c in col_idx):
                continue  # not a parseable per-product sheet — skip (fallback handles it)
            for row in rows[1:]:
                if not row or b_i >= len(row) or row[b_i] is None:
                    continue
                branch = str(row[b_i]).strip()
                if not branch:
                    continue
                total = sum(_num(row[ci]) if (ci is not None and ci < len(row)) else 0 for ci in col_idx)
                key = (branch.upper(), pt)
                acc[key] = acc.get(key, 0) + total
        return acc
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


def _has_pos_sheet(path):
    """True if the workbook carries a 'POS' sheet (a generated Month-End report).
    False for a raw PAR (Sheet1/Sheet2) or on any read error."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return 'POS' in wb.sheetnames
        finally:
            wb.close()
    except Exception:
        return False


def _par_product_type(prod, pid):
    """Resolve a PAR row's product to a GrowwithmeDB product_type_id (1 IGL / 2 FIG /
    3 IL). 'IGL & FIG' is split via ProductID (starts '6' or contains FIG -> FIG)."""
    p = (prod or '').strip().upper()
    if p == 'IGL & FIG':
        s = str(pid or '').upper()
        p = 'FIG' if (s.startswith('6') or 'FIG' in s) else 'IGL'
    if p not in ('IGL', 'FIG'):
        p = 'IL'
    return _PORTFOLIO_PT_ID.get(p)


def _parse_par_pos(path):
    """Build branch×product POS rows straight from a RAW PAR file, so the Portfolio
    tab can ingest a PAR without first generating a Month-End report.

    Mirrors the Month-End engine (eod_processor.build_employee_report): bucket each
    account's PrincipalOS by its DPD Days into regular/sma0/sma1/pnpa/npa, then sum
    per (BranchName, product). Returns the SAME shape as _parse_pos_sheet, plus
    per-bucket account counts:
      [{branch, product_type_id, pos:{regular,sma0,sma1,pnpa,npa,total},
        acc, acc_buckets:{regular,sma0,sma1,pnpa,npa}}, ...]
    where `acc` is the total account (row) count per branch×product (Total Account
    card) and `acc_buckets` are the per-DPD-bucket counts (Active Accounts card =
    non-NPA buckets). `acc` == sum(acc_buckets).

    Streams the sheet row-by-row with openpyxl read_only — a raw PAR can be 100 MB+
    with 700k rows, so we never load it into a DataFrame.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        # Prefer the account-level detail sheet ('Sheet1'); else the first sheet
        # whose header carries PrincipalOS.
        sheet = None
        for name in (['Sheet1'] + [s for s in wb.sheetnames if s != 'Sheet1']):
            ws = wb[name]
            hdr = next(ws.iter_rows(values_only=True), None)
            if hdr and any(str(c).strip() == 'PrincipalOS' for c in hdr if c is not None):
                sheet = name
                break
        if sheet is None:
            raise ValueError('PAR has no sheet with a PrincipalOS column.')

        ws = wb[sheet]
        it = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else '' for c in next(it)]
        idx = {h: i for i, h in enumerate(header)}
        dpd_col = next((c for c in ('DPD Days', 'Days Group', 'Days group', 'DaysGroup') if c in idx), None)
        if dpd_col is None or 'PrincipalOS' not in idx or 'BranchName' not in idx:
            raise ValueError('PAR missing required columns (need BranchName, DPD Days, PrincipalOS).')
        i_branch, i_dpd, i_pos = idx['BranchName'], idx[dpd_col], idx['PrincipalOS']
        i_prod = idx.get('Product Name')
        i_pid = idx.get('ProductID')

        # (branch, product_type_id) -> {bucket: pos_sum, ..., '_acc': count}
        agg = {}
        for row in it:
            if not row or i_branch >= len(row):
                continue
            branch = str(row[i_branch]).strip() if row[i_branch] is not None else ''
            if not branch:
                continue
            bucket = _PAR_DPD_POS.get(str(row[i_dpd]).strip().lower() if (i_dpd < len(row) and row[i_dpd] is not None) else '')
            if not bucket:
                continue
            prod = row[i_prod] if (i_prod is not None and i_prod < len(row)) else ''
            pid = row[i_pid] if (i_pid is not None and i_pid < len(row)) else ''
            pt = _par_product_type(prod, pid)
            if pt is None:
                continue
            pos_val = _num(row[i_pos]) if (i_pos < len(row)) else 0
            key = (branch, pt)
            slot = agg.get(key)
            if slot is None:
                slot = agg[key] = {
                    'pos': {'regular': 0.0, 'sma0': 0.0, 'sma1': 0.0, 'pnpa': 0.0, 'npa': 0.0},
                    'acc': {'regular': 0, 'sma0': 0, 'sma1': 0, 'pnpa': 0, 'npa': 0},
                }
            slot['pos'][bucket] += pos_val
            slot['acc'][bucket] += 1

        out = []
        for (branch, pt), slot in agg.items():
            pos = slot['pos']
            pos['total'] = pos['regular'] + pos['sma0'] + pos['sma1'] + pos['pnpa'] + pos['npa']
            acc_buckets = slot['acc']
            out.append({
                'branch': branch, 'product_type_id': int(pt),
                'pos': pos,
                'acc': int(sum(acc_buckets.values())),
                'acc_buckets': acc_buckets,
            })
        return out
    finally:
        wb.close()


@growwithme_bp.route('/sync-portfolio', methods=['POST'])
def sync_portfolio():
    """Push portfolio POS into GrowwithmeDB.portfolio_* (monthly).

    Body (JSON or multipart): {"period_month":"YYYY-MM-01"} (or month+year), plus
    an optional `file` (.xlsx). The file may be EITHER a Month-End report (its POS
    sheet is read) OR a raw PAR (POS is built here from PrincipalOS × DPD, same as
    the Month-End engine). Without a file, the latest generated Month-End report is
    used. Whole-month override — re-running for the same month replaces it.
    """
    period_month = _resolve_period_month({
        'period_month': _param('period_month'), 'month': _param('month'), 'year': _param('year'),
    })
    if not period_month:
        return jsonify({'success': False,
                        'message': 'period_month required (YYYY-MM-01), or month+year (e.g. "MAR" + 2026).'}), 400

    try:
        up, is_temp = _uploaded_file()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    path = up or _month_end_report_path()
    if not path:
        return jsonify({'success': False,
                        'message': 'No Month-End Employee Report found. Generate one first, or upload one.'}), 404
    try:
        if _has_pos_sheet(path):
            # Generated Month-End report — read its POS sheet.
            rows = _parse_pos_sheet(path)
            # Derive Total Account from the demand-bucket counts (matches the live site).
            # Best-effort — a derivation failure just falls back to a POS-sheet column.
            try:
                acc_map = _parse_demand_accounts(path)
            except Exception as e:
                logger.warning(f'GrowwithmeDB portfolio sync: account derivation failed: {e}')
                acc_map = {}
        else:
            # Raw PAR upload — build the POS (PrincipalOS × DPD) directly. Account
            # counts come from the PAR itself (per branch×product), so no acc_map.
            logger.info('GrowwithmeDB portfolio sync: no POS sheet — treating upload as a raw PAR.')
            rows = _parse_par_pos(path)
            acc_map = {}
    except Exception as e:
        logger.warning(f'GrowwithmeDB portfolio sync: POS parse failed: {e}')
        return jsonify({'success': False, 'message': f'POS parse failed: {e}'}), 500
    finally:
        _cleanup(up, is_temp)
    if not rows:
        return jsonify({'success': False, 'message': 'No POS rows found in the report.'}), 400

    batch = {'period_month': period_month, 'rows': rows}
    body, status = _push_batch('/api/portfolio/sync', batch)
    body.update(period_month=period_month, branch_products=len(rows))
    if not body.get('success'):
        return jsonify(body), status
    logger.info(f"GrowwithmeDB portfolio sync: {body['inserted']} branch×product rows for {period_month}")

    # Total Account counts (pos_status 'total_acc'). Prefer the live-style derivation
    # (sum of demand-bucket account counts per branch×product); fall back to a POS-
    # sheet account column when the report lacks the per-product demand sheets.
    # Best-effort, runs after the POS push succeeds.
    acc_rows = []
    for r in rows:
        acc = acc_map.get((str(r['branch']).strip().upper(), r['product_type_id']))
        if acc is None:
            acc = r.get('acc')  # POS-sheet column / PAR-derived fallback
        if acc is not None:
            entry = {'branch': r['branch'], 'product_type_id': r['product_type_id'], 'acc': int(round(acc))}
            # PAR-derived rows also carry per-DPD-bucket counts — drives the
            # "Active Accounts" card (pos_status 8-12). Absent for POS-sheet syncs.
            if r.get('acc_buckets'):
                entry['acc_buckets'] = {k: int(v) for k, v in r['acc_buckets'].items()}
            acc_rows.append(entry)
    if acc_rows:
        ok_a, res_a = _post('/api/portfolio/sync-accounts', {'period_month': period_month, 'rows': acc_rows})
        if ok_a:
            matched = int((res_a or {}).get('matched') or 0)
            bucket_rows = int((res_a or {}).get('bucketRows') or 0)
            body['accounts_matched'] = matched
            body['active_account_rows'] = bucket_rows
            extra = f' (+ Active Accounts for {bucket_rows})' if bucket_rows else ''
            body['message'] = f"{body.get('message', '')} · {matched} Total Account rows{extra}".strip(' ·')
            logger.info(f"GrowwithmeDB portfolio accounts sync: {matched} matched, {bucket_rows} with buckets for {period_month}")
        else:
            body['accounts_ok'] = False
            body['message'] = f"{body.get('message', '')} · Total Account push FAILED: {res_a}".strip()
            logger.warning(f'GrowwithmeDB portfolio accounts sync failed: {res_a}')
    return jsonify(body), status


# ── Staff (HR master) sync — refresh employee DETAIL fields (name, phone, joining
#    date, DOB, reporting manager) into GrowwithmeDB. Mirrors the Coll_Db staff
#    upload format: a "Working" sheet where row 0 = column numbers, row 1 = headers,
#    row 2+ = data. DETAILS-ONLY on the API side (never touches branch/role/hierarchy).
_STAFF_COLS = {
    'emp_id':               ['nmempid', 'emp id', 'emp_id', 'empid'],
    'full_name':            ['name(asperaadhar)', 'name', 'as per aadhaar'],
    'mobile':               ['personalmobile', 'personal mobile', 'mobile'],
    'date_of_joining':      ['date of joining', 'doj', 'joining'],
    'date_of_birth':        ['date of birth', 'dob'],
    'reporting_officer_id': ['reportingofficerempid', 'reporting officer emp'],
}


def _excel_date(v):
    """Normalise an Excel cell to 'YYYY-MM-DD' (or None). Handles datetimes, Excel
    serial numbers, and common date strings."""
    if v is None or v == '':
        return None
    if hasattr(v, 'strftime'):
        try:
            return v.strftime('%Y-%m-%d')
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    try:
        n = float(s)
        if n > 1000:  # Excel serial date
            from datetime import datetime as _dt, timedelta
            return (_dt(1899, 12, 30) + timedelta(days=n)).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        pass
    from datetime import datetime as _dt
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d-%b-%Y'):
        try:
            return _dt.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def _parse_staff_sheet(path):
    """Parse the staff master's 'Working' sheet into detail rows for /sync-staff.
    Returns [{emp_id, full_name, mobile, date_of_joining, date_of_birth,
    reporting_officer_id}, ...]. Header row is row index 1 (row 0 = column numbers)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next((s for s in wb.sheetnames if 'working' in s.lower()), wb.sheetnames[0])
        rows = list(wb[sheet].iter_rows(values_only=True))
        if len(rows) < 3:
            return []
        headers = [_norm(h) for h in rows[1]]

        def findcol(keys):
            for k in keys:
                for i, h in enumerate(headers):
                    if h and k in h:
                        return i
            return None

        idx = {field: findcol(keys) for field, keys in _STAFF_COLS.items()}
        if idx['emp_id'] is None:
            raise ValueError("No employee-id column (NMEmpId / EMP ID) in the Working sheet.")

        def cell(row, field):
            i = idx[field]
            if i is None or i >= len(row) or row[i] is None:
                return None
            return row[i]

        out, seen = [], set()
        for row in rows[2:]:
            if not row:
                continue
            raw = cell(row, 'emp_id')
            code = str(raw).strip() if raw is not None else ''
            if not code or code in seen:
                continue
            seen.add(code)
            txt = lambda f: (str(cell(row, f)).strip() if cell(row, f) is not None else None)
            out.append({
                'emp_id': code,
                'full_name': txt('full_name'),
                'mobile': txt('mobile'),
                'date_of_joining': _excel_date(cell(row, 'date_of_joining')),
                'date_of_birth': _excel_date(cell(row, 'date_of_birth')),
                'reporting_officer_id': txt('reporting_officer_id'),
            })
        return out
    finally:
        wb.close()


# ── Onboarding / access sheet ─────────────────────────────────────────────
# The 'Employee_Onboarding_Template.xlsx' / 'Employee_Access_Current.xlsx' sheet
# ('Employees', headers on the FIRST row) carries a `scope` column and optional
# branch/role/designation. Unlike the HR 'Working' master (details-only), this
# drives the FULL onboard on the API side (create employee + login/password +
# info + assignment + SCOPE). Detected by the presence of a `scope` column.
_ONBOARD_FIELDS = ['emp_id', 'name', 'mobile', 'branch', 'role', 'designation',
                   'scope', 'reporting_officer_id', 'date_of_joining', 'date_of_birth', 'gender']


def _parse_onboard_sheet(path):
    """If `path` is an onboarding/access sheet (has emp_id + scope columns), return
    its rows as onboard dicts. Otherwise return None (caller falls back to the HR
    'Working' staff parser). Header row is auto-detected in the first 3 rows."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        sheet = next((s for s in names if s.strip().lower() == 'employees'), names[0])
        rows = list(wb[sheet].iter_rows(values_only=True))
        low = lambda x: str(x).strip().lower() if x is not None else ''
        hi, colmap = None, None
        for r in range(min(3, len(rows))):
            hdr = [low(c) for c in rows[r]]
            cm = {}
            for f in _ONBOARD_FIELDS:
                alt = f.replace('_', ' ')
                for i, h in enumerate(hdr):
                    if h == f or h == alt:
                        cm[f] = i
                        break
            if 'emp_id' in cm and 'scope' in cm:
                hi, colmap = r, cm
                break
        if colmap is None:
            return None  # not an onboarding sheet

        def cell(row, f):
            i = colmap.get(f)
            if i is None or i >= len(row):
                return None
            return row[i]

        out, seen = [], set()
        for row in rows[hi + 1:]:
            if not row:
                continue
            raw = cell(row, 'emp_id')
            code = str(raw).strip() if raw is not None else ''
            if not code or code.lower() == 'emp_id' or code in seen:
                continue
            seen.add(code)
            txt = lambda f: (str(cell(row, f)).strip() if cell(row, f) is not None else None)
            out.append({
                'emp_id': code,
                'name': txt('name'),
                'mobile': txt('mobile'),
                'branch': txt('branch'),
                'role': txt('role'),
                'designation': txt('designation'),
                'scope': txt('scope'),
                'reporting_officer_id': txt('reporting_officer_id'),
                'date_of_joining': _excel_date(cell(row, 'date_of_joining')),
                'date_of_birth': _excel_date(cell(row, 'date_of_birth')),
                'gender': txt('gender'),
            })
        return out
    finally:
        wb.close()


@growwithme_bp.route('/sync-staff', methods=['POST'])
def sync_staff():
    """Upload handler for BOTH staff formats. If the file is an onboarding/access
    sheet (an 'Employees' sheet with a `scope` column) it runs the FULL onboard
    (create employee + login/password + info + branch/role/designation assignment
    + SCOPE). Otherwise it treats the file as the HR 'Working' master and refreshes
    DETAILS ONLY (name/phone/joining/DOB/manager). Upsert; never deletes; idempotent."""
    try:
        up, is_temp = _uploaded_file()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    if not up:
        return jsonify({'success': False, 'message': 'Upload a staff / access Excel file.'}), 400
    try:
        # Access/onboarding sheet? (has a scope column) → full onboard.
        onboard = _parse_onboard_sheet(up)
        if onboard is not None:
            if not onboard:
                return jsonify({'success': False, 'message': 'No rows found in the Employees sheet.'}), 400
            ok, res = _post('/api/employees/onboard', {'rows': onboard})
            if not ok:
                logger.warning(f'GrowwithmeDB onboard failed: {res}')
                return jsonify({'success': False, 'message': res}), 502
            res = res or {}
            msg = (f"{res.get('created', 0)} new · {res.get('existed', 0)} updated · "
                   f"{res.get('scoped', 0)} scope set · {res.get('assignments_set', 0)} assignments · "
                   f"{res.get('logins_created', 0)} new logins")
            if res.get('warnings'):
                msg += f" · {len(res['warnings'])} warning(s)"
            logger.info(f'GrowwithmeDB onboard: {len(onboard)} rows → {msg}')
            return jsonify({'success': True, 'onboard_rows': len(onboard), 'message': msg, **res})

        # Otherwise the HR 'Working' master → details-only sync.
        rows = _parse_staff_sheet(up)
        if not rows:
            return jsonify({'success': False, 'message': 'No staff rows found (need a "Working" sheet or an "Employees" access sheet with a scope column).'}), 400
        ok, res = _post('/api/employees/sync-staff', {'rows': rows})
        if not ok:
            logger.warning(f'GrowwithmeDB staff sync failed: {res}')
            return jsonify({'success': False, 'message': res}), 502
        res = res or {}
        msg = (f"{res.get('inserted_employees', 0)} new · {res.get('name_updates', 0)} updated · "
               f"{res.get('contacts', 0)} phones · {res.get('personals', 0)} joining/DOB · "
               f"{res.get('managers_set', 0)} managers")
        logger.info(f'GrowwithmeDB staff sync: {len(rows)} rows → {msg}')
        return jsonify({'success': True, 'staff_rows': len(rows), 'message': msg, **res})
    except Exception as e:
        logger.warning(f'GrowwithmeDB staff/onboard sync: parse failed: {e}')
        return jsonify({'success': False, 'message': f'Upload failed: {e}'}), 500
    finally:
        _cleanup(up, is_temp)


# ── Single-employee editor (fetch + save details/scope) ───────────────────
@growwithme_bp.route('/scope-options', methods=['GET'])
def scope_options():
    """Region/division/area/branch name lists for the scope-target picker."""
    ok, res = _post('/api/employees/scope-options', {})
    if not ok:
        return jsonify({'success': False, 'message': str(res)}), 502
    return jsonify({'success': True, 'options': res or {}})


@growwithme_bp.route('/employee/<code>', methods=['GET'])
def get_employee(code):
    """Fetch one employee's editable details + current scope from GrowwithmeDB.
    Returns found=False (still success) when the code isn't in the DB, so the UI
    can offer to create it — distinct from a real fetch failure (success=False)."""
    ok, res = _post('/api/employees/lookup', {'emp_id': str(code).strip()})
    if not ok:
        return jsonify({'success': False, 'message': str(res)}), 502
    res = res or {}
    if not res.get('found'):
        return jsonify({'success': True, 'found': False, 'emp_id': str(code).strip()})
    return jsonify({'success': True, 'found': True, 'employee': res})


@growwithme_bp.route('/employee', methods=['POST'])
def save_employee():
    """Save one employee's edited details + scope via the full-onboard endpoint."""
    row = request.get_json(silent=True) or {}
    if not str(row.get('emp_id', '')).strip():
        return jsonify({'success': False, 'message': 'emp_id is required'}), 400
    ok, res = _post('/api/employees/onboard', {'rows': [row]})
    if not ok:
        return jsonify({'success': False, 'message': str(res)}), 502
    res = res or {}
    res.pop('passwords', None)  # never surface passwords to the UI
    created = 'created' if res.get('created') else 'updated'
    parts = [f"{created}", f"scope={row.get('scope') or 'self'}"]
    if res.get('warnings'):
        parts.append('; '.join(res['warnings'][:3]))
    return jsonify({'success': True, 'message': ' · '.join(parts), **res})


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

        # Per-day rows (grain = disb_date) for the Daily tab — `agg` already holds
        # the daily grain, so the same aggregates feed both pushes below.
        daily_rows = [
            {
                'disb_date': iso,
                'branch_name': branch,
                'emp_id': emp_id or None,
                'product_type_id': _DISB_PRODUCT_TYPE_ID.get(prod, 1),
                'officer_name': v.get('officer_name') or None,
                'disb_count': v['cnt'],
                'disb_amount': round(v['amt'], 2),
            }
            for (iso, branch, emp_id, prod), v in agg.items()
        ]

        # Roll the same aggregates up to the month for the monthly Disbursement tab.
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

        # 1) Monthly grain — drives the Disbursement tab. A failure here aborts.
        ok, res = _post('/api/disbursement/sync', {'rows': rows})
        if not ok:
            logger.warning(f'GrowwithmeDB disbursement sync failed: {res}')
            return jsonify({'success': False, 'message': res}), 502
        monthly_inserted = int((res or {}).get('count') or 0)

        # 2) Daily grain — drives the Disbursement → Daily tab (per-date override on
        #    the API side). Best-effort: a daily failure does not undo the monthly
        #    push but is surfaced in the response message.
        ok_d, res_d = _post('/api/disbursement/sync-daily', {'rows': daily_rows})
        daily_inserted = int((res_d or {}).get('inserted') or 0) if ok_d else 0
        daily_dates = (res_d or {}).get('dates_overridden') or [] if ok_d else []

        msg = f'{monthly_inserted} monthly rows synced to growwithme-local (disbursement)'
        if ok_d:
            msg += f' · {daily_inserted} daily rows across {len(daily_dates)} date(s)'
        else:
            logger.warning(f'GrowwithmeDB disbursement daily sync failed: {res_d}')
            msg += f' · daily push FAILED: {res_d}'

        logger.info(f'GrowwithmeDB disbursement sync: monthly={monthly_inserted} '
                    f'daily={daily_inserted} (daily_ok={ok_d})')
        return jsonify({
            'success': True,
            'inserted': monthly_inserted,
            'daily_inserted': daily_inserted,
            'daily_ok': ok_d,
            'message': msg,
        })
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
