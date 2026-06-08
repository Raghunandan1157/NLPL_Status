"""
QUICK Blueprint - Quick Report processing.
Takes 3 files (PAR, Collection daily, Collection Report hourly) and produces
the final hourly fast report in one go, bypassing the two-step EOD → Hourly flow.
"""

import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests as http_requests
from flask import Blueprint, jsonify, request, send_file, send_from_directory

import config
from services.column_matcher import find_column
from services.memory_manager import try_acquire_processing, release_processing, gc_checkpoint
from services.error_handler import user_error
from services.gdrive import (
    parse_folder_id,
    list_folder_files_public,
    download_file as gdrive_download_file,
    load_gdrive_config,
    save_gdrive_config,
)

logger = logging.getLogger(__name__)

quick_bp = Blueprint('quick', __name__, static_folder=str(config.STATIC_DIR / 'quick'))

QUICK_STATIC = str(config.STATIC_DIR / 'quick')
QUICK_GDRIVE_CONFIG = config.DATA_DIR / 'quick_gdrive_config.json'
QUICK_GDRIVE_DIR = config.GDRIVE_DOWNLOAD_DIR / 'quick'

# Tracks the original PAR filename used in the last /process run so that
# /sync-to-dashboard can derive the correct YYYY-MM-DD for Coll_Db's hourly tab.
LAST_PAR_META = config.BACKEND_DATA_DIR / 'last_quick_par.meta.json'


# ── PAR filename date helper ──────────────────────────────────────────

# Primary pattern (confirmed from user's Downloads): `Par as on 20-04-2026.xlsx`.
# Also tolerated: `PAR_20-04-2026`, `PAR 20/04/2026`, `PAR_20_04_2026`, etc.
# Separated form is tried first (preferred, least ambiguous); if absent, we
# fall back to a packed DDMMYYYY form (e.g. `PAR_20042026.xlsx`).
# Input is assumed to be a PAR filename — we don't require the literal `PAR`
# token because real-world names use `Par as on ...` with arbitrary text.
_PAR_DATE_RE_SEP = re.compile(r'(?<!\d)(\d{2})[\-_./](\d{2})[\-_./](\d{4})(?!\d)')
_PAR_DATE_RE_PACKED = re.compile(r'(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)')


def _parse_par_filename_date(filename):
    """Parse a PAR filename and return the embedded date as 'YYYY-MM-DD' or None.

    Accepts the conventions used in this project:
      Par as on 20-04-2026.xlsx  -> '2026-04-20'   (primary — confirmed)
      PAR_20-04-2026.xlsx        -> '2026-04-20'
      PAR 20-04-2026.xlsx        -> '2026-04-20'
      PAR_20_04_2026.xlsx        -> '2026-04-20'
      PAR 20.04.2026.xlsx        -> '2026-04-20'
      PAR_20042026.xlsx          -> '2026-04-20'   (packed fallback)

    Returns None if no plausible DD-MM-YYYY is found or the date fails to validate.
    Only call this on filenames known to be PAR files; the Collection filename
    date is ignored by design upstream.
    """
    if not filename:
        return None
    # Use the basename only — upstream filenames may include paths
    name = Path(str(filename)).name
    for rx in (_PAR_DATE_RE_SEP, _PAR_DATE_RE_PACKED):
        for match in rx.finditer(name):
            day, month, year = match.group(1), match.group(2), match.group(3)
            try:
                parsed = datetime.strptime(f"{day}-{month}-{year}", '%d-%m-%Y')
            except ValueError:
                continue
            return parsed.strftime('%Y-%m-%d')
    return None


def _capture_par_original_filename():
    """Snapshot the PAR upload's original filename BEFORE _resolve_file consumes it.

    Works for both upload and GDrive paths. Returns None if unresolvable.
    """
    # GDrive path: look for the sidecar meta written by /gdrive-download
    if request.form.get('useGDrive_par') == 'true':
        meta_path = QUICK_GDRIVE_DIR / 'gdrive_par_last.meta.json'
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text()).get('originalName')
            except Exception as exc:
                logger.warning(f"Could not read PAR GDrive meta {meta_path}: {exc}")
        return None
    # Upload path: FileStorage.filename is still available until .save() runs
    par_file = request.files.get('par')
    if par_file and par_file.filename:
        return par_file.filename
    return None


def _persist_last_par_filename(original_name):
    """Record the PAR filename used by the latest /process run."""
    if not original_name:
        return
    try:
        LAST_PAR_META.parent.mkdir(parents=True, exist_ok=True)
        LAST_PAR_META.write_text(json.dumps({
            'originalName': original_name,
            'capturedAt': datetime.now().isoformat(timespec='seconds'),
            'parDate': _parse_par_filename_date(original_name),
        }))
        logger.info(f"Recorded last PAR filename: {original_name}")
    except Exception as exc:
        logger.warning(f"Could not persist last PAR filename: {exc}")


