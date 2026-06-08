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

import pandas as pd

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
    """Check if a .xlsb month-end file exists in BACKUP_DATA."""
    BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = [f for f in os.listdir(BACKUP_DATA_DIR) if f.lower().endswith(".xlsb")]
    if files:
        return jsonify({"exists": True, "filename": files[0]})
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
    """Save .xlsb month-end file to BACKUP_DATA directory."""
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

            # --- Step 1: Read PAR file ---
            try:
                df = pd.read_excel(tmp.name, sheet_name="Sheet1", engine="openpyxl")
            except ValueError as e:
                if "Sheet1" in str(e):
                    xl = pd.ExcelFile(tmp.name, engine="openpyxl")
                    err = f"'Sheet1' not found. Available sheets: {', '.join(xl.sheet_names)}"
                    xl.close()
                    os.unlink(tmp.name)
                    yield _send_sse({"error": err})
                    return
                raise

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

            BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            xlsb_files = [f for f in os.listdir(BACKUP_DATA_DIR) if f.lower().endswith(".xlsb")]

            if xlsb_files:
                xlsb_path = BACKUP_DATA_DIR / xlsb_files[0]
                df_od = pd.read_excel(str(xlsb_path), sheet_name="Data", engine="pyxlsb")
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
                    "detail": f"Month-end file: {xlsb_files[0]} | Day threshold: {day_threshold}",
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
                    "detail": "No .xlsb file in BACKUP_DATA — FTOD column left blank"
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
                    df_ins = pd.read_excel(str(ins_path), sheet_name="Data", engine="openpyxl")

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

                direct_mask = par_ids_str.isin(direct_lookup.keys())
                df_filtered.loc[direct_mask, "Insurance OD"] = "Insurance OD"
                for idx in df_filtered.index[direct_mask]:
                    acc_id = str(df_filtered.loc[idx, "AccountID"]).strip()
                    df_filtered.loc[idx, "Death Person"] = direct_lookup.get(acc_id, "")

                prefixed_mask = par_ids_str.isin(prefixed_lookup.keys()) & ~direct_mask
                df_filtered.loc[prefixed_mask, "Insurance OD"] = "Nominee Insurance OD"
                for idx in df_filtered.index[prefixed_mask]:
                    acc_id = str(df_filtered.loc[idx, "AccountID"]).strip()
                    df_filtered.loc[idx, "Death Person"] = prefixed_lookup.get(acc_id, "")

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
            output_filename = "OD Report.xlsx"
            output_path = DOWNLOADS_FOLDER / output_filename
            counter = 1
            while output_path.exists():
                output_filename = f"OD Report_{counter}.xlsx"
                output_path = DOWNLOADS_FOLDER / output_filename
                counter += 1

            df_output = df_filtered.copy()
            if "DisbursementDate_original" in df_output.columns:
                df_output["DisbursementDate"] = df_output["DisbursementDate_original"]
                df_output.drop(columns=["DisbursementDate_original"], inplace=True)
            if "_rgn" in df_output.columns:
                df_output.drop(columns=["_rgn"], inplace=True)

            output_columns = len(df_output.columns)
            df_output.to_excel(str(output_path), sheet_name="Sheet1", index=False, engine="openpyxl")
            os.unlink(tmp.name)

            yield _send_sse({
                "step": 6, "title": "Save Output", "status": "done",
                "detail": f"Saved '{output_filename}' — {len(df_filtered):,} rows, {output_columns} columns"
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
