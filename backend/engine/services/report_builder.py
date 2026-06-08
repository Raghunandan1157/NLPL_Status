"""
report_builder.py - Generate formatted Excel report directly from precomputed data.
==================================================================================
Replaces the VBA macro (vba_template.js) with pure Python xlsxwriter output.
All report sheets are generated server-side in <1 second -- no VBA execution needed.

The precomputed DataFrame (_precomp) already contains all aggregated metrics.
This module reads those metrics and writes fully formatted report sheets.

Two layouts are used:
  - Summary layout (OverAll, FY sheets): ON DATE + RANK + PERFORMANCE
  - Entity layout (region, division, area, branch sheets): ON DATE + RANK only, 4 side-by-side views
"""

import logging
import time
from pathlib import Path

import pandas as pd
import xlsxwriter


# ---------------------------------------------------------------------------
# Summary layout columns (0-based, for OverAll / FY sheets)
# B=Name, C-E=OnDate, F-I=Regular, J-M=1-30, N-Q=31-60, R-U=PNPA, V-Z=NPA, AA=RANK, AB=PERF
# ---------------------------------------------------------------------------
COL_NAME = 1
COL_OD_DEM = 2
COL_OD_COL = 3
COL_OD_PCT = 4
COL_REG_DEM = 5
COL_REG_COL = 6
COL_REG_FTOD = 7
COL_REG_PCT = 8
COL_130_DEM = 9
COL_130_COL = 10
COL_130_BAL = 11
COL_130_PCT = 12
COL_3160_DEM = 13
COL_3160_COL = 14
COL_3160_BAL = 15
COL_3160_PCT = 16
COL_PNPA_DEM = 17
COL_PNPA_COL = 18
COL_PNPA_BAL = 19
COL_PNPA_PCT = 20
COL_190_DEM = 21
COL_190_COL = 22
COL_190_BAL = 23
COL_190_PCT = 24
COL_NPA_DEM = 25
COL_NPA_ACT_ACC = 26
COL_NPA_ACT_AMT = 27
COL_NPA_CLO_ACC = 28
COL_NPA_CLO_AMT = 29
COL_RANK = 30
COL_PERF = 31
LAST_COL = COL_PERF

# ---------------------------------------------------------------------------
# Entity layout columns (0-based, for region/division/area/branch sheets)
# B=Name, C-E=OnDate, F-I=Regular, J-M=1-30, N-Q=31-60, R-U=PNPA, V-Z=NPA, AA=RANK
# ---------------------------------------------------------------------------
E_NAME = 1
E_OD_DEM = 2
E_OD_COL = 3
E_OD_PCT = 4
E_REG_DEM = 5
E_REG_COL = 6
E_REG_FTOD = 7
E_REG_PCT = 8
E_130_DEM = 9
E_130_COL = 10
E_130_BAL = 11
E_130_PCT = 12
E_3160_DEM = 13
E_3160_COL = 14
E_3160_BAL = 15
E_3160_PCT = 16
E_PNPA_DEM = 17
E_PNPA_COL = 18
E_PNPA_BAL = 19
E_PNPA_PCT = 20
E_190_DEM = 21
E_190_COL = 22
E_190_BAL = 23
E_190_PCT = 24
E_NPA_DEM = 25
E_NPA_ACT_ACC = 26
E_NPA_ACT_AMT = 27
E_NPA_CLO_ACC = 28
E_NPA_CLO_AMT = 29
E_RANK = 30
E_LAST = E_RANK

# Precomp column name mappings
COUNT_MAP = {
    'reg_dem': 'reg_demand',
    'reg_col': 'reg_collection',
    'dem_130': 'dem_130',
    'col_130': 'col_130',
    'dem_3160': 'dem_3160',
    'col_3160': 'col_3160',
    'pnpa_dem': 'pnpa_demand',
    'pnpa_col': 'pnpa_collection',
    'npa_cases': 'npa_cases',
    'npa_act_acc': 'npa_act_acc',
    'npa_act_amt': 'npa_act_amt',
    'npa_clo_acc': 'npa_clo_acc',
    'npa_clo_amt': 'npa_clo_amt',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(v):
    """Convert value to float, NaN/None -> 0."""
    try:
        if v is None:
            return 0.0
        f = float(v)
        return 0.0 if f != f else f  # NaN check
    except (ValueError, TypeError):
        return 0.0


def _pct(num, den):
    n, d = _safe(num), _safe(den)
    return n / d if d > 0 else 0.0


def _get_fy_label(target_date):
    """Return Indian Financial Year label like 'FY_25-26' for the given date.

    Delegates to the centralized get_fy_label in eod_processor.
    """
    from services.eod_processor import get_fy_label
    return get_fy_label(target_date)


def _build_index(pc_df):
    """Build a fast lookup index: (filter_type, filter_value, scope, product) -> {group_value: dict}."""
    idx = {}
    cols = pc_df.columns.tolist()
    for vals in pc_df.itertuples(index=False, name=None):
        row = dict(zip(cols, vals))
        key = (row['filter_type'], str(row['filter_value']).strip(),
               row['scope'], row['product'])
        gv = str(row['group_value']).strip()
        if key not in idx:
            idx[key] = {}
        idx[key][gv] = row
    return idx


def _query(pc_idx, filter_type, filter_value, scope, product):
    """Query pre-built index. Returns {group_value: dict}."""
    return pc_idx.get((filter_type, str(filter_value).strip(), scope, product), {})


def _get_unique_filter_values(pc_idx, filter_type, scope, product):
    """Get unique filter_value entries for a given filter_type from the index."""
    vals = set()
    for key in pc_idx:
        ft, fv, sc, pr = key
        if ft == filter_type and sc == scope and pr == product and fv != 'ALL':
            vals.add(fv)
    return sorted(vals)


# ---------------------------------------------------------------------------
# Format factory
# ---------------------------------------------------------------------------

def _create_formats(wb):
    """Create all reusable cell formats."""
    f = {}

    f['title'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#FCE4D6', 'border': 1,
    })
    f['title_blue'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#BDD7EE', 'border': 1,
    })
    f['title_grey'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#C8C8C8', 'border': 1,
    })

    f['hdr_orange'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#FCE4D6', 'border': 1, 'text_wrap': True,
    })
    f['hdr_blue'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#BDD7EE', 'border': 1, 'text_wrap': True,
    })

    f['lhdr_orange'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#FCE4D6', 'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['lhdr_green'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#E2EFDA', 'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['lhdr_yellow'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#FFFFCC', 'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['lhdr_npa_green'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#E2EFDA', 'border': 1, 'font_size': 9,
    })

    f['name_hdr'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter',
        'bg_color': '#F4B084', 'border': 1,
    })

    for suffix, color in [('orange', '#FCE4D6'), ('green', '#E2EFDA'), ('yellow', '#FFFFCC')]:
        f[f'd_{suffix}'] = wb.add_format({
            'align': 'center', 'valign': 'vcenter', 'border': 1,
            'bg_color': color, 'num_format': '#,##0',
        })
        f[f'd_{suffix}_pct'] = wb.add_format({
            'align': 'center', 'valign': 'vcenter', 'border': 1,
            'bg_color': color, 'num_format': '0.0%',
        })
        f[f'd_{suffix}_str'] = wb.add_format({
            'align': 'center', 'valign': 'vcenter', 'border': 1,
            'bg_color': color,
        })

    f['d_name'] = wb.add_format({
        'valign': 'vcenter', 'border': 1, 'bg_color': '#FCE4D6',
    })
    f['d_name_bold'] = wb.add_format({
        'valign': 'vcenter', 'border': 1, 'bg_color': '#FCE4D6', 'bold': True,
    })

    f['gt'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#F4B084', 'num_format': '#,##0',
    })
    f['gt_pct'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#F4B084', 'num_format': '0.0%',
    })
    f['gt_name'] = wb.add_format({
        'bold': True, 'valign': 'vcenter', 'border': 1, 'bg_color': '#F4B084',
    })
    f['gt_str'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#F4B084',
    })

    f['rank'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#DDEBF7',
    })
    f['perf_above'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#C6EFCE', 'font_color': '#006100',
    })
    f['perf_below'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#FFC7CE', 'font_color': '#9C0006',
    })
    f['perf_na'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#F2F2F2',
    })

    f['bo_branch'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#D9E2F3', 'num_format': '#,##0',
    })
    f['bo_branch_pct'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#D9E2F3', 'num_format': '0.0%',
    })
    f['bo_branch_name'] = wb.add_format({
        'bold': True, 'valign': 'vcenter', 'border': 1, 'bg_color': '#D9E2F3',
    })
    f['bo_branch_str'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#D9E2F3',
    })

    f['bo_officer'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '#,##0',
    })
    f['bo_officer_pct'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.0%',
    })
    f['bo_officer_name'] = wb.add_format({
        'valign': 'vcenter', 'border': 1, 'indent': 2,
    })
    f['bo_officer_str'] = wb.add_format({
        'align': 'center', 'valign': 'vcenter', 'border': 1,
    })

    # Area subtotal row in B+O sections (dark blue #5B9BD5 + white text)
    f['bo_area'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#5B9BD5', 'font_color': '#FFFFFF', 'num_format': '#,##0',
    })
    f['bo_area_pct'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#5B9BD5', 'font_color': '#FFFFFF', 'num_format': '0.0%',
    })
    f['bo_area_name'] = wb.add_format({
        'bold': True, 'valign': 'vcenter', 'border': 1,
        'bg_color': '#5B9BD5', 'font_color': '#FFFFFF',
    })
    f['bo_area_str'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1,
        'bg_color': '#5B9BD5', 'font_color': '#FFFFFF',
    })

    return f


# ---------------------------------------------------------------------------
# Cell writer helpers
# ---------------------------------------------------------------------------

def _write_val(ws, r, col, value, fmt_num, fmt_str):
    """Write a value: if 0 write '-', else write number."""
    v = _safe(value)
    if v == 0:
        ws.write_string(r, col, '-', fmt_str)
    else:
        ws.write_number(r, col, v, fmt_num)


def _write_pct(ws, r, col, num, den, fmt_pct, fmt_str):
    """Write a percentage: if numerator or denominator is 0 write '-', else write ratio."""
    n, d = _safe(num), _safe(den)
    if d == 0 or n == 0:
        ws.write_string(r, col, '-', fmt_str)
    else:
        ws.write_number(r, col, n / d, fmt_pct)


# ---------------------------------------------------------------------------
# Extract metrics from precomp row
# ---------------------------------------------------------------------------

def _extract_metrics(row_data, metric_map):
    """Extract all metric values from a precomp row dict."""
    g = row_data.get
    return {
        'reg_dem': _safe(g(metric_map['reg_dem'], 0)),
        'reg_col': _safe(g(metric_map['reg_col'], 0)),
        'd130': _safe(g(metric_map['dem_130'], 0)),
        'c130': _safe(g(metric_map['col_130'], 0)),
        'd3160': _safe(g(metric_map['dem_3160'], 0)),
        'c3160': _safe(g(metric_map['col_3160'], 0)),
        'pnpa_d': _safe(g(metric_map['pnpa_dem'], 0)),
        'pnpa_c': _safe(g(metric_map['pnpa_col'], 0)),
        'npa': _safe(g(metric_map['npa_cases'], 0)),
        'npa_aa': _safe(g(metric_map['npa_act_acc'], 0)),
        'npa_am': _safe(g(metric_map['npa_act_amt'], 0)),
        'npa_ca': _safe(g(metric_map['npa_clo_acc'], 0)),
        'npa_cm': _safe(g(metric_map['npa_clo_amt'], 0)),
        'od_dem': _safe(g('od_demand', 0)),
        'od_col': _safe(g('od_collection', 0)),
        'od_dem_next': _safe(g('od_demand_next', 0)),
        # Hourly-collection variants of the DPD bucket collections + NPA
        # activation — same formula the Daily Collection Report OverAll uses.
        'c130_h': _safe(g('hourly_col_130', 0)),
        'c3160_h': _safe(g('hourly_col_3160', 0)),
        'pnpa_c_h': _safe(g('hourly_pnpa_collection', 0)),
        'npa_aa_h': _safe(g('npa_hourly_acc', 0)),
        'npa_am_h': _safe(g('npa_hourly_amt', 0)),
    }


