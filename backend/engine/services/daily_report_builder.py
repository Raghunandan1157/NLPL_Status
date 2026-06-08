"""
Daily Report Builder - Replicates VBA template output using pre-computed data.
==============================================================================
Produces an Excel workbook with 4 sheets:
  1. OverAll              - Region/Division/Area/Branch + Products + Branch+Officer (21 cols)
  2. OverAll_On-Date      - All sections with DEMAND/COLLECTION/COLLECTION% (5 cols)
  3. FY_<YY>-<YY+1>      - Same as OverAll but scope=FY (Loan Date=1)
  4. FY_<YY>-<YY+1>_On-Date - On-date for FY scope

Column layout (main sheets, 0-indexed):
  0=EMP ID, 1=Name, 2-5=Regular Demand, 6-9=1-30 DPD,
  10-13=31-60 DPD, 14-17=PNPA, 18-21=1-90 DPD, 22-24=NPA
"""

import logging
from pathlib import Path
import pandas as pd

# ---------------------------------------------------------------------------
# Colors (RGB tuples for xlsxwriter)
# ---------------------------------------------------------------------------
ORANGE = '#FCE4D6'       # Light Orange
GREEN = '#E2EFDA'        # Light Green
YELLOW = '#FFFFCC'       # Light Yellow
GRAND_TOTAL = '#F4B084'  # Dark Orange
BLUE = '#BDD7EE'         # Light Blue
GREY = '#C8C8C8'         # Grey

# Last data column (0-indexed): Y = col 24
MAX_COL = 24


def build_daily_report(precomp_df, output_path, target_date, has_officer=False,
                       formatted_dt=None, eod_target_date=None, hourly_mode=False,
                       employee_data=None):
    """Build the VBA-template-style report from precomp data.

    Args:
        eod_target_date: The EOD processing date (one day before the hourly report date).
            Used to compute the "tomorrow" On-Date date.  When None, falls back to
            target_date - 1 day.
        employee_data: Optional per-employee DataFrame (all products combined). When
            provided, an 'Employee Data' sheet is appended (same layout as the EOD
            report). Built by the caller via build_employee_report.
        hourly_mode: When True, use hourly VBA column mapping:
            - Regular Collection = hourly_reg_collection (DPD Days=0 Days, DPD Group=1-30, Full EMI Paid & has_collection)
            - Demand = FTOD (remaining balance = reg_demand - reg_collection)
            - NPA = npa_hourly_* (Loan Status-Last Month=NPA only, no extra filters)
    """
    import xlsxwriter

    report_date = formatted_dt if formatted_dt else target_date.strftime('%d-%m-%Y')
    # Date-only version for On-Date titles (strip time suffix like "@ 3:00 PM")
    report_date_only = report_date.split('@')[0].strip() if '@' in report_date else report_date
    eod_date = eod_target_date if eod_target_date else target_date
    eod_date_str = eod_date.strftime('%d-%m-%Y')
    # The On-Date sheet shows demand for the day AFTER target_date — the precomp's
    # next_day_mask is keyed off target_date + 1. The label must match that date
    # in every mode (a PAR file dated the 19th → On-Date demand for the 20th).
    next_day_str = (target_date + pd.Timedelta(days=1)).strftime('%d-%m-%Y')

    from services.eod_processor import get_fy_label
    fy_label = get_fy_label(target_date)

    wb = xlsxwriter.Workbook(str(output_path), {'strings_to_urls': False})
    fmts = _create_formats(wb)
    pc = precomp_df

    # Sheet 1: OverAll
    ws = wb.add_worksheet('OverAll')
    _build_main_sheet(ws, pc, 'OA', report_date, has_officer, fmts, '(OverAll)',
                      hourly_mode=hourly_mode, eod_date_str=eod_date_str)

    # Sheet 2: OverAll_On-Date — demand-only for next day
    ws2 = wb.add_worksheet('OverAll_On-Date')
    _build_ondate_sheet(ws2, pc, 'OA', next_day_str, has_officer, fmts,
                        title=f'Today On-Date Demand Report ({next_day_str})',
                        label='(OverAll)', report_date_full=report_date)

    # Sheet 3: FY
    ws3 = wb.add_worksheet(fy_label)
    _build_main_sheet(ws3, pc, 'FY', report_date, has_officer, fmts, f'({fy_label})',
                      hourly_mode=hourly_mode, eod_date_str=eod_date_str)

    # Sheet 4: FY_On-Date — demand-only for next day
    ws4 = wb.add_worksheet(f'{fy_label}_On-Date')
    _build_ondate_sheet(ws4, pc, 'FY', next_day_str, has_officer, fmts,
                        title=f'FY Today On-Date Demand Report ({next_day_str})',
                        label=f'({fy_label})', report_date_full=report_date)

    # Sheet 5: IL Reports — OverAll layout, IL (VVY) product only
    try:
        ws5 = wb.add_worksheet('IL Reports')
        _build_il_sheet(ws5, pc, 'OA', report_date, has_officer, fmts, '(IL Reports)',
                        hourly_mode=hourly_mode)
    except Exception as e:
        logging.warning(f"DAILY_REPORT_BUILDER: 'IL Reports' sheet skipped: {e}")

    # Sheet 6: Employee Data — per-employee, all products combined
    if employee_data is not None:
        try:
            from services.report_builder import _build_employee_data_sheet
            _build_employee_data_sheet(wb, employee_data, fmts)
        except Exception as e:
            logging.warning(f"DAILY_REPORT_BUILDER: 'Employee Data' sheet skipped: {e}")

    wb.close()
    logging.info(f"DAILY_REPORT_BUILDER: Written {output_path}")


