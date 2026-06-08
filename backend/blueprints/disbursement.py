"""
Disbursement Blueprint
======================
Accepts a client-disbursement CSV (ESAF ClientDisbursementDetail export),
normalises it (repairs unquoted LoanPurpose commas), filters Active rows,
aggregates by (disb_date, branch_name, emp_id, product_name), and pushes
to the Coll_Db Postgres `disbursement_daily` table on EC2.

Transport: ssh + psql pipe (no HTTP endpoint exists on the Node server for
disbursement uploads yet). The SSH key lives at ~/.ssh/aws-ec2.pem.
"""

import csv
import logging
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, jsonify, request

import config

logger = logging.getLogger(__name__)
disbursement_bp = Blueprint('disbursement', __name__)


def _resolve_ec2_key():
    """Locate the AWS EC2 SSH key. Same default as before, but configurable so
    the key can live in the engine project or anywhere via EC2_KEY_PATH.

    Order: $EC2_KEY_PATH / $EC2_SSH_KEY -> ~/.ssh/aws-ec2.pem -> a .pem next to
    the engine project (aws-ec2.pem / .ssh/ / secrets/). Falls back to the
    original default path if none exist (preview still works; push errors clearly).
    """
    env_path = os.environ.get('EC2_KEY_PATH') or os.environ.get('EC2_SSH_KEY')
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return str(p)
    default = Path.home() / '.ssh' / 'aws-ec2.pem'
    if default.exists():
        return str(default)
    try:
        base = Path(getattr(config, 'BASE_DIR', '.'))
        for cand in (base / 'aws-ec2.pem', base / '.ssh' / 'aws-ec2.pem',
                     base / 'secrets' / 'aws-ec2.pem'):
            if cand.exists():
                return str(cand)
    except Exception:
        pass
    return str(default)


# EC2 / Postgres connection — same defaults as the source, env-overridable so a
# real key/host/creds can be supplied without editing code.
EC2_HOST = os.environ.get('EC2_HOST', 'ec2-user@52.66.163.52')
EC2_KEY = _resolve_ec2_key()
PG_USER = os.environ.get('EC2_PG_USER', 'Raghunandan1157')
PG_PASS = os.environ.get('EC2_PG_PASS', 'raghu')
PG_DB = os.environ.get('EC2_PG_DB', 'postgres')

_MONTHS = {
    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
    'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
}

_EMP_RE = re.compile(r'(.*?)\((\d+)\)\s*$')

_PRODUCT_MAP = {
    '204207': 'IGL', '104207': 'IGL',
    '604001': 'IL',
    '81402': 'FIG', '264203': 'FIG',
}


def _dmy_to_iso(s):
    m = re.match(r'(\d{2})-([A-Za-z]{3})-(\d{4})', (s or '').strip())
    if not m:
        return None
    return f"{m.group(3)}-{_MONTHS[m.group(2)]}-{m.group(1)}"


def _parse_officer(raw):
    if not raw:
        return None, None
    m = _EMP_RE.match(raw.strip())
    if not m:
        return raw.strip(), None
    name = m.group(1).strip()
    code = m.group(2)
    if code.startswith('0300') and len(code) >= 9:
        emp_id = 'NL' + code[4:]
    else:
        emp_id = 'NL' + code[-5:]
    return name, emp_id


def _parse_csv(path):
    """Repair unquoted LoanPurpose commas, then parse.

    Returns list of dict rows. Silently skips rows that still mis-align
    after repair (rare).
    """
    with open(path, encoding='utf-8') as f:
        raw = list(csv.reader(f))
    if not raw:
        return []
    hdr = raw[0]
    ncol = len(hdr)
    if 'LoanPurpose' in hdr:
        pi = hdr.index('LoanPurpose')
    else:
        pi = 32
    out = []
    for row in raw[1:]:
        extra = len(row) - ncol
        if extra > 0:
            row = row[:pi] + [','.join(row[pi:pi + 1 + extra])] + row[pi + 1 + extra:]
        if len(row) == ncol:
            out.append(dict(zip(hdr, row)))
    return out