def _write_common_cols(ws, r, m, fn, fp, fs, col_reg_dem, col_reg_col,
                       col_reg_ftod, col_reg_pct, col_130_dem, col_130_col,
                       col_130_bal, col_130_pct, col_3160_dem, col_3160_col,
                       col_3160_bal, col_3160_pct, col_pnpa_dem, col_pnpa_col,
                       col_pnpa_bal, col_pnpa_pct, col_npa_dem, col_npa_act_acc,
                       col_npa_act_amt, col_npa_clo_acc, col_npa_clo_amt,
                       fn_g=None, fs_g=None, fp_str=None, hourly_buckets=False):
    """Write the common metric columns (Regular through NPA) at specified positions.

    fp_str : str format for the '-' dash in COLLECTION % columns. Defaults to
    fs. Pass the yellow string format so pct dashes stay yellow while orange
    columns (DEMAND/FTOD/BALANCE) keep their orange dash via fs.

    hourly_buckets : accepted for compatibility but NOT used — the EOD report
    has no hourly-collection data (the hourly_col_*/npa_hourly_* precompute
    fields are empty without the Quick/Hourly overlay), so the OverAll keeps the
    daily-collection fields (col_*, npa_act_*).
    """
    if fn_g is None:
        fn_g = fn
    if fs_g is None:
        fs_g = fs
    if fp_str is None:
        fp_str = fs
    c130 = m['c130']
    c3160 = m['c3160']
    pnpa_c = m['pnpa_c']
    npa_aa = m['npa_aa']
    npa_am = m['npa_am']
    _write_val(ws, r, col_reg_dem, m['reg_dem'], fn, fs)
    _write_val(ws, r, col_reg_col, m['reg_col'], fn_g, fs_g)
    _write_val(ws, r, col_reg_ftod, m['reg_dem'] - m['reg_col'], fn, fs)
    _write_pct(ws, r, col_reg_pct, m['reg_col'], m['reg_dem'], fp, fp_str)
    _write_val(ws, r, col_130_dem, m['d130'], fn, fs)
    _write_val(ws, r, col_130_col, c130, fn_g, fs_g)
    _write_val(ws, r, col_130_bal, m['d130'] - c130, fn, fs)
    _write_pct(ws, r, col_130_pct, c130, m['d130'], fp, fp_str)
    _write_val(ws, r, col_3160_dem, m['d3160'], fn, fs)
    _write_val(ws, r, col_3160_col, c3160, fn_g, fs_g)
    _write_val(ws, r, col_3160_bal, m['d3160'] - c3160, fn, fs)
    _write_pct(ws, r, col_3160_pct, c3160, m['d3160'], fp, fp_str)
    _write_val(ws, r, col_pnpa_dem, m['pnpa_d'], fn, fs)
    _write_val(ws, r, col_pnpa_col, pnpa_c, fn_g, fs_g)
    _write_val(ws, r, col_pnpa_bal, m['pnpa_d'] - pnpa_c, fn, fs)
    _write_pct(ws, r, col_pnpa_pct, pnpa_c, m['pnpa_d'], fp, fp_str)
    # 1-90 DPD combined bucket
    dem_190 = m['d130'] + m['d3160'] + m['pnpa_d']
    col_190 = c130 + c3160 + pnpa_c
    _write_val(ws, r, col_pnpa_pct + 1, dem_190, fn, fs)
    _write_val(ws, r, col_pnpa_pct + 2, col_190, fn_g, fs_g)
    _write_val(ws, r, col_pnpa_pct + 3, dem_190 - col_190, fn, fs)
    _write_pct(ws, r, col_pnpa_pct + 4, col_190, dem_190, fp, fp_str)
    _write_val(ws, r, col_npa_dem, m['npa'], fn, fs)
    _write_val(ws, r, col_npa_act_acc, npa_aa, fn, fs)
    _write_val(ws, r, col_npa_act_amt, npa_am, fn, fs)
    _write_val(ws, r, col_npa_clo_acc, m['npa_ca'], fn_g, fs_g)
    _write_val(ws, r, col_npa_clo_amt, m['npa_cm'], fn_g, fs_g)


# ============================================================================
# SUMMARY LAYOUT (OverAll / FY sheets)
# ============================================================================