# ── Static file serving ────────────────────────────────────────────────

@quick_bp.route('/')
def index():
    return send_from_directory(QUICK_STATIC, 'index.html')


@quick_bp.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(QUICK_STATIC, filename)


# ── Helper: resolve file from upload or GDrive cache ──────────────────

def _resolve_file(form_key, gdrive_target, temp_dir):
    """
    Resolve an input file from either a multipart upload or the GDrive cache.

    Returns (path, cleanup_needed) or raises ValueError with a user-facing message.
    """
    use_gdrive = request.form.get(f'useGDrive_{form_key}') == 'true'
    use_last_cache = request.form.get('useLastCache') == 'true'

    if use_gdrive:
        cached = QUICK_GDRIVE_DIR / f'gdrive_{gdrive_target}_last.xlsx'
        if not cached.exists():
            raise ValueError(
                f"No cached Google Drive file found for '{gdrive_target}'. "
                "Please scan and download again."
            )
        logger.info(f"Using GDrive-cached {gdrive_target}: {cached}")
        return cached, False

    f = request.files.get(form_key)

    # Fallback to EOD-side last-cache when useLastCache=true and no upload
    # supplied. Only par/collection have daily_*_last.xlsx snapshots; the
    # collectionReport is always a fresh upload.
    if (not f or not f.filename) and use_last_cache and form_key in ('par', 'collection'):
        cached = config.DB_CACHE_DIR / f'daily_{form_key}_last.xlsx'
        if cached.exists():
            logger.info(f"Using last-cache {form_key}: {cached}")
            return cached, False

    if not f or not f.filename:
        raise ValueError(f"No file provided for '{form_key}'.")

    dest = Path(temp_dir) / f'{gdrive_target}.xlsx'
    f.save(dest)
    return dest, True


# ── POST /process — Main unified processing endpoint ─────────────────

