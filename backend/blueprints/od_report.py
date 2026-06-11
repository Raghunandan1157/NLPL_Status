"""
OD Report Blueprint - OD (Overdue) Report Generation
=====================================================
Migrated from OD_REPORT/server.py into a Flask Blueprint.
All endpoints preserved, paths use config module.
"""

from flask import Blueprint, send_from_directory, jsonify, request, Response
from pathlib import Path
import tempfile
import logging
import json
import os
import re
import threading

import pandas as pd
import xlsxwriter

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
od_report_bp = Blueprint('od_report', __name__)

# ---------------------------------------------------------------------------
# Paths from config
# ---------------------------------------------------------------------------
STATIC_OD_DIR = config.STATIC_DIR / 'od_report'
BACKUP_DATA_DIR = config.OD_BACKUP_DATA_DIR
INS_TEMP_DIR = config.OD_INS_TEMP_DIR
DOWNLOADS_FOLDER = Path.home() / 'Downloads'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_ins_file():
    """Get the insurance file path from temp storage."""
    INS_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in os.listdir(INS_TEMP_DIR) if not f.startswith(".")]
    if files:
        return INS_TEMP_DIR / files[0]
    return None


def _send_sse(data):
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# Month-end OD file may arrive as either the bank's native .xlsb export
# (sheet "Data") or a re-saved .xlsx (sheet "Sheet1"). Accept both.
OD_MONTHEND_EXTS = (".xlsb", ".xlsx")


def get_od_monthend_file():
    """Return the staged month-end OD file path (.xlsb or .xlsx), or None."""
    BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in os.listdir(BACKUP_DATA_DIR)
             if f.lower().endswith(OD_MONTHEND_EXTS)]
    return (BACKUP_DATA_DIR / files[0]) if files else None


def open_excel_fast(path):
    """Open an Excel file as a pandas ExcelFile using the fastest available
    engine: calamine (Rust, ~10x faster than openpyxl) for .xlsx/.xlsb, with a
    graceful fallback to openpyxl/pyxlsb if calamine is unavailable."""
    try:
        return pd.ExcelFile(str(path), engine="calamine")
    except Exception:
        engine = "pyxlsb" if str(path).lower().endswith(".xlsb") else "openpyxl"
        return pd.ExcelFile(str(path), engine=engine)


def read_excel_fast(path, sheet_name=0, **kwargs):
    """Read one sheet with the fastest available engine (calamine, fallback
    openpyxl/pyxlsb). Mirrors pd.read_excel's return for a single sheet."""
    xl = open_excel_fast(path)
    try:
        return xl.parse(sheet_name, **kwargs)
    finally:
        xl.close()


def read_od_monthend(path):
    """Read the month-end OD workbook regardless of format.

    Uses the fastest engine and the first matching sheet from "Data"/"Sheet1",
    falling back to the first sheet in the book.
    """
    xl = open_excel_fast(path)
    try:
        sheet = next((s for s in ("Data", "Sheet1") if s in xl.sheet_names),
                     xl.sheet_names[0])
        return xl.parse(sheet)
    finally:
        xl.close()


# ---------------------------------------------------------------------------
# ESAF OD Report summary sheet
# ---------------------------------------------------------------------------
# Rebuilds the multi-section "ESAF OD Report" dashboard sheet that the bank's
# native export carries, so our generated workbook matches it. Eight pivot
# blocks, all derived from the processed Sheet1 data:
#   Region/Bucket OD · Region FTOD · Region Early-Delinquency · Region/Bucket
#   Non-Starters · Region/Bucket FY-disbursement.
# Amount columns ('OD' = TotalArrear, 'POS' = PrincipalOS) are already stored
# per-row in crores, so block amounts are a plain sum rounded to 2 decimals.

# This is a faithful Python/openpyxl port of the bank's "CreateODReport" VBA
# macro (static/od_report/script.js), so the generated sheet matches the
# native export cell-for-cell: same 8 pivot blocks, dynamic row positions
# (each section starts 2 rows below the taller of the two blocks above it),
# fill colours, header label casing, number formats, thin borders and fonts.

_BUCKET_ORDER = [
    '1: 1-30', '2: 31-60', '3: 61-90', '4: 91-120',
    '5: 121-180', '6: 181-365', '7: >365 Days',
]
_MONTHS = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
           'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# Fill colours (ARGB) — exactly as RGB() in the VBA macro.