def _write_section_headers(ws, r, entity_label, fmts):
    """Write the 3-row header block for summary sheets."""
    h2 = r
    ws.merge_range(h2, COL_NAME, h2 + 1, COL_NAME, f'{entity_label} NAME', fmts['name_hdr'])
    ws.merge_range(h2, COL_OD_DEM, h2, COL_OD_PCT, 'ON DATE Demand Vs Collection', fmts['hdr_orange'])
    ws.merge_range(h2, COL_REG_DEM, h2, COL_REG_PCT, 'REGULAR DEMAND VS COLLECTION', fmts['hdr_orange'])
    ws.merge_range(h2, COL_130_DEM, h2, COL_130_PCT, '1-30 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_3160_DEM, h2, COL_3160_PCT, '31-60 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_PNPA_DEM, h2, COL_PNPA_PCT, 'PNPA', fmts['hdr_orange'])
    ws.merge_range(h2, COL_190_DEM, h2, COL_190_PCT, '1-90 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_NPA_DEM, h2, COL_NPA_CLO_AMT, 'NPA', fmts['hdr_orange'])
    ws.merge_range(h2, COL_RANK, h2, COL_PERF, 'METRICS', fmts['hdr_blue'])
    r += 1

    h3 = r
    for col, text, fmt_key in [
        (COL_OD_DEM, 'DEMAND', 'lhdr_orange'), (COL_OD_COL, 'COLLECTION', 'lhdr_green'),
        (COL_OD_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_REG_DEM, 'DEMAND', 'lhdr_orange'), (COL_REG_COL, 'COLLECTION', 'lhdr_green'),
        (COL_REG_FTOD, 'FTOD', 'lhdr_orange'), (COL_REG_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_130_DEM, 'DEMAND', 'lhdr_orange'), (COL_130_COL, 'COLLECTION', 'lhdr_green'),
        (COL_130_BAL, 'BALANCE', 'lhdr_orange'), (COL_130_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_3160_DEM, 'DEMAND', 'lhdr_orange'), (COL_3160_COL, 'COLLECTION', 'lhdr_green'),
        (COL_3160_BAL, 'BALANCE', 'lhdr_orange'), (COL_3160_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_PNPA_DEM, 'DEMAND', 'lhdr_orange'), (COL_PNPA_COL, 'COLLECTION', 'lhdr_green'),
        (COL_PNPA_BAL, 'BALANCE', 'lhdr_orange'), (COL_PNPA_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_190_DEM, 'DEMAND', 'lhdr_orange'), (COL_190_COL, 'COLLECTION', 'lhdr_green'),
        (COL_190_BAL, 'BALANCE', 'lhdr_orange'), (COL_190_PCT, 'COLLECTION %', 'lhdr_yellow'),
    ]:
        ws.merge_range(h3, col, h3 + 1, col, text, fmts[fmt_key])

    ws.merge_range(h3, COL_NPA_DEM, h3 + 1, COL_NPA_DEM, 'DEMAND', fmts['lhdr_orange'])
    ws.merge_range(h3, COL_NPA_ACT_ACC, h3, COL_NPA_ACT_AMT, 'ACTIVATION', fmts['lhdr_orange'])
    ws.merge_range(h3, COL_NPA_CLO_ACC, h3, COL_NPA_CLO_AMT, 'CLOSURE', fmts['lhdr_orange'])
    ws.write(h3 + 1, COL_NPA_ACT_ACC, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_ACT_AMT, 'AMOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_CLO_ACC, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_CLO_AMT, 'AMOUNT', fmts['lhdr_npa_green'])

    ws.merge_range(h3, COL_RANK, h3 + 1, COL_RANK, 'RANK', fmts['hdr_blue'])
    ws.merge_range(h3, COL_PERF, h3 + 1, COL_PERF, 'PERFORMANCE', fmts['hdr_blue'])

    return h3 + 2


def _write_data_row(ws, r, name, row_data, metric_map, fmts,
                    rank, gt_pct, is_grand_total=False):
    """Write a summary data row with RANK + PERFORMANCE."""
    m = _extract_metrics(row_data, metric_map)

    if is_grand_total:
        fn, fp, fs = fmts['gt'], fmts['gt_pct'], fmts['gt_str']
        ws.write(r, COL_NAME, name, fmts['gt_name'])
        # ON DATE
        _write_val(ws, r, COL_OD_DEM, m['od_dem'], fn, fs)
        _write_val(ws, r, COL_OD_COL, m['od_col'], fn, fs)
        _write_pct(ws, r, COL_OD_PCT, m['od_col'], m['od_dem'], fp, fs)
        _write_common_cols(ws, r, m, fn, fp, fs,
                           COL_REG_DEM, COL_REG_COL, COL_REG_FTOD, COL_REG_PCT,
                           COL_130_DEM, COL_130_COL, COL_130_BAL, COL_130_PCT,
                           COL_3160_DEM, COL_3160_COL, COL_3160_BAL, COL_3160_PCT,
                           COL_PNPA_DEM, COL_PNPA_COL, COL_PNPA_BAL, COL_PNPA_PCT,
                           COL_NPA_DEM, COL_NPA_ACT_ACC, COL_NPA_ACT_AMT,
                           COL_NPA_CLO_ACC, COL_NPA_CLO_AMT, hourly_buckets=True)
        ws.write_string(r, COL_RANK, '-', fs)
        ws.write_string(r, COL_PERF, '-', fs)
    else:
        fn_o, fn_g = fmts['d_orange'], fmts['d_green']
        fp_y = fmts['d_yellow_pct']
        fs_o, fs_g, fs_y = fmts['d_orange_str'], fmts['d_green_str'], fmts['d_yellow_str']

        ws.write(r, COL_NAME, name, fmts['d_name'])
        # ON DATE
        _write_val(ws, r, COL_OD_DEM, m['od_dem'], fn_o, fs_o)
        _write_val(ws, r, COL_OD_COL, m['od_col'], fn_g, fs_g)
        _write_pct(ws, r, COL_OD_PCT, m['od_col'], m['od_dem'], fp_y, fs_y)
        _write_common_cols(ws, r, m, fn_o, fp_y, fs_o,
                           COL_REG_DEM, COL_REG_COL, COL_REG_FTOD, COL_REG_PCT,
                           COL_130_DEM, COL_130_COL, COL_130_BAL, COL_130_PCT,
                           COL_3160_DEM, COL_3160_COL, COL_3160_BAL, COL_3160_PCT,
                           COL_PNPA_DEM, COL_PNPA_COL, COL_PNPA_BAL, COL_PNPA_PCT,
                           COL_NPA_DEM, COL_NPA_ACT_ACC, COL_NPA_ACT_AMT,
                           COL_NPA_CLO_ACC, COL_NPA_CLO_AMT,
                           fn_g=fn_g, fs_g=fs_g, fp_str=fs_y, hourly_buckets=True)

        reg_pct = _pct(m['reg_col'], m['reg_dem'])
        if m['reg_dem'] == 0:
            ws.write_string(r, COL_RANK, '#VALUE!', fmts['rank'])
            ws.write(r, COL_PERF, '\u25CF N/A', fmts['perf_na'])
        elif reg_pct >= gt_pct:
            ws.write_number(r, COL_RANK, rank, fmts['rank'])
            ws.write(r, COL_PERF, '\u25B2 Above Average', fmts['perf_above'])
        else:
            ws.write_number(r, COL_RANK, rank, fmts['rank'])
            ws.write(r, COL_PERF, '\u25BC Below Average', fmts['perf_below'])


def _write_section(ws, start_row, data, metric_map, title, report_date,
                   fmts, sheet_label=''):
    """Write a summary section (Region/Division/Area/Branch on OverAll/FY sheets)."""
    r = start_row
    items = sorted(k for k in data if k != 'Grand Total')
    if not items:
        return r

    grand_total = data.get('Grand Total')

    label = f'{title} - WISE COLLECTION REPORT - as on {report_date} {sheet_label}'.strip()
    ws.merge_range(r, COL_NAME, r, LAST_COL, label, fmts['title'])
    r += 1

    r = _write_section_headers(ws, r, title, fmts)

    gt_reg_pct = 0.0
    if grand_total is not None:
        gt_reg_pct = _pct(
            _safe(grand_total.get(metric_map['reg_col'], 0)),
            _safe(grand_total.get(metric_map['reg_dem'], 0)),
        )

    pct_values = []
    for name in items:
        row_data = data[name]
        reg_pct = _pct(
            _safe(row_data.get(metric_map['reg_col'], 0)),
            _safe(row_data.get(metric_map['reg_dem'], 0)),
        )
        pct_values.append((name, reg_pct))

    sorted_by_pct = sorted(pct_values, key=lambda x: -x[1])
    rank_map = {name: idx for idx, (name, _) in enumerate(sorted_by_pct, 1)}
    items_sorted = [name for name, _ in sorted_by_pct]

    for name in items_sorted:
        _write_data_row(ws, r, name, data[name], metric_map, fmts,
                        rank_map.get(name, 0), gt_reg_pct)
        r += 1

    if grand_total is not None:
        _write_data_row(ws, r, 'Grand Total', grand_total, metric_map, fmts,
                        0, gt_reg_pct, is_grand_total=True)
        r += 1

    return r + 3  # 3 blank rows between sections (matches VBA)


def _write_branch_section_grouped(ws, start_row, pc_idx, scope, product,
                                   report_date, fmts, sheet_label=''):
    """Write BRANCH section grouped by area (for OverAll/FY summary sheets)."""
    r = start_row
    all_branch_data = _query(pc_idx, 'All_Branch', 'ALL', scope, product)
    items = [k for k in all_branch_data if k != 'Grand Total']
    if not items:
        return r

    grand_total = all_branch_data.get('Grand Total')

    label = f'BRANCH - WISE COLLECTION REPORT - as on {report_date} {sheet_label}'.strip()
    ws.merge_range(r, COL_NAME, r, LAST_COL, label, fmts['title'])
    r += 1
    r = _write_section_headers(ws, r, 'BRANCH', fmts)

    # Compute global rank map
    gt_reg_pct = 0.0
    if grand_total is not None:
        gt_reg_pct = _pct(
            _safe(grand_total.get(COUNT_MAP['reg_col'], 0)),
            _safe(grand_total.get(COUNT_MAP['reg_dem'], 0)),
        )
    pct_values = [
        (name, _pct(_safe(all_branch_data[name].get(COUNT_MAP['reg_col'], 0)),
                    _safe(all_branch_data[name].get(COUNT_MAP['reg_dem'], 0))))
        for name in items
    ]
    rank_map = {name: idx for idx, (name, _) in enumerate(sorted(pct_values, key=lambda x: -x[1]), 1)}

    areas = _get_unique_filter_values(pc_idx, 'Area', scope, product)
    if areas:
        all_area_data = _query(pc_idx, 'All_Area', 'ALL', scope, product)
        fn, fp, fs = fmts['bo_area'], fmts['bo_area_pct'], fmts['bo_area_str']
        for area in areas:
            m = _extract_metrics(all_area_data.get(area, {}), COUNT_MAP)
            ws.write(r, COL_NAME, f'{area.upper()} AREA', fmts['bo_area_name'])
            _write_val(ws, r, COL_OD_DEM, m['od_dem'], fn, fs)
            _write_val(ws, r, COL_OD_COL, m['od_col'], fn, fs)
            _write_pct(ws, r, COL_OD_PCT, m['od_col'], m['od_dem'], fp, fs)
            _write_common_cols(ws, r, m, fn, fp, fs,
                               COL_REG_DEM, COL_REG_COL, COL_REG_FTOD, COL_REG_PCT,
                               COL_130_DEM, COL_130_COL, COL_130_BAL, COL_130_PCT,
                               COL_3160_DEM, COL_3160_COL, COL_3160_BAL, COL_3160_PCT,
                               COL_PNPA_DEM, COL_PNPA_COL, COL_PNPA_BAL, COL_PNPA_PCT,
                               COL_NPA_DEM, COL_NPA_ACT_ACC, COL_NPA_ACT_AMT,
                               COL_NPA_CLO_ACC, COL_NPA_CLO_AMT, hourly_buckets=True)
            ws.write_string(r, COL_RANK, '-', fs)
            ws.write_string(r, COL_PERF, '-', fs)
            r += 1
            branch_data = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(
                (k for k in branch_data if k != 'Grand Total'),
                key=lambda b: rank_map.get(b, float('inf'))
            ):
                _write_data_row(ws, r, branch, branch_data[branch], COUNT_MAP, fmts,
                                rank_map.get(branch, 0), gt_reg_pct)
                r += 1
    else:
        items_sorted = [name for name, _ in sorted(pct_values, key=lambda x: -x[1])]
        for name in items_sorted:
            _write_data_row(ws, r, name, all_branch_data[name], COUNT_MAP, fmts,
                            rank_map.get(name, 0), gt_reg_pct)
            r += 1

    if grand_total is not None:
        _write_data_row(ws, r, 'Grand Total', grand_total, COUNT_MAP, fmts,
                        0, gt_reg_pct, is_grand_total=True)
        r += 1

    return r + 3


def _compute_officer_ranks(officers):
    """Rank officers within a branch by Regular Demand vs Collection % (descending).
    Returns {emp_id: rank_int}. Officers with 0 reg_demand get rank 0 → '#VALUE!'."""
    items = [k for k in officers if k != 'Grand Total']
    ranked = [
        (emp, _pct(_safe(officers[emp].get('reg_collection', 0)),
                   _safe(officers[emp].get('reg_demand', 0))))
        for emp in items if _safe(officers[emp].get('reg_demand', 0)) > 0
    ]
    rank_map = {emp: idx for idx, (emp, _) in enumerate(sorted(ranked, key=lambda x: -x[1]), 1)}
    for emp in items:
        if emp not in rank_map:
            rank_map[emp] = 0  # 0 → '#VALUE!'
    return rank_map


# Summary B+O row writer
def _write_bo_row(ws, r, name, row_data, metric_map, fmts, is_branch=True, rank=None):
    """Write a Branch or Officer row in B+O section (summary layout)."""
    m = _extract_metrics(row_data, metric_map)

    fn = fmts['bo_branch'] if is_branch else fmts['bo_officer']
    fp = fmts['bo_branch_pct'] if is_branch else fmts['bo_officer_pct']
    fn_name = fmts['bo_branch_name'] if is_branch else fmts['bo_officer_name']
    fs = fmts['bo_branch_str'] if is_branch else fmts['bo_officer_str']

    display_name = name
    if not is_branch:
        officer_name = str(row_data.get('officer_name', ''))
        if officer_name:
            display_name = f'{name} - {officer_name}'

    ws.write(r, COL_NAME, display_name, fn_name)
    # ON DATE
    _write_val(ws, r, COL_OD_DEM, m['od_dem'], fn, fs)
    _write_val(ws, r, COL_OD_COL, m['od_col'], fn, fs)
    _write_pct(ws, r, COL_OD_PCT, m['od_col'], m['od_dem'], fp, fs)
    _write_common_cols(ws, r, m, fn, fp, fs,
                       COL_REG_DEM, COL_REG_COL, COL_REG_FTOD, COL_REG_PCT,
                       COL_130_DEM, COL_130_COL, COL_130_BAL, COL_130_PCT,
                       COL_3160_DEM, COL_3160_COL, COL_3160_BAL, COL_3160_PCT,
                       COL_PNPA_DEM, COL_PNPA_COL, COL_PNPA_BAL, COL_PNPA_PCT,
                       COL_NPA_DEM, COL_NPA_ACT_ACC, COL_NPA_ACT_AMT,
                       COL_NPA_CLO_ACC, COL_NPA_CLO_AMT, hourly_buckets=True)
    if not is_branch and rank is not None:
        if rank > 0:
            ws.write_number(r, COL_RANK, rank, fmts['rank'])
        else:
            ws.write_string(r, COL_RANK, '#VALUE!', fmts['rank'])


def _write_bo_area_row(ws, r, area_name, row_data, metric_map, fmts):
    """Write an area subtotal row in B+O section (summary layout)."""
    m = _extract_metrics(row_data, metric_map)
    fn = fmts['bo_area']
    fp = fmts['bo_area_pct']
    fn_name = fmts['bo_area_name']
    fs = fmts['bo_area_str']
    ws.write(r, COL_NAME, f'{area_name.upper()} AREA', fn_name)
    _write_val(ws, r, COL_OD_DEM, m['od_dem'], fn, fs)
    _write_val(ws, r, COL_OD_COL, m['od_col'], fn, fs)
    _write_pct(ws, r, COL_OD_PCT, m['od_col'], m['od_dem'], fp, fs)
    _write_common_cols(ws, r, m, fn, fp, fs,
                       COL_REG_DEM, COL_REG_COL, COL_REG_FTOD, COL_REG_PCT,
                       COL_130_DEM, COL_130_COL, COL_130_BAL, COL_130_PCT,
                       COL_3160_DEM, COL_3160_COL, COL_3160_BAL, COL_3160_PCT,
                       COL_PNPA_DEM, COL_PNPA_COL, COL_PNPA_BAL, COL_PNPA_PCT,
                       COL_NPA_DEM, COL_NPA_ACT_ACC, COL_NPA_ACT_AMT,
                       COL_NPA_CLO_ACC, COL_NPA_CLO_AMT, hourly_buckets=True)


def _write_branch_officer_expanded(ws, start_row, pc_idx, scope, product,
                                   report_date, fmts, title_label):
    """Write fully expanded Branch + Officer section (summary layout)."""
    r = start_row
    ws.merge_range(r, 0, r, LAST_COL,
                   f'BRANCH + OFFICER NAME WISE COLLECTION REPORT {title_label}'.strip(),
                   fmts['title_grey'])
    r += 1

    h2 = r
    ws.merge_range(h2, COL_NAME, h2 + 1, COL_NAME, 'BRANCH / OFFICER', fmts['name_hdr'])
    ws.merge_range(h2, COL_OD_DEM, h2, COL_OD_PCT, 'ON DATE Demand Vs Collection', fmts['hdr_orange'])
    ws.merge_range(h2, COL_REG_DEM, h2, COL_REG_PCT, 'REGULAR DEMAND VS COLLECTION', fmts['hdr_orange'])
    ws.merge_range(h2, COL_130_DEM, h2, COL_130_PCT, '1-30 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_3160_DEM, h2, COL_3160_PCT, '31-60 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_PNPA_DEM, h2, COL_PNPA_PCT, 'PNPA', fmts['hdr_orange'])
    ws.merge_range(h2, COL_190_DEM, h2, COL_190_PCT, '1-90 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, COL_NPA_DEM, h2, COL_NPA_CLO_AMT, 'NPA', fmts['hdr_orange'])
    ws.merge_range(h2, COL_RANK, h2, COL_PERF, 'METRICS', fmts['hdr_blue'])
    r += 1

    h3 = r
    for col, text, fmt_key in [
        (COL_OD_DEM, 'DEMAND', 'lhdr_orange'), (COL_OD_COL, 'COLLECTION', 'lhdr_green'),
        (COL_OD_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_REG_DEM, 'DEMAND', 'lhdr_orange'), (COL_REG_COL, 'COLLECTION', 'lhdr_green'),
        (COL_REG_FTOD, 'FTOD', 'lhdr_orange'), (COL_REG_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_130_DEM, 'DEMAND', 'lhdr_orange'), (COL_130_COL, 'COLLECTION', 'lhdr_green'),
        (COL_130_BAL, 'BALANCE', 'lhdr_orange'), (COL_130_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_3160_DEM, 'DEMAND', 'lhdr_orange'), (COL_3160_COL, 'COLLECTION', 'lhdr_green'),
        (COL_3160_BAL, 'BALANCE', 'lhdr_orange'), (COL_3160_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_PNPA_DEM, 'DEMAND', 'lhdr_orange'), (COL_PNPA_COL, 'COLLECTION', 'lhdr_green'),
        (COL_PNPA_BAL, 'BALANCE', 'lhdr_orange'), (COL_PNPA_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (COL_190_DEM, 'DEMAND', 'lhdr_orange'), (COL_190_COL, 'COLLECTION', 'lhdr_green'),
        (COL_190_BAL, 'BALANCE', 'lhdr_orange'), (COL_190_PCT, 'COLLECTION %', 'lhdr_yellow'),
    ]:
        ws.merge_range(h3, col, h3 + 1, col, text, fmts[fmt_key])

    ws.merge_range(h3, COL_NPA_DEM, h3 + 1, COL_NPA_DEM, 'DEMAND', fmts['lhdr_orange'])
    ws.merge_range(h3, COL_NPA_ACT_ACC, h3, COL_NPA_ACT_AMT, 'ACTIVATION', fmts['lhdr_orange'])
    ws.merge_range(h3, COL_NPA_CLO_ACC, h3, COL_NPA_CLO_AMT, 'CLOSURE', fmts['lhdr_orange'])
    ws.write(h3 + 1, COL_NPA_ACT_ACC, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_ACT_AMT, 'AMOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_CLO_ACC, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, COL_NPA_CLO_AMT, 'AMOUNT', fmts['lhdr_npa_green'])
    ws.merge_range(h3, COL_RANK, h3 + 1, COL_RANK, 'RANK', fmts['hdr_blue'])
    ws.merge_range(h3, COL_PERF, h3 + 1, COL_PERF, 'PERFORMANCE', fmts['hdr_blue'])
    r = h3 + 2

    flat_data = _query(pc_idx, 'All_Branch', 'ALL', scope, product)
    areas = _get_unique_filter_values(pc_idx, 'Area', scope, product)

    if areas:
        all_area_data = _query(pc_idx, 'All_Area', 'ALL', scope, product)
        for area in areas:
            _write_bo_area_row(ws, r, area, all_area_data.get(area, {}), COUNT_MAP, fmts)
            r += 1
            branch_data = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(k for k in branch_data if k != 'Grand Total'):
                _write_bo_row(ws, r, branch, branch_data[branch], COUNT_MAP, fmts, is_branch=True)
                r += 1
                officers = _query(pc_idx, 'BranchName', branch, scope, product)
                off_ranks = _compute_officer_ranks(officers)
                for emp_id in sorted(
                    (k for k in officers if k != 'Grand Total'),
                    key=lambda e: off_ranks.get(e, 0) if off_ranks.get(e, 0) > 0 else float('inf')
                ):
                    _write_bo_row(ws, r, emp_id, officers[emp_id], COUNT_MAP, fmts,
                                  is_branch=False, rank=off_ranks.get(emp_id))
                    r += 1
    else:
        for branch in sorted(k for k in flat_data if k != 'Grand Total'):
            _write_bo_row(ws, r, branch, flat_data[branch], COUNT_MAP, fmts, is_branch=True)
            r += 1
            officers = _query(pc_idx, 'BranchName', branch, scope, product)
            off_ranks = _compute_officer_ranks(officers)
            for emp_id in sorted(
                (k for k in officers if k != 'Grand Total'),
                key=lambda e: off_ranks.get(e, 0) if off_ranks.get(e, 0) > 0 else float('inf')
            ):
                _write_bo_row(ws, r, emp_id, officers[emp_id], COUNT_MAP, fmts,
                              is_branch=False, rank=off_ranks.get(emp_id))
                r += 1

    gt = flat_data.get('Grand Total')
    if gt is not None:
        _write_data_row(ws, r, 'Grand Total', gt, COUNT_MAP, fmts, 0, 0.0, is_grand_total=True)
        r += 1

    return r + 2


# ---------------------------------------------------------------------------
# Tomorrow On-Date helpers (simple Name + Demand tables for side-by-side views)
# ---------------------------------------------------------------------------

def _write_tom_ondate_section(ws, start_row, data, title, next_day_str, fmts, col_start):
    """Write a simple tomorrow on-date demand section (Name + Demand only)."""
    r = start_row
    items = sorted(k for k in data if k != 'Grand Total')
    if not items:
        return r
    grand_total = data.get('Grand Total')

    c_name = col_start
    c_dem = col_start + 1

    # Title row
    ws.merge_range(r, c_name, r, c_dem,
                   f'{title} - tom ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 1
    # Header row
    ws.write(r, c_name, f'{title} NAME', fmts['name_hdr'])
    ws.write(r, c_dem, 'DEMAND', fmts['lhdr_orange'])
    r += 1

    for name in items:
        od_next = _safe(data[name].get('od_demand_next', 0))
        ws.write(r, c_name, name, fmts['d_name'])
        _write_val(ws, r, c_dem, od_next, fmts['d_orange'], fmts['d_orange_str'])
        r += 1

    if grand_total is not None:
        od_next = _safe(grand_total.get('od_demand_next', 0))
        ws.write(r, c_name, 'Grand Total', fmts['gt_name'])
        _write_val(ws, r, c_dem, od_next, fmts['gt'], fmts['gt_str'])
        r += 1

    return r + 3


def _write_tom_ondate_bo_section(ws, start_row, pc_idx, scope, product,
                                  next_day_str, fmts, col_start,
                                  filter_type='All_Branch', filter_value='ALL'):
    """Write tomorrow on-date B+O section (branch bold blue, officer indented)."""
    r = start_row
    c_name = col_start
    c_dem = col_start + 1

    ws.merge_range(r, c_name, r, c_dem,
                   f'B+O - tom ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 1
    ws.write(r, c_name, 'BRANCH / OFFICER', fmts['name_hdr'])
    ws.write(r, c_dem, 'DEMAND', fmts['lhdr_orange'])
    r += 1

    flat_data = _query(pc_idx, filter_type, filter_value, scope, product)

    # Determine area grouping source
    if filter_type == 'All_Branch':
        areas = _get_unique_filter_values(pc_idx, 'Area', scope, product)
        area_totals_ft, area_totals_fv = 'All_Area', 'ALL'
    elif filter_type == 'Region_Branch':
        region_area = _query(pc_idx, 'Region_Area', filter_value, scope, product)
        areas = sorted(k for k in region_area if k != 'Grand Total')
        area_totals_ft, area_totals_fv = 'Region_Area', filter_value
    elif filter_type == 'Division_Branch':
        div_area = _query(pc_idx, 'Division_Area', filter_value, scope, product)
        areas = sorted(k for k in div_area if k != 'Grand Total')
        area_totals_ft, area_totals_fv = 'Division_Area', filter_value
    else:
        areas = []
        area_totals_ft, area_totals_fv = None, None

    if areas:
        all_area_data = _query(pc_idx, area_totals_ft, area_totals_fv, scope, product)
        for area in areas:
            od_area = _safe(all_area_data.get(area, {}).get('od_demand_next', 0))
            ws.write(r, c_name, f'{area.upper()} AREA', fmts['bo_area_name'])
            _write_val(ws, r, c_dem, od_area, fmts['bo_area'], fmts['bo_area_str'])
            r += 1
            branch_data = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(k for k in branch_data if k != 'Grand Total'):
                od_next = _safe(branch_data[branch].get('od_demand_next', 0))
                ws.write(r, c_name, branch, fmts['bo_branch_name'])
                _write_val(ws, r, c_dem, od_next, fmts['bo_branch'], fmts['bo_branch_str'])
                r += 1
                officers = _query(pc_idx, 'BranchName', branch, scope, product)
                for emp_id in sorted(k for k in officers if k != 'Grand Total'):
                    officer_name = str(officers[emp_id].get('officer_name', ''))
                    display_name = f'{emp_id} - {officer_name}' if officer_name else emp_id
                    od_next_o = _safe(officers[emp_id].get('od_demand_next', 0))
                    ws.write(r, c_name, display_name, fmts['bo_officer_name'])
                    _write_val(ws, r, c_dem, od_next_o, fmts['bo_officer'], fmts['bo_officer_str'])
                    r += 1
    else:
        for branch in sorted(k for k in flat_data if k != 'Grand Total'):
            od_next = _safe(flat_data[branch].get('od_demand_next', 0))
            ws.write(r, c_name, branch, fmts['bo_branch_name'])
            _write_val(ws, r, c_dem, od_next, fmts['bo_branch'], fmts['bo_branch_str'])
            r += 1
            officers = _query(pc_idx, 'BranchName', branch, scope, product)
            for emp_id in sorted(k for k in officers if k != 'Grand Total'):
                officer_name = str(officers[emp_id].get('officer_name', ''))
                display_name = f'{emp_id} - {officer_name}' if officer_name else emp_id
                od_next_o = _safe(officers[emp_id].get('od_demand_next', 0))
                ws.write(r, c_name, display_name, fmts['bo_officer_name'])
                _write_val(ws, r, c_dem, od_next_o, fmts['bo_officer'], fmts['bo_officer_str'])
                r += 1

    gt = flat_data.get('Grand Total')
    if gt is not None:
        od_next = _safe(gt.get('od_demand_next', 0))
        ws.write(r, c_name, 'Grand Total', fmts['gt_name'])
        _write_val(ws, r, c_dem, od_next, fmts['gt'], fmts['gt_str'])
        r += 1

    return r + 2


# ============================================================================
# ENTITY LAYOUT (region / division / area / branch sheets)
# ============================================================================

def _setup_entity_worksheet(ws):
    """Common entity worksheet setup - covers all 4 side-by-side views."""
    ws.set_column(0, 0, 2)
    # View 1: OverAll (cols 1-30)
    ws.set_column(E_NAME, E_NAME, 22)
    ws.set_column(E_OD_DEM, E_NPA_CLO_AMT, 12)
    ws.set_column(E_RANK, E_RANK, 8)
    # Gap cols 31-32
    ws.set_column(31, 32, 2)
    # View 2: tom_OnDate (cols 33-34)
    ws.set_column(33, 33, 22)  # name
    ws.set_column(34, 34, 12)  # demand
    # Gap cols 35-36 (unused, but set narrow)
    ws.set_column(35, 36, 2)
    # Gap col 37
    ws.set_column(37, 37, 2)
    # View 3: FY (cols 39-68 = E_NAME+38 to E_RANK+38)
    ws.set_column(E_NAME + 38, E_NAME + 38, 22)
    ws.set_column(E_OD_DEM + 38, E_NPA_CLO_AMT + 38, 12)
    ws.set_column(E_RANK + 38, E_RANK + 38, 8)
    # Gap cols 69-70
    ws.set_column(69, 70, 2)
    # View 4: FY tom_OnDate (cols 71-72)
    ws.set_column(71, 71, 22)  # name
    ws.set_column(72, 72, 12)  # demand
    ws.hide_gridlines(2)


def _write_entity_headers(ws, r, entity_label, fmts, col_offset=0):
    """Write 3-row header block for entity sheets (with ON DATE + RANK only)."""
    co = col_offset
    h2 = r
    ws.merge_range(h2, E_NAME + co, h2 + 1, E_NAME + co, f'{entity_label} NAME', fmts['name_hdr'])
    ws.merge_range(h2, E_OD_DEM + co, h2, E_OD_PCT + co, 'ON DATE Demand Vs Collection', fmts['hdr_orange'])
    ws.merge_range(h2, E_REG_DEM + co, h2, E_REG_PCT + co, 'REGULAR DEMAND VS COLLECTION', fmts['hdr_orange'])
    ws.merge_range(h2, E_130_DEM + co, h2, E_130_PCT + co, '1-30 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, E_3160_DEM + co, h2, E_3160_PCT + co, '31-60 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, E_PNPA_DEM + co, h2, E_PNPA_PCT + co, 'PNPA', fmts['hdr_orange'])
    ws.merge_range(h2, E_190_DEM + co, h2, E_190_PCT + co, '1-90 DPD', fmts['hdr_orange'])
    ws.merge_range(h2, E_NPA_DEM + co, h2, E_NPA_CLO_AMT + co, 'NPA', fmts['hdr_orange'])
    ws.write(h2, E_RANK + co, 'METRICS', fmts['hdr_blue'])
    r += 1

    h3 = r
    for col, text, fmt_key in [
        (E_OD_DEM, 'DEMAND', 'lhdr_orange'), (E_OD_COL, 'COLLECTION', 'lhdr_green'),
        (E_OD_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (E_REG_DEM, 'DEMAND', 'lhdr_orange'), (E_REG_COL, 'COLLECTION', 'lhdr_green'),
        (E_REG_FTOD, 'FTOD', 'lhdr_orange'), (E_REG_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (E_130_DEM, 'DEMAND', 'lhdr_orange'), (E_130_COL, 'COLLECTION', 'lhdr_green'),
        (E_130_BAL, 'BALANCE', 'lhdr_orange'), (E_130_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (E_3160_DEM, 'DEMAND', 'lhdr_orange'), (E_3160_COL, 'COLLECTION', 'lhdr_green'),
        (E_3160_BAL, 'BALANCE', 'lhdr_orange'), (E_3160_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (E_PNPA_DEM, 'DEMAND', 'lhdr_orange'), (E_PNPA_COL, 'COLLECTION', 'lhdr_green'),
        (E_PNPA_BAL, 'BALANCE', 'lhdr_orange'), (E_PNPA_PCT, 'COLLECTION %', 'lhdr_yellow'),
        (E_190_DEM, 'DEMAND', 'lhdr_orange'), (E_190_COL, 'COLLECTION', 'lhdr_green'),
        (E_190_BAL, 'BALANCE', 'lhdr_orange'), (E_190_PCT, 'COLLECTION %', 'lhdr_yellow'),
    ]:
        ws.merge_range(h3, col + co, h3 + 1, col + co, text, fmts[fmt_key])

    ws.merge_range(h3, E_NPA_DEM + co, h3 + 1, E_NPA_DEM + co, 'DEMAND', fmts['lhdr_orange'])
    ws.merge_range(h3, E_NPA_ACT_ACC + co, h3, E_NPA_ACT_AMT + co, 'ACTIVATION', fmts['lhdr_orange'])
    ws.merge_range(h3, E_NPA_CLO_ACC + co, h3, E_NPA_CLO_AMT + co, 'CLOSURE', fmts['lhdr_orange'])
    ws.write(h3 + 1, E_NPA_ACT_ACC + co, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, E_NPA_ACT_AMT + co, 'AMOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, E_NPA_CLO_ACC + co, 'ACCOUNT', fmts['lhdr_npa_green'])
    ws.write(h3 + 1, E_NPA_CLO_AMT + co, 'AMOUNT', fmts['lhdr_npa_green'])

    ws.merge_range(h3, E_RANK + co, h3 + 1, E_RANK + co, 'RANK', fmts['hdr_blue'])

    return h3 + 2


def _write_entity_data_row(ws, r, name, row_data, metric_map, fmts,
                           rank, is_grand_total=False, col_offset=0):
    """Write a data row in entity layout (with ON DATE, RANK only)."""
    co = col_offset
    m = _extract_metrics(row_data, metric_map)

    if is_grand_total:
        fn, fp, fs = fmts['gt'], fmts['gt_pct'], fmts['gt_str']
        ws.write(r, E_NAME + co, name, fmts['gt_name'])
        # ON DATE
        _write_val(ws, r, E_OD_DEM + co, m['od_dem'], fn, fs)
        _write_val(ws, r, E_OD_COL + co, m['od_col'], fn, fs)
        _write_pct(ws, r, E_OD_PCT + co, m['od_col'], m['od_dem'], fp, fs)
        # Common cols
        _write_common_cols(ws, r, m, fn, fp, fs,
                           E_REG_DEM + co, E_REG_COL + co, E_REG_FTOD + co, E_REG_PCT + co,
                           E_130_DEM + co, E_130_COL + co, E_130_BAL + co, E_130_PCT + co,
                           E_3160_DEM + co, E_3160_COL + co, E_3160_BAL + co, E_3160_PCT + co,
                           E_PNPA_DEM + co, E_PNPA_COL + co, E_PNPA_BAL + co, E_PNPA_PCT + co,
                           E_NPA_DEM + co, E_NPA_ACT_ACC + co, E_NPA_ACT_AMT + co,
                           E_NPA_CLO_ACC + co, E_NPA_CLO_AMT + co)
        ws.write_string(r, E_RANK + co, '-', fs)
    else:
        fn_o, fn_g = fmts['d_orange'], fmts['d_green']
        fp_y = fmts['d_yellow_pct']
        fs_o, fs_g, fs_y = fmts['d_orange_str'], fmts['d_green_str'], fmts['d_yellow_str']

        ws.write(r, E_NAME + co, name, fmts['d_name'])
        # ON DATE
        _write_val(ws, r, E_OD_DEM + co, m['od_dem'], fn_o, fs_o)
        _write_val(ws, r, E_OD_COL + co, m['od_col'], fn_g, fs_g)
        _write_pct(ws, r, E_OD_PCT + co, m['od_col'], m['od_dem'], fp_y, fs_y)
        # Common cols
        _write_common_cols(ws, r, m, fn_o, fp_y, fs_o,
                           E_REG_DEM + co, E_REG_COL + co, E_REG_FTOD + co, E_REG_PCT + co,
                           E_130_DEM + co, E_130_COL + co, E_130_BAL + co, E_130_PCT + co,
                           E_3160_DEM + co, E_3160_COL + co, E_3160_BAL + co, E_3160_PCT + co,
                           E_PNPA_DEM + co, E_PNPA_COL + co, E_PNPA_BAL + co, E_PNPA_PCT + co,
                           E_NPA_DEM + co, E_NPA_ACT_ACC + co, E_NPA_ACT_AMT + co,
                           E_NPA_CLO_ACC + co, E_NPA_CLO_AMT + co,
                           fn_g=fn_g, fs_g=fs_g, fp_str=fs_y)
        # RANK
        if m['reg_dem'] == 0:
            ws.write_string(r, E_RANK + co, '#VALUE!', fmts['rank'])
        else:
            ws.write_number(r, E_RANK + co, rank, fmts['rank'])


def _write_entity_bo_row(ws, r, name, row_data, metric_map, fmts, is_branch=True, col_offset=0, rank=None):
    """Write a Branch or Officer row in B+O section (entity layout)."""
    co = col_offset
    m = _extract_metrics(row_data, metric_map)

    fn = fmts['bo_branch'] if is_branch else fmts['bo_officer']
    fp = fmts['bo_branch_pct'] if is_branch else fmts['bo_officer_pct']
    fn_name = fmts['bo_branch_name'] if is_branch else fmts['bo_officer_name']
    fs = fmts['bo_branch_str'] if is_branch else fmts['bo_officer_str']

    display_name = name
    if not is_branch:
        officer_name = str(row_data.get('officer_name', ''))
        if officer_name:
            display_name = f'{name} - {officer_name}'

    ws.write(r, E_NAME + co, display_name, fn_name)
    # ON DATE
    _write_val(ws, r, E_OD_DEM + co, m['od_dem'], fn, fs)
    _write_val(ws, r, E_OD_COL + co, m['od_col'], fn, fs)
    _write_pct(ws, r, E_OD_PCT + co, m['od_col'], m['od_dem'], fp, fs)
    # Common cols
    _write_common_cols(ws, r, m, fn, fp, fs,
                       E_REG_DEM + co, E_REG_COL + co, E_REG_FTOD + co, E_REG_PCT + co,
                       E_130_DEM + co, E_130_COL + co, E_130_BAL + co, E_130_PCT + co,
                       E_3160_DEM + co, E_3160_COL + co, E_3160_BAL + co, E_3160_PCT + co,
                       E_PNPA_DEM + co, E_PNPA_COL + co, E_PNPA_BAL + co, E_PNPA_PCT + co,
                       E_NPA_DEM + co, E_NPA_ACT_ACC + co, E_NPA_ACT_AMT + co,
                       E_NPA_CLO_ACC + co, E_NPA_CLO_AMT + co)
    if not is_branch and rank is not None:
        if rank > 0:
            ws.write_number(r, E_RANK + co, rank, fmts['rank'])
        else:
            ws.write_string(r, E_RANK + co, '#VALUE!', fmts['rank'])


def _write_entity_bo_area_row(ws, r, area_name, row_data, metric_map, fmts, col_offset=0):
    """Write an area subtotal row in B+O section (entity layout)."""
    co = col_offset
    m = _extract_metrics(row_data, metric_map)
    fn = fmts['bo_area']
    fp = fmts['bo_area_pct']
    fn_name = fmts['bo_area_name']
    fs = fmts['bo_area_str']
    ws.write(r, E_NAME + co, f'{area_name.upper()} AREA', fn_name)
    _write_val(ws, r, E_OD_DEM + co, m['od_dem'], fn, fs)
    _write_val(ws, r, E_OD_COL + co, m['od_col'], fn, fs)
    _write_pct(ws, r, E_OD_PCT + co, m['od_col'], m['od_dem'], fp, fs)
    _write_common_cols(ws, r, m, fn, fp, fs,
                       E_REG_DEM + co, E_REG_COL + co, E_REG_FTOD + co, E_REG_PCT + co,
                       E_130_DEM + co, E_130_COL + co, E_130_BAL + co, E_130_PCT + co,
                       E_3160_DEM + co, E_3160_COL + co, E_3160_BAL + co, E_3160_PCT + co,
                       E_PNPA_DEM + co, E_PNPA_COL + co, E_PNPA_BAL + co, E_PNPA_PCT + co,
                       E_NPA_DEM + co, E_NPA_ACT_ACC + co, E_NPA_ACT_AMT + co,
                       E_NPA_CLO_ACC + co, E_NPA_CLO_AMT + co)


def _write_entity_branch_officer_expanded(ws, start_row, pc_idx, scope, product,
                                          report_date, fmts, title_label,
                                          filter_type='All_Branch', filter_value='ALL',
                                          col_offset=0):
    """Write fully expanded Branch + Officer section (entity layout with ON DATE)."""
    co = col_offset
    r = start_row
    ws.merge_range(r, E_NAME + co, r, E_LAST + co,
                   f'BRANCH + OFFICER NAME WISE COLLECTION REPORT {title_label}'.strip(),
                   fmts['title_grey'])
    r += 1

    r = _write_entity_headers(ws, r, 'BRANCH / OFFICER', fmts, col_offset=co)

    flat_data = _query(pc_idx, filter_type, filter_value, scope, product)

    # Determine area grouping source
    if filter_type == 'All_Branch':
        areas = _get_unique_filter_values(pc_idx, 'Area', scope, product)
        area_totals_ft, area_totals_fv = 'All_Area', 'ALL'
    elif filter_type == 'Region_Branch':
        region_area = _query(pc_idx, 'Region_Area', filter_value, scope, product)
        areas = sorted(k for k in region_area if k != 'Grand Total')
        area_totals_ft, area_totals_fv = 'Region_Area', filter_value
    elif filter_type == 'Division_Branch':
        div_area = _query(pc_idx, 'Division_Area', filter_value, scope, product)
        areas = sorted(k for k in div_area if k != 'Grand Total')
        area_totals_ft, area_totals_fv = 'Division_Area', filter_value
    else:
        areas = []
        area_totals_ft, area_totals_fv = None, None

    if areas:
        all_area_data = _query(pc_idx, area_totals_ft, area_totals_fv, scope, product)
        for area in areas:
            _write_entity_bo_area_row(ws, r, area, all_area_data.get(area, {}),
                                      COUNT_MAP, fmts, col_offset=co)
            r += 1
            branch_data = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(k for k in branch_data if k != 'Grand Total'):
                _write_entity_bo_row(ws, r, branch, branch_data[branch], COUNT_MAP, fmts,
                                     is_branch=True, col_offset=co)
                r += 1
                officers = _query(pc_idx, 'BranchName', branch, scope, product)
                off_ranks = _compute_officer_ranks(officers)
                for emp_id in sorted(
                    (k for k in officers if k != 'Grand Total'),
                    key=lambda e: off_ranks.get(e, 0) if off_ranks.get(e, 0) > 0 else float('inf')
                ):
                    _write_entity_bo_row(ws, r, emp_id, officers[emp_id], COUNT_MAP, fmts,
                                         is_branch=False, col_offset=co, rank=off_ranks.get(emp_id))
                    r += 1
    else:
        for branch in sorted(k for k in flat_data if k != 'Grand Total'):
            _write_entity_bo_row(ws, r, branch, flat_data[branch], COUNT_MAP, fmts,
                                 is_branch=True, col_offset=co)
            r += 1
            officers = _query(pc_idx, 'BranchName', branch, scope, product)
            off_ranks = _compute_officer_ranks(officers)
            for emp_id in sorted(
                (k for k in officers if k != 'Grand Total'),
                key=lambda e: off_ranks.get(e, 0) if off_ranks.get(e, 0) > 0 else float('inf')
            ):
                _write_entity_bo_row(ws, r, emp_id, officers[emp_id], COUNT_MAP, fmts,
                                     is_branch=False, col_offset=co, rank=off_ranks.get(emp_id))
                r += 1

    gt = flat_data.get('Grand Total')
    if gt is not None:
        _write_entity_data_row(ws, r, 'Grand Total', gt, COUNT_MAP, fmts,
                               0, is_grand_total=True, col_offset=co)
        r += 1

    return r + 2


def _write_entity_section(ws, start_row, data, metric_map, title, report_date,
                          fmts, col_offset=0):
    """Write a section in entity layout (with ON DATE, RANK only)."""
    co = col_offset
    r = start_row
    items = sorted(k for k in data if k != 'Grand Total')
    if not items:
        return r

    grand_total = data.get('Grand Total')

    label = f'{title} - WISE COLLECTION REPORT - as on {report_date} (OverAll)'
    ws.merge_range(r, E_NAME + co, r, E_LAST + co, label, fmts['title'])
    r += 1

    r = _write_entity_headers(ws, r, title, fmts, col_offset=co)

    # Ranking by ON DATE collection % (descending)
    pct_values = []
    for name in items:
        row_data = data[name]
        od_dem = _safe(row_data.get('od_demand', 0))
        od_col = _safe(row_data.get('od_collection', 0))
        pct = _pct(od_col, od_dem)
        pct_values.append((name, pct))

    sorted_by_pct = sorted(pct_values, key=lambda x: -x[1])
    rank_map = {name: idx for idx, (name, _) in enumerate(sorted_by_pct, 1)}
    items_sorted = [name for name, _ in sorted_by_pct]

    for name in items_sorted:
        _write_entity_data_row(ws, r, name, data[name], metric_map, fmts,
                               rank_map.get(name, 0), col_offset=co)
        r += 1

    if grand_total is not None:
        _write_entity_data_row(ws, r, 'Grand Total', grand_total, metric_map, fmts,
                               0, is_grand_total=True, col_offset=co)
        r += 1

    return r + 3  # 3 blank rows


# ============================================================================
# Sheet builders
# ============================================================================

def _setup_worksheet(ws):
    """Common summary worksheet setup."""
    ws.set_column(0, 0, 2)
    ws.set_column(COL_NAME, COL_NAME, 22)
    ws.set_column(COL_OD_DEM, COL_NPA_CLO_AMT, 12)
    ws.set_column(COL_RANK, COL_RANK, 8)
    ws.set_column(COL_PERF, COL_PERF, 16)
    ws.hide_gridlines(2)


def _build_summary_sheet(wb, pc_idx, sheet_name, scope, report_date, fmts,
                         has_officer_col, product='ALL'):
    """Build a summary sheet in the OverAll layout.

    product='ALL'  -> the OverAll / FY sheet (all products + per-product
                      REGION-WISE breakdowns).
    product='VVY'  -> a single-product sheet (e.g. 'IL Reports'): every
                      section filtered to that product, no extra breakdowns.
    """
    ws = wb.add_worksheet(sheet_name)
    _setup_worksheet(ws)

    row = 1

    data = _query(pc_idx, 'All_Region', 'ALL', scope, product)
    row = _write_section(ws, row, data, COUNT_MAP, 'REGION', report_date,
                         fmts, f'({sheet_name})')

    data = _query(pc_idx, 'All_Division', 'ALL', scope, product)
    row = _write_section(ws, row, data, COUNT_MAP, 'DIVISION', report_date,
                         fmts, f'({sheet_name})')

    data = _query(pc_idx, 'All_Area', 'ALL', scope, product)
    row = _write_section(ws, row, data, COUNT_MAP, 'AREA', report_date,
                         fmts, f'({sheet_name})')

    row = _write_branch_section_grouped(ws, row, pc_idx, scope, product,
                                        report_date, fmts, f'({sheet_name})')

    # Per-product REGION-WISE breakdowns — only on the all-products sheet
    if product == 'ALL':
        for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
            row += 2
            ws.merge_range(row, 1, row, LAST_COL,
                           f'REGION WISE - {display} REPORT', fmts['title_grey'])
            row += 2
            data = _query(pc_idx, 'All_Region', 'ALL', scope, prod)
            row = _write_section(ws, row, data, COUNT_MAP, 'REGION', report_date,
                                 fmts, f'({sheet_name})')

    if has_officer_col:
        row += 2
        row = _write_branch_officer_expanded(
            ws, row, pc_idx, scope, product, report_date, fmts, f'({sheet_name})')

        if product == 'ALL':
            for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
                row += 2
                row = _write_branch_officer_expanded(
                    ws, row, pc_idx, scope, prod, report_date, fmts,
                    f'({sheet_name} - {display})')


def _build_ondate_sheet(wb, pc_idx, report_date, next_day_str, fmts, scope='OA', prefix='OverAll'):
    """On-Date sheet — EOD/report-date on-date on the LEFT and TOMORROW
    (next_day_str) on the RIGHT, two blocks side by side (no VS / difference).

    Both sides use the exact same column structure and styling; only the source
    metric differs — od_demand/od_collection (left = the EOD/report date) vs
    od_demand_next/od_collection_next (right = tomorrow). DATA logic unchanged.
    """
    ws2 = wb.add_worksheet(f'tom_{prefix}_On-Date')
    # Left block cols 1-4, narrow gap col 5, right block cols 6-9.
    ws2.set_column(0, 0, 2)
    ws2.set_column(1, 1, 22)
    ws2.set_column(2, 4, 14)
    ws2.set_column(5, 5, 3)
    ws2.set_column(6, 6, 22)
    ws2.set_column(7, 9, 14)
    ws2.hide_gridlines(2)

    LEFT, RIGHT = 1, 6

    ws2.write(0, 0,
              f'On-Date Demand Report - {report_date} & {next_day_str}',
              fmts['title'])

    row = 2
    for filter_type, title in [('All_Region', 'REGION'), ('All_Division', 'DIVISION'),
                                ('All_Area', 'AREA'), ('All_Branch', 'BRANCH')]:
        data = _query(pc_idx, filter_type, 'ALL', scope, 'ALL')
        items = sorted(k for k in data if k != 'Grand Total')
        if not items:
            continue

        # Side titles (no VS): left = EOD/report date, right = tomorrow.
        ws2.merge_range(row, LEFT, row, LEFT + 3,
                        f'{title} ON-DATE DEMAND - {report_date}', fmts['title'])
        ws2.merge_range(row, RIGHT, row, RIGHT + 3,
                        f'{title} ON-DATE DEMAND - {next_day_str}', fmts['title'])
        row += 1
        # Column headers — identical on both sides.
        for base in (LEFT, RIGHT):
            ws2.write(row, base, f'{title} NAME', fmts['name_hdr'])
            ws2.write(row, base + 1, 'DEMAND', fmts['lhdr_orange'])
            ws2.write(row, base + 2, 'COLLECTION', fmts['lhdr_green'])
            ws2.write(row, base + 3, 'COLLECTION %', fmts['lhdr_yellow'])
        row += 1

        for name in items:
            m = data[name]
            l_d = _safe(m.get('od_demand', 0))
            l_c = _safe(m.get('od_collection', 0))
            r_d = _safe(m.get('od_demand_next', 0))
            r_c = _safe(m.get('od_collection_next', 0))
            # Left: EOD/report date
            ws2.write(row, LEFT, name, fmts['d_name'])
            _write_val(ws2, row, LEFT + 1, l_d, fmts['d_orange'], fmts['d_orange_str'])
            _write_val(ws2, row, LEFT + 2, l_c, fmts['d_green'], fmts['d_green_str'])
            _write_pct(ws2, row, LEFT + 3, l_c, l_d, fmts['d_yellow_pct'], fmts['d_yellow_str'])
            # Right: TOMORROW
            ws2.write(row, RIGHT, name, fmts['d_name'])
            _write_val(ws2, row, RIGHT + 1, r_d, fmts['d_orange'], fmts['d_orange_str'])
            _write_val(ws2, row, RIGHT + 2, r_c, fmts['d_green'], fmts['d_green_str'])
            _write_pct(ws2, row, RIGHT + 3, r_c, r_d, fmts['d_yellow_pct'], fmts['d_yellow_str'])
            row += 1

        gt = data.get('Grand Total')
        if gt is not None:
            l_d = _safe(gt.get('od_demand', 0))
            l_c = _safe(gt.get('od_collection', 0))
            r_d = _safe(gt.get('od_demand_next', 0))
            r_c = _safe(gt.get('od_collection_next', 0))
            ws2.write(row, LEFT, 'Grand Total', fmts['gt_name'])
            _write_val(ws2, row, LEFT + 1, l_d, fmts['gt'], fmts['gt_str'])
            _write_val(ws2, row, LEFT + 2, l_c, fmts['gt'], fmts['gt_str'])
            _write_pct(ws2, row, LEFT + 3, l_c, l_d, fmts['gt_pct'], fmts['gt_str'])
            ws2.write(row, RIGHT, 'Grand Total', fmts['gt_name'])
            _write_val(ws2, row, RIGHT + 1, r_d, fmts['gt'], fmts['gt_str'])
            _write_val(ws2, row, RIGHT + 2, r_c, fmts['gt'], fmts['gt_str'])
            _write_pct(ws2, row, RIGHT + 3, r_c, r_d, fmts['gt_pct'], fmts['gt_str'])
            row += 1

        row += 2


# ---------------------------------------------------------------------------
# Per-entity sheet builders (entity layout)
# ---------------------------------------------------------------------------

def _write_entity_branch_section_grouped(ws, start_row, pc_idx, filter_type_branch,
                                          filter_value, scope, product,
                                          report_date, fmts, col_offset=0,
                                          area_ft=None, area_fv=None):
    """Write BRANCH section grouped by area (entity layout).

    Parameters
    ----------
    filter_type_branch : str
        The filter_type for branch data (e.g. 'Region_Branch', 'Division_Branch').
    filter_value : str
        The filter_value for branch data (e.g. region name, division name).
    area_ft, area_fv : str
        Filter type/value for area subtotals (e.g. 'Region_Area'/region or 'Division_Area'/division).
    """
    co = col_offset
    r = start_row

    branch_data_all = _query(pc_idx, filter_type_branch, filter_value, scope, product)
    items = [k for k in branch_data_all if k != 'Grand Total']
    if not items:
        return r

    grand_total = branch_data_all.get('Grand Total')

    label = f'BRANCH - WISE COLLECTION REPORT - as on {report_date} (OverAll)'
    ws.merge_range(r, E_NAME + co, r, E_LAST + co, label, fmts['title'])
    r += 1
    r = _write_entity_headers(ws, r, 'BRANCH', fmts, col_offset=co)

    # Global rank by OD collection %
    pct_values = [
        (name, _pct(_safe(branch_data_all[name].get('od_collection', 0)),
                    _safe(branch_data_all[name].get('od_demand', 0))))
        for name in items
    ]
    rank_map = {name: idx for idx, (name, _) in enumerate(sorted(pct_values, key=lambda x: -x[1]), 1)}

    area_data = _query(pc_idx, area_ft, area_fv, scope, product) if area_ft else {}
    area_names = sorted(k for k in area_data if k != 'Grand Total') if area_data else []

    if area_names:
        fn, fp, fs = fmts['bo_area'], fmts['bo_area_pct'], fmts['bo_area_str']
        for area in area_names:
            m = _extract_metrics(area_data.get(area, {}), COUNT_MAP)
            ws.write(r, E_NAME + co, f'{area.upper()} AREA', fmts['bo_area_name'])
            _write_val(ws, r, E_OD_DEM + co, m['od_dem'], fn, fs)
            _write_val(ws, r, E_OD_COL + co, m['od_col'], fn, fs)
            _write_pct(ws, r, E_OD_PCT + co, m['od_col'], m['od_dem'], fp, fs)
            _write_common_cols(ws, r, m, fn, fp, fs,
                               E_REG_DEM + co, E_REG_COL + co, E_REG_FTOD + co, E_REG_PCT + co,
                               E_130_DEM + co, E_130_COL + co, E_130_BAL + co, E_130_PCT + co,
                               E_3160_DEM + co, E_3160_COL + co, E_3160_BAL + co, E_3160_PCT + co,
                               E_PNPA_DEM + co, E_PNPA_COL + co, E_PNPA_BAL + co, E_PNPA_PCT + co,
                               E_NPA_DEM + co, E_NPA_ACT_ACC + co, E_NPA_ACT_AMT + co,
                               E_NPA_CLO_ACC + co, E_NPA_CLO_AMT + co)
            ws.write_string(r, E_RANK + co, '-', fs)
            r += 1
            area_branches = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(
                (k for k in area_branches if k != 'Grand Total' and k in branch_data_all),
                key=lambda b: rank_map.get(b, float('inf'))
            ):
                _write_entity_data_row(ws, r, branch, branch_data_all[branch], COUNT_MAP, fmts,
                                       rank_map.get(branch, 0), col_offset=co)
                r += 1
    else:
        items_sorted = [name for name, _ in sorted(pct_values, key=lambda x: -x[1])]
        for name in items_sorted:
            _write_entity_data_row(ws, r, name, branch_data_all[name], COUNT_MAP, fmts,
                                   rank_map.get(name, 0), col_offset=co)
            r += 1

    if grand_total is not None:
        _write_entity_data_row(ws, r, 'Grand Total', grand_total, COUNT_MAP, fmts,
                               0, is_grand_total=True, col_offset=co)
        r += 1

    return r + 3


def _write_tom_ondate_branch_section_grouped(ws, start_row, pc_idx,
                                              filter_type_branch, filter_value,
                                              scope, product,
                                              next_day_str, fmts, col_start,
                                              area_ft=None, area_fv=None):
    """Write tomorrow on-date BRANCH section grouped by area."""
    r = start_row
    c_name = col_start
    c_dem = col_start + 1

    branch_data_all = _query(pc_idx, filter_type_branch, filter_value, scope, product)
    items = [k for k in branch_data_all if k != 'Grand Total']
    if not items:
        return r

    grand_total = branch_data_all.get('Grand Total')

    ws.merge_range(r, c_name, r, c_dem,
                   f'BRANCH - TOMORROW ON-DATE DEMAND - {next_day_str}', fmts['title'])
    r += 1
    ws.write(r, c_name, 'BRANCH NAME', fmts['name_hdr'])
    ws.write(r, c_dem, 'DEMAND', fmts['lhdr_orange'])
    r += 1

    area_data = _query(pc_idx, area_ft, area_fv, scope, product) if area_ft else {}
    area_names = sorted(k for k in area_data if k != 'Grand Total') if area_data else []

    if area_names:
        for area in area_names:
            od_area = _safe(area_data.get(area, {}).get('od_demand_next', 0))
            ws.write(r, c_name, f'{area.upper()} AREA', fmts['bo_area_name'])
            _write_val(ws, r, c_dem, od_area, fmts['bo_area'], fmts['bo_area_str'])
            r += 1
            area_branches = _query(pc_idx, 'Area', area, scope, product)
            for branch in sorted(k for k in area_branches if k != 'Grand Total' and k in branch_data_all):
                od_next = _safe(branch_data_all[branch].get('od_demand_next', 0))
                ws.write(r, c_name, branch, fmts['bo_branch_name'])
                _write_val(ws, r, c_dem, od_next, fmts['bo_branch'], fmts['bo_branch_str'])
                r += 1
    else:
        for branch in sorted(items):
            od_next = _safe(branch_data_all[branch].get('od_demand_next', 0))
            ws.write(r, c_name, branch, fmts['bo_branch_name'])
            _write_val(ws, r, c_dem, od_next, fmts['bo_branch'], fmts['bo_branch_str'])
            r += 1

    if grand_total is not None:
        od_next = _safe(grand_total.get('od_demand_next', 0))
        ws.write(r, c_name, 'Grand Total', fmts['gt_name'])
        _write_val(ws, r, c_dem, od_next, fmts['gt'], fmts['gt_str'])
        r += 1

    return r + 3


def _build_region_sheet(wb, pc_idx, region, report_date, fmts,
                        has_officer_col=True, next_day_str='', fy_label='FY'):
    """Build a per-region sheet with 4 side-by-side views, row-aligned."""
    safe_name = f'region_{region}'[:31]
    ws = wb.add_worksheet(safe_name)
    _setup_entity_worksheet(ws)

    tom_col = 33
    fy_offset = 38
    fy_tom_col = 71

    # Title row (row 1) — all 4 views
    r = 1
    ws.merge_range(r, E_NAME, r, E_LAST,
                   f'{region} - COLLECTION REPORT - {report_date}', fmts['title'])
    ws.merge_range(r, tom_col, r, tom_col + 1,
                   f'{region} - ON-DATE - {next_day_str}', fmts['title_grey'])
    ws.merge_range(r, E_NAME + fy_offset, r, E_LAST + fy_offset,
                   f'{region} - FY REPORT - {report_date}', fmts['title_grey'])
    ws.merge_range(r, fy_tom_col, r, fy_tom_col + 1,
                   f'{region} - FY ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 2

    # --- Division section ---
    sr = r
    data_oa = _query(pc_idx, 'Region_Division', region, 'OA', 'ALL')
    data_fy = _query(pc_idx, 'Region_Division', region, 'FY', 'ALL')
    r1 = _write_entity_section(ws, sr, data_oa, COUNT_MAP, 'DIVISION', report_date, fmts) if data_oa else sr
    r2 = _write_tom_ondate_section(ws, sr, data_oa, 'DIVISION', next_day_str, fmts, tom_col) if data_oa else sr
    r3 = _write_entity_section(ws, sr, data_fy, COUNT_MAP, 'DIVISION', report_date, fmts, col_offset=fy_offset) if data_fy else sr
    r4 = _write_tom_ondate_section(ws, sr, data_fy, 'DIVISION', next_day_str, fmts, fy_tom_col) if data_fy else sr
    r = max(r1, r2, r3, r4)

    # --- Area section ---
    sr = r
    data_oa = _query(pc_idx, 'Region_Area', region, 'OA', 'ALL')
    data_fy = _query(pc_idx, 'Region_Area', region, 'FY', 'ALL')
    r1 = _write_entity_section(ws, sr, data_oa, COUNT_MAP, 'AREA', report_date, fmts) if data_oa else sr
    r2 = _write_tom_ondate_section(ws, sr, data_oa, 'AREA', next_day_str, fmts, tom_col) if data_oa else sr
    r3 = _write_entity_section(ws, sr, data_fy, COUNT_MAP, 'AREA', report_date, fmts, col_offset=fy_offset) if data_fy else sr
    r4 = _write_tom_ondate_section(ws, sr, data_fy, 'AREA', next_day_str, fmts, fy_tom_col) if data_fy else sr
    r = max(r1, r2, r3, r4)

    # --- Branch section (area-grouped) ---
    sr = r
    r1 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'OA', 'ALL', report_date, fmts,
                                               area_ft='Region_Area', area_fv=region)
    r2 = _write_tom_ondate_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'OA', 'ALL', next_day_str, fmts, tom_col,
                                                   area_ft='Region_Area', area_fv=region)
    r3 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'FY', 'ALL', report_date, fmts, col_offset=fy_offset,
                                               area_ft='Region_Area', area_fv=region)
    r4 = _write_tom_ondate_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'FY', 'ALL', next_day_str, fmts, fy_tom_col,
                                                   area_ft='Region_Area', area_fv=region)
    r = max(r1, r2, r3, r4)

    # --- Branch + Officer section ---
    if has_officer_col:
        sr = r + 2
        r1 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'OA', 'ALL', report_date, fmts, '(OverAll)',
            filter_type='Region_Branch', filter_value=region)
        r2 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'OA', 'ALL', next_day_str, fmts, tom_col,
                                           filter_type='Region_Branch', filter_value=region)
        r3 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'FY', 'ALL', report_date, fmts, f'({fy_label})',
            filter_type='Region_Branch', filter_value=region, col_offset=fy_offset)
        r4 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'FY', 'ALL', next_day_str, fmts, fy_tom_col,
                                           filter_type='Region_Branch', filter_value=region)
        r = max(r1, r2, r3, r4)

    # --- Product sections (Branch-wise, area-grouped) ---
    for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
        sr = r + 2
        ws.merge_range(sr, E_NAME, sr, E_LAST,
                       f'BRANCH WISE - {display} REPORT', fmts['title_grey'])
        ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                       f'BRANCH WISE - {display} REPORT (FY)', fmts['title_grey'])
        sr += 2
        r1 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'OA', prod, report_date, fmts,
                                                   area_ft='Region_Area', area_fv=region)
        r3 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Region_Branch', region, 'FY', prod, report_date, fmts, col_offset=fy_offset,
                                                   area_ft='Region_Area', area_fv=region)
        r = max(r1, r3)

    # --- Product B+O sections ---
    if has_officer_col:
        for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
            sr = r + 2
            ws.merge_range(sr, E_NAME, sr, E_LAST,
                           f'BRANCH + OFFICER - {display} REPORT', fmts['title_grey'])
            ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                           f'BRANCH + OFFICER - {display} REPORT (FY)', fmts['title_grey'])
            sr += 2
            r1 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'OA', prod, report_date, fmts, f'(OverAll - {display})',
                filter_type='Region_Branch', filter_value=region)
            r3 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'FY', prod, report_date, fmts, f'(FY - {display})',
                filter_type='Region_Branch', filter_value=region, col_offset=fy_offset)
            r = max(r1, r3)


