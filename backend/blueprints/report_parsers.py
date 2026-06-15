"""
Report parsers (shared)
=======================
Excel parsers for the generated EOD Employee Report and Quick Hourly Report,
plus the product-type id maps. Extracted from the old ``supabase_sync`` blueprint
so the GrowwithmeDB push (``growwithme_sync``) keeps working after the Supabase
sync was removed. No network/Supabase code here — parsing only.
"""

import logging

from openpyxl import load_workbook

import config

logger = logging.getLogger(__name__)

# Sheet (product) name -> product_type_id. VVY is renamed to IL.
_PRODUCT_TYPE_ID = {'IGL': 1, 'FIG': 2, 'IL': 3, 'VVY': 3}

# Disbursement product name -> product_type_id.
_DISB_PRODUCT_TYPE_ID = {'IGL': 1, 'FIG': 2, 'IL': 3}

# Quick Report carries no product split — all rows are combined (id 0).
_QUICK_HOURLY_PT_ID = 0

# (db_column, normalised header text) — order defines positional fallback.
_METRIC_HEADERS = [
    ('regular_demand',         'regular demand'),
    ('regular_collection',     'regular collection'),
    ('demand_1_30',            '1-30 demand'),
    ('collection_1_30',        '1-30 collection'),
    ('demand_31_60',           '31-60 demand'),
    ('collection_31_60',       '31-60 collection'),
    ('pnpa_demand',            'pnpa demand'),
    ('pnpa_collection',        'pnpa collection'),
    ('npa_cases',              'npa cases'),
    ('npa_act_acc',            'npa act acc'),
    ('npa_act_amt',            'npa act amt'),
    ('npa_clo_acc',            'npa clo acc'),
    ('npa_clo_amt',            'npa clo amt'),
    ('on_date_demand',         'on-date demand'),
    ('on_date_collection',     'on-date collection'),
    ('regular_demand_amt',     'regular demand amt'),
    ('regular_collection_amt', 'regular collection amt'),
    ('demand_1_30_amt',        '1-30 demand amt'),
    ('collection_1_30_amt',    '1-30 collection amt'),
    ('demand_31_60_amt',       '31-60 demand amt'),
    ('collection_31_60_amt',   '31-60 collection amt'),
    ('pnpa_demand_amt',        'pnpa demand amt'),
    ('pnpa_collection_amt',    'pnpa collection amt'),
    ('on_date_demand_amt',     'on-date demand amt'),
    ('on_date_collection_amt', 'on-date collection amt'),
]
_METRIC_DB_COLS = [c for c, _ in _METRIC_HEADERS]


def _norm(h):
    return ' '.join(str(h or '').strip().lower().split())


def _num(v):
    if v is None or v == '':
        return 0
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0
    return n if n == n and n not in (float('inf'), float('-inf')) else 0


def _safe_num(v):
    if v is None or v == '' or v == '-':
        return 0
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0
    return n if n == n and n not in (float('inf'), float('-inf')) else 0


def _employee_report_path():
    """Locate the latest EOD Employee Report."""
    for name in ('Employee_Report_Latest.xlsx', 'EOD_Report_Latest.xlsx'):
        p = config.BACKEND_DATA_DIR / name
        if p.exists():
            return p
    return None