def _parse_xlsx(path):
    """Parse an ESAF disbursement xlsx. Header row is the first row with
    'DisbStatus' (case-insensitive) — allows optional title rows above."""
    import pandas as pd
    # Read without header first to locate the header row
    head = pd.read_excel(path, header=None, nrows=15, engine='openpyxl')
    header_row = None
    for i in range(len(head)):
        cells = [str(x).strip() for x in head.iloc[i].tolist()]
        if any(c.lower() == 'disbstatus' for c in cells):
            header_row = i
            break
    if header_row is None:
        header_row = 0
    df = pd.read_excel(path, header=header_row, engine='openpyxl')
    df.columns = [str(c).strip() for c in df.columns]
    # Normalise LoanDisbDate — could be a datetime or string
    if 'LoanDisbDate' in df.columns:
        def _fmt(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ''
            if hasattr(v, 'strftime'):
                try:
                    return v.strftime('%d-%b-%Y')
                except Exception:
                    pass
            return str(v).strip()
        df['LoanDisbDate'] = df['LoanDisbDate'].apply(_fmt)
    # Cast everything to str for the dict-row pipeline
    df = df.fillna('').astype(str)
    return df.to_dict(orient='records')


def _parse_file(path, filename):
    ext = Path(filename).suffix.lower()
    if ext == '.csv':
        return _parse_csv(path)
    if ext in ('.xlsx', '.xls'):
        return _parse_xlsx(path)
    raise ValueError(f"Unsupported extension: {ext}")


def _aggregate(rows, keep_dates=None):
    """Aggregate Active rows by (disb_date, branch, emp_id, product).

    keep_dates: set of 'YYYY-MM-DD' strings — when provided, only rows on
    those dates are kept.
    """
    agg = defaultdict(lambda: {'cnt': 0, 'amt': 0.0, 'officer_name': ''})
    dates = set()

    for r in rows:
        if (r.get('DisbStatus') or '').strip() != 'Active':
            continue
        iso = _dmy_to_iso(r.get('LoanDisbDate'))
        if not iso:
            continue
        if keep_dates is not None and iso not in keep_dates:
            continue
        dates.add(iso)
        branch = (r.get('BranchName') or '').strip().upper()
        if not branch:
            continue
        name, emp_id = _parse_officer(r.get('CreditOffcierName'))
        prod = _PRODUCT_MAP.get((r.get('SchemeID/ProductID') or '').strip(), 'IGL')
        k = (iso, branch, emp_id or '', prod)
        agg[k]['cnt'] += 1
        try:
            agg[k]['amt'] += float(r.get('DisbAmount') or 0)
        except ValueError:
            pass
        if name and not agg[k]['officer_name']:
            agg[k]['officer_name'] = name
    return agg, dates


def _per_date_summary(rows):
    """Return [{date, row_count, amount, cancelled_count, cancelled_amount}, ...].

    `row_count`/`amount` cover Active rows (used by aggregator + DB push).
    `cancelled_count`/`cancelled_amount` cover Cancelled rows for visibility —
    they are NOT pushed to the DB, just shown so users can verify nothing
    is silently dropped.
    """
    per = defaultdict(lambda: {
        'row_count': 0, 'amount': 0.0,
        'cancelled_count': 0, 'cancelled_amount': 0.0,
    })
    for r in rows:
        st = (r.get('DisbStatus') or '').strip()
        iso = _dmy_to_iso(r.get('LoanDisbDate'))
        if not iso:
            continue
        try:
            amt = float(r.get('DisbAmount') or 0)
        except ValueError:
            amt = 0.0
        if st == 'Active':
            per[iso]['row_count'] += 1
            per[iso]['amount'] += amt
        elif st == 'Cancelled':
            per[iso]['cancelled_count'] += 1
            per[iso]['cancelled_amount'] += amt
    return [
        {
            'date': d,
            'row_count': v['row_count'],
            'amount': round(v['amount'], 2),
            'cancelled_count': v['cancelled_count'],
            'cancelled_amount': round(v['cancelled_amount'], 2),
        }
        for d, v in sorted(per.items())
    ]


def _existing_dates(dates):
    """Query EC2 Postgres for existing rows per date. Returns {date: {count, amount}}."""
    if not dates:
        return {}
    dates_sql = ','.join(f"'{d}'" for d in sorted(dates))
    q = (
        f"SELECT disb_date::text, COUNT(*)::int, COALESCE(SUM(disb_amount),0)::numeric "
        f"FROM disbursement_daily WHERE disb_date IN ({dates_sql}) "
        f"GROUP BY disb_date ORDER BY disb_date;"
    )
    cmd = [
        'ssh', '-i', EC2_KEY, '-o', 'StrictHostKeyChecking=no',
        '-o', 'ConnectTimeout=10', EC2_HOST,
        f"PGPASSWORD={PG_PASS} psql -U {PG_USER} -h 127.0.0.1 -d {PG_DB} -tAF'|' -c \"{q}\"",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {}
    result = {}
    if proc.returncode != 0:
        logger.warning(f"existing_dates psql failed: {proc.stderr.strip()[:200]}")
        return result
    for line in (proc.stdout or '').strip().splitlines():
        parts = line.split('|')
        if len(parts) != 3:
            continue
        try:
            result[parts[0]] = {'count': int(parts[1]), 'amount': float(parts[2])}
        except ValueError:
            continue
    return result


def _build_sql(rows, keep_dates=None):
    """Return (sql_text, stats). Optional keep_dates filter."""
    agg, dates = _aggregate(rows, keep_dates=keep_dates)
    inserted = len(agg)
    total_amount = sum(v['amt'] for v in agg.values())
    total_count = sum(v['cnt'] for v in agg.values())

    if not agg:
        return None, {
            'inserted': 0, 'dates': 0, 'total_count': 0, 'total_amount': 0.0,
        }

    date_list = ','.join(f"'{d}'" for d in sorted(dates))
    lines = [
        'BEGIN;',
        f'DELETE FROM disbursement_daily WHERE disb_date IN ({date_list});',
        'CREATE TEMP TABLE _disb_stage (disb_date date, branch_name text, '
        'emp_id text, officer_name text, product_name text, '
        'disb_count int, disb_amount numeric);',
    ]
    for (iso, br, emp, prod), v in agg.items():
        off = (v['officer_name'] or '').replace("'", "''")
        emp_sql = f"'{emp}'" if emp else 'NULL'
        off_sql = f"'{off}'" if off else 'NULL'
        br_sql = br.replace("'", "''")
        lines.append(
            f"INSERT INTO _disb_stage VALUES "
            f"('{iso}','{br_sql}',{emp_sql},{off_sql},'{prod}',{v['cnt']},{v['amt']:.2f});"
        )
    lines.append(
        "INSERT INTO disbursement_daily "
        "(disb_date, region_name, district_name, branch_name, emp_id, officer_name, "
        "product_name, disb_count, disb_amount) "
        "SELECT s.disb_date, "
        "COALESCE(UPPER(em.region_name), 'UNKNOWN'), "
        "COALESCE(UPPER(em.region_name), 'UNKNOWN'), "
        "s.branch_name, s.emp_id, s.officer_name, s.product_name, s.disb_count, s.disb_amount "
        "FROM _disb_stage s "
        "LEFT JOIN (SELECT DISTINCT ON (UPPER(branch_name)) UPPER(branch_name) AS up, region_name "
        "FROM employee_master WHERE branch_name IS NOT NULL "
        "ORDER BY UPPER(branch_name), region_name) em ON em.up = s.branch_name;"
    )
    lines.append('COMMIT;')

    return '\n'.join(lines), {
        'inserted': inserted,
        'dates': len(dates),
        'total_count': total_count,
        'total_amount': round(total_amount, 2),
    }


def _push_to_ec2(sql_text):
    """Pipe SQL through ssh+psql. Returns (ok, stdout_tail, stderr_tail)."""
    cmd = [
        'ssh', '-i', EC2_KEY,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'ConnectTimeout=10',
        EC2_HOST,
        f"PGPASSWORD={PG_PASS} psql -U {PG_USER} -h 127.0.0.1 -d {PG_DB} -v ON_ERROR_STOP=1",
    ]
    proc = subprocess.run(
        cmd, input=sql_text, capture_output=True, text=True, timeout=300,
    )
    ok = proc.returncode == 0
    out = (proc.stdout or '').strip().splitlines()[-10:]
    err = (proc.stderr or '').strip().splitlines()[-10:]
    return ok, '\n'.join(out), '\n'.join(err)


def _save_upload(f, filename):
    ext = Path(filename).suffix.lower() or '.csv'
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                      dir=str(config.TEMP_DIR)) as tf:
        tmp = Path(tf.name)
        f.save(tf)
    return tmp


@disbursement_bp.route('/preview', methods=['POST'])
def preview():
    """Parse CSV + return per-date summary + which dates already exist in DB.

    Response: {dates: [{date, row_count, amount, exists: {count, amount} | null}]}
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    if not f.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Expected .csv / .xlsx file'}), 400

    tmp = _save_upload(f, f.filename)
    try:
        rows = _parse_file(tmp, f.filename)
        per = _per_date_summary(rows)
        existing = _existing_dates([d['date'] for d in per])
        total_cancelled = sum(d['cancelled_count'] for d in per)
        logger.info(
            f"Disbursement preview: {len(rows)} rows · {len(per)} dates · "
            f"{len(existing)} existing in DB · {total_cancelled} cancelled "
            f"(excluded from push)"
        )
        out = []
        for d in per:
            ex = existing.get(d['date'])
            out.append({
                'date': d['date'],
                'row_count': d['row_count'],
                'amount': d['amount'],
                'cancelled_count': d['cancelled_count'],
                'cancelled_amount': d['cancelled_amount'],
                'exists': ex,
            })
        return jsonify({
            'success': True,
            'dates': out,
            'csv_rows': len(rows),
            'cancelled_total': total_cancelled,
        })
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


@disbursement_bp.route('/process', methods=['POST'])
def process():
    """Push selected dates to Coll_Db. Overwrites existing rows for those dates.

    Form fields:
      - file: CSV
      - dates: comma-separated YYYY-MM-DD list. Empty/missing → all dates in CSV.
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    if not f.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Expected .csv / .xlsx file'}), 400

    raw_dates = (request.form.get('dates') or '').strip()
    keep_dates = None
    if raw_dates:
        keep_dates = {d.strip() for d in raw_dates.split(',') if d.strip()}

    tmp = _save_upload(f, f.filename)

    try:
        rows = _parse_file(tmp, f.filename)
        logger.info(
            f"Disbursement: parsed {len(rows)} CSV rows from {f.filename}"
            f"{' (filtering to ' + str(len(keep_dates)) + ' dates)' if keep_dates else ''}"
        )
        sql, stats = _build_sql(rows, keep_dates=keep_dates)
        if not sql:
            return jsonify({
                'success': False,
                'message': (
                    'No Active disbursement rows match the selected dates.'
                    if keep_dates else
                    'No Active disbursement rows found in CSV.'
                ),
                **stats,
            }), 400

        ok, out, err = _push_to_ec2(sql)
        if not ok:
            logger.warning(f"Disbursement push failed: {err}")
            return jsonify({
                'success': False,
                'message': f'EC2 push failed: {err or out}',
                **stats,
            }), 502

        logger.info(
            f"Disbursement: inserted {stats['inserted']} rows "
            f"across {stats['dates']} dates, ₹{stats['total_amount']:,.2f}"
        )
        return jsonify({
            'success': True,
            'message': (
                f"{stats['inserted']} rows · {stats['dates']} dates · "
                f"₹{stats['total_amount']:,.2f} total"
            ),
            **stats,
        })
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