def _build_area_sheet(wb, pc_idx, area, report_date, fmts,
                      has_officer_col=True, next_day_str='', fy_label='FY'):
    """Build a per-area sheet with 4 side-by-side views, row-aligned."""
    safe_name = f'area_{area}'[:31]
    ws = wb.add_worksheet(safe_name)
    _setup_entity_worksheet(ws)

    tom_col = 33
    fy_offset = 38
    fy_tom_col = 71

    # Title row
    r = 1
    ws.merge_range(r, E_NAME, r, E_LAST,
                   f'{area} - COLLECTION REPORT - {report_date}', fmts['title'])
    ws.merge_range(r, tom_col, r, tom_col + 1,
                   f'{area} - ON-DATE - {next_day_str}', fmts['title_grey'])
    ws.merge_range(r, E_NAME + fy_offset, r, E_LAST + fy_offset,
                   f'{area} - FY REPORT - {report_date}', fmts['title_grey'])
    ws.merge_range(r, fy_tom_col, r, fy_tom_col + 1,
                   f'{area} - FY ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 2

    # --- Branch section ---
    sr = r
    data_oa = _query(pc_idx, 'Area', area, 'OA', 'ALL')
    data_fy = _query(pc_idx, 'Area', area, 'FY', 'ALL')
    r1 = _write_entity_section(ws, sr, data_oa, COUNT_MAP, 'BRANCH', report_date, fmts) if data_oa else sr
    r2 = _write_tom_ondate_section(ws, sr, data_oa, 'BRANCH', next_day_str, fmts, tom_col) if data_oa else sr
    r3 = _write_entity_section(ws, sr, data_fy, COUNT_MAP, 'BRANCH', report_date, fmts, col_offset=fy_offset) if data_fy else sr
    r4 = _write_tom_ondate_section(ws, sr, data_fy, 'BRANCH', next_day_str, fmts, fy_tom_col) if data_fy else sr
    r = max(r1, r2, r3, r4)

    # --- Branch + Officer section ---
    if has_officer_col:
        sr = r + 2
        r1 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'OA', 'ALL', report_date, fmts, '(OverAll)',
            filter_type='Area', filter_value=area)
        r2 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'OA', 'ALL', next_day_str, fmts, tom_col,
                                           filter_type='Area', filter_value=area)
        r3 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'FY', 'ALL', report_date, fmts, f'({fy_label})',
            filter_type='Area', filter_value=area, col_offset=fy_offset)
        r4 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'FY', 'ALL', next_day_str, fmts, fy_tom_col,
                                           filter_type='Area', filter_value=area)
        r = max(r1, r2, r3, r4)

    # --- Product sections ---
    for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
        sr = r + 2
        ws.merge_range(sr, E_NAME, sr, E_LAST,
                       f'BRANCH WISE - {display} REPORT', fmts['title_grey'])
        ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                       f'BRANCH WISE - {display} REPORT (FY)', fmts['title_grey'])
        sr += 2
        data_oa = _query(pc_idx, 'Area', area, 'OA', prod)
        data_fy = _query(pc_idx, 'Area', area, 'FY', prod)
        r1 = _write_entity_section(ws, sr, data_oa, COUNT_MAP, 'BRANCH', report_date, fmts) if data_oa else sr
        r3 = _write_entity_section(ws, sr, data_fy, COUNT_MAP, 'BRANCH', report_date, fmts, col_offset=fy_offset) if data_fy else sr
        r = max(r1, r3)

    # --- Product B+O sections ---
    if has_officer_col:
        for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
            sr = r + 2
            ws.merge_range(sr, E_NAME, sr, E_LAST,
                           f'BRANCH + OFFICER - {display} REPORT', fmts['title_grey'])
            ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                           f'BRANCH + OFFICER - {display} REPORT (FY)', fmts['title_grey'])
            sr += 2
            r1 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'OA', prod, report_date, fmts, f'(OverAll - {display})',
                filter_type='Area', filter_value=area)
            r3 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'FY', prod, report_date, fmts, f'(FY - {display})',
                filter_type='Area', filter_value=area, col_offset=fy_offset)
            r = max(r1, r3)