def _parse_report(path):
    """Return list of row dicts: {emp_id, product_type_id, <25 metrics>}.

    Sheets ending in `_FY` and the `POS` / `EMP_POS` sheets are skipped, so
    only the per-product EOD sheets (IGL / FIG / VVY) are returned.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    try:
        for sheet_name in wb.sheetnames:
            if sheet_name.endswith('_FY') or sheet_name in ('POS', 'EMP_POS'):
                continue
            pt_id = _PRODUCT_TYPE_ID.get(sheet_name.strip().upper())
            if pt_id is None:
                logger.info(f"Report parse: skipping unknown sheet '{sheet_name}'")
                continue

            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            header = rows[0]
            # emp id column
            emp_idx = 3
            norm_hdr = [_norm(h) for h in header]
            for i, h in enumerate(norm_hdr):
                if h in ('emp id', 'empid', 'emp_id'):
                    emp_idx = i
                    break
            metrics_start = emp_idx + 2

            # header-name -> column index (first match wins)
            hdr_idx = {}
            for i, h in enumerate(norm_hdr):
                if h and h not in hdr_idx:
                    hdr_idx[h] = i
            col_map = {db: hdr_idx[txt] for db, txt in _METRIC_HEADERS if txt in hdr_idx}
            use_header_map = len(col_map) >= 20

            for row in rows[1:]:
                if not row or len(row) <= emp_idx:
                    continue
                if not row[0] or not row[emp_idx]:
                    continue
                emp_id = str(row[emp_idx]).strip()
                if not emp_id:
                    continue
                rec = {'emp_id': emp_id, 'product_type_id': pt_id}
                for i, db_col in enumerate(_METRIC_DB_COLS):
                    if use_header_map and db_col in col_map:
                        ci = col_map[db_col]
                    else:
                        ci = metrics_start + i
                    rec[db_col] = _num(row[ci]) if ci < len(row) else 0
                out.append(rec)
    finally:
        wb.close()
    return out


def _quick_report_path():
    """Locate the latest Quick Hourly Report."""
    p = config.BACKEND_DATA_DIR / 'Quick_Report_Latest.xlsx'
    return p if p.exists() else None


def _parse_quick_report(path):
    """Parse the Quick Report into hourly row dicts (product_type_id = 0).

    Mirrors the Coll_Db /api/upload-hourly Quick Report parser:
      - OverAll sheet Branch+Officer section (starts 4 rows after 'EMP ID')
      - OverAll_On-Date sheet supplies on_date_demand / on_date_collection
      - column layout (0-indexed): 0=EMP ID 2=Reg Demand 3=Reg Collection
        6=1-30 Demand 7=1-30 Collection 10=31-60 Demand 11=31-60 Collection
        14=PNPA Demand 15=PNPA Collection 22=NPA Cases 23=NPA Act Account
        24=NPA Act Amount.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if 'OverAll' not in wb.sheetnames:
            raise ValueError("OverAll sheet not found in Quick Report")
        over_rows = list(wb['OverAll'].iter_rows(values_only=True))

        officer_start = -1
        for r, row in enumerate(over_rows):
            if row and row[0] is not None and str(row[0]).strip() == 'EMP ID':
                officer_start = r
                break
        if officer_start < 0:
            raise ValueError("Could not find Branch+Officer section (no EMP ID header)")
        data_start = officer_start + 4

        # On-Date sheet → {emp_id: {demand, collection}}
        on_date = {}
        if 'OverAll_On-Date' in wb.sheetnames:
            od_rows = list(wb['OverAll_On-Date'].iter_rows(values_only=True))
            od_start = -1
            for r, row in enumerate(od_rows):
                if row and row[0] is not None and str(row[0]).strip() == 'EMP ID':
                    od_start = r
                    break
            if od_start >= 0:
                for row in od_rows[od_start + 4:]:
                    if not row or not row[0]:
                        continue
                    emp = str(row[0]).strip()
                    if not emp or emp == 'EMP ID':
                        continue
                    on_date[emp] = {
                        'demand': _safe_num(row[2] if len(row) > 2 else 0),
                        'collection': _safe_num(row[3] if len(row) > 3 else 0),
                    }

        out = []
        for row in over_rows[data_start:]:
            if not row:
                continue
            col0 = str(row[0]).strip() if row[0] is not None else ''
            # Skip branch rows (blank col0), Grand Total, nested headers.
            if not col0 or col0 in ('Grand Total', 'EMP ID'):
                continue
            emp = col0
            od = on_date.get(emp, {'demand': 0, 'collection': 0})

            def g(i):
                return _safe_num(row[i]) if len(row) > i else 0

            out.append({
                'emp_id': emp,
                'product_type_id': _QUICK_HOURLY_PT_ID,
                'regular_demand': g(2),
                'regular_collection': g(3),
                'demand_1_30': g(6),
                'collection_1_30': g(7),
                'demand_31_60': g(10),
                'collection_31_60': g(11),
                'pnpa_demand': g(14),
                'pnpa_collection': g(15),
                'npa_cases': g(22),
                'npa_act_acc': g(23),
                'npa_act_amt': g(24),
                'npa_clo_acc': 0,
                'npa_clo_amt': 0,
                'on_date_demand': od['demand'],
                'on_date_collection': od['collection'],
                'regular_demand_amt': 0, 'regular_collection_amt': 0,
                'demand_1_30_amt': 0, 'collection_1_30_amt': 0,
                'demand_31_60_amt': 0, 'collection_31_60_amt': 0,
                'pnpa_demand_amt': 0, 'pnpa_collection_amt': 0,
                'on_date_demand_amt': 0, 'on_date_collection_amt': 0,
            })
        return out
    finally:
        wb.close()