_AMBER = 'FFFFC000'   # title + every Grand Total row
_BLUE = 'FFD6E4F0'    # all header rows + Region OD / Region FTOD data
_GREEN = 'FFC6EFCE'   # Region Non-Starters data
_PEACH = 'FFFCE4D6'   # Region FY-disbursement data


def _find_col(df, *names):
    """Case-insensitive column lookup (mirrors the VBA LCase header match)."""
    lower = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def _agg_rows(df, mask, key_col, keys, od_col, pos_col):
    """Return [(key, acc, od, pos), ...] for `keys`, summing OD/POS in `mask`."""
    d = df[mask]
    if key_col is not None and key_col in d.columns:
        keyser = d[key_col].astype(str).str.strip()
    else:
        keyser = pd.Series([], dtype=str)
    od = (pd.to_numeric(d[od_col], errors='coerce').fillna(0.0)
          if od_col and od_col in d.columns else pd.Series(0.0, index=d.index))
    pos = (pd.to_numeric(d[pos_col], errors='coerce').fillna(0.0)
           if pos_col and pos_col in d.columns else pd.Series(0.0, index=d.index))
    out = []
    for k in keys:
        m = keyser == k
        out.append((k, int(m.sum()), float(od[m].sum()), float(pos[m].sum())))
    return out


def _xw_fmt(wb, cache, **props):
    """Cached xlsxwriter Format with the shared Arial-10 + thin-border base."""
    base = {'font_name': 'Arial', 'font_size': 10, 'border': 1}
    base.update(props)
    key = tuple(sorted(base.items()))
    f = cache.get(key)
    if f is None:
        f = wb.add_format(base)
        cache[key] = f
    return f


def _write_block(ws, wb, cache, top, left, title, key_label, rows, data_fill=None,
                 acc_label='# Acc', title_rows=1):
    """Write one titled pivot block (xlsxwriter) and return its Grand-Total row
    index (1-based, matching the caller's coordinate scheme)."""
    r0, c0 = top - 1, left - 1  # xlsxwriter is 0-indexed

    # Title — merged across the 4 columns, spanning `title_rows` rows.
    title_fmt = _xw_fmt(wb, cache, bold=True, align='center', valign='vcenter',
                        **({'text_wrap': True} if title_rows > 1 else {}))
    ws.merge_range(r0, c0, r0 + title_rows - 1, c0 + 3, title, title_fmt)

    # Header row (light blue).
    hr = r0 + title_rows
    hdr_fmt = _xw_fmt(wb, cache, bold=True, align='center', bg_color='#' + _BLUE[2:])
    for j, h in enumerate((key_label, acc_label, 'OD Amt', 'POS')):
        ws.write(hr, c0 + j, h, hdr_fmt)

    # Data rows (optional body fill).
    fillkw = {'bg_color': '#' + data_fill[2:]} if data_fill else {}
    key_f = _xw_fmt(wb, cache, align='center', **fillkw)
    int_f = _xw_fmt(wb, cache, align='center', num_format='#0', **fillkw)
    dec_f = _xw_fmt(wb, cache, align='center', num_format='0.00', **fillkw)
    rr = hr + 1
    for k, acc, od, pos in rows:
        ws.write(rr, c0, k, key_f)
        ws.write_number(rr, c0 + 1, acc, int_f)
        ws.write_number(rr, c0 + 2, round(od, 2), dec_f)
        ws.write_number(rr, c0 + 3, round(pos, 2), dec_f)
        rr += 1

    # Grand Total row (amber, bold; key cell left-aligned).
    amber = '#' + _AMBER[2:]
    gt_key = _xw_fmt(wb, cache, bold=True, align='left', bg_color=amber)
    gt_int = _xw_fmt(wb, cache, bold=True, align='center', num_format='#0', bg_color=amber)
    gt_dec = _xw_fmt(wb, cache, bold=True, align='center', num_format='0.00', bg_color=amber)
    t_acc = sum(r[1] for r in rows)
    t_od = sum(r[2] for r in rows)
    t_pos = sum(r[3] for r in rows)
    ws.write(rr, c0, 'Grand Total', gt_key)
    ws.write_number(rr, c0 + 1, t_acc, gt_int)
    ws.write_number(rr, c0 + 2, round(t_od, 2), gt_dec)
    ws.write_number(rr, c0 + 3, round(t_pos, 2), gt_dec)
    return rr + 1  # 1-based Grand-Total row