def _build_division_sheet(wb, pc_idx, division, report_date, fmts,
                          has_officer_col=True, next_day_str='', fy_label='FY'):
    """Build a per-division sheet with 4 side-by-side views, row-aligned.

    Shows Area summary, then Branch section grouped by area, then B+O expanded.
    Uses Division_Area to get areas, Division_Branch to get branches.
    """
    safe_name = f'division_{division}'[:31]
    ws = wb.add_worksheet(safe_name)
    _setup_entity_worksheet(ws)

    tom_col = 33
    fy_offset = 38
    fy_tom_col = 71

    # Title row
    r = 1
    ws.merge_range(r, E_NAME, r, E_LAST,
                   f'{division} - COLLECTION REPORT - {report_date}', fmts['title'])
    ws.merge_range(r, tom_col, r, tom_col + 1,
                   f'{division} - ON-DATE - {next_day_str}', fmts['title_grey'])
    ws.merge_range(r, E_NAME + fy_offset, r, E_LAST + fy_offset,
                   f'{division} - FY REPORT - {report_date}', fmts['title_grey'])
    ws.merge_range(r, fy_tom_col, r, fy_tom_col + 1,
                   f'{division} - FY ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 2

    # --- Area section ---
    sr = r
    data_oa = _query(pc_idx, 'Division_Area', division, 'OA', 'ALL')
    data_fy = _query(pc_idx, 'Division_Area', division, 'FY', 'ALL')
    r1 = _write_entity_section(ws, sr, data_oa, COUNT_MAP, 'AREA', report_date, fmts) if data_oa else sr
    r2 = _write_tom_ondate_section(ws, sr, data_oa, 'AREA', next_day_str, fmts, tom_col) if data_oa else sr
    r3 = _write_entity_section(ws, sr, data_fy, COUNT_MAP, 'AREA', report_date, fmts, col_offset=fy_offset) if data_fy else sr
    r4 = _write_tom_ondate_section(ws, sr, data_fy, 'AREA', next_day_str, fmts, fy_tom_col) if data_fy else sr
    r = max(r1, r2, r3, r4)

    # --- Branch section (area-grouped) ---
    sr = r
    r1 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'OA', 'ALL', report_date, fmts,
                                               area_ft='Division_Area', area_fv=division)
    r2 = _write_tom_ondate_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'OA', 'ALL', next_day_str, fmts, tom_col,
                                                   area_ft='Division_Area', area_fv=division)
    r3 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'FY', 'ALL', report_date, fmts, col_offset=fy_offset,
                                               area_ft='Division_Area', area_fv=division)
    r4 = _write_tom_ondate_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'FY', 'ALL', next_day_str, fmts, fy_tom_col,
                                                   area_ft='Division_Area', area_fv=division)
    r = max(r1, r2, r3, r4)

    # --- Branch + Officer section ---
    if has_officer_col:
        sr = r + 2
        r1 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'OA', 'ALL', report_date, fmts, '(OverAll)',
            filter_type='Division_Branch', filter_value=division)
        r2 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'OA', 'ALL', next_day_str, fmts, tom_col,
                                           filter_type='Division_Branch', filter_value=division)
        r3 = _write_entity_branch_officer_expanded(
            ws, sr, pc_idx, 'FY', 'ALL', report_date, fmts, f'({fy_label})',
            filter_type='Division_Branch', filter_value=division, col_offset=fy_offset)
        r4 = _write_tom_ondate_bo_section(ws, sr, pc_idx, 'FY', 'ALL', next_day_str, fmts, fy_tom_col,
                                           filter_type='Division_Branch', filter_value=division)
        r = max(r1, r2, r3, r4)

    # --- Product sections (Branch-wise, area-grouped) ---
    for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
        sr = r + 2
        ws.merge_range(sr, E_NAME, sr, E_LAST,
                       f'BRANCH WISE - {display} REPORT', fmts['title_grey'])
        ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                       f'BRANCH WISE - {display} REPORT (FY)', fmts['title_grey'])
        sr += 2
        r1 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'OA', prod, report_date, fmts,
                                                   area_ft='Division_Area', area_fv=division)
        r3 = _write_entity_branch_section_grouped(ws, sr, pc_idx, 'Division_Branch', division, 'FY', prod, report_date, fmts, col_offset=fy_offset,
                                                   area_ft='Division_Area', area_fv=division)
        r = max(r1, r3)

    # --- Product B+O sections ---
    if has_officer_col:
        for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
            sr = r + 2
            ws.merge_range(sr, E_NAME, sr, E_LAST,
                           f'BRANCH + OFFICER - {display} REPORT', fmts['title_grey'])
            ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                           f'BRANCH + OFFICER - {display} REPORT (FY)', fmts['title_grey'])
            sr += 2
            r1 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'OA', prod, report_date, fmts, f'(OverAll - {display})',
                filter_type='Division_Branch', filter_value=division)
            r3 = _write_entity_branch_officer_expanded(
                ws, sr, pc_idx, 'FY', prod, report_date, fmts, f'(FY - {display})',
                filter_type='Division_Branch', filter_value=division, col_offset=fy_offset)
            r = max(r1, r3)


