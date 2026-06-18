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


@growwithme_bp.route('/sync-portfolio', methods=['POST'])
def sync_portfolio():
    """Push the Month-End report's POS sheet into GrowwithmeDB.portfolio_* (monthly).

    Body (JSON or multipart): {"period_month":"YYYY-MM-01"} (or month+year), plus
    an optional `file` (a Month-End report .xlsx with a POS sheet). With a file,
    that file is parsed; without one, the latest generated Month-End report is used.
    Whole-month override — re-running for the same month replaces it.
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
        rows = _parse_pos_sheet(path)
        # Derive Total Account from the demand-bucket counts (matches the live site).
        # Best-effort — a derivation failure just falls back to a POS-sheet column.
        try:
            acc_map = _parse_demand_accounts(path)
        except Exception as e:
            logger.warning(f'GrowwithmeDB portfolio sync: account derivation failed: {e}')
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
            acc = r.get('acc')  # POS-sheet column fallback
        if acc is not None:
            acc_rows.append({'branch': r['branch'], 'product_type_id': r['product_type_id'], 'acc': int(round(acc))})
    if acc_rows:
        ok_a, res_a = _post('/api/portfolio/sync-accounts', {'period_month': period_month, 'rows': acc_rows})
        if ok_a:
            matched = int((res_a or {}).get('matched') or 0)
            body['accounts_matched'] = matched
            body['message'] = f"{body.get('message', '')} · {matched} Total Account rows".strip(' ·')
            logger.info(f"GrowwithmeDB portfolio accounts sync: {matched} matched for {period_month}")
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


@growwithme_bp.route('/sync-staff', methods=['POST'])
def sync_staff():
    """Push an HR/staff master (a 'Working' sheet) into GrowwithmeDB — refreshes
    name, phone, joining date, DOB and reporting manager. DETAILS-ONLY (never
    changes branch/role/hierarchy). Upsert; never deletes. Requires an uploaded
    `file`. Re-running is safe (idempotent — no duplicates)."""
    try:
        up, is_temp = _uploaded_file()
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    if not up:
        return jsonify({'success': False, 'message': 'Upload a staff Excel file (with a "Working" sheet).'}), 400
    try:
        rows = _parse_staff_sheet(up)
    except Exception as e:
        logger.warning(f'GrowwithmeDB staff sync: parse failed: {e}')
        return jsonify({'success': False, 'message': f'Staff parse failed: {e}'}), 500
    finally:
        _cleanup(up, is_temp)
    if not rows:
        return jsonify({'success': False, 'message': 'No staff rows found in the Working sheet.'}), 400

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