def _build_esaf_summary_sheet(ws, wb, df, date_label, fy_start, fy_end, report_dt):
    """Populate worksheet `ws` (xlsxwriter) with the 8-block ESAF OD Report
    dashboard — a faithful port of the bank's VBA macro."""
    cache = {}
    mm, yyyy = report_dt.month, report_dt.year

    rgn_col = _find_col(df, 'Region')
    dpd_col = _find_col(df, 'DPD Days')
    od_col = _find_col(df, 'OD')
    pos_col = _find_col(df, 'POS', 'POS in Cr', 'PrincipalOS')
    ftod_col = _find_col(df, 'FTOD')
    ns_col = _find_col(df, 'Non starter')
    disb_col = _find_col(df, 'DisbursementDate')

    regions = (sorted({str(r).strip() for r in df[rgn_col].dropna() if str(r).strip()})
               if rgn_col else [])
    buckets = _BUCKET_ORDER

    all_mask = pd.Series(True, index=df.index)
    ftod_mask = (df[ftod_col].astype(str).str.strip() == 'FTOD'
                 if ftod_col else pd.Series(False, index=df.index))
    if ns_col:
        ns_raw = df[ns_col].astype(str).str.strip()
        ns_mask = ns_raw.ne('') & ns_raw.str.lower().ne('nan')
    else:
        ns_mask = pd.Series(False, index=df.index)

    dd = (pd.to_datetime(df[disb_col], errors='coerce')
          if disb_col else pd.Series(pd.NaT, index=df.index))

    # Financial year (Apr–Mar) window.
    fy_label = f"{fy_start.year}-{fy_end.year}"
    fy_mask = (dd >= fy_start) & (dd <= fy_end)

    # Early-delinquency = the 3-month window ending 2 months before the report
    # month (e.g. report May -> Jan..Mar; report Jun -> Feb..Apr).
    ed_end_m, ed_end_y = mm - 2, yyyy
    if ed_end_m <= 0:
        ed_end_m += 12
        ed_end_y -= 1
    ed_start_m, ed_start_y = ed_end_m - 2, ed_end_y
    if ed_start_m <= 0:
        ed_start_m += 12
        ed_start_y -= 1
    ed_start = pd.Timestamp(ed_start_y, ed_start_m, 1)
    ed_end = pd.Timestamp(ed_end_y, ed_end_m, 1) + pd.offsets.MonthEnd(0)
    ed_label = (f"{_MONTHS[ed_start_m]}{ed_start_y % 100:02d} TO "
                f"{_MONTHS[ed_end_m]}{ed_end_y % 100:02d}")
    ed_mask = (dd >= ed_start) & (dd <= ed_end)

    # Main title — merged A1:I1, amber, Arial bold 14, row height 30.
    amber = '#' + _AMBER[2:]
    title_fmt = _xw_fmt(wb, cache, bold=True, font_size=14, align='center',
                        valign='vcenter', bg_color=amber)
    ws.merge_range(0, 0, 0, 8, f"*ESAF Over Due report as on  {date_label}*", title_fmt)
    ws.set_row(0, 30)

    def rgn_rows(mask):
        return _agg_rows(df, mask, rgn_col, regions, od_col, pos_col)

    def bkt_rows(mask):
        return _agg_rows(df, mask, dpd_col, buckets, od_col, pos_col)

    # Section 1 — Region OD (blue) | Region FTOD (blue).
    g1 = _write_block(ws, wb, cache, 2, 1, f"Region Wise OD Summary as on  {date_label}",
                      'Region', rgn_rows(all_mask), data_fill=_BLUE)
    g2 = _write_block(ws, wb, cache, 2, 6, f"Region Wise FTOD Summary as on  {date_label}",
                      'Region', rgn_rows(ftod_mask), data_fill=_BLUE)

    # Section 2 — Bucket OD (white) | Early Delinquency (white, 2-row title).
    p2 = max(g1, g2) + 2
    g3 = _write_block(ws, wb, cache, p2, 1, f"Bucket Wise OD Summary as on  {date_label}",
                      'Bucket', bkt_rows(all_mask))
    g4 = _write_block(ws, wb, cache, p2, 6,
                      f"ESAF Early Delinquency Details as on  {date_label}({ed_label})",
                      'Region', rgn_rows(ed_mask), acc_label='# acc', title_rows=2)

    # Section 3 — Region Non-Starters (green) | Bucket Non-Starters (white).
    p3 = max(g3, g4) + 2
    g5 = _write_block(ws, wb, cache, p3, 1, f"Region Wise Non Starters Summary as on  {date_label}",
                      'Region', rgn_rows(ns_mask), data_fill=_GREEN)
    g6 = _write_block(ws, wb, cache, p3, 6, f"Bucket Wise Non Starters Summary as on  {date_label}",
                      'Bucket', bkt_rows(ns_mask))

    # Section 4 — FY-disbursement OD: Region (peach) | Bucket (white).
    p4 = max(g5, g6) + 2
    _write_block(ws, wb, cache, p4, 1,
                 f"Financial year {fy_label} Disbursement clients OD summary",
                 'Region', rgn_rows(fy_mask), data_fill=_PEACH,
                 acc_label='# acc', title_rows=2)
    _write_block(ws, wb, cache, p4, 6,
                 f"Bucket Wise Financial year {fy_label} Disbursement clients OD summary",
                 'Bucket', bkt_rows(fy_mask), acc_label='# acc', title_rows=2)

    # Column widths (A-D left blocks, E spacer, F-I right blocks) — VBA values.
    ws.set_column(0, 0, 18)   # A
    ws.set_column(1, 3, 12)   # B-D
    ws.set_column(4, 4, 4)    # E spacer
    ws.set_column(5, 5, 18)   # F
    ws.set_column(6, 8, 12)   # G-I