def _build_branch_sheet(wb, pc_idx, branch, report_date, fmts, next_day_str=''):
    """Build a per-branch sheet with 4 side-by-side views, row-aligned."""
    safe_name = f'branch_{branch}'[:31]
    ws = wb.add_worksheet(safe_name)
    _setup_entity_worksheet(ws)
    ws.set_column(E_NAME, E_NAME, 30)
    fy_offset = 38
    ws.set_column(E_NAME + fy_offset, E_NAME + fy_offset, 30)

    tom_col = 33
    fy_tom_col = 71

    def _emp_display(data):
        dd = {}
        for emp_id, metrics in data.items():
            on = str(metrics.get('officer_name', ''))
            dd[f'{emp_id} - {on}' if on else emp_id] = metrics
        return dd

    # Title row
    r = 1
    ws.merge_range(r, E_NAME, r, E_LAST,
                   f'{branch} - COLLECTION REPORT - {report_date}', fmts['title'])
    ws.merge_range(r, tom_col, r, tom_col + 1,
                   f'{branch} - ON-DATE - {next_day_str}', fmts['title_grey'])
    ws.merge_range(r, E_NAME + fy_offset, r, E_LAST + fy_offset,
                   f'{branch} - FY REPORT - {report_date}', fmts['title_grey'])
    ws.merge_range(r, fy_tom_col, r, fy_tom_col + 1,
                   f'{branch} - FY ON-DATE - {next_day_str}', fmts['title_grey'])
    r += 2

    # --- Officer section ---
    sr = r
    data_oa = _query(pc_idx, 'BranchName', branch, 'OA', 'ALL')
    data_fy = _query(pc_idx, 'BranchName', branch, 'FY', 'ALL')
    dd_oa = _emp_display(data_oa) if data_oa else {}
    dd_fy = _emp_display(data_fy) if data_fy else {}
    r1 = _write_entity_section(ws, sr, dd_oa, COUNT_MAP, 'EMP ID', report_date, fmts) if dd_oa else sr
    r2 = _write_tom_ondate_section(ws, sr, dd_oa, 'EMP ID', next_day_str, fmts, tom_col) if dd_oa else sr
    r3 = _write_entity_section(ws, sr, dd_fy, COUNT_MAP, 'EMP ID', report_date, fmts, col_offset=fy_offset) if dd_fy else sr
    r4 = _write_tom_ondate_section(ws, sr, dd_fy, 'EMP ID', next_day_str, fmts, fy_tom_col) if dd_fy else sr
    r = max(r1, r2, r3, r4)

    # --- Product sections ---
    for prod, display in [('IGL', 'IGL'), ('FIG', 'FIG'), ('VVY', 'IL')]:
        data_oa = _query(pc_idx, 'BranchName', branch, 'OA', prod)
        data_fy = _query(pc_idx, 'BranchName', branch, 'FY', prod)
        if data_oa or data_fy:
            sr = r + 2
            ws.merge_range(sr, E_NAME, sr, E_LAST,
                           f'EMP ID WISE - {display} REPORT', fmts['title_grey'])
            ws.merge_range(sr, E_NAME + fy_offset, sr, E_LAST + fy_offset,
                           f'EMP ID WISE - {display} REPORT (FY)', fmts['title_grey'])
            sr += 2
            dd_oa = _emp_display(data_oa) if data_oa else {}
            dd_fy = _emp_display(data_fy) if data_fy else {}
            r1 = _write_entity_section(ws, sr, dd_oa, COUNT_MAP, 'EMP ID', report_date, fmts) if dd_oa else sr
            r3 = _write_entity_section(ws, sr, dd_fy, COUNT_MAP, 'EMP ID', report_date, fmts, col_offset=fy_offset) if dd_fy else sr
            r = max(r1, r3)


