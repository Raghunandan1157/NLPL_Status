"""
QUICK MONTH-END Blueprint
==========================
Takes 4 files (Demand, Last Month PAR, PAR, Collection) and produces
the Employee Excel report in one go, using the month-end VBA template.
"""

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests as http_requests
from flask import Blueprint, jsonify, request, send_file, send_from_directory

import config
from services.memory_manager import try_acquire_processing, release_processing, gc_checkpoint
from services.error_handler import user_error

logger = logging.getLogger(__name__)

quick_month_end_bp = Blueprint(
    'quick_month_end', __name__,
    static_folder=str(config.STATIC_DIR / 'quick_month_end'),
)

QME_STATIC = str(config.STATIC_DIR / 'quick_month_end')


def _detect_target_date(collection_path):
    """Detect the report date from the uploaded collection file."""
    from services.eod_processor import parse_trxdate
    from services.excel_reader import smart_read_excel

    try:
        coll_df_dates = smart_read_excel(collection_path, usecols=['Trxdate'])
        coll_df_dates['Trxdate'] = parse_trxdate(coll_df_dates['Trxdate'])
        max_date = coll_df_dates['Trxdate'].dropna().max()
        if pd.notna(max_date):
            target_date = max_date.to_pydatetime()
            logger.info(
                "Auto-detected target date from Collection Trxdate: "
                f"{target_date.strftime('%d-%m-%Y')}"
            )
            return target_date
        logger.warning("Could not detect date from Trxdate, using today")
    except Exception as e:
        logger.warning(f"Trxdate auto-detect failed: {e}, using today")
    return datetime.now()


def _copy_if_exists(src, dest):
    src = Path(src) if src else None
    if not src or not src.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    return True