@quick_bp.route('/process', methods=['POST'])
def process():
    if not try_acquire_processing():
        return jsonify({
            'error': 'Server is busy processing another request. Please try again in a moment.'
        }), 503

    try:
        import time as _time
        t0 = _time.time()

        # ── 1. Parse form metadata ────────────────────────────────────
        form_date_str = request.form.get('date', '')      # dd-mm-yyyy (optional)
        hour = request.form.get('hour', '')
        minute = request.form.get('minute', '')
        ampm = request.form.get('ampm', '')

        # ── Date is sourced from the PAR filename (authoritative) ────
        # Frontend-supplied form date is kept only as a fallback for
        # older clients; PAR filename wins when parseable. This matches
        # the Coll_Db sync contract which already keys off PAR date.
        par_original_name = _capture_par_original_filename()
        # Locked re-runs use cached PAR (no upload this round) — reuse the
        # filename recorded by the last successful /process. Prefer EOD's
        # cache meta (written on EOD /process runs) over quick's meta, so a
        # post-EOD Hourly auto-fire uses the PAR the EOD run just saved.
        if not par_original_name and request.form.get('useLastCache') == 'true':
            eod_par_meta = config.DB_CACHE_DIR / 'daily_par_last.meta.json'
            for candidate in (eod_par_meta, LAST_PAR_META):
                try:
                    if candidate.exists():
                        cand_name = json.loads(candidate.read_text()).get('originalName')
                        if _parse_par_filename_date(cand_name):
                            par_original_name = cand_name
                            break
                        if cand_name and not par_original_name:
                            par_original_name = cand_name
                except Exception as exc:
                    logger.warning(f"Could not read {candidate} for date fallback: {exc}")
        par_filename_date = _parse_par_filename_date(par_original_name)  # 'YYYY-MM-DD' or None

        if par_filename_date:
            # target_date = PAR snapshot date — drives demand masks and the
            # On-Date sheet (which is keyed off target_date + 1). The report
            # LABEL is the day after: PAR 'as on 20-05' → report 'as on 21-05',
            # and On-Date demand (target_date + 1) then lands on that label.
            target_date = datetime.strptime(par_filename_date, '%Y-%m-%d')
            date_str = (target_date + timedelta(days=1)).strftime('%d-%m-%Y')
            logger.info(f"Target date from PAR filename '{par_original_name}': "
                        f"{target_date.strftime('%d-%m-%Y')}, report label {date_str}")
        elif form_date_str:
            try:
                # form date is the report LABEL (CLI sends PAR date + 1);
                # target_date is the day before, matching the PAR branch.
                report_date = datetime.strptime(form_date_str, '%d-%m-%Y')
                target_date = report_date - timedelta(days=1)
                date_str = form_date_str
                logger.warning(f"PAR filename '{par_original_name}' has no parseable "
                               f"date — falling back to form date {date_str}")
            except ValueError:
                return jsonify({
                    'error': (
                        f"Could not derive a target date. PAR filename "
                        f"'{par_original_name}' has no parseable date and form "
                        f"date '{form_date_str}' is invalid. Expected PAR filename "
                        f"like 'Par as on DD-MM-YYYY.xlsx'."
                    ),
                }), 400
        else:
            return jsonify({
                'error': (
                    f"Target date unavailable. PAR filename "
                    f"'{par_original_name or '(missing)'}' has no parseable date "
                    f"and no form date provided. Rename PAR to include "
                    f"DD-MM-YYYY (e.g. 'Par as on 22-04-2026.xlsx')."
                ),
            }), 400

        # Build formatted datetime for report header
        formatted_dt = f"{date_str} @ {hour}:{minute} {ampm}" if hour and minute and ampm else date_str

        eod_target_date = target_date - timedelta(days=1)

        temp_dir = tempfile.mkdtemp(dir=str(config.TEMP_DIR))
        temp_path = Path(temp_dir)

        try:
            # PAR original filename already captured above (pre-resolve) for
            # target-date derivation + /sync-to-dashboard Coll_Db date keying.

            # ── 2. Resolve all 3 input files ──────────────────────────
            try:
                par_path, _ = _resolve_file('par', 'par', temp_dir)
                collection_path, _ = _resolve_file('collection', 'collection', temp_dir)
                coll_report_path, _ = _resolve_file('collectionReport', 'collectionReport', temp_dir)
            except ValueError as ve:
                return jsonify({'error': str(ve)}), 400

            logger.info(f"Quick Report: PAR={par_path.name}, "
                        f"Collection={collection_path.name}, "
                        f"CollectionReport={coll_report_path.name}, "
                        f"date={date_str}")

            # ── 3. Run EOD processing (PAR + Collection → df_eod) ─────
            logger.info("STEP 1: Running EOD processing (PAR + Collection)")

            from services import eod_processor as processor
            from services.db_manager import get_db_manager

            db_manager = get_db_manager()

            # EOD needs a demand file — use backend demand master
            demand_files = list(config.BACKEND_DATA_DIR.glob("Demand_Sheet_Master_*"))
            if not demand_files:
                return jsonify({
                    'error': 'No backend demand file found. '
                             'Please upload one via the EOD module first.',
                    'suggestion': 'Go to EOD → Backend Data and upload a Demand Sheet Master file.'
                }), 400
            demand_path = demand_files[0]

            eod_output_path = temp_path / "eod_output.xlsx"

            df_eod, report_path = processor.process_files(
                demand_path, collection_path, par_path, eod_output_path,
                auto_fix_sheets=False,
                db_manager=db_manager,
                target_date=eod_target_date,
                sheets_dir=None,
                skip_output=True,
            )

            if df_eod is None or len(df_eod) == 0:
                return jsonify({
                    'error': 'EOD processing completed but produced no results.',
                    'suggestion': 'Verify that your PAR and Collection files contain valid data.'
                }), 500

            logger.info(f"STEP 1 complete: EOD produced {len(df_eod)} rows")

            # NOTE: Do NOT exclude rows with DPD Group=NaN here.
            # Those accounts may still have valid 'DPD Group - Last Month' and
            # 'Loan Status - Last Month' from the Last Month PAR file, which are
            # used for 1-30, 31-60, PNPA, and NPA bucket calculations.

            # ── 4. Read & pivot hourly collection report ──────────────
            logger.info("STEP 2: Reading hourly Collection Report")

            collection_df = pd.read_excel(coll_report_path, engine='calamine')
            logger.info(f"Collection Report loaded: {len(collection_df)} rows, "
                        f"columns: {list(collection_df.columns)}")

            # Filter ReverseTotal == 0
            col_reverse = find_column(collection_df, 'ReverseTotal', 'Reverse Total')
            if not col_reverse:
                return jsonify({
                    'error': f"Column 'ReverseTotal' not found in Collection Report. "
                             f"Available: {list(collection_df.columns)}"
                }), 400
            filtered_df = collection_df[collection_df[col_reverse] == 0]
            logger.info(f"Filtered ({col_reverse} == 0): {len(filtered_df)} rows")

            # Pivot: AccountID → sum(CollectionTotal)
            col_account_coll = find_column(filtered_df, 'AccountID', 'Account ID')
            if not col_account_coll:
                return jsonify({
                    'error': f"Column 'AccountID' not found in Collection Report. "
                             f"Available: {list(collection_df.columns)}"
                }), 400

            col_coll_total = find_column(filtered_df, 'CollectionTotal', 'Collection Total')
            if not col_coll_total:
                return jsonify({
                    'error': f"Column 'CollectionTotal' not found in Collection Report. "
                             f"Available: {list(collection_df.columns)}"
                }), 400

            pivot_df = filtered_df.groupby(col_account_coll)[col_coll_total].sum().reset_index()
            pivot_df.columns = ['AccountID', 'Sum of CollectionTotal']
            logger.info(f"Pivot: {len(pivot_df)} unique AccountIDs")

            # ── 5. Merge hourly collection onto EOD output ────────────
            logger.info("STEP 3: Merging hourly collection onto EOD output")

            new_col_name = f"Collection as on {formatted_dt}"

            col_account_eod = find_column(df_eod, 'Account ID', 'AccountID')
            if not col_account_eod:
                return jsonify({
                    'error': f"Column 'Account ID' not found in EOD output. "
                             f"Available: {list(df_eod.columns)}"
                }), 400

            lookup = dict(zip(pivot_df['AccountID'], pivot_df['Sum of CollectionTotal']))
            df_eod[new_col_name] = df_eod[col_account_eod].map(lookup)
            matched = df_eod[new_col_name].notna().sum()
            logger.info(f"Matched {matched}/{len(df_eod)} rows")

            # Add Remark / Remark2 columns
            col_installment = find_column(df_eod, 'Installment Amount')
            col_cumulative = find_column(df_eod, 'Cumulative Demand')
            col_dpd_last = find_column(df_eod, 'DPD Group - Last Month')

            for label, col in [
                ('Installment Amount', col_installment),
                ('Cumulative Demand', col_cumulative),
                ('DPD Group - Last Month', col_dpd_last),
            ]:
                if not col:
                    return jsonify({
                        'error': f"Column '{label}' not found in EOD output. "
                                 f"Available: {list(df_eod.columns)}"
                    }), 400

            has_collection = df_eod[new_col_name].notna()
            is_zero_days = df_eod[col_dpd_last] == '0 Days'

            df_eod['Remark'] = np.where(
                ~has_collection, None,
                np.where(is_zero_days,
                         df_eod[col_cumulative] - df_eod[new_col_name],
                         df_eod[col_installment] - df_eod[new_col_name])
            )
            df_eod['Remark2'] = np.where(
                ~has_collection, 'Not Collected',
                np.where(df_eod['Remark'].astype(float) <= 0, 'Full Collected', 'Partially Collected')
            )
            logger.info("Added Remark and Remark2 columns")

            # ── 6. Build fast report ──────────────────────────────────
            # Two-precomp approach matching the VBA hourly flow:
            #   Precomp 1 (EOD baseline): DAILY Collection → reg_demand, reg_collection,
            #     npa_cases. Used for DEMAND = FTOD = reg_demand - reg_collection.
            #   Precomp 2 (Hourly overlay): HOURLY Collection → reg_collection_display,
            #     col_130, col_3160, pnpa_collection, npa_hourly_*. Used for COLLECTION.
            logger.info("STEP 4: Building fast report")

            from services.eod_processor import _compute_precomputed_sheets
            from services.daily_report_builder import build_daily_report

            has_officer = 'Emp ID' in df_eod.columns

            # Step 4a: EOD baseline precomp (DAILY Collection, unchanged).
            # Gives reg_demand, reg_collection for demand = FTOD = reg_demand - reg_collection.
            # Also gives npa_cases (NPA demand is kept from daily in hourly format).
            logger.info("Computing EOD baseline for demand adjustment")
            precomp_eod = _compute_precomputed_sheets(df_eod, target_date)

            # Step 4b: Replace ONLY the Collection column with hourly values.
            # Keep Partial Amount and installment-collected value from DAILY (unchanged).
            # The hourly VBA uses DAILY filter conditions (Remark2/Partial Amount)
            # but counts HOURLY Collection (non-null). has_collection in the precomp
            # will reflect hourly Collection, giving the correct intersection.
            df_eod['Collection'] = df_eod[new_col_name]
            logger.info(f"Set Collection to hourly ({df_eod['Collection'].notna().sum()} accounts)")

            # Recalculate Partial Amount and installment-collected columns
            # based on hourly Collection. Matches Remark2 logic from lines 245-254:
            # For "0 Days" DPD: compare with Cumulative Demand
            # For others: compare with Installment Amount
            _inst_amt = pd.to_numeric(df_eod.get('Installment Amount', 0), errors='coerce').fillna(0)
            _cum_demand = pd.to_numeric(df_eod.get(col_cumulative, 0), errors='coerce').fillna(0)
            _hourly_coll = pd.to_numeric(df_eod['Collection'], errors='coerce').fillna(0)
            _has_hourly = df_eod['Collection'].notna()
            _dpd_last = df_eod.get(col_dpd_last, pd.Series('', index=df_eod.index)).fillna('').astype(str)
            _is_0days = _dpd_last == '0 Days'
            # For 0 Days: use cumulative demand; for others: use installment
            _threshold = np.where(_is_0days, _cum_demand, _inst_amt)
            _diff = _threshold - _hourly_coll
            df_eod['Partial Amount'] = 'Not Collected'
            df_eod.loc[_has_hourly & (_diff <= 0), 'Partial Amount'] = 'Full EMI Paid'
            df_eod.loc[_has_hourly & (_diff > 0), 'Partial Amount'] = 'Partial Amount'
            # installment - collected uses installment for all buckets (1-30, 31-60, PNPA)
            _inst_diff = _inst_amt - _hourly_coll
            df_eod['installment - collected amt'] = _inst_diff
            df_eod['installment - collected value'] = (_inst_diff <= 0).astype(int)
            df_eod.loc[~_has_hourly, 'installment - collected value'] = 0
            logger.info(f"Recalculated Partial Amount for hourly: "
                        f"Full={(_has_hourly & (_diff <= 0)).sum()}, "
                        f"Partial={(_has_hourly & (_diff > 0)).sum()}")

            # Step 4c: Hourly precomp (HOURLY Collection + recalculated derivatives).
            # Gives reg_collection_display, col_130, col_3160, pnpa_collection,
            # npa_hourly_acc, npa_hourly_amt from hourly data.
            logger.info("Computing aggregations with hourly collection")
            precomp = _compute_precomputed_sheets(df_eod, target_date)

            # Step 4d: Merge — take demand from EOD baseline, collections from hourly.
            if (precomp_eod and '_precomp' in precomp_eod and
                    precomp and '_precomp' in precomp):
                pc_eod = precomp_eod['_precomp']
                pc = precomp['_precomp']
                key_cols = ['filter_type', 'filter_value', 'group_value', 'scope', 'product']
                eod_idx = pc_eod.set_index(key_cols)
                pc_idx = pc.set_index(key_cols)
                # Demand columns from EOD baseline (daily FTOD)
                for col in ['reg_demand', 'reg_collection', 'reg_demand_amt', 'reg_collection_amt',
                            'reg_demand_total', 'reg_demand_total_amt', 'npa_cases']:
                    if col in eod_idx.columns:
                        pc_idx[col] = eod_idx[col]
                precomp['_precomp'] = pc_idx.reset_index()
                del precomp_eod
                logger.info("Merged: demand/NPA-cases from EOD, collections from hourly")
            else:
                del precomp_eod
            gc_checkpoint("quick-precomp-computed")

            output_filename = f"Daily Collection Report as on {date_str} @ {hour}.{minute} {ampm}.xlsx"
            fast_report_path = temp_path / output_filename

            # Build per-employee data for the 'Employee Data' sheet (all products
            # combined). df_eod['Collection'] is already the hourly value, so the
            # numbers come out hourly-mode consistent with the rest of the report.
            employee_data = None
            try:
                from services.eod_processor import build_employee_report
                _emp_tmp = temp_path / 'emp_for_report.xlsx'
                if build_employee_report(df_eod, target_date, _emp_tmp) and _emp_tmp.exists():
                    _x = pd.read_excel(_emp_tmp, sheet_name=['IGL', 'FIG', 'VVY'])
                    _all = pd.concat([_x['IGL'], _x['FIG'], _x['VVY']], ignore_index=True)
                    _idc = ['Region', 'Division', 'Area', 'Branch', 'Emp ID']
                    _mc = [c for c in _all.columns if c not in _idc + ['Officer Name']]
                    _g = _all.groupby(_idc, as_index=False)[_mc].sum()
                    _onm = (_all[_all['Officer Name'].astype(str) != '']
                            .groupby('Emp ID')['Officer Name'].first().to_dict())
                    _g['Officer Name'] = _g['Emp ID'].map(_onm).fillna('')
                    employee_data = _g
            except Exception as _emp_err:
                logger.warning(f"Quick report 'Employee Data' sheet skipped "
                               f"({type(_emp_err).__name__}: {_emp_err})")

            if precomp and '_precomp' in precomp:
                build_daily_report(precomp['_precomp'], fast_report_path, target_date, has_officer,
                                   formatted_dt=formatted_dt, eod_target_date=eod_target_date,
                                   hourly_mode=True, employee_data=employee_data)
                logger.info(f"Fast report generated: {fast_report_path.name}")

                # Zero-collection ext tables (right side, aligned per designation section)
                try:
                    from services.zero_collection_ext import (
                        append_zero_collection_tables,
                        build_branch_region_map,
                    )
                    branch_region = build_branch_region_map(df_eod)
                    append_zero_collection_tables(
                        fast_report_path,
                        precomp['_precomp'],
                        target_date,
                        branch_region=branch_region,
                        selected_date_str=date_str,
                        selected_time_str=f"{hour}:{minute} {ampm}",
                    )
                    logger.info("Zero-collection ext tables appended")
                except Exception as ext_err:
                    logger.warning(f"Zero-collection ext skipped: {ext_err}")
            else:
                # Fallback: write the merged DataFrame directly
                from services.eod_processor import _write_excel_fast
                df_eod_out = df_eod.fillna('')
                _write_excel_fast(df_eod_out, str(fast_report_path))
                logger.info("Precomp unavailable — wrote merged DataFrame as fallback")

            del df_eod
            gc_checkpoint("quick-precomp-freed")

            # Save a persistent copy to backend
            latest_copy = config.BACKEND_DATA_DIR / 'Quick_Report_Latest.xlsx'
            try:
                shutil.copy2(str(fast_report_path), str(latest_copy))
                logger.info(f"Saved latest quick report: {latest_copy}")
            except Exception as cpy_err:
                logger.warning(f"Could not save latest quick report: {cpy_err}")

            # Record the PAR original filename so /sync-to-dashboard can
            # extract the correct date for Coll_Db's hourly tab.
            _persist_last_par_filename(par_original_name)

            t_total = _time.time() - t0
            logger.info(f"Quick Report completed in {t_total:.2f}s")

            response = send_file(
                str(fast_report_path),
                as_attachment=True,
                download_name=output_filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )

            @response.call_on_close
            def _cleanup_temp():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            return response

        except Exception:
            # Clean up temp on error
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    except Exception as e:
        err = user_error(e, context='quick-process')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500
    finally:
        gc_checkpoint("quick-request-complete")
        release_processing()