# ---------------------------------------------------------------------------
# Format factory
# ---------------------------------------------------------------------------
def _create_formats(wb):
    """Create all reusable formats."""
    f = {}

    f['title'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': ORANGE, 'border': 1,
    })
    f['title_blue'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': BLUE, 'border': 1,
    })
    f['title_grey'] = wb.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'bg_color': GREY, 'border': 1,
    })

    f['hdr_orange'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': ORANGE,
        'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['hdr_green'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': GREEN,
        'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['hdr_yellow'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': YELLOW,
        'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['hdr_blue'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': BLUE,
        'border': 1, 'text_wrap': True, 'font_size': 9,
    })
    f['hdr_grand'] = wb.add_format({
        'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': GRAND_TOTAL,
        'border': 1,
    })

    # Data cells
    f['name_orange'] = wb.add_format({'bg_color': ORANGE, 'border': 1, 'valign': 'vcenter'})
    f['num_orange'] = wb.add_format({
        'bg_color': ORANGE, 'border': 1, 'align': 'center', 'valign': 'vcenter',
        'num_format': '#,##0',
    })
    f['num_green'] = wb.add_format({
        'bg_color': GREEN, 'border': 1, 'align': 'center', 'valign': 'vcenter',
        'num_format': '#,##0',
    })
    f['pct_yellow'] = wb.add_format({
        'bg_color': YELLOW, 'border': 1, 'align': 'center', 'valign': 'vcenter',
        'num_format': '0.0%',
    })
    f['num_green_npa'] = wb.add_format({
        'bg_color': GREEN, 'border': 1, 'align': 'center', 'valign': 'vcenter',
        'num_format': '#,##0',
    })

    # Grand Total row
    f['gt_name'] = wb.add_format({
        'bold': True, 'bg_color': GRAND_TOTAL, 'border': 1, 'valign': 'vcenter',
    })
    f['gt_num'] = wb.add_format({
        'bold': True, 'bg_color': GRAND_TOTAL, 'border': 1, 'align': 'center',
        'valign': 'vcenter', 'num_format': '#,##0',
    })
    f['gt_pct'] = wb.add_format({
        'bold': True, 'bg_color': GRAND_TOTAL, 'border': 1, 'align': 'center',
        'valign': 'vcenter', 'num_format': '0.0%',
    })

    # Branch+Officer: branch row (blue)
    f['br_name'] = wb.add_format({
        'bold': True, 'bg_color': BLUE, 'border': 1, 'valign': 'vcenter',
    })
    f['br_num'] = wb.add_format({
        'bold': True, 'bg_color': BLUE, 'border': 1, 'align': 'center',
        'valign': 'vcenter', 'num_format': '#,##0',
    })
    f['br_pct'] = wb.add_format({
        'bold': True, 'bg_color': BLUE, 'border': 1, 'align': 'center',
        'valign': 'vcenter', 'num_format': '0.0%',
    })

    # Officer row (white)
    f['off_name'] = wb.add_format({'border': 1, 'valign': 'vcenter', 'indent': 1})
    f['off_empid'] = wb.add_format({'border': 1, 'valign': 'vcenter', 'align': 'center'})
    f['off_num'] = wb.add_format({
        'border': 1, 'align': 'center', 'valign': 'vcenter', 'num_format': '#,##0',
    })
    f['off_pct'] = wb.add_format({
        'border': 1, 'align': 'center', 'valign': 'vcenter', 'num_format': '0.0%',
    })

    return f


# ---------------------------------------------------------------------------
# Precomp query helpers
# ---------------------------------------------------------------------------
def _query(pc, filter_type, scope, product='ALL'):
    """Get rows from precomp matching filter criteria, sorted by collection % descending."""
    mask = (
        (pc['filter_type'] == filter_type) &
        (pc['scope'] == scope) &
        (pc['product'] == product)
    )
    df = pc[mask].copy()
    gt = df[df['group_value'] == 'Grand Total']
    data = df[df['group_value'] != 'Grand Total'].copy()
    # Sort by Regular Collection % descending (matches report_builder.py behavior)
    rd = pd.to_numeric(data.get('reg_demand', 0), errors='coerce').fillna(0)
    rc = pd.to_numeric(data.get('reg_collection', 0), errors='coerce').fillna(0)
    data['_sort_pct'] = rc / rd.replace(0, float('nan'))
    data['_sort_pct'] = data['_sort_pct'].fillna(0)
    data = data.sort_values('_sort_pct', ascending=False).drop(columns=['_sort_pct'])
    return data, gt


def _safe_pct(num, denom):
    """Safe percentage: num/denom or 0."""
    if denom and denom != 0:
        return num / denom
    return 0


def _safe_val(v):
    """Ensure numeric value, replace NaN/None with 0."""
    try:
        if pd.isna(v):
            return 0
    except (TypeError, ValueError):
        pass
    return v if v else 0


# ---------------------------------------------------------------------------
# Write leaf column headers (21-col layout: cols 2-20)
# ---------------------------------------------------------------------------
def _write_leaf_headers(ws, hRow3, hRow4, fmts):
    """Write leaf-level column headers at hRow3, merged with hRow4.

    Column layout (0-indexed):
      2=REG DEMAND, 3=REG COLLECTION, 4=FTOD, 5=COLLECTION%
      6=130 DEMAND, 7=COLLECTION, 8=BALANCE, 9=COLLECTION%
      10=3160 DEMAND, 11=COLLECTION, 12=BALANCE, 13=COLLECTION%
      14=PNPA DEMAND, 15=COLLECTION, 16=BALANCE, 17=COLLECTION%
      18=1-90 DEMAND, 19=COLLECTION, 20=BALANCE, 21=COLLECTION%
      22=NPA DEMAND, 23=ACCOUNT (90+ Days), 24=AMOUNT (90+ Days)
    """
    leaf = [
        # Regular Demand
        (2, 'DEMAND', 'orange'), (3, 'COLLECTION', 'green'),
        (4, 'FTOD', 'orange'), (5, 'COLLECTION %', 'yellow'),
        # 1-30 DPD
        (6, 'DEMAND', 'orange'), (7, 'COLLECTION', 'green'),
        (8, 'BALANCE', 'orange'), (9, 'COLLECTION %', 'yellow'),
        # 31-60 DPD
        (10, 'DEMAND', 'orange'), (11, 'COLLECTION', 'green'),
        (12, 'BALANCE', 'orange'), (13, 'COLLECTION %', 'yellow'),
        # PNPA
        (14, 'DEMAND', 'orange'), (15, 'COLLECTION', 'green'),
        (16, 'BALANCE', 'orange'), (17, 'COLLECTION %', 'yellow'),
        # 1-90 DPD
        (18, 'DEMAND', 'orange'), (19, 'COLLECTION', 'green'),
        (20, 'BALANCE', 'orange'), (21, 'COLLECTION %', 'yellow'),
    ]

    for col, text, color in leaf:
        fmt = fmts[f'hdr_{color}']
        ws.merge_range(hRow3, col, hRow4, col, text, fmt)

    # NPA sub-headers: col 22 = NPA DEMAND (merged), 23-24 = "90+ Days" split
    ws.merge_range(hRow3, 22, hRow4, 22, 'DEMAND', fmts['hdr_orange'])
    # "90+ Days" spans cols 23-24 at hRow3, then splits at hRow4
    ws.merge_range(hRow3, 23, hRow3, 24, '90+ Days', fmts['hdr_orange'])
    ws.write(hRow4, 23, 'ACCOUNT', fmts['hdr_green'])
    ws.write(hRow4, 24, 'AMOUNT', fmts['hdr_green'])