# ---------------------------------------------------------------------------
# Routes - Static files
# ---------------------------------------------------------------------------

@od_report_bp.route('/')
def index():
    """Serve OD Report index.html."""
    return send_from_directory(str(STATIC_OD_DIR), 'index.html')


@od_report_bp.route('/<path:filename>')
def serve_static(filename):
    """Serve OD Report static files (CSS, JS)."""
    return send_from_directory(str(STATIC_OD_DIR), filename)


# ---------------------------------------------------------------------------
# Routes - File checks
# ---------------------------------------------------------------------------

@od_report_bp.route('/check-od')
def check_od():
    """Check if a month-end OD file (.xlsb or .xlsx) exists in BACKUP_DATA."""
    od_path = get_od_monthend_file()
    if od_path:
        return jsonify({"exists": True, "filename": od_path.name})
    return jsonify({"exists": False})


@od_report_bp.route('/check-ins')
def check_ins():
    """Check if an insurance file exists in ins_temp."""
    INS_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in os.listdir(INS_TEMP_DIR) if not f.startswith(".")]
    if files:
        return jsonify({"exists": True, "filename": files[0]})
    return jsonify({"exists": False})


# ---------------------------------------------------------------------------
# Routes - File uploads (OD month-end + Insurance)
# ---------------------------------------------------------------------------