# ── GDrive config ─────────────────────────────────────────────────────

@quick_bp.route('/gdrive-config', methods=['GET'])
def quick_gdrive_config_get():
    """Load saved GDrive folder URL for the Quick module."""
    try:
        cfg = load_gdrive_config(QUICK_GDRIVE_CONFIG)
        return jsonify({'success': True, 'folder_url': cfg.get('folder_url', '')})
    except Exception as e:
        err = user_error(e, context='quick-gdrive-config')
        return jsonify({'error': err['user_message']}), 500


@quick_bp.route('/gdrive-config', methods=['POST'])
def quick_gdrive_config_save():
    """Save GDrive folder URL for the Quick module."""
    try:
        data = request.get_json()
        folder_url = data.get('folder_url', '')
        save_gdrive_config(QUICK_GDRIVE_CONFIG, {'folder_url': folder_url})
        return jsonify({'success': True})
    except Exception as e:
        err = user_error(e, context='quick-gdrive-config-save')
        return jsonify({'error': err['user_message']}), 500


# ── GDrive scan ───────────────────────────────────────────────────────

@quick_bp.route('/gdrive-scan', methods=['POST'])
def quick_gdrive_scan():
    """Scan a GDrive folder for all files (user picks PAR / Collection / CollectionReport)."""
    try:
        data = request.get_json()
        folder_url = data.get('folder_url', '')
        folder_id = parse_folder_id(folder_url)
        if not folder_id:
            return jsonify({'success': False, 'message': 'Invalid Google Drive folder URL'}), 400

        all_files = list_folder_files_public(folder_id)

        # Persist folder URL
        save_gdrive_config(QUICK_GDRIVE_CONFIG, {'folder_url': folder_url})

        return jsonify({
            'success': True,
            'files': all_files,
            'total_files': len(all_files),
        })
    except Exception as e:
        err = user_error(e, context='quick-gdrive-scan')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── GDrive download ──────────────────────────────────────────────────