# ---------------------------------------------------------------------------
# Write one section (Region/Division/Area/Branch/Product) - 21-col layout
# ---------------------------------------------------------------------------
def _write_complex_section(ws, pc, filter_type, scope, product, start_row,
                           report_date, fmts, label, hourly_mode=False):
    """Write one combined section. Returns the row after the section ends."""
    if filter_type == 'All_Region':
        level = 'REGION'
    elif filter_type == 'All_Division':
        level = 'DIVISION'
    elif filter_type == 'All_Area':
        level = 'AREA'
    elif filter_type == 'All_Branch':
        level = 'BRANCH'
    else:
        level = filter_type.upper()

    data, gt_rows = _query(pc, filter_type, scope, product)
    if len(data) == 0:
        return start_row

    # Column mapping depends on daily vs hourly mode.
    # Daily: reg_collection (FTOD, DPD Group excludes 1-30)
    # Hourly: hourly_reg_collection (DPD Days=0 Days, DPD Group=1-30, Remark2=Full Collected)
    # Hourly NPA: Loan Status-Last Month=NPA only (no dpd_last/dpd_not_blank filters)
    if hourly_mode:
        # Hourly VBA SyncAllColumns uses CURRENT DPD Days + has_collection
        reg_d, reg_c = 'reg_demand', 'hourly_reg_collection'
        d130, c130 = 'dem_130', 'hourly_col_130'
        d3160, c3160 = 'dem_3160', 'hourly_col_3160'
        pnpa_d, pnpa_c = 'pnpa_demand', 'hourly_pnpa_collection'
        npa_cases_col = 'npa_cases'  # NPA demand kept from daily
        npa_aa, npa_aam = 'npa_hourly_acc', 'npa_hourly_amt'
    else:
        reg_d, reg_c = 'reg_demand', 'reg_collection'
        d130, c130 = 'dem_130', 'col_130'
        d3160, c3160 = 'dem_3160', 'col_3160'
        pnpa_d, pnpa_c = 'pnpa_demand', 'pnpa_collection'
        npa_cases_col = 'npa_cases'
        npa_aa, npa_aam = 'npa_act_acc', 'npa_act_amt'

    row = start_row

    # Title row
    title_text = f"{level} - WISE COLLECTION REPORT - as on {report_date} {label}"
    ws.merge_range(row, 1, row, MAX_COL, title_text, fmts['title'])
    row += 1

    # Section headers
    hRow2 = row
    hRow3 = row + 1
    hRow4 = row + 2

    # NAME (merge hRow2:hRow3, col 1)
    ws.merge_range(hRow2, 1, hRow3, 1, f'{level} NAME', fmts['hdr_grand'])

    # Group headers
    ws.merge_range(hRow2, 2, hRow2, 5, 'REGULAR DEMAND VS COLLECTION', fmts['hdr_orange'])
    ws.merge_range(hRow2, 6, hRow2, 9, '1-30 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 10, hRow2, 13, '31-60 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 14, hRow2, 17, 'PNPA', fmts['hdr_orange'])
    ws.merge_range(hRow2, 18, hRow2, 21, '1-90 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 22, hRow2, 24, 'NPA', fmts['hdr_orange'])

    # Leaf headers
    _write_leaf_headers(ws, hRow3, hRow4, fmts)

    row = hRow4 + 1

    # Data rows
    for _, r_data in data.iterrows():
        _write_data_row(ws, row, r_data['group_value'], r_data, fmts, False,
                        reg_d, reg_c, d130, c130, d3160, c3160,
                        pnpa_d, pnpa_c, npa_cases_col, npa_aa, npa_aam,
                        hourly_mode=hourly_mode)
        row += 1

    # Grand Total
    if len(gt_rows) > 0:
        gt = gt_rows.iloc[0]
        _write_grand_total_row(ws, row, gt, fmts,
                               reg_d, reg_c, d130, c130, d3160, c3160,
                               pnpa_d, pnpa_c, npa_cases_col, npa_aa, npa_aam,
                               hourly_mode=hourly_mode)
    else:
        ws.write(row, 1, 'Grand Total', fmts['gt_name'])
        for c in range(2, MAX_COL + 1):
            ws.write(row, c, 0, fmts['gt_num'])
    row += 1

    return row


# ---------------------------------------------------------------------------
# Data row writer (21-col layout, no ON DATE columns)
# ---------------------------------------------------------------------------
def _write_data_row(ws, row, name, d, fmts, is_officer,
                    reg_d, reg_c, d130, c130, d3160, c3160,
                    pnpa_d, pnpa_c, npa_cases_col, npa_aa, npa_aam,
                    emp_id='', officer_name='', hourly_mode=False):
    """Write one data row with all metric columns (cols 2-20).

    In hourly_mode, DEMAND = FTOD (remaining balance from EOD = reg_demand - reg_collection).
    The hourly VBA does C=E (DEMAND=FTOD), then repopulates COLLECTION from hourly pivot.
    """
    rd = _safe_val(d.get(reg_d, 0))
    rc = _safe_val(d.get(reg_c, 0))
    if hourly_mode:
        # Hourly DEMAND = EOD FTOD = reg_demand - reg_collection (daily collection)
        eod_rc = _safe_val(d.get('reg_collection', 0))
        rd = rd - eod_rc
    dm130 = _safe_val(d.get(d130, 0))
    cm130 = _safe_val(d.get(c130, 0))
    dm3160 = _safe_val(d.get(d3160, 0))
    cm3160 = _safe_val(d.get(c3160, 0))
    pd_val = _safe_val(d.get(pnpa_d, 0))
    pc_val = _safe_val(d.get(pnpa_c, 0))
    nc = _safe_val(d.get(npa_cases_col, 0))
    aa = _safe_val(d.get(npa_aa, 0))
    aam = _safe_val(d.get(npa_aam, 0))

    if is_officer:
        name_fmt = fmts['off_name']
        num_fmt = fmts['off_num']
        pct_fmt = fmts['off_pct']
        if emp_id:
            ws.write(row, 0, emp_id, fmts['off_empid'])
        ws.write(row, 1, f'  {officer_name}' if officer_name else f'  {name}', name_fmt)
    else:
        name_fmt = fmts['name_orange']
        num_fmt = fmts['num_orange']
        pct_fmt = fmts['pct_yellow']
        ws.write(row, 1, name, name_fmt)

    green_fmt = fmts['num_green'] if not is_officer else fmts['off_num']
    green_npa = fmts['num_green_npa'] if not is_officer else fmts['off_num']

    # REGULAR (cols 2-5)
    ws.write_number(row, 2, rd, num_fmt)
    ws.write_number(row, 3, rc, green_fmt)
    ws.write_number(row, 4, rd - rc, num_fmt)  # FTOD
    ws.write_number(row, 5, _safe_pct(rc, rd), pct_fmt)

    # 1-30 DPD (cols 6-9)
    ws.write_number(row, 6, dm130, num_fmt)
    ws.write_number(row, 7, cm130, green_fmt)
    ws.write_number(row, 8, dm130 - cm130, num_fmt)
    ws.write_number(row, 9, _safe_pct(cm130, dm130), pct_fmt)

    # 31-60 DPD (cols 10-13)
    ws.write_number(row, 10, dm3160, num_fmt)
    ws.write_number(row, 11, cm3160, green_fmt)
    ws.write_number(row, 12, dm3160 - cm3160, num_fmt)
    ws.write_number(row, 13, _safe_pct(cm3160, dm3160), pct_fmt)

    # PNPA (cols 14-17)
    ws.write_number(row, 14, pd_val, num_fmt)
    ws.write_number(row, 15, pc_val, green_fmt)
    ws.write_number(row, 16, pd_val - pc_val, num_fmt)
    ws.write_number(row, 17, _safe_pct(pc_val, pd_val), pct_fmt)

    # 1-90 DPD (cols 18-21): sum of 1-30 + 31-60 + PNPA
    d190 = dm130 + dm3160 + pd_val
    c190 = cm130 + cm3160 + pc_val
    ws.write_number(row, 18, d190, num_fmt)
    ws.write_number(row, 19, c190, green_fmt)
    ws.write_number(row, 20, d190 - c190, num_fmt)
    ws.write_number(row, 21, _safe_pct(c190, d190), pct_fmt)

    # NPA (cols 22-24)
    ws.write_number(row, 22, nc, num_fmt)
    ws.write_number(row, 23, aa, green_npa)
    ws.write_number(row, 24, aam, green_npa)


def _write_grand_total_row(ws, row, gt, fmts,
                           reg_d, reg_c, d130, c130, d3160, c3160,
                           pnpa_d, pnpa_c, npa_cases_col, npa_aa, npa_aam,
                           hourly_mode=False, hourly_adjust_demand=True):
    """Write Grand Total row (cols 2-20)."""
    rd = _safe_val(gt.get(reg_d, 0))
    rc = _safe_val(gt.get(reg_c, 0))
    if hourly_mode and hourly_adjust_demand:
        eod_rc = _safe_val(gt.get('reg_collection', 0))
        rd = rd - eod_rc
    dm130 = _safe_val(gt.get(d130, 0))
    cm130 = _safe_val(gt.get(c130, 0))
    dm3160 = _safe_val(gt.get(d3160, 0))
    cm3160 = _safe_val(gt.get(c3160, 0))
    pd_val = _safe_val(gt.get(pnpa_d, 0))
    pc_val = _safe_val(gt.get(pnpa_c, 0))
    nc = _safe_val(gt.get(npa_cases_col, 0))
    aa = _safe_val(gt.get(npa_aa, 0))
    aam = _safe_val(gt.get(npa_aam, 0))

    gn = fmts['gt_num']
    gp = fmts['gt_pct']

    ws.write(row, 1, 'Grand Total', fmts['gt_name'])

    # REGULAR (cols 2-5)
    ws.write_number(row, 2, rd, gn)
    ws.write_number(row, 3, rc, gn)
    ws.write_number(row, 4, rd - rc, gn)
    ws.write_number(row, 5, _safe_pct(rc, rd), gp)

    # 1-30 DPD (cols 6-9)
    ws.write_number(row, 6, dm130, gn)
    ws.write_number(row, 7, cm130, gn)
    ws.write_number(row, 8, dm130 - cm130, gn)
    ws.write_number(row, 9, _safe_pct(cm130, dm130), gp)

    # 31-60 DPD (cols 10-13)
    ws.write_number(row, 10, dm3160, gn)
    ws.write_number(row, 11, cm3160, gn)
    ws.write_number(row, 12, dm3160 - cm3160, gn)
    ws.write_number(row, 13, _safe_pct(cm3160, dm3160), gp)

    # PNPA (cols 14-17)
    ws.write_number(row, 14, pd_val, gn)
    ws.write_number(row, 15, pc_val, gn)
    ws.write_number(row, 16, pd_val - pc_val, gn)
    ws.write_number(row, 17, _safe_pct(pc_val, pd_val), gp)

    # 1-90 DPD (cols 18-21): sum of 1-30 + 31-60 + PNPA
    d190 = dm130 + dm3160 + pd_val
    c190 = cm130 + cm3160 + pc_val
    ws.write_number(row, 18, d190, gn)
    ws.write_number(row, 19, c190, gn)
    ws.write_number(row, 20, d190 - c190, gn)
    ws.write_number(row, 21, _safe_pct(c190, d190), gp)

    # NPA (cols 22-24)
    ws.write_number(row, 22, nc, gn)
    ws.write_number(row, 23, aa, gn)
    ws.write_number(row, 24, aam, gn)


# ---------------------------------------------------------------------------
# Branch + Officer section (21-col layout)
# ---------------------------------------------------------------------------
def _write_branch_officer_section(ws, pc, scope, start_row, report_date, fmts, label,
                                  hourly_mode=False, product='ALL'):
    """Write Branch + Officer Name hierarchical table.

    product : 'ALL' (default) or a product code ('VVY' for the IL Reports sheet).
              Filters the branch/officer data rows to that product.
    """
    row = start_row

    # Title
    ws.merge_range(row, 0, row, MAX_COL,
                   'BRANCH + OFFICER NAME WISE COLLECTION REPORT',
                   fmts['title_grey'])
    row += 2  # Blank row after grey title (matches correct report)

    # Sub-title: B+O section uses date without time suffix (matches correct report)
    bo_date = report_date.split('@')[0].strip() if '@' in report_date else report_date
    ws.merge_range(row, 0, row, MAX_COL,
                   f'BRANCH + OFFICER NAME - WISE COLLECTION REPORT - as on {bo_date} {label}',
                   fmts['title'])
    row += 1

    hRow2 = row
    hRow3 = row + 1
    hRow4 = row + 2

    # EMP ID header (col 0)
    ws.merge_range(hRow2, 0, hRow4, 0, 'EMP ID', fmts['hdr_grand'])
    # NAME header (col 1)
    ws.merge_range(hRow2, 1, hRow3, 1, 'BRANCH / OFFICER NAME', fmts['hdr_grand'])

    # Group headers (same as complex section)
    ws.merge_range(hRow2, 2, hRow2, 5, 'REGULAR DEMAND VS COLLECTION', fmts['hdr_orange'])
    ws.merge_range(hRow2, 6, hRow2, 9, '1-30 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 10, hRow2, 13, '31-60 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 14, hRow2, 17, 'PNPA', fmts['hdr_orange'])
    ws.merge_range(hRow2, 18, hRow2, 21, '1-90 DPD', fmts['hdr_orange'])
    ws.merge_range(hRow2, 22, hRow2, 24, 'NPA', fmts['hdr_orange'])

    _write_leaf_headers(ws, hRow3, hRow4, fmts)

    row = hRow4 + 1

    # Get branch-level data (B+O section: alphabetical sort, matching report_builder.py)
    branch_data, branch_gt = _query(pc, 'All_Branch', scope, product)
    branch_data = branch_data.sort_values('group_value')  # alphabetical for B+O
    # Get emp-level data grouped by branch
    emp_mask = (
        (pc['filter_type'] == 'BranchName') &
        (pc['scope'] == scope) &
        (pc['product'] == product) &
        (pc['group_value'] != 'Grand Total')
    )
    emp_data = pc[emp_mask].copy()

    # For FY scope, include officers that exist in OA but are missing from FY
    if scope == 'FY':
        oa_emp_mask = (
            (pc['filter_type'] == 'BranchName') &
            (pc['scope'] == 'OA') &
            (pc['product'] == product) &
            (pc['group_value'] != 'Grand Total')
        )
        oa_emps = pc[oa_emp_mask].copy()
        fy_keys = set(zip(emp_data['filter_value'], emp_data['group_value']))
        missing = oa_emps[~oa_emps.apply(lambda r: (r['filter_value'], r['group_value']) in fy_keys, axis=1)]
        if len(missing) > 0:
            zero_rows = missing.copy()
            zero_rows['scope'] = 'FY'
            metric_cols_all = [c for c in zero_rows.columns if c.startswith(('reg_', 'dem_', 'col_', 'pnpa_', 'npa_', 'od_'))]
            zero_rows[metric_cols_all] = 0
            emp_data = pd.concat([emp_data, zero_rows], ignore_index=True)

    # Build officer name lookup
    emp_all_mask = (
        (pc['filter_type'] == 'All_EmpID') &
        (pc['scope'] == 'OA') &
        (pc['product'] == 'ALL') &
        (pc['group_value'] != 'Grand Total')
    )
    emp_name_df = pc[emp_all_mask][['group_value', 'officer_name']].drop_duplicates()
    emp_name_map = dict(zip(emp_name_df['group_value'], emp_name_df['officer_name']))

    for _, br in branch_data.iterrows():
        branch_name = br['group_value']

        # Branch subtotal row (blue)
        ws.write(row, 1, branch_name, fmts['br_name'])
        ws.write(row, 0, '', fmts['br_name'])
        _write_row_values(ws, row, br, fmts, is_branch=True, hourly_mode=hourly_mode)
        row += 1

        # Officer rows under this branch (sorted by EMP ID, matching correct report)
        officers = emp_data[emp_data['filter_value'] == branch_name].sort_values('group_value')
        for _, off in officers.iterrows():
            emp_id = off['group_value']
            off_name = emp_name_map.get(emp_id, off.get('officer_name', ''))

            ws.write(row, 0, emp_id, fmts['off_empid'])
            ws.write(row, 1, f'  {off_name}', fmts['off_name'])
            _write_row_values(ws, row, off, fmts, is_branch=False, hourly_mode=hourly_mode)
            row += 1

    # Grand Total (B+O section): hourly uses FTOD-adjusted demand (reg_demand - reg_collection),
    # daily uses reg_demand_total (full portfolio).
    if hourly_mode:
        bo_reg_d = 'reg_demand'
        bo_reg_c = 'hourly_reg_collection'
        bo_npa_cases = 'npa_cases'  # NPA demand kept from daily
        bo_npa_aa, bo_npa_aam = 'npa_hourly_acc', 'npa_hourly_amt'
    else:
        bo_reg_d = 'reg_demand_total'
        bo_reg_c = 'reg_collection'
        bo_npa_cases = 'npa_cases'
        bo_npa_aa, bo_npa_aam = 'npa_act_acc', 'npa_act_amt'
    if len(branch_gt) > 0:
        gt = branch_gt.iloc[0]
        bo_c130 = 'hourly_col_130' if hourly_mode else 'col_130'
        bo_c3160 = 'hourly_col_3160' if hourly_mode else 'col_3160'
        bo_pnpa_c = 'hourly_pnpa_collection' if hourly_mode else 'pnpa_collection'
        _write_grand_total_row(ws, row, gt, fmts,
                               bo_reg_d, bo_reg_c,
                               'dem_130', bo_c130, 'dem_3160', bo_c3160,
                               'pnpa_demand', bo_pnpa_c,
                               bo_npa_cases, bo_npa_aa, bo_npa_aam,
                               hourly_mode=hourly_mode,
                               hourly_adjust_demand=True)
        row += 1

    return row


def _write_row_values(ws, row, d, fmts, is_branch=False, hourly_mode=False):
    """Write metric values for a branch/officer row (cols 2-20).

    Uses reg_demand_total (total portfolio demand, not FTOD-filtered)
    for the demand column to match the correct Daily Collection Report.
    """
    if is_branch:
        nf, pf = fmts['br_num'], fmts['br_pct']
    else:
        nf, pf = fmts['off_num'], fmts['off_pct']

    # B+O section demand: in hourly mode use reg_demand (no adjustment);
    # in daily mode use reg_demand_total (full portfolio).
    if hourly_mode:
        rd = _safe_val(d.get('reg_demand', 0))
        eod_rc = _safe_val(d.get('reg_collection', 0))
        rd = rd - eod_rc  # FTOD adjustment: outstanding accounts only
        rc_col = 'hourly_reg_collection'
        c130_col, c3160_col, pnpac_col = 'hourly_col_130', 'hourly_col_3160', 'hourly_pnpa_collection'
    else:
        rd = _safe_val(d.get('reg_demand_total', d.get('reg_demand', 0)))
        rc_col = 'reg_collection'
        c130_col, c3160_col, pnpac_col = 'col_130', 'col_3160', 'pnpa_collection'
    rc = _safe_val(d.get(rc_col, 0))
    dm130 = _safe_val(d.get('dem_130', 0))
    cm130 = _safe_val(d.get(c130_col, 0))
    dm3160 = _safe_val(d.get('dem_3160', 0))
    cm3160 = _safe_val(d.get(c3160_col, 0))
    pd_val = _safe_val(d.get('pnpa_demand', 0))
    pc_val = _safe_val(d.get(pnpac_col, 0))
    if hourly_mode:
        nc = _safe_val(d.get('npa_cases', 0))  # NPA demand kept from daily
        aa = _safe_val(d.get('npa_hourly_acc', 0))
        aam = _safe_val(d.get('npa_hourly_amt', 0))
    else:
        nc = _safe_val(d.get('npa_cases', 0))
        aa = _safe_val(d.get('npa_act_acc', 0))
        aam = _safe_val(d.get('npa_act_amt', 0))

    # REGULAR (cols 2-5)
    ws.write_number(row, 2, rd, nf)
    ws.write_number(row, 3, rc, nf)
    ws.write_number(row, 4, rd - rc, nf)
    ws.write_number(row, 5, _safe_pct(rc, rd), pf)

    # 1-30 DPD (cols 6-9)
    ws.write_number(row, 6, dm130, nf)
    ws.write_number(row, 7, cm130, nf)
    ws.write_number(row, 8, dm130 - cm130, nf)
    ws.write_number(row, 9, _safe_pct(cm130, dm130), pf)

    # 31-60 DPD (cols 10-13)
    ws.write_number(row, 10, dm3160, nf)
    ws.write_number(row, 11, cm3160, nf)
    ws.write_number(row, 12, dm3160 - cm3160, nf)
    ws.write_number(row, 13, _safe_pct(cm3160, dm3160), pf)

    # PNPA (cols 14-17)
    ws.write_number(row, 14, pd_val, nf)
    ws.write_number(row, 15, pc_val, nf)
    ws.write_number(row, 16, pd_val - pc_val, nf)
    ws.write_number(row, 17, _safe_pct(pc_val, pd_val), pf)

    # 1-90 DPD (cols 18-21): sum of 1-30 + 31-60 + PNPA
    d190 = dm130 + dm3160 + pd_val
    c190 = cm130 + cm3160 + pc_val
    ws.write_number(row, 18, d190, nf)
    ws.write_number(row, 19, c190, nf)
    ws.write_number(row, 20, d190 - c190, nf)
    ws.write_number(row, 21, _safe_pct(c190, d190), pf)

    # NPA (cols 22-24)
    ws.write_number(row, 22, nc, nf)
    ws.write_number(row, 23, aa, nf)
    ws.write_number(row, 24, aam, nf)


# ---------------------------------------------------------------------------
# Main sheet builder (OverAll or FY) - 21-col layout, no ON DATE, no Amount
# ---------------------------------------------------------------------------
def _build_main_sheet(ws, pc, scope, report_date, has_officer, fmts, label,
                      hourly_mode=False, eod_date_str=None):
    """Build the OverAll or FY sheet with all sections."""
    ws.set_column(0, 0, 2)    # Col A narrow
    ws.set_column(1, 1, 18)   # Col B (Name)
    ws.set_column(2, MAX_COL, 12)  # Data cols

    row = 1  # Start at row 1 (0-indexed)

    # Region section
    row = _write_complex_section(ws, pc, 'All_Region', scope, 'ALL', row,
                                 report_date, fmts, label, hourly_mode=hourly_mode)
    row += 3  # 3 blank rows between sections (matches correct report)

    # Division section
    row = _write_complex_section(ws, pc, 'All_Division', scope, 'ALL', row,
                                 report_date, fmts, label, hourly_mode=hourly_mode)
    row += 3

    # Area section
    row = _write_complex_section(ws, pc, 'All_Area', scope, 'ALL', row,
                                 report_date, fmts, label, hourly_mode=hourly_mode)
    row += 3

    # Branch section
    row = _write_complex_section(ws, pc, 'All_Branch', scope, 'ALL', row,
                                 report_date, fmts, label, hourly_mode=hourly_mode)
    row += 3

    # Product sections (IGL, FIG, IL/VVY)
    for display_name, filter_product in [('IGL', 'IGL'), ('FIG', 'FIG'), ('IL', 'VVY')]:
        ws.merge_range(row, 1, row, MAX_COL,
                       f'REGION WISE - {display_name} REPORT',
                       fmts['title_grey'])
        row += 2  # Blank row after grey title (matches correct report)
        row = _write_complex_section(ws, pc, 'All_Region', scope, filter_product, row,
                                     report_date, fmts, label, hourly_mode=hourly_mode)
        row += 3

    # Branch + Officer section
    if has_officer:
        row = _write_branch_officer_section(ws, pc, scope, row, report_date, fmts, label,
                                            hourly_mode=hourly_mode)


# ---------------------------------------------------------------------------
# IL Reports sheet - OverAll layout filtered to IL (VVY) product only
# ---------------------------------------------------------------------------
def _build_il_sheet(ws, pc, scope, report_date, has_officer, fmts, label,
                    hourly_mode=False):
    """Build the IL Reports sheet — same 21-col layout as the main sheet,
    every section filtered to the IL (VVY) product. No per-product breakdowns.
    """
    ws.set_column(0, 0, 2)
    ws.set_column(1, 1, 18)
    ws.set_column(2, MAX_COL, 12)

    row = 1

    for filter_type in ['All_Region', 'All_Division', 'All_Area', 'All_Branch']:
        row = _write_complex_section(ws, pc, filter_type, scope, 'VVY', row,
                                     report_date, fmts, label, hourly_mode=hourly_mode)
        row += 3

    # Branch + Officer section (IL only)
    if has_officer:
        row = _write_branch_officer_section(ws, pc, scope, row, report_date, fmts, label,
                                            hourly_mode=hourly_mode, product='VVY')


# ---------------------------------------------------------------------------
# On-Date sheet - ALL sections with DEMAND/COLLECTION/COLLECTION% (5 cols)
# ---------------------------------------------------------------------------
def _build_ondate_sheet(ws, pc, scope, report_date, has_officer, fmts,
                        title='Today On-Date Demand Report', label='',
                        report_date_full=None):
    """Build On-Date sheet with all sections (Region, Division, Area, Branch,
    IGL, FIG, IL, Branch+Officer) using 5 columns.

    report_date_full: The full report date string including time (e.g. '24-03-2026 @ 4:00 PM')
                      used in section titles. Falls back to report_date if not provided.
    """
    # Section titles use the full report date (with time), not the on-date date
    section_date = report_date_full if report_date_full else report_date
    ws.set_column(0, 0, 2)
    ws.set_column(1, 1, 18)
    ws.set_column(2, 4, 14)

    # Title spans the data table (B:E) so it centers over the report, matching
    # the section headers below — column A is a 2-wide spacer.
    ws.merge_range(0, 1, 0, 4, title, fmts['title'])

    row = 1

    # Region / Division / Area / Branch sections (pass label for section header suffix)
    # Section titles use full report date (with time), On-Date data uses report_date
    for filter_type, level in [('All_Region', 'REGION'), ('All_Division', 'DIVISION'),
                                ('All_Area', 'AREA'), ('All_Branch', 'BRANCH')]:
        row = _write_ondate_section(ws, pc, filter_type, scope, 'ALL', row,
                                    section_date, level, fmts, label=label)
        row += 3  # 3 blank rows between sections (matches correct report)

    # Product sections (IGL, FIG, IL)
    for display_name, filter_product in [('IGL', 'IGL'), ('FIG', 'FIG'), ('IL', 'VVY')]:
        ws.merge_range(row, 1, row, 4,
                       f'REGION WISE - {display_name} REPORT',
                       fmts['title_grey'])
        row += 2  # Blank row after grey title (matches correct report)
        row = _write_ondate_section(ws, pc, 'All_Region', scope, filter_product, row,
                                    section_date, 'REGION', fmts, label=label)
        row += 3

    # Branch + Officer section
    if has_officer:
        row = _write_ondate_branch_officer(ws, pc, scope, row, section_date, fmts,
                                           label=label)


def _write_od_triplet(ws, row, demand, collection, f_dem, f_coll, f_pct):
    """Write the On-Date DEMAND / COLLECTION / COLLECTION% cells (cols C:E).

    demand == 0 → '-' / 0 / '-'.
    demand > 0  → demand / collection / (collection / demand).
    """
    if demand == 0:
        ws.write_string(row, 2, '-', f_dem)
        ws.write_number(row, 3, 0, f_coll)
        ws.write_string(row, 4, '-', f_pct)
    else:
        ws.write_number(row, 2, demand, f_dem)
        ws.write_number(row, 3, collection, f_coll)
        ws.write_number(row, 4, collection / demand, f_pct)


def _write_ondate_section(ws, pc, filter_type, scope, product, start_row,
                          report_date, level, fmts, label=''):
    """Write a 5-column on-date section using od_demand/od_collection."""
    data, gt_rows = _query(pc, filter_type, scope, product)
    # On-Date sections use alphabetical sort (matching correct report)
    data = data.sort_values('group_value')
    if len(data) == 0:
        return start_row

    row = start_row

    # Title with optional sheet label suffix (e.g., "(OverAll)" or "(FY_25-26)").
    # No "as on DATE" here — the On-Date sheet is narrow (5 cols) and the date is
    # already shown in the sheet title row above; including it overflows the cell.
    title_suffix = f' {label}' if label else ''
    ws.merge_range(row, 1, row, 4,
                   f'{level} - WISE COLLECTION REPORT{title_suffix}',
                   fmts['title'])
    row += 1

    # Group header
    ws.write(row, 1, f'{level} NAME', fmts['hdr_grand'])
    ws.merge_range(row, 2, row, 4, 'ON DATE Demand Vs Collection', fmts['hdr_orange'])
    row += 1

    # Sub-headers
    ws.write(row, 2, 'DEMAND', fmts['hdr_orange'])
    ws.write(row, 3, 'COLLECTION', fmts['hdr_green'])
    ws.write(row, 4, 'COLLECTION %', fmts['hdr_yellow'])
    row += 1

    # Blank row after headers
    row += 1

    # On-Date: DEMAND = od_demand_next (next-day demand),
    # COLLECTION = od_collection_next (next-day Full-EMI-Paid collection).
    for _, d in data.iterrows():
        od = _safe_val(d.get('od_demand_next', 0))
        oc = _safe_val(d.get('od_collection_next', 0))
        ws.write(row, 1, d['group_value'], fmts['name_orange'])
        _write_od_triplet(ws, row, od, oc,
                          fmts['num_orange'], fmts['num_green'], fmts['pct_yellow'])
        row += 1

    # Grand Total
    if len(gt_rows) > 0:
        gt = gt_rows.iloc[0]
        od = _safe_val(gt.get('od_demand_next', 0))
        oc = _safe_val(gt.get('od_collection_next', 0))
        ws.write(row, 1, 'Grand Total', fmts['gt_name'])
        _write_od_triplet(ws, row, od, oc,
                          fmts['gt_num'], fmts['gt_num'], fmts['gt_pct'])
        row += 1

    return row


def _write_ondate_branch_officer(ws, pc, scope, start_row, report_date, fmts,
                                  label=''):
    """Write Branch + Officer on-date section (5 cols)."""
    row = start_row

    ws.merge_range(row, 0, row, 4,
                   'BRANCH + OFFICER NAME WISE COLLECTION REPORT',
                   fmts['title_grey'])
    row += 2  # Blank row after grey title (matches correct report)

    # No "as on DATE" — date is in the sheet title row; keeps the narrow
    # On-Date sheet from clipping the header.
    label_suffix = f' {label}' if label else ''
    ws.merge_range(row, 0, row, 4,
                   f'BRANCH + OFFICER NAME - WISE COLLECTION REPORT{label_suffix}',
                   fmts['title'])
    row += 1

    # Headers (2-row structure matching correct report)
    ws.merge_range(row, 0, row + 1, 0, 'EMP ID', fmts['hdr_grand'])
    ws.write(row, 1, 'BRANCH / OFFICER NAME', fmts['hdr_grand'])
    ws.merge_range(row, 2, row, 4, 'ON DATE Demand Vs Collection', fmts['hdr_orange'])
    row += 1
    ws.write(row, 1, '', fmts['hdr_grand'])
    ws.write(row, 2, 'DEMAND', fmts['hdr_orange'])
    ws.write(row, 3, 'COLLECTION', fmts['hdr_green'])
    ws.write(row, 4, 'COLLECTION %', fmts['hdr_yellow'])
    row += 1
    # Blank row after headers
    row += 1

    # Branch data (On-Date B+O: alphabetical sort for branches, EMP ID for officers)
    branch_data, branch_gt = _query(pc, 'All_Branch', scope, 'ALL')
    branch_data = branch_data.sort_values('group_value')  # alphabetical for B+O
    emp_mask = (
        (pc['filter_type'] == 'BranchName') &
        (pc['scope'] == scope) &
        (pc['product'] == 'ALL') &
        (pc['group_value'] != 'Grand Total')
    )
    emp_data = pc[emp_mask].copy()

    # For FY scope, include officers from OA that are missing
    if scope == 'FY':
        oa_emp_mask = (
            (pc['filter_type'] == 'BranchName') &
            (pc['scope'] == 'OA') &
            (pc['product'] == 'ALL') &
            (pc['group_value'] != 'Grand Total')
        )
        oa_emps = pc[oa_emp_mask].copy()
        fy_keys = set(zip(emp_data['filter_value'], emp_data['group_value']))
        missing = oa_emps[~oa_emps.apply(lambda r: (r['filter_value'], r['group_value']) in fy_keys, axis=1)]
        if len(missing) > 0:
            zero_rows = missing.copy()
            zero_rows['scope'] = 'FY'
            metric_cols_all = [c for c in zero_rows.columns if c.startswith(('reg_', 'dem_', 'col_', 'pnpa_', 'npa_', 'od_'))]
            zero_rows[metric_cols_all] = 0
            emp_data = pd.concat([emp_data, zero_rows], ignore_index=True)

    # Officer name lookup
    emp_all_mask = (
        (pc['filter_type'] == 'All_EmpID') &
        (pc['scope'] == 'OA') &
        (pc['product'] == 'ALL') &
        (pc['group_value'] != 'Grand Total')
    )
    emp_name_df = pc[emp_all_mask][['group_value', 'officer_name']].drop_duplicates()
    emp_name_map = dict(zip(emp_name_df['group_value'], emp_name_df['officer_name']))

    # On-Date: DEMAND = od_demand_next, COLLECTION = od_collection_next.
    for _, br in branch_data.iterrows():
        branch_name = br['group_value']
        od = _safe_val(br.get('od_demand_next', 0))
        oc = _safe_val(br.get('od_collection_next', 0))

        ws.write(row, 0, '', fmts['br_name'])
        ws.write(row, 1, branch_name, fmts['br_name'])
        _write_od_triplet(ws, row, od, oc,
                          fmts['br_num'], fmts['br_num'], fmts['br_pct'])
        row += 1

        officers = emp_data[emp_data['filter_value'] == branch_name].sort_values('group_value')
        for _, off in officers.iterrows():
            emp_id = off['group_value']
            off_name = emp_name_map.get(emp_id, off.get('officer_name', ''))
            od_off = _safe_val(off.get('od_demand_next', 0))
            oc_off = _safe_val(off.get('od_collection_next', 0))

            ws.write(row, 0, emp_id, fmts['off_empid'])
            ws.write(row, 1, f'  {off_name}', fmts['off_name'])
            _write_od_triplet(ws, row, od_off, oc_off,
                              fmts['off_num'], fmts['off_num'], fmts['off_pct'])
            row += 1

    # Grand Total
    if len(branch_gt) > 0:
        gt = branch_gt.iloc[0]
        od = _safe_val(gt.get('od_demand_next', 0))
        oc = _safe_val(gt.get('od_collection_next', 0))
        ws.write(row, 1, 'Grand Total', fmts['gt_name'])
        _write_od_triplet(ws, row, od, oc,
                          fmts['gt_num'], fmts['gt_num'], fmts['gt_pct'])
        row += 1

    return row