def _sync_employee_report_to_colldb(emp_output, target_date):
    """Upload the generated employee report to the same database endpoints EOD uses."""
    if not config.COLLDB_URL:
        return {
            'success': False,
            'message': 'COLLDB_URL is not configured.',
        }

    target_url = f"{config.COLLDB_URL.rstrip('/')}/api/upload"
    daily_url = f"{config.COLLDB_URL.rstrip('/')}/api/upload-daily"
    date_iso = pd.Timestamp(target_date).strftime('%Y-%m-%d')

    try:
        with open(emp_output, 'rb') as f:
            resp = http_requests.post(
                target_url,
                files={'file': ('Employee_Report.xlsx', f,
                                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                timeout=60,
            )
        if resp.status_code != 200:
            return {
                'success': False,
                'target': target_url,
                'message': f"Upload failed ({resp.status_code}): {(resp.text or '')[:300]}",
            }

        try:
            stats = resp.json()
        except ValueError:
            stats = {}

        daily_status = {'attempted': True, 'date': date_iso, 'target': daily_url}
        try:
            with open(emp_output, 'rb') as f:
                daily_resp = http_requests.post(
                    daily_url,
                    files={'file': ('Employee_Report.xlsx', f,
                                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                    data={'date': date_iso},
                    timeout=60,
                )
            if daily_resp.status_code == 200:
                try:
                    daily_json = daily_resp.json()
                except ValueError:
                    daily_json = {}
                daily_status.update({'ok': True, 'stats': daily_json})
            else:
                daily_status.update({
                    'ok': False,
                    'httpStatus': daily_resp.status_code,
                    'body': (daily_resp.text or '')[:300],
                })
        except Exception as daily_err:
            daily_status.update({'ok': False, 'error': str(daily_err)})

        emp_count = stats.get('employees') or stats.get('empCount') or 0
        perf_count = stats.get('performance') or stats.get('perfCount') or 0
        return {
            'success': True,
            'target': target_url,
            'message': f"Database updated - {emp_count} employees, {perf_count} records",
            'stats': stats,
            'daily': daily_status,
        }
    except Exception as e:
        return {
            'success': False,
            'target': target_url,
            'message': str(e),
        }


# ── Static file serving ────────────────────────────────────────────────

@quick_month_end_bp.route('/')
def index():
    return send_from_directory(QME_STATIC, 'index.html')


@quick_month_end_bp.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(QME_STATIC, filename)


# ── POST /process — Main processing endpoint ──────────────────────────

@quick_month_end_bp.route('/process', methods=['POST'])
def process():
    if not try_acquire_processing():
        return jsonify({
            'error': 'Server is busy processing another request. Please try again in a moment.'
        }), 503

    try:
        import time as _time
        t0 = _time.time()
        upload_to_database = request.form.get('uploadToDatabase') == 'true'

        temp_dir = tempfile.mkdtemp(dir=str(config.TEMP_DIR))
        temp_path = Path(temp_dir)

        try:
            # ── 1. Resolve all 4 input files ──────────────────────────
            required_files = {
                'demand': 'Demand Sheet Master',
                'lastMonthPar': 'Last Month PAR',
                'par': 'PAR',
                'collection': 'Collection',
            }
            paths = {}
            for key, label in required_files.items():
                f = request.files.get(key)
                if not f or not f.filename:
                    return jsonify({'error': f"No file provided for '{label}'."}), 400
                dest = temp_path / f'{key}.xlsx'
                f.save(dest)
                paths[key] = dest

            # ── 2. Auto-detect target date from Collection Trxdate ────
            target_date = _detect_target_date(paths['collection'])
            date_str = target_date.strftime('%d-%m-%Y')

            logger.info(
                f"Quick Month-End: Demand={paths['demand'].name}, "
                f"LastMonthPAR={paths['lastMonthPar'].name}, "
                f"PAR={paths['par'].name}, Collection={paths['collection'].name}, "
                f"date={date_str}, upload_to_database={upload_to_database}"
            )

            # ── 3. Ingest Last Month PAR into DB ─────────────────────
            from services.db_manager import get_db_manager

            db_manager = get_db_manager()

            if db_manager:
                try:
                    # Temporarily copy Last Month PAR to backend dir for ingestion
                    lm_par_dest = config.BACKEND_DATA_DIR / f'Last_Month_PAR_{paths["lastMonthPar"].name}'
                    # Clean old ones
                    for old in config.BACKEND_DATA_DIR.glob("Last_Month_PAR_*"):
                        old.unlink()
                    shutil.copy2(paths['lastMonthPar'], lm_par_dest)
                    success, msg = db_manager.ingest_last_month_par(lm_par_dest)
                    if success:
                        logger.info(f"Last Month PAR ingested: {msg}")
                    else:
                        logger.warning(f"Last Month PAR ingestion failed: {msg}")
                except Exception as e:
                    logger.warning(f"Last Month PAR ingestion error: {e}")

            # ── 4. Run full EOD processing (Demand + PAR + Collection → Excel + report) ─
            logger.info("STEP 1: Running full EOD processing (Regular Demand vs Collection + EOD report)")

            from services import eod_processor as processor

            eod_output_path = temp_path / "eod_output.xlsx"

            df_eod, report_path = processor.process_files(
                paths['demand'], paths['collection'], paths['par'], eod_output_path,
                auto_fix_sheets=False,
                db_manager=db_manager,
                target_date=target_date,
                sheets_dir=str(config.BACKEND_DATA_DIR / 'sheets'),
                skip_output=False,
                force_demand_file=True,
                # Month-end module computes demand with REGULAR daily-report rules
                # (PNPA keeps the "Active Loan" filter; Regular Collection excludes
                # only 1-30), matching the day-before daily report rather than the
                # month-end VBA template.
                force_regular_rules=True,
            )

            if df_eod is None or len(df_eod) == 0:
                return jsonify({
                    'error': 'EOD processing completed but produced no results.',
                    'suggestion': 'Verify that your files contain valid data.'
                }), 500

            logger.info(f"STEP 1 complete: EOD produced {len(df_eod)} rows")

            # Persist the same artifacts EOD exposes so the month-end module can
            # download the Regular Demand vs Collection workbook and EOD report.
            eod_latest = config.BACKEND_DATA_DIR / 'EOD_Output_Latest.xlsx'
            report_latest = config.BACKEND_DATA_DIR / 'EOD_Report_Latest.xlsx'
            _copy_if_exists(eod_output_path, eod_latest)
            if report_path:
                _copy_if_exists(report_path, report_latest)
            (config.BACKEND_DATA_DIR / '.target_date').write_text(date_str)

            # ── 5. Generate Employee Report (aggregated only, no accounts) ─
            logger.info("STEP 2: Generating Employee Report")

            emp_output_path = config.BACKEND_DATA_DIR / 'Employee_Report_Latest.xlsx'
            emp_path = processor.build_employee_report(df_eod, target_date, emp_output_path, par_file=paths['par'], force_regular_rules=True)

            if emp_path is None:
                return jsonify({
                    'error': 'Employee report generation failed.',
                    'suggestion': 'Ensure your EOD data contains Emp ID column.'
                }), 500

            logger.info(f"STEP 2 complete: Employee report at {emp_output_path.name}")

            del df_eod
            gc_checkpoint("qme-employee-report-done")

            # Save a persistent copy to backend
            latest_copy = config.BACKEND_DATA_DIR / 'Quick_Month_End_Employee_Latest.xlsx'
            try:
                shutil.copy2(str(emp_output_path), str(latest_copy))
                logger.info(f"Saved latest month-end employee report: {latest_copy}")
            except Exception as cpy_err:
                logger.warning(f"Could not save latest month-end report: {cpy_err}")

            database_upload = None
            if upload_to_database:
                logger.info("STEP 3: Uploading month-end employee report to database")
                database_upload = _sync_employee_report_to_colldb(emp_output_path, target_date)
                if database_upload.get('success'):
                    logger.info(f"Month-end database upload OK: {database_upload.get('message')}")
                else:
                    logger.warning(f"Month-end database upload failed: {database_upload.get('message')}")

            t_total = _time.time() - t0
            logger.info(f"Quick Month-End completed in {t_total:.2f}s")

            shutil.rmtree(temp_dir, ignore_errors=True)

            available = []
            if eod_latest.exists():
                available.append('eod')
            if report_latest.exists():
                available.append('report')
            if emp_output_path.exists():
                available.append('employee')

            result = {
                'status': 'success',
                'success': True,
                'available': available,
                'reportDate': date_str,
                'message': 'Month-end processing complete. Download the generated reports below.',
                'downloads': {
                    'eod': '/quick-month-end/download-output',
                    'report': '/quick-month-end/download-report',
                    'employee': '/quick-month-end/download-employee-report',
                },
            }
            if database_upload is not None:
                result['databaseUpload'] = database_upload
            return jsonify(result)

        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    except Exception as e:
        err = user_error(e, context='quick-month-end-process')
        return jsonify({
            'error': err['user_message'],
            'suggestion': err['suggestion'],
        }), 500
    finally:
        gc_checkpoint("qme-request-complete")
        release_processing()


# ── Save to Downloads ────────────────────────────────────────────────

@quick_month_end_bp.route('/download-output', methods=['GET'])
def download_output():
    """Download the month-end Regular Demand vs Collection workbook."""
    output_file = config.BACKEND_DATA_DIR / 'EOD_Output_Latest.xlsx'
    if not output_file.exists():
        return jsonify({'error': 'No month-end output available. Generate it first.'}), 404
    return send_file(
        output_file,
        as_attachment=True,
        download_name='Regular Demand Vs Collection.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@quick_month_end_bp.route('/download-report', methods=['GET'])
def download_report():
    """Download the month-end EOD report workbook."""
    report_file = config.BACKEND_DATA_DIR / 'EOD_Report_Latest.xlsx'
    if not report_file.exists():
        return jsonify({'error': 'No month-end EOD report available. Generate it first.'}), 404
    return send_file(
        report_file,
        as_attachment=True,
        download_name='EOD_Report.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@quick_month_end_bp.route('/download-employee-report', methods=['GET'])
def download_employee_report():
    """Download the month-end employee report workbook."""
    emp_file = config.BACKEND_DATA_DIR / 'Employee_Report_Latest.xlsx'
    if not emp_file.exists():
        return jsonify({'error': 'No month-end employee report available. Generate it first.'}), 404
    return send_file(
        emp_file,
        as_attachment=True,
        download_name='Employee_Report_Month_End.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@quick_month_end_bp.route('/save-to-downloads', methods=['POST'])
def save_to_downloads():
    """Save the latest Month-End report bundle to ~/Downloads."""
    try:
        files = [
            (config.BACKEND_DATA_DIR / 'EOD_Output_Latest.xlsx', 'Regular Demand Vs Collection.xlsx'),
            (config.BACKEND_DATA_DIR / 'EOD_Report_Latest.xlsx', 'EOD_Report.xlsx'),
            (config.BACKEND_DATA_DIR / 'Employee_Report_Latest.xlsx', 'Employee_Report_Month_End.xlsx'),
        ]
        existing = [(src, name) for src, name in files if src.exists()]
        if not existing:
            return jsonify({
                'success': False,
                'message': 'No month-end reports available. Generate them first.'
            }), 404

        dl_dir = Path.home() / 'Downloads'
        dl_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir = dl_dir / f"Month_End_Bundle_{datetime.now().strftime('%d-%m-%Y_%H-%M-%S')}"
        bundle_dir.mkdir(parents=True, exist_ok=False)

        saved = []
        for src, name in existing:
            dest = bundle_dir / name
            shutil.copy2(str(src), str(dest))
            saved.append(name)

        logger.info(f"Month-end report bundle saved to Downloads: {bundle_dir}")

        return jsonify({'success': True, 'path': str(bundle_dir), 'saved': saved})

    except Exception as e:
        err = user_error(e, context='qme-save-downloads')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Sync to Dashboard (EC2 Portfolio) ───────────────────────────────

@quick_month_end_bp.route('/dashboard-months', methods=['GET'])
def dashboard_months():
    """Fetch available months from EC2 portfolio database."""
    try:
        EC2_UPLOAD_URL = config.EC2_UPLOAD_URL
        if not EC2_UPLOAD_URL:
            return jsonify({'success': False, 'message': 'EC2 upload URL not configured.'}), 500

        base_url = EC2_UPLOAD_URL.rsplit('/api/', 1)[0]
        resp = http_requests.get(f'{base_url}/api/portfolio/months', timeout=10)
        if resp.status_code == 200:
            months = resp.json()
            return jsonify({'success': True, 'months': months})
        else:
            return jsonify({'success': False, 'message': f'EC2 error ({resp.status_code})'}), 502
    except Exception as e:
        err = user_error(e, context='qme-dashboard-months')
        return jsonify({'success': False, 'message': err['user_message']}), 500


@quick_month_end_bp.route('/sync-to-dashboard', methods=['POST'])
def sync_to_dashboard():
    """Upload the latest Month-End Employee Report to EC2 portfolio database."""
    try:
        latest = config.BACKEND_DATA_DIR / 'Quick_Month_End_Employee_Latest.xlsx'
        if not latest.exists():
            return jsonify({'success': False, 'message': 'No month-end report available. Generate one first.'}), 404

        EC2_UPLOAD_URL = config.EC2_UPLOAD_URL
        if not EC2_UPLOAD_URL:
            return jsonify({'success': False, 'message': 'EC2 upload URL not configured.'}), 500

        data = request.get_json() or {}
        month_label = data.get('month', '').strip().upper()
        if not month_label:
            return jsonify({'success': False, 'message': 'Month label is required (e.g. MAR).'}), 400

        base_url = EC2_UPLOAD_URL.rsplit('/api/', 1)[0]
        portfolio_url = f'{base_url}/api/portfolio/upload'

        # Dashboard auth token
        dashboard_token = 'colldb-admin-2024'

        with open(latest, 'rb') as f:
            resp = http_requests.post(
                portfolio_url,
                files={'file': ('Employee_Report.xlsx', f,
                                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                data={'month': month_label},
                headers={'x-dashboard-token': dashboard_token},
                timeout=60,
            )

        if resp.status_code == 200:
            result = resp.json()
            return jsonify({
                'success': True,
                'message': f"Portfolio updated for {month_label} — {result.get('inserted', 0)} records inserted",
                'stats': result,
            })
        else:
            return jsonify({'success': False, 'message': f"Upload failed ({resp.status_code}): {resp.text}"}), 502

    except Exception as e:
        err = user_error(e, context='qme-sync-dashboard')
        return jsonify({'success': False, 'message': err['user_message']}), 500


# ── Check Columns ────────────────────────────────────────────────────

# Expected columns per file type
EXPECTED_COLUMNS = {
    'demand': {
        'required': [
            'Account ID', 'Meeting Date', 'Regular Demand', 'No of Regular Demand',
            'Cumulative Demand', 'No of Cumulative', 'Installment Amount',
            'Product Name', 'DPD Days',
        ],
        'optional': [
            'Region', 'Area', 'BranchName', 'Emp ID', 'Officer Name',
            'Due Days', 'Current Loan Status',
        ],
    },
    'lastMonthPar': {
        'required': [
            'AccountID', 'DPD Days', 'LoanStatus',
        ],
        'optional': [
            'Product Name', 'Region', 'Area', 'BranchName',
        ],
    },
    'par': {
        'required': [
            'AccountID',
        ],
        'dpd_any_of': ['DPD Group', 'DPD Days', 'Days Group', 'Days group', 'DaysGroup', 'Due Days'],
        'optional': [
            'Product Name', 'Region', 'Area', 'BranchName',
        ],
    },
    'collection': {
        'required': [
            'AccountID', 'CollectionTotal', 'Trxdate', 'ReverseTotal',
        ],
        'optional': [],
    },
}


@quick_month_end_bp.route('/check-columns', methods=['POST'])
def check_columns():
    """Check uploaded files for expected column names."""
    try:
        results = {}

        for key, spec in EXPECTED_COLUMNS.items():
            f = request.files.get(key)
            if not f or not f.filename:
                results[key] = {
                    'filename': None,
                    'status': 'missing',
                    'message': 'No file uploaded',
                }
                continue

            # Read just the header row
            try:
                df = pd.read_excel(f, engine='calamine', nrows=0)
                columns = list(df.columns)
                f.seek(0)  # Reset for potential re-read
            except Exception as e:
                results[key] = {
                    'filename': f.filename,
                    'status': 'error',
                    'message': f'Cannot read file: {e}',
                }
                continue

            missing_required = []
            for col in spec.get('required', []):
                if col not in columns:
                    missing_required.append(col)

            # Check "any_of" groups (e.g., DPD column can have multiple names)
            missing_any_of = []
            if 'dpd_any_of' in spec:
                found = any(c in columns for c in spec['dpd_any_of'])
                if not found:
                    missing_any_of.append(f"one of: {', '.join(spec['dpd_any_of'])}")

            found_optional = [col for col in spec.get('optional', []) if col in columns]
            missing_optional = [col for col in spec.get('optional', []) if col not in columns]

            if missing_required or missing_any_of:
                status = 'error'
                msg_parts = []
                if missing_required:
                    msg_parts.append(f"Missing required: {', '.join(missing_required)}")
                if missing_any_of:
                    msg_parts.append(f"Missing {', '.join(missing_any_of)}")
                message = '. '.join(msg_parts)
            else:
                status = 'ok'
                message = 'All required columns found'

            results[key] = {
                'filename': f.filename,
                'status': status,
                'message': message,
                'columns_found': len(columns),
                'missing_required': missing_required,
                'missing_any_of': missing_any_of,
                'found_optional': found_optional,
                'missing_optional': missing_optional,
            }

        return jsonify({'success': True, 'results': results})

    except Exception as e:
        err = user_error(e, context='qme-check-columns')
        return jsonify({'error': err['user_message']}), 500


# ── VBA Template (month-end) ─────────────────────────────────────────

@quick_month_end_bp.route('/vba-template', methods=['GET'])
def get_vba_template():
    """Return the month-end VBA template text for copying."""
    try:
        vba_src = config.STATIC_DIR / 'eod' / 'vba_template_month_end.js'
        if not vba_src.exists():
            return jsonify({'error': 'VBA template not found.'}), 404

        content = vba_src.read_text(encoding='utf-8')
        # Extract template from JS backtick string
        start = content.find('`')
        end = content.rfind('`')
        if start != -1 and end > start:
            vba_text = content[start + 1:end]
        else:
            vba_text = content

        # Inject target date if available
        target_date_file = config.BACKEND_DATA_DIR / '.target_date'
        if target_date_file.exists():
            try:
                td_str = target_date_file.read_text().strip()
                parts = td_str.split('-')
                if len(parts) == 3:
                    dd, mm, yyyy = int(parts[0]), int(parts[1]), int(parts[2])
                    vba_text = vba_text.replace(
                        "dateWasInjected = False",
                        "dateWasInjected = True"
                    )
                    vba_text = vba_text.replace(
                        "' targetDate = DateSerial(YYYY, MM, DD) <-- Date will be filled here\n    targetDate = Date ' Default to Today\n    reportDate = Format(targetDate, \"dd-mm-yyyy\") ' Default",
                        f"targetDate = DateSerial({yyyy}, {mm}, {dd})\n    reportDate = \"{td_str}\""
                    )
            except Exception as e:
                logger.warning(f"Could not inject date into VBA: {e}")

        return jsonify({'success': True, 'vba': vba_text})

    except Exception as e:
        err = user_error(e, context='qme-vba-template')
        return jsonify({'error': err['user_message']}), 500