# ---------------------------------------------------------------------------
# Employee Data + IL Reports sheets (per-employee, fed from build_employee_report)
# ---------------------------------------------------------------------------

def _build_employee_data_sheet(wb, df, fmts):
    """Sheet 2 — per-employee, all products combined, 3-row grouped header.

    Mirrors the layout of the manual 'Employee Data.xlsx' (EMP Wise sheet):
    Sl No / EMP ID / EMP Name / Branch / Area / Division / Region, then
    REGULAR / 1-30 / 31-60 / PNPA / 1-90 DPD blocks and an NPA block
    (DEMAND + ACTIVATION + CLOSURE). Zero cells render as '-'.
    """
    ws = wb.add_worksheet('Employee Data')

    # Group header (row 1/2) — light peach; column header (row 3) — orange.
    title = wb.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter',
                           'border': 1, 'bg_color': '#FCE4D6'})
    # Id-column header (orange) + per-metric header fills matching manual file:
    #   DEMAND/FTOD/BALANCE -> peach, COLLECTION/ACCOUNT/AMOUNT -> green,
    #   COLLECTION % -> yellow.
    hdr = wb.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter',
                         'border': 1, 'bg_color': '#F4B084', 'text_wrap': True})
    _hbase = {'bold': True, 'align': 'center', 'valign': 'vcenter',
              'border': 1, 'text_wrap': True}
    hdr_peach = wb.add_format({**_hbase, 'bg_color': '#FCE4D6'})
    hdr_green = wb.add_format({**_hbase, 'bg_color': '#E2EFDA'})
    hdr_yellow = wb.add_format({**_hbase, 'bg_color': '#FFFFCC'})

    def _hfmt(name):
        if name in ('COLLECTION', 'ACCOUNT', 'AMOUNT'):
            return hdr_green
        if name == 'COLLECTION %':
            return hdr_yellow
        return hdr_peach
    cell = wb.add_format({'border': 1})
    num = wb.add_format({'border': 1, 'num_format': '#,##0'})
    pct = wb.add_format({'border': 1, 'num_format': '0.0%'})
    dash = wb.add_format({'border': 1, 'align': 'center'})
    tot = wb.add_format({'bold': True, 'border': 1, 'num_format': '#,##0',
                         'bg_color': '#FCE4D6'})
    tot_dash = wb.add_format({'bold': True, 'border': 1, 'align': 'center',
                              'bg_color': '#FCE4D6'})

    # Group headers — NPA on row 0, the rest on row 1. Id cols (0-6) and
    # row 0 elsewhere stay blank/white (no banding).
    ws.merge_range(0, 27, 0, 31, 'NPA', title)
    ws.merge_range(1, 7, 1, 10, 'REGULAR DEMAND VS COLLECTION', title)
    ws.merge_range(1, 11, 1, 14, '1-30 DPD', title)
    ws.merge_range(1, 15, 1, 18, '31-60 DPD', title)
    ws.merge_range(1, 19, 1, 22, 'PNPA', title)
    ws.merge_range(1, 23, 1, 26, '1-90 DPD', title)
    ws.merge_range(1, 28, 1, 29, 'ACTIVATION', title)
    ws.merge_range(1, 30, 1, 31, 'CLOSURE', title)

    # Id columns 0-6 — header label on row 2 only (no vertical merge)
    id_cols = ['Sl No', 'EMP ID', 'EMP Name', 'Branch', 'Area', 'Division', 'Region']
    for c, name in enumerate(id_cols):
        ws.write(2, c, name, hdr)

    # Row 2 — metric column names
    row2 = ['DEMAND', 'COLLECTION', 'FTOD', 'COLLECTION %',          # 7-10
            'DEMAND', 'COLLECTION', 'BALANCE', 'COLLECTION %',        # 11-14
            'DEMAND', 'COLLECTION', 'BALANCE', 'COLLECTION %',        # 15-18
            'DEMAND', 'COLLECTION', 'BALANCE', 'COLLECTION %',        # 19-22
            'DEMAND', 'COLLECTION', 'BALANCE', 'COLLECTION %',        # 23-26
            'DEMAND',                                                # 27
            'ACCOUNT', 'AMOUNT', 'ACCOUNT', 'AMOUNT']                # 28-31
    for i, name in enumerate(row2):
        ws.write(2, 7 + i, name, _hfmt(name))

    def _wd(r, c, v):
        v = 0 if v is None or v == '' else v
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0
        if v == 0:
            ws.write(r, c, '-', dash)
        else:
            ws.write_number(r, c, v, num)

    def _wp(r, c, n, d):
        if not d:
            ws.write(r, c, '-', dash)
        else:
            ws.write_number(r, c, float(n) / float(d), pct)

    # ── DPD blocks: (start_col, demand_key, collection_key) ──
    blocks = [
        (11, '1-30 Demand', '1-30 Collection'),
        (15, '31-60 Demand', '31-60 Collection'),
        (19, 'PNPA Demand', 'PNPA Collection'),
        (23, '1-90 Demand', '1-90 Collection'),
    ]
    totals = {}

    def _g(row, key):
        v = row.get(key, 0)
        try:
            return float(v) if v not in (None, '') and not pd.isna(v) else 0.0
        except (TypeError, ValueError):
            return 0.0

    r = 3
    for sl, (_, row) in enumerate(df.iterrows(), start=1):
        ws.write_number(r, 0, sl, num)
        ws.write(r, 1, str(row.get('Emp ID', '') or ''), cell)
        ws.write(r, 2, str(row.get('Officer Name', '') or ''), cell)
        ws.write(r, 3, str(row.get('Branch', '') or ''), cell)
        ws.write(r, 4, str(row.get('Area', '') or ''), cell)
        ws.write(r, 5, str(row.get('Division', '') or ''), cell)
        ws.write(r, 6, str(row.get('Region', '') or ''), cell)

        rd, rc = _g(row, 'Regular Demand'), _g(row, 'Regular Collection')
        _wd(r, 7, rd)
        _wd(r, 8, rc)
        _wd(r, 9, rd - rc)
        _wp(r, 10, rc, rd)
        totals['rd'] = totals.get('rd', 0) + rd
        totals['rc'] = totals.get('rc', 0) + rc

        for start, dk, ck in blocks:
            d, c2 = _g(row, dk), _g(row, ck)
            _wd(r, start, d)
            _wd(r, start + 1, c2)
            _wd(r, start + 2, d - c2)
            _wp(r, start + 3, c2, d)
            totals[dk] = totals.get(dk, 0) + d
            totals[ck] = totals.get(ck, 0) + c2

        npa = _g(row, 'NPA Cases')
        aa, am = _g(row, 'NPA Act Acc'), _g(row, 'NPA Act Amt')
        ca, cm = _g(row, 'NPA Clo Acc'), _g(row, 'NPA Clo Amt')
        _wd(r, 27, npa)
        _wd(r, 28, aa)
        _wd(r, 29, am)
        _wd(r, 30, ca)
        _wd(r, 31, cm)
        for k, v in (('npa', npa), ('aa', aa), ('am', am), ('ca', ca), ('cm', cm)):
            totals[k] = totals.get(k, 0) + v
        r += 1

    # ── Grand Total row ──
    def _t(c, v):
        v = totals.get(v, 0) if isinstance(v, str) else v
        if v == 0:
            ws.write(r, c, '-', tot_dash)
        else:
            ws.write_number(r, c, v, tot)

    for c in range(0, 7):
        ws.write(r, c, 'GRAND TOTAL' if c == 2 else '', tot_dash)
    _t(7, 'rd')
    _t(8, 'rc')
    _t(9, totals.get('rd', 0) - totals.get('rc', 0))
    if totals.get('rd', 0):
        ws.write_number(r, 10, totals.get('rc', 0) / totals['rd'],
                        wb.add_format({'bold': True, 'border': 1,
                                       'num_format': '0.00%', 'bg_color': '#FCE4D6'}))
    else:
        ws.write(r, 10, '-', tot_dash)
    for start, dk, ck in blocks:
        d, c2 = totals.get(dk, 0), totals.get(ck, 0)
        _t(start, d)
        _t(start + 1, c2)
        _t(start + 2, d - c2)
        if d:
            ws.write_number(r, start + 3, c2 / d,
                            wb.add_format({'bold': True, 'border': 1,
                                           'num_format': '0.00%', 'bg_color': '#FCE4D6'}))
        else:
            ws.write(r, start + 3, '-', tot_dash)
    _t(27, 'npa')
    _t(28, 'aa')
    _t(29, 'am')
    _t(30, 'ca')
    _t(31, 'cm')

    ws.freeze_panes(3, 7)
    ws.set_column(0, 0, 6)
    ws.set_column(1, 1, 12)
    ws.set_column(2, 2, 24)
    ws.set_column(3, 6, 14)
    ws.set_column(7, 31, 12)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_report(pc_df, output_file, target_date, has_officer_col=True, sheets_dir=None,
                  employee_data=None):
    """Build fully formatted report Excel from precomputed data.

    Parameters
    ----------
    sheets_dir : str | Path | None
        If provided, also write each sheet as an individual .xlsx file into
        this directory (for fast email attachment assembly). Writes a
        manifest.json alongside the sheet files.
    """
    import json
    import re
    from datetime import datetime, timezone

    t0 = time.perf_counter()
    output_file = Path(output_file)

    target_date = pd.Timestamp(target_date)
    report_date = target_date.strftime('%d-%m-%Y')
    next_day = target_date + pd.Timedelta(days=1)
    next_day_str = next_day.strftime('%d-%m-%Y')

    for col in ['filter_type', 'filter_value', 'group_value', 'scope', 'product']:
        if col in pc_df.columns:
            pc_df[col] = pc_df[col].astype(str)

    pc_idx = _build_index(pc_df)

    wb = xlsxwriter.Workbook(str(output_file), {'strings_to_urls': False})
    fmts = _create_formats(wb)

    # ── Individual sheet file generation ──
    _unsafe_re = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    manifest_sheets = {}
    _used_filenames = set()

    if sheets_dir:
        sheets_dir = Path(sheets_dir)
        # Clear previous extraction
        if sheets_dir.exists():
            for old in sheets_dir.glob('*.xlsx'):
                try:
                    old.unlink()
                except OSError:
                    pass
        sheets_dir.mkdir(parents=True, exist_ok=True)

    def _emit(sheet_name, builder, *args, **kwargs):
        """Write a single-sheet xlsxwriter workbook for fast email attachment assembly."""
        if not sheets_dir:
            return
        try:
            safe = _unsafe_re.sub('_', sheet_name).strip(' ._')
            safe = re.sub(r'_+', '_', safe).strip('_') or 'sheet'
            # Handle duplicate filenames
            fname = safe
            if fname in _used_filenames:
                counter = 2
                while f"{safe}_{counter}" in _used_filenames:
                    counter += 1
                fname = f"{safe}_{counter}"
            _used_filenames.add(fname)

            out_file = sheets_dir / f"{fname}.xlsx"
            mini_wb = xlsxwriter.Workbook(str(out_file), {'strings_to_urls': False})
            mini_fmts = _create_formats(mini_wb)
            builder(mini_wb, mini_fmts)
            mini_wb.close()

            size_bytes = out_file.stat().st_size
            manifest_sheets[sheet_name] = {
                'path': out_file.name,
                'size_bytes': size_bytes,
            }
        except Exception as e:
            logging.warning(f"Failed to emit individual sheet '{sheet_name}': {e}")

    # 1. OverAll sheet
    _build_summary_sheet(wb, pc_idx, 'OverAll', 'OA', report_date, fmts, has_officer_col)
    _emit('OverAll', lambda w, f: _build_summary_sheet(w, pc_idx, 'OverAll', 'OA', report_date, f, has_officer_col))

    # 1b. Employee Data sheet (sheet 2) — per-employee, all products combined
    if employee_data is not None:
        try:
            _build_employee_data_sheet(wb, employee_data, fmts)
            _emit('Employee Data', lambda w, f: _build_employee_data_sheet(w, employee_data, f))
        except Exception as e:
            logging.warning(f"Failed to build 'Employee Data' sheet: {e}")

    # 1c. IL Reports sheet (sheet 3) — OverAll layout, IL (VVY) product only
    try:
        _build_summary_sheet(wb, pc_idx, 'IL Reports', 'OA', report_date, fmts,
                             has_officer_col, product='VVY')
        _emit('IL Reports', lambda w, f: _build_summary_sheet(
            w, pc_idx, 'IL Reports', 'OA', report_date, f,
            has_officer_col, product='VVY'))
    except Exception as e:
        logging.warning(f"Failed to build 'IL Reports' sheet: {e}")

    # 2. OverAll tom_On-Date sheet
    _build_ondate_sheet(wb, pc_idx, report_date, next_day_str, fmts, scope='OA', prefix='OverAll')
    _emit('tom_OverAll_On-Date', lambda w, f: _build_ondate_sheet(w, pc_idx, report_date, next_day_str, f, scope='OA', prefix='OverAll'))

    # 3. FY sheet
    fy_label = _get_fy_label(target_date)
    _build_summary_sheet(wb, pc_idx, fy_label, 'FY', report_date, fmts, has_officer_col)
    _emit(fy_label, lambda w, f: _build_summary_sheet(w, pc_idx, fy_label, 'FY', report_date, f, has_officer_col))

    # 4. FY tom_On-Date sheet
    _build_ondate_sheet(wb, pc_idx, report_date, next_day_str, fmts, scope='FY', prefix=fy_label)
    _emit(f'tom_{fy_label}_On-Date', lambda w, f: _build_ondate_sheet(w, pc_idx, report_date, next_day_str, f, scope='FY', prefix=fy_label))

    # 5. Per-Region sheets
    regions = _get_unique_filter_values(pc_idx, 'Region_Division', 'OA', 'ALL')
    for region in regions:
        sheet_name = f'region_{region}'[:31]
        try:
            _build_region_sheet(wb, pc_idx, region, report_date, fmts, has_officer_col,
                               next_day_str=next_day_str, fy_label=fy_label)
            _emit(sheet_name, lambda w, f, r=region: _build_region_sheet(w, pc_idx, r, report_date, f, has_officer_col, next_day_str=next_day_str, fy_label=fy_label))
        except Exception as e:
            logging.warning(f"Failed to build region sheet '{region}': {e}")

    # 6. Per-Division sheets
    divisions = _get_unique_filter_values(pc_idx, 'Division_Area', 'OA', 'ALL')
    for division in divisions:
        sheet_name = f'division_{division}'[:31]
        try:
            _build_division_sheet(wb, pc_idx, division, report_date, fmts, has_officer_col,
                                  next_day_str=next_day_str, fy_label=fy_label)
            _emit(sheet_name, lambda w, f, d=division: _build_division_sheet(w, pc_idx, d, report_date, f, has_officer_col, next_day_str=next_day_str, fy_label=fy_label))
        except Exception as e:
            logging.warning(f"Failed to build division sheet '{division}': {e}")

    # 7. Per-Area sheets
    areas = _get_unique_filter_values(pc_idx, 'Area', 'OA', 'ALL')
    for area in areas:
        sheet_name = f'area_{area}'[:31]
        try:
            _build_area_sheet(wb, pc_idx, area, report_date, fmts, has_officer_col,
                              next_day_str=next_day_str, fy_label=fy_label)
            _emit(sheet_name, lambda w, f, a=area: _build_area_sheet(w, pc_idx, a, report_date, f, has_officer_col, next_day_str=next_day_str, fy_label=fy_label))
        except Exception as e:
            logging.warning(f"Failed to build area sheet '{area}': {e}")

    # 8. Per-Branch sheets
    if has_officer_col:
        branches = _get_unique_filter_values(pc_idx, 'BranchName', 'OA', 'ALL')
        for branch in branches:
            sheet_name = f'branch_{branch}'[:31]
            try:
                _build_branch_sheet(wb, pc_idx, branch, report_date, fmts,
                                    next_day_str=next_day_str)
                _emit(sheet_name, lambda w, f, b=branch: _build_branch_sheet(w, pc_idx, b, report_date, f, next_day_str=next_day_str))
            except Exception as e:
                logging.warning(f"Failed to build branch sheet '{branch}': {e}")

    wb.close()

    # Write sheet manifest if individual sheets were generated
    if sheets_dir and manifest_sheets:
        source_mtime = output_file.stat().st_mtime if output_file.exists() else 0
        manifest = {
            'source': output_file.name,
            'source_mtime': source_mtime,
            'extracted_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
            'sheet_count': len(manifest_sheets),
            'sheets': manifest_sheets,
        }
        manifest_path = sheets_dir / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2))
        logging.info(f"REPORT: Wrote {len(manifest_sheets)} individual sheet files + manifest")

    elapsed = time.perf_counter() - t0
    logging.info(f"REPORT: Built formatted report in {elapsed:.3f}s -> {output_file.name}")

    return output_file