@od_report_bp.route('/upload-od', methods=['POST'])
def upload_od():
    """Save month-end OD file (.xlsb or .xlsx) to BACKUP_DATA directory."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        original_filename = file.filename or "month_end.xlsb"

        BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Clear existing files
        for existing in os.listdir(BACKUP_DATA_DIR):
            os.remove(BACKUP_DATA_DIR / existing)

        save_path = BACKUP_DATA_DIR / original_filename
        file.save(save_path)

        logger.info(f"[OD_REPORT] Saved month-end file: {original_filename}")
        return jsonify({
            "success": True,
            "filename": original_filename,
            "message": f"Saved to BACKUP_DATA/{original_filename}",
        })

    except Exception as e:
        logger.error(f"[OD_REPORT] Error uploading OD file: {e}")
        return jsonify({"error": str(e)}), 500


@od_report_bp.route('/upload-ins', methods=['POST'])
def upload_ins():
    """Save insurance file to ins_temp directory."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        original_filename = file.filename or "insurance.xlsx"

        INS_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        # Clear existing files
        for existing in os.listdir(INS_TEMP_DIR):
            os.remove(INS_TEMP_DIR / existing)

        save_path = INS_TEMP_DIR / original_filename
        file.save(save_path)

        logger.info(f"[OD_REPORT] Saved insurance file: {original_filename}")
        return jsonify({
            "success": True,
            "filename": original_filename,
            "message": f"Insurance file ready: {original_filename}",
        })

    except Exception as e:
        logger.error(f"[OD_REPORT] Error uploading insurance file: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Routes - Main processing (SSE stream)
# ---------------------------------------------------------------------------

@od_report_bp.route('/upload', methods=['POST'])
def upload():
    """
    Process a PAR xlsx file through 6 steps and stream progress via SSE.
    Steps: read -> filter 0 days -> FY disbursement -> FTOD analysis ->
           insurance OD matching -> save output.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No PAR file uploaded"}), 400

    file = request.files['file']
    original_filename = file.filename or "par.xlsx"

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    file.save(tmp.name)
    tmp.close()

    def generate():
        try:
            logger.info(f"[OD_REPORT] Processing: {original_filename}")

            # Kick off the month-end OD read in a background thread now — it is
            # independent of the PAR steps and calamine releases the GIL, so its
            # read overlaps steps 1-3 instead of adding to the total time.
            od_path = get_od_monthend_file()
            od_holder = {}
            od_thread = None
            if od_path:
                def _load_od():
                    try:
                        od_holder['df'] = read_od_monthend(od_path)
                    except Exception as exc:  # surfaced when joined at step 4
                        od_holder['err'] = exc
                od_thread = threading.Thread(target=_load_od, daemon=True)
                od_thread.start()

            # --- Step 1: Read PAR file (fast engine) ---
            xl = open_excel_fast(tmp.name)
            if "Sheet1" not in xl.sheet_names:
                err = f"'Sheet1' not found. Available sheets: {', '.join(xl.sheet_names)}"
                xl.close()
                os.unlink(tmp.name)
                yield _send_sse({"error": err})
                return
            df = xl.parse("Sheet1")
            xl.close()

            if "DPD Days" not in df.columns:
                err = f"'DPD Days' column not found. Available columns: {', '.join(str(c) for c in df.columns)}"
                os.unlink(tmp.name)
                yield _send_sse({"error": err})
                return

            original_count = len(df)
            total_columns = len(df.columns)

            yield _send_sse({
                "step": 1, "title": "Read PAR File", "status": "done",
                "detail": f"Loaded '{original_filename}' — {original_count:,} rows, {total_columns} columns"
            })

            # --- Step 2: Remove "0 Days" ---
            mask = df["DPD Days"].astype(str).str.strip() == "0 Days"
            removed_count = int(mask.sum())
            df_filtered = df[~mask].copy()

            yield _send_sse({
                "step": 2, "title": "Filter 0 Days", "status": "done",
                "detail": f"Removed {removed_count:,} rows with '0 Days' — {len(df_filtered):,} rows remaining"
            })

            # --- Step 3: DisbursementDate FY flag ---
            from services.eod_processor import derive_fy_bounds
            date_parts = re.search(r"(\d{2})-(\d{2})-(\d{4})", original_filename)
            if date_parts:
                f_dd, f_mm, f_yyyy = int(date_parts.group(1)), int(date_parts.group(2)), int(date_parts.group(3))
                file_date = pd.Timestamp(f"{f_yyyy}-{f_mm:02d}-{f_dd:02d}")
            else:
                file_date = pd.Timestamp.now()

            fy_start, fy_end = derive_fy_bounds(file_date)
            fy_s, fy_e = fy_start.year, fy_end.year

            if "DisbursementDate" in df_filtered.columns:
                df_filtered["DisbursementDate_original"] = df_filtered["DisbursementDate"]
                dates = pd.to_datetime(df_filtered["DisbursementDate"], errors="coerce")
                fy_flag = ((dates >= fy_start) & (dates <= fy_end)).astype(int)
                fy_count = int(fy_flag.sum())
                df_filtered["DisbursementDate"] = fy_flag

                yield _send_sse({
                    "step": 3, "title": "FY Disbursement Check", "status": "done",
                    "detail": f"FY {fy_s}-{fy_e} — {fy_count:,} accounts disbursed within FY period"
                })
            else:
                yield _send_sse({
                    "step": 3, "title": "FY Disbursement Check", "status": "skipped",
                    "detail": "No 'DisbursementDate' column found"
                })

            # --- Step 4: FTOD Logic ---
            date_match = re.search(r"(\d{2})-\d{2}-\d{4}", original_filename)
            if date_match:
                day_threshold = int(date_match.group(1))
            else:
                day_threshold = 6

            if od_path:
                od_thread.join()  # finished during steps 1-3 in most cases
                if 'err' in od_holder:
                    raise od_holder['err']
                df_od = od_holder['df']
                od_account_ids = set(df_od["AccountID"].dropna().astype(int))

                par_ids = df_filtered["AccountID"].astype(int)
                found_mask = par_ids.isin(od_account_ids)

                df_filtered["FTOD"] = ""
                na_mask = ~found_mask
                due_days = pd.to_numeric(df_filtered["DueDays"], errors="coerce").fillna(0).astype(int)

                ftod_mask = na_mask & (due_days >= 1) & (due_days <= day_threshold)
                verify_mask = na_mask & (due_days > day_threshold)
                zero_na_mask = na_mask & (due_days == 0)

                df_filtered.loc[ftod_mask, "FTOD"] = "FTOD"
                df_filtered.loc[verify_mask, "FTOD"] = "Dear Team, Please verify this Account"
                df_filtered.loc[zero_na_mask, "FTOD"] = "#N/A"

                ftod_count = int(ftod_mask.sum())
                verify_count = int(verify_mask.sum())
                na_count = int(na_mask.sum())
                in_monthend = int(found_mask.sum())

                sort_order = df_filtered["FTOD"].map({
                    "Dear Team, Please verify this Account": 0,
                    "FTOD": 1, "#N/A": 2, "": 3
                }).fillna(3)
                df_filtered = df_filtered.iloc[sort_order.argsort(kind="stable")]

                yield _send_sse({
                    "step": 4, "title": "FTOD Analysis", "status": "done",
                    "detail": f"Month-end file: {od_path.name} | Day threshold: {day_threshold}",
                    "sub": [
                        f"In month-end OD: {in_monthend:,}",
                        f"Not in month-end: {na_count:,}",
                        f"Marked FTOD (DueDays 1-{day_threshold}): {ftod_count:,}",
                        f"Needs verification (DueDays > {day_threshold}): {verify_count:,}",
                    ]
                })
            else:
                df_filtered["FTOD"] = ""
                yield _send_sse({
                    "step": 4, "title": "FTOD Analysis", "status": "skipped",
                    "detail": "No month-end OD file (.xlsb/.xlsx) staged — FTOD column left blank"
                })

            # --- Step 5: Insurance OD Logic ---
            df_filtered["Insurance OD"] = ""
            df_filtered["Death Person"] = ""

            ins_path = get_ins_file()
            if ins_path:
                ext = ins_path.suffix.lower()
                if ext == ".csv":
                    df_ins = pd.read_csv(str(ins_path))
                else:
                    df_ins = read_excel_fast(str(ins_path), sheet_name="Data")

                ins_col = " Loan A/ No ( As Per Enrollment Form ) "
                death_col = "Death Person (Member/Nominee)"
                df_ins[ins_col] = df_ins[ins_col].astype(str).str.strip()

                direct_lookup = {}
                prefixed_lookup = {}

                for _, row in df_ins.iterrows():
                    raw = str(row[ins_col]).strip()
                    death_person = str(row.get(death_col, "")).strip()
                    if raw in ("nan", "", "None"):
                        continue
                    alpha_match = re.match(r'^[A-Za-z]+(\d+)', raw)
                    suffix_match = re.match(r'^(\d+)[-]\d*$', raw)
                    if alpha_match:
                        prefixed_lookup[alpha_match.group(1)] = death_person
                    elif suffix_match:
                        prefixed_lookup[suffix_match.group(1)] = death_person
                    else:
                        digits = re.sub(r'[^\d]', '', raw)
                        if digits:
                            direct_lookup[digits] = death_person

                par_ids_str = df_filtered["AccountID"].astype(str).str.strip()

                # Vectorized lookup (was a per-row .loc loop — slow on 100k+ rows).
                direct_mask = par_ids_str.isin(direct_lookup.keys())
                df_filtered.loc[direct_mask, "Insurance OD"] = "Insurance OD"
                df_filtered.loc[direct_mask, "Death Person"] = (
                    par_ids_str[direct_mask].map(direct_lookup).fillna(""))

                prefixed_mask = par_ids_str.isin(prefixed_lookup.keys()) & ~direct_mask
                df_filtered.loc[prefixed_mask, "Insurance OD"] = "Nominee Insurance OD"
                df_filtered.loc[prefixed_mask, "Death Person"] = (
                    par_ids_str[prefixed_mask].map(prefixed_lookup).fillna(""))

                ins_direct = int(direct_mask.sum())
                ins_nominee = int(prefixed_mask.sum())
                ins_total_records = len(df_ins)

                yield _send_sse({
                    "step": 5, "title": "Insurance OD Matching", "status": "done",
                    "detail": f"Insurance file: {ins_path.name} ({ins_total_records:,} records)",
                    "sub": [
                        f"Direct match (Insurance OD): {ins_direct:,}",
                        f"Nominee match (Nominee Insurance OD): {ins_nominee:,}",
                        f"Total matched: {ins_direct + ins_nominee:,}",
                    ]
                })
            else:
                yield _send_sse({
                    "step": 5, "title": "Insurance OD Matching", "status": "skipped",
                    "detail": "No insurance file uploaded — columns left blank"
                })

            # --- Step 6: Write output ---
            date_label = file_date.strftime("%d-%m-%Y")
            base_name = f"ESAF Over Due report as on {date_label}"
            output_filename = f"{base_name}.xlsx"
            output_path = DOWNLOADS_FOLDER / output_filename
            counter = 1
            while output_path.exists():
                output_filename = f"{base_name} ({counter}).xlsx"
                output_path = DOWNLOADS_FOLDER / output_filename
                counter += 1

            df_output = df_filtered.copy()
            if "DisbursementDate_original" in df_output.columns:
                df_output["DisbursementDate"] = df_output["DisbursementDate_original"]
                df_output.drop(columns=["DisbursementDate_original"], inplace=True)
            if "_rgn" in df_output.columns:
                df_output.drop(columns=["_rgn"], inplace=True)

            output_columns = len(df_output.columns)
            summary_ok = False
            # Write directly with xlsxwriter (much faster than pandas.to_excel for
            # 100k+ rows). The summary worksheet is created first → tab 0.
            wb = xlsxwriter.Workbook(
                str(output_path),
                {'default_date_format': 'yyyy-mm-dd hh:mm:ss', 'constant_memory': False},
            )
            try:
                ws_summary = wb.add_worksheet("ESAF OD Report")
                try:
                    _build_esaf_summary_sheet(ws_summary, wb, df_output, date_label,
                                              fy_start, fy_end, file_date)
                    summary_ok = True
                except Exception as e:
                    logger.warning(f"[OD_REPORT] ESAF summary sheet failed (Sheet1 still written): {e}")

                ws_data = wb.add_worksheet("Sheet1")
                for j, col in enumerate(df_output.columns):
                    ws_data.write(0, j, str(col))
                # Object view with NaN/NaT -> None so blanks stay blank; .tolist()
                # coerces numpy scalars/Timestamps to native types xlsxwriter writes.
                obj = df_output.astype(object).where(pd.notnull(df_output), None)
                for i, row in enumerate(obj.values.tolist(), start=1):
                    ws_data.write_row(i, 0, row)
            finally:
                wb.close()
            os.unlink(tmp.name)

            yield _send_sse({
                "step": 6, "title": "Save Output", "status": "done",
                "detail": (
                    f"Saved '{output_filename}' — {len(df_filtered):,} rows, {output_columns} columns"
                    + (" · incl. ESAF OD Report summary" if summary_ok else "")
                )
            })

            # Final done event
            yield _send_sse({"done": True, "filename": output_filename})
            logger.info(f"[OD_REPORT] Complete — {output_filename}")

        except Exception as e:
            logger.error(f"[OD_REPORT] Error: {e}")
            yield _send_sse({"error": str(e)})
            # Clean up temp file on error
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    return Response(generate(), mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@od_report_bp.route('/api/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "message": "OD Report module is running"})