@quick_bp.route('/gdrive-download', methods=['POST'])
def quick_gdrive_download():
    """Download a specific file from GDrive for the Quick module."""
    try:
        data = request.get_json()
        file_id = data.get('fileId', '')
        file_name = data.get('fileName', '')
        target = data.get('target', '')  # 'par', 'collection', or 'collectionReport'

        if not file_id or not file_name:
            return jsonify({'success': False, 'message': 'Missing fileId or fileName'}), 400

        valid_targets = ('par', 'collection', 'collectionReport')
        if target not in valid_targets:
            return jsonify({
                'success': False,
                'message': f"Invalid target '{target}'. Must be one of: {valid_targets}"
            }), 400

        QUICK_GDRIVE_DIR.mkdir(parents=True, exist_ok=True)

        dest = QUICK_GDRIVE_DIR / file_name
        downloaded = gdrive_download_file(file_id, dest)

        # Save as the canonical cached file for this target
        cache_path = QUICK_GDRIVE_DIR / f'gdrive_{target}_last.xlsx'
        if cache_path.exists():
            cache_path.unlink()
        shutil.copy2(str(downloaded), str(cache_path))
        logger.info(f"GDrive {target} cached: {cache_path}")

        # Record original filename alongside so downstream code (e.g. PAR date
        # extraction for /sync-to-dashboard) can recover it after rename.
        try:
            meta_path = QUICK_GDRIVE_DIR / f'gdrive_{target}_last.meta.json'
            meta_path.write_text(json.dumps({'originalName': file_name}))
        except Exception as meta_err:
            logger.warning(f"Could not write GDrive {target} meta: {meta_err}")

        return jsonify({
            'success': True,
            'path': str(cache_path),
            'filename': file_name,
            'target': target,
        })

    except Exception as e:
        err = user_error(e, context='quick-gdrive-download')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Save to Downloads ────────────────────────────────────────────────

@quick_bp.route('/save-to-downloads', methods=['POST'])
def save_to_downloads():
    """Save the latest Quick Report to the server's ~/Downloads folder."""
    try:
        latest = config.BACKEND_DATA_DIR / 'Quick_Report_Latest.xlsx'
        if not latest.exists():
            return jsonify({'success': False, 'message': 'No quick report available. Generate one first.'}), 404

        dl_dir = Path.home() / 'Downloads'
        dl_dir.mkdir(parents=True, exist_ok=True)
        # Use the latest report's actual filename if available
        latest_name = latest.name  # fallback
        # Try to find a better name from the most recent quick report output
        dest = dl_dir / 'Daily Collection Report.xlsx'

        # Dedup naming if file exists
        if dest.exists():
            i = 1
            while True:
                dest = dl_dir / f'Daily Collection Report ({i}).xlsx'
                if not dest.exists():
                    break
                i += 1

        shutil.copy2(str(latest), str(dest))
        logger.info(f"Quick report saved to Downloads: {dest}")

        return jsonify({'success': True, 'path': str(dest), 'filename': dest.name})

    except Exception as e:
        err = user_error(e, context='quick-save-downloads')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Sync to Dashboard (Coll_Db) ──────────────────────────────────────

@quick_bp.route('/sync-to-dashboard', methods=['POST'])
def sync_to_dashboard():
    """Upload the latest Quick Report to the Coll_Db dashboard (hourly tab).

    Target: POST {COLLDB_URL}/api/upload-hourly
    Form fields:
      - file : the Quick Report xlsx (multer single)
      - date : YYYY-MM-DD derived from the PAR filename used in /process
               (Collection filename date is intentionally ignored — the Coll_Db
               hourly tab keys off the PAR snapshot date.)
    """
    try:
        quick_output = config.BACKEND_DATA_DIR / 'Quick_Report_Latest.xlsx'
        if not quick_output.exists():
            return jsonify({
                'success': False,
                'message': 'Quick report not found. Generate one first.',
            }), 404

        # Derive the required date from the last PAR filename recorded during /process.
        par_original_name = None
        if LAST_PAR_META.exists():
            try:
                par_original_name = json.loads(LAST_PAR_META.read_text()).get('originalName')
            except Exception as meta_err:
                logger.warning(f"Could not read {LAST_PAR_META}: {meta_err}")

        if not par_original_name:
            return jsonify({
                'success': False,
                'message': (
                    'No PAR filename on record. Run Quick processing first so the '
                    'hourly date can be derived from the PAR filename.'
                ),
            }), 400

        par_date = _parse_par_filename_date(par_original_name)
        if not par_date:
            return jsonify({
                'success': False,
                'message': (
                    f"Could not parse a date from PAR filename '{par_original_name}'. "
                    "Expected patterns like PAR_DDMMYYYY, PAR DD-MM-YYYY, PAR_DD_MM_YYYY."
                ),
            }), 400

        # Report is for the day AFTER the PAR snapshot — match /process,
        # which derives target_date as PAR date + 1. Coll_Db's hourly tab
        # must key off the same date the report header shows.
        par_date = (datetime.strptime(par_date, '%Y-%m-%d')
                    + timedelta(days=1)).strftime('%Y-%m-%d')

        target_url = f"{config.COLLDB_URL.rstrip('/')}/api/upload-hourly"

        try:
            with open(quick_output, 'rb') as f:
                resp = http_requests.post(
                    target_url,
                    files={'file': ('Quick_Report.xlsx', f,
                                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                    data={'date': par_date},
                    timeout=60,
                )
        except (http_requests.exceptions.ConnectionError,
                http_requests.exceptions.ConnectTimeout) as conn_err:
            logger.warning(f"Quick sync: Coll_Db not reachable at {config.COLLDB_URL}: {conn_err}")
            return jsonify({
                'success': False,
                'target': target_url,
                'message': (
                    f"Coll_Db server not reachable at {config.COLLDB_URL}. "
                    "Start it with `cd /Users/raghunandanmali/Desktop/Coll_Db/server && node index.js`"
                ),
            }), 503

        if resp.status_code == 200:
            try:
                db_result = resp.json()
            except ValueError:
                db_result = {}
            emp_count = db_result.get('employees') or db_result.get('empCount') or 0
            perf_count = db_result.get('performance') or db_result.get('perfCount') or 0
            return jsonify({
                'success': True,
                'target': target_url,
                'message': (
                    f"Coll_Db hourly tab updated for {par_date} — "
                    f"{emp_count} employees, {perf_count} records"
                ),
                'stats': db_result,
                'parFilename': par_original_name,
                'date': par_date,
            })
        else:
            body_text = (resp.text or '')[:500]
            return jsonify({
                'success': False,
                'target': target_url,
                'message': f"Upload failed ({resp.status_code}): {body_text}",
            }), 502

    except Exception as e:
        err = user_error(e, context='quick-sync-dashboard')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Backend files status ─────────────────────────────────────────────

@quick_bp.route('/backend-files-status', methods=['GET'])
def quick_backend_files_status():
    """Check which GDrive-cached files exist for the Quick module."""
    try:
        QUICK_GDRIVE_DIR.mkdir(parents=True, exist_ok=True)

        status = {}
        for target in ('par', 'collection', 'collectionReport'):
            cache_path = QUICK_GDRIVE_DIR / f'gdrive_{target}_last.xlsx'
            if cache_path.exists():
                mtime = cache_path.stat().st_mtime
                upload_dt = datetime.fromtimestamp(mtime)
                # Expire at midnight: same logic as hourly module
                midnight = upload_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                is_expired = datetime.now() >= midnight
                status[target] = {
                    'cached': True,
                    'filename': cache_path.name,
                    'timestamp': upload_dt.strftime('%d-%b-%Y %I:%M:%S %p'),
                    'expired': is_expired,
                }
            else:
                status[target] = {'cached': False}

        # Check if demand master is available (required for EOD processing)
        demand_files = list(config.BACKEND_DATA_DIR.glob("Demand_Sheet_Master_*"))
        status['demandMaster'] = {
            'available': len(demand_files) > 0,
            'filename': demand_files[0].name if demand_files else None,
        }

        # Check if a previous quick report exists
        latest = config.BACKEND_DATA_DIR / 'Quick_Report_Latest.xlsx'
        status['quickReport'] = {
            'available': latest.exists(),
        }
        if latest.exists():
            mtime = latest.stat().st_mtime
            status['quickReport']['timestamp'] = datetime.fromtimestamp(mtime).strftime(
                '%d-%b-%Y %I:%M:%S %p'
            )

        return jsonify(status)

    except Exception as e:
        err = user_error(e, context='quick-backend-status')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500


# ── Cache uploaded PAR / Collection file (lock until midnight) ────────

@quick_bp.route('/cache-upload', methods=['POST'])
def cache_upload():
    """Cache a manually uploaded PAR or Collection file so it persists across reloads."""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400

        f = request.files['file']
        target = request.form.get('target', '')

        valid_targets = ('par', 'collection')
        if target not in valid_targets:
            return jsonify({
                'success': False,
                'message': f"Invalid target '{target}'. Must be one of: {valid_targets}"
            }), 400

        QUICK_GDRIVE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = QUICK_GDRIVE_DIR / f'gdrive_{target}_last.xlsx'
        if cache_path.exists():
            cache_path.unlink()
        f.save(cache_path)
        logger.info(f"Cached uploaded {target} file: {cache_path}")

        mtime = cache_path.stat().st_mtime
        timestamp = datetime.fromtimestamp(mtime).strftime('%d-%b-%Y %I:%M:%S %p')

        return jsonify({
            'success': True,
            'target': target,
            'filename': f.filename,
            'timestamp': timestamp,
        })

    except Exception as e:
        err = user_error(e, context='quick-cache-upload')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Delete cached PAR / Collection file ──────────────────────────────

@quick_bp.route('/delete-cached-file', methods=['POST'])
def delete_cached_file():
    """Delete a cached GDrive file (par or collection) from the Quick module."""
    try:
        data = request.get_json()
        target = data.get('target', '')

        valid_targets = ('par', 'collection')
        if target not in valid_targets:
            return jsonify({
                'success': False,
                'message': f"Invalid target '{target}'. Must be one of: {valid_targets}"
            }), 400

        cache_path = QUICK_GDRIVE_DIR / f'gdrive_{target}_last.xlsx'
        if cache_path.exists():
            try:
                cache_path.unlink()
                logger.info(f"Deleted cached {target} file: {cache_path}")
            except PermissionError:
                logger.warning(f"Cannot delete cached {target} file (locked): {cache_path}")
                return jsonify({'success': False, 'message': 'File is locked (possibly open in Excel)'}), 423

        return jsonify({'success': True, 'deleted': target})

    except Exception as e:
        err = user_error(e, context='quick-delete-cached')
        return jsonify({
            'success': False,
            'message': err['user_message'],
        }), 500
