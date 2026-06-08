"""
DB Module Blueprint
===================
Serves the DB module frontend, handles .xlsx / .csv file uploads,
and manages the "Backend data for DB" reference file used for
VLOOKUP enrichment (Region, RM Name, Area, DM Name via BranchID).
"""

from flask import Blueprint, send_from_directory, jsonify, request
import config
import os
import json
from pathlib import Path
from flask import send_file
import shutil
import logging

db_bp = Blueprint('db', __name__)

STATIC_DB_DIR = config.STATIC_DIR / 'db'
BACKEND_FILE_PATH = config.DB_DIR / 'backend_data.xlsx'


@db_bp.route('/')
def index():
    """Serve DB module index.html."""
    return send_from_directory(str(STATIC_DB_DIR), 'index.html')


# ── Backend Reference File Endpoints ────────────────────────────
# NOTE: These must be registered BEFORE the /<path:filename> catch-all
# so Flask matches them first.


@db_bp.route('/upload-backend', methods=['POST'])
def upload_backend():
    """Accept .xlsx/.csv backend reference file and persist it."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('xlsx', 'csv'):
        return jsonify({'error': 'Only .xlsx and .csv files accepted'}), 400

    # Save with original extension
    save_path = config.DB_DIR / ('backend_data.' + ext)
    # Remove old file if extension changed
    for old_ext in ('xlsx', 'csv'):
        old_path = config.DB_DIR / ('backend_data.' + old_ext)
        if old_path.exists() and old_path != save_path:
            old_path.unlink()

    f.save(str(save_path))

    # Parse and return lookup data
    lookup, row_count = _parse_backend_file(save_path)

    return jsonify({
        'success': True,
        'filename': f.filename,
        'rowCount': row_count,
        'lookup': lookup
    })


@db_bp.route('/backend-status')
def backend_status():
    """Return whether a backend reference file exists."""
    path = _find_backend_file()
    if path and path.exists():
        _, row_count = _parse_backend_file(path)
        return jsonify({
            'exists': True,
            'filename': path.name,
            'rowCount': row_count
        })
    return jsonify({'exists': False, 'filename': None, 'rowCount': 0})


@db_bp.route('/backend-data')
def backend_data():
    """Return the parsed lookup table (BranchID → Region/RM/Area/DM)."""
    path = _find_backend_file()
    if not path or not path.exists():
        return jsonify({'error': 'No backend file uploaded'}), 404

    lookup, row_count = _parse_backend_file(path)
    return jsonify({'lookup': lookup, 'rowCount': row_count})


# ── Processed File Upload (for VBA runner & email) ────────────────
DB_PROCESSED_DIR = config.DB_DIR / 'processed'
DB_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


@db_bp.route('/upload-processed', methods=['POST'])
def upload_processed():
    """Accept the processed DB report from the browser for VBA runner/email."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    save_path = DB_PROCESSED_DIR / 'DB_Processed_Latest.xlsx'
    f.save(str(save_path))

    # Save target date if provided
    target_date = request.form.get('target_date', '')
    if target_date:
        (DB_PROCESSED_DIR / '.target_date').write_text(target_date)

    # Integration: also keep a Latest copy under the backend dir so the shared
    # per-run report history (reports_archive_db) can snapshot it. The report
    # content/format is unchanged — this is only a copy used for archiving.
    try:
        backend_latest = config.BACKEND_DATA_DIR / 'DB_Disbursement_Report_Latest.xlsx'
        shutil.copy2(str(save_path), str(backend_latest))
    except Exception as copy_err:
        logging.warning(f'DB: could not copy Latest for archive: {copy_err}')

    logging.info(f'DB: Processed file saved ({save_path.stat().st_size / 1024:.0f} KB)')
    return jsonify({'success': True, 'filename': f.filename})


# ── Download Processed Output ─────────────────────────────────────


@db_bp.route('/download-output', methods=['GET'])
def download_output():
    """Download the processed DB output."""
    output_file = DB_PROCESSED_DIR / 'DB_Processed_Latest.xlsx'
    if not output_file.exists():
        return jsonify({'error': 'No output available. Run DB processing first.'}), 404
    return send_file(
        output_file,
        as_attachment=True,
        download_name='DB_Disbursement_Report.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ── Bundle Save ────────────────────────────────────────────────────


def _get_db_latest_mtime():
    """Get mtime of the latest processed DB file (for duplicate detection)."""
    processed_file = DB_PROCESSED_DIR / 'DB_Processed_Latest.xlsx'
    if processed_file.exists():
        return processed_file.stat().st_mtime
    return None


def _find_existing_db_save(mtime):
    """Check if DB_Bundle already has a save matching this processing run's mtime."""
    downloads = Path.home() / 'Downloads'
    if not downloads.exists():
        downloads = Path.home() / 'Desktop'
    bundle_dir = downloads / 'DB_Bundle'
    marker_file = bundle_dir / '.last_save_mtime'
    if marker_file.exists():
        try:
            lines = marker_file.read_text().strip().split('\n')
            saved_mtime = float(lines[0])
            saved_path = lines[1] if len(lines) > 1 else ''
            if abs(saved_mtime - mtime) < 0.01 and saved_path and Path(saved_path).exists():
                return saved_path
        except (ValueError, IndexError):
            pass
    return None


def _save_db_bundle(save_dir):
    """Copy latest DB report + VBA template to the given directory."""
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    processed_file = DB_PROCESSED_DIR / 'DB_Processed_Latest.xlsx'
    if processed_file.exists():
        shutil.copy2(processed_file, save_dir / 'DB_Disbursement_Report.xlsx')
        saved.append('DB_Disbursement_Report.xlsx')

    # Read persisted target_date for VBA injection
    target_date_file = DB_PROCESSED_DIR / '.target_date'
    vba_date_injection = None
    if target_date_file.exists():
        try:
            td_str = target_date_file.read_text().strip()  # "dd-mm-yyyy"
            parts = td_str.split('-')
            if len(parts) == 3:
                dd, mm, yyyy = int(parts[0]), int(parts[1]), int(parts[2])
                vba_date_injection = (yyyy, mm, dd, td_str)
                logging.info(f"DB VBA date injection: {td_str} -> DateSerial({yyyy},{mm},{dd})")
        except Exception as e:
            logging.warning(f"Could not read target_date for DB VBA injection: {e}")

    # Extract VBA from JS template and save as plain .txt
    vba_js_path = STATIC_DB_DIR / 'vba_db_template.js'
    if vba_js_path.exists():
        content = vba_js_path.read_text(encoding='utf-8')
        start = content.find('`')
        end = content.rfind('`')
        if start != -1 and end > start:
            vba_text = content[start + 1:end]
        else:
            vba_text = content

        # Inject target date into VBA if available
        if vba_date_injection:
            yyyy, mm, dd, td_str = vba_date_injection
            vba_text = vba_text.replace(
                "dateWasInjected = False",
                "dateWasInjected = True"
            )
            vba_text = vba_text.replace(
                "' targetDate = DateSerial(YYYY, MM, DD) <-- Date will be filled here\n    targetDate = Date ' Default to Today\n    reportDate = Format(targetDate, \"dd-mm-yyyy\") ' Default",
                f"targetDate = DateSerial({yyyy}, {mm}, {dd})\n    reportDate = \"{td_str}\""
            )

        (save_dir / 'VBA_DB_Template.txt').write_text(vba_text, encoding='utf-8')
        saved.append('VBA_DB_Template.txt')

    # Save target_date metadata for the runner
    if vba_date_injection:
        (save_dir / '.target_date').write_text(vba_date_injection[3])

    # Write marker so we can detect duplicate saves
    downloads = Path.home() / 'Downloads'
    if not downloads.exists():
        downloads = Path.home() / 'Desktop'
    marker = downloads / 'DB_Bundle' / '.last_save_mtime'
    mtime = _get_db_latest_mtime()
    if mtime is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{mtime}\n{save_dir}")

    return saved, str(save_dir)


@db_bp.route('/save-bundle-to-server', methods=['POST'])
def save_bundle_to_server():
    """Save the bundle to Downloads/DB_Bundle/<timestamp>.

    Supports duplicate detection:
      - Default (no action): checks if same data already saved → returns prompt
      - action=replace: overwrite the existing folder
      - action=new: create a new timestamped folder
    """
    from datetime import datetime

    action = request.json.get('action') if request.is_json else None
    mtime = _get_db_latest_mtime()

    downloads = Path.home() / 'Downloads'
    if not downloads.exists():
        downloads = Path.home() / 'Desktop'

    # Check for existing save of the same data
    if action is None and mtime is not None:
        existing = _find_existing_db_save(mtime)
        if existing:
            return jsonify(
                already_saved=True,
                existing_path=existing,
                existing_name=Path(existing).name
            )

    now = datetime.now()
    timestamp = f"{now.strftime('%Hh.%Mm.%Ss')} on {now.strftime('%d-%m-%Y')}"

    if action == 'replace':
        existing = _find_existing_db_save(mtime) if mtime else None
        if existing:
            save_dir = existing
        else:
            save_dir = str(downloads / 'DB_Bundle' / timestamp)
    else:
        # action=new or first save
        save_dir = str(downloads / 'DB_Bundle' / timestamp)

    saved, path = _save_db_bundle(save_dir)
    return jsonify(success=True, saved=saved, path=path)


# ── VBA Runner ────────────────────────────────────────────────────


@db_bp.route('/vba-runner')
def vba_runner():
    """Serve the DB VBA runner page."""
    return send_from_directory(str(STATIC_DB_DIR), 'vba_runner.html')


@db_bp.route('/vba-runner/bundles')
def vba_runner_bundles():
    """List all DB_Bundle folders sorted newest-first."""
    bundle_root = Path.home() / 'Downloads' / 'DB_Bundle'
    if not bundle_root.exists():
        return jsonify({'bundles': []})

    bundles = []
    for d in sorted(bundle_root.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        files = [f.name for f in d.iterdir()
                 if f.is_file() and not f.name.startswith('~$') and not f.name.startswith('.')]
        td_file = d / '.target_date'
        td = td_file.read_text().strip() if td_file.exists() else None
        bundles.append({
            'name': d.name,
            'path': str(d),
            'files': sorted(files),
            'target_date': td,
        })

    return jsonify({'bundles': bundles})


@db_bp.route('/vba-runner/run', methods=['POST'])
def vba_runner_run():
    """Run VBA macro on the DB file in a bundle.

    POST JSON: {bundle_path: str, script: "daily"}

    Windows: Uses VBScript (cscript.exe) for reliable COM automation.
    Mac:     Uses AppleScript to automate Excel UI.
    """
    import platform as _plat
    import time

    data = request.get_json(force=True)
    bundle_path = Path(data.get('bundle_path', ''))
    script_type = data.get('script', 'daily')

    if not bundle_path.exists() or not bundle_path.is_dir():
        return jsonify({'error': 'Bundle folder not found'}), 404

    # Find the xlsx file
    raw_xlsx = bundle_path / 'DB_Disbursement_Report.xlsx'
    if not raw_xlsx.exists():
        # Fallback: find any xlsx
        xlsx_files = [f for f in bundle_path.glob('*.xlsx') if not f.name.startswith('~$')]
        if not xlsx_files:
            return jsonify({'error': 'No .xlsx file found in bundle'}), 404
        raw_xlsx = xlsx_files[0]

    # Pick VBA file from bundle
    vba_file = bundle_path / 'VBA_DB_Template.txt'
    if not vba_file.exists():
        return jsonify({'error': 'VBA_DB_Template.txt not found in bundle'}), 404

    macro_name = 'CreateDisbursementReport'

    if _plat.system() == 'Windows':
        import subprocess as _sp_check
        _sp_check.run(
            ['taskkill', '/F', '/IM', 'EXCEL.EXE'],
            capture_output=True, timeout=10,
        )
        time.sleep(1)
        return _db_vba_runner_windows(raw_xlsx, vba_file, macro_name)
    else:
        return _db_vba_runner_mac(raw_xlsx, vba_file, macro_name)


def _db_vba_runner_windows(raw_xlsx, vba_file, macro_name):
    """Run VBA macro via VBScript (cscript.exe).

    Bypasses pywin32 — VBScript uses native Windows COM.
    """
    import subprocess as _sp
    import tempfile
    import time

    xlsx_path = str(raw_xlsx.resolve())
    vba_path = str(vba_file.resolve())

    vbs_lines = [
        'On Error Resume Next',
        'Dim xlApp, wb, vbMod, fso, ts, vbaCode, modName',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        'Set xlApp = CreateObject("Excel.Application")',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: Could not start Excel"',
        '    WScript.Quit 1',
        'End If',
        'On Error GoTo 0',
        '',
        'xlApp.DisplayAlerts = False',
        'xlApp.Visible = True',
        'xlApp.AutomationSecurity = 1',
        '',
        'Dim origPath, xlsmPath',
        f'origPath = "{xlsx_path}"',
        'xlsmPath = Replace(origPath, ".xlsx", ".xlsm")',
        'If fso.FileExists(xlsmPath) Then fso.DeleteFile xlsmPath',
        '',
        'Set wb = xlApp.Workbooks.Open(origPath)',
        'If wb Is Nothing Then',
        '    WScript.StdErr.WriteLine "ERROR: Could not open workbook"',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'wb.SaveAs xlsmPath, 52',
        '',
        f'Set ts = fso.OpenTextFile("{vba_path}", 1, False, 0)',
        'vbaCode = ts.ReadAll',
        'ts.Close',
        '',
        'On Error Resume Next',
        'Set vbMod = wb.VBProject.VBComponents.Add(1)',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: VBA access blocked"',
        '    xlApp.DisplayAlerts = True',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'Err.Clear',
        'vbMod.CodeModule.AddFromString vbaCode',
        'If Err.Number <> 0 Then',
        '    WScript.StdErr.WriteLine "ERROR: Failed to inject VBA: " & Err.Description',
        '    xlApp.DisplayAlerts = True',
        '    xlApp.Quit',
        '    WScript.Quit 1',
        'End If',
        'On Error GoTo 0',
        '',
        f'xlApp.Run "{macro_name}"',
        '',
        'wb.VBProject.VBComponents.Remove vbMod',
        'wb.SaveAs origPath, 51',
        'If fso.FileExists(xlsmPath) Then fso.DeleteFile xlsmPath',
        '',
        'xlApp.DisplayAlerts = True',
        '',
        'WScript.StdOut.WriteLine "OK"',
    ]
    vbs_content = '\r\n'.join(vbs_lines) + '\r\n'

    tmp_vbs = None
    try:
        t0 = time.perf_counter()
        logging.info(f"DB VBA-RUNNER [Windows/VBS]: Launching cscript for {raw_xlsx.name}")

        tmp_vbs = tempfile.NamedTemporaryFile(
            mode='w', suffix='.vbs', delete=False, encoding='utf-8'
        )
        tmp_vbs.write(vbs_content)
        tmp_vbs.close()

        result = _sp.run(
            ['cscript.exe', '//Nologo', tmp_vbs.name],
            capture_output=True, text=True, timeout=900,
        )

        logging.info(f"DB VBA-RUNNER [Windows/VBS] stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            logging.warning(f"DB VBA-RUNNER [Windows/VBS] stderr: {result.stderr.strip()}")

        elapsed = time.perf_counter() - t0
        output = result.stdout.strip()
        err_output = result.stderr.strip()

        if result.returncode == 0 and output == 'OK':
            logging.info(f"DB VBA-RUNNER [Windows/VBS]: Completed in {elapsed:.1f}s")
            return jsonify({
                'success': True,
                'output': 'Macro executed on ' + raw_xlsx.name,
                'elapsed': round(elapsed, 1),
                'message': f'{macro_name} completed successfully',
            })
        else:
            error_msg = output or err_output or 'Unknown error from cscript'
            if 'VBA access blocked' in error_msg:
                return jsonify({
                    'error': 'Excel blocked VBA access. Fix: Excel > File > Options > '
                             'Trust Center > Trust Center Settings > Macro Settings > '
                             'check "Trust access to the VBA project object model", then retry.'
                }), 403
            return jsonify({'error': error_msg}), 500

    except _sp.TimeoutExpired:
        logging.error("DB VBA-RUNNER [Windows/VBS]: Timed out after 15 minutes")
        return jsonify({
            'error': 'Excel automation timed out (15 min). The macro may still be running in Excel.'
        }), 504
    except Exception as e:
        logging.exception(f"DB VBA-RUNNER [Windows/VBS]: Failed: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if tmp_vbs:
            try:
                os.unlink(tmp_vbs.name)
            except OSError:
                pass


def _db_vba_runner_mac(raw_xlsx, vba_file, macro_name):
    """Run VBA macro using AppleScript (macOS only)."""
    import subprocess as _sp
    import time

    try:
        t0 = time.perf_counter()
        logging.info(f"DB VBA-RUNNER [Mac]: Opening Excel and running VBA on {raw_xlsx.name}")

        xlsx_path = str(raw_xlsx)

        def _osa(script, label, timeout=30):
            r = _sp.run(['osascript', '-e', script],
                        capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                err = r.stderr.strip()
                logging.error(f"DB VBA-RUNNER [{label}]: {err}")
                raise RuntimeError(f'{label}: {err}')
            logging.info(f"DB VBA-RUNNER [{label}]: OK - {r.stdout.strip()}")
            return r.stdout.strip()

        # Close existing workbooks
        _osa('''
tell application "Microsoft Excel"
    activate
    try
        close every workbook without saving
    end try
end tell
return "closed"
''', 'close-all')

        time.sleep(1)

        # Open the file
        _osa(f'''
tell application "Microsoft Excel"
    open "{xlsx_path}"
end tell
delay 5
tell application "Microsoft Excel"
    return name of active workbook
end tell
''', 'open-file', timeout=60)

        # Copy VBA to clipboard
        vba_code = vba_file.read_bytes()
        _sp.run(['pbcopy'], input=vba_code, timeout=10)
        logging.info("DB VBA-RUNNER [clipboard]: VBA code copied")

        # Record a dummy macro to create a VB module
        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Record New Macro..." of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
delay 1.5
tell application "System Events"
    keystroke return
end tell
delay 1
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Stop Recording" of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
return "recorded"
''', 'record-macro')

        time.sleep(1)

        # Open VBE editor via Macros dialog
        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Macros..." of menu 1 of menu item "Macro" of menu "Tools" of menu bar 1
    end tell
end tell
delay 2
tell application "System Events"
    tell process "Microsoft Excel"
        click button "Edit" of front window
    end tell
end tell
delay 2
return "vbe-open"
''', 'open-vbe-edit')

        # Re-copy and paste VBA code
        _sp.run(['pbcopy'], input=vba_code, timeout=10)

        _osa('''
tell application "System Events"
    tell process "Microsoft Excel"
        click menu item "Select All" of menu "Edit" of menu bar 1
        delay 0.5
        click menu item "Paste" of menu "Edit" of menu bar 1
        delay 5
    end tell
end tell
return "pasted"
''', 'paste-vba', timeout=30)

        # Run the macro
        _osa(f'''
tell application "Microsoft Excel"
    activate
end tell
delay 1
tell application "Microsoft Excel"
    run VB macro "{macro_name}"
end tell
tell application "Microsoft Excel"
    save active workbook
end tell
return "done"
''', 'run-macro', timeout=900)

        elapsed = time.perf_counter() - t0
        logging.info(f"DB VBA-RUNNER [Mac]: Macro completed in {elapsed:.1f}s")

        return jsonify({
            'success': True,
            'output': 'Macro executed on ' + raw_xlsx.name,
            'elapsed': round(elapsed, 1),
            'message': f'{macro_name} completed successfully',
        })

    except _sp.TimeoutExpired:
        logging.error("DB VBA-RUNNER [Mac]: Timed out after 15 minutes")
        return jsonify({'error': 'Excel automation timed out (15 min). The macro may still be running in Excel.'}), 504
    except Exception as e:
        logging.exception(f"DB VBA-RUNNER [Mac]: Failed: {e}")
        return jsonify({'error': str(e)}), 500


# ── Email Preview ─────────────────────────────────────────────────


@db_bp.route('/email-preview')
def email_preview():
    """Serve the DB email preview page."""
    return send_from_directory(str(STATIC_DB_DIR), 'email_preview.html')


@db_bp.route('/send-email', methods=['POST'])
def send_email():
    """Send the DB disbursement report via email."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email.mime.text import MIMEText
    from email import encoders

    data = request.get_json(force=True)
    recipients = data.get('recipients', [])
    subject = data.get('subject', 'Disbursement Report')

    gmail_user = config.GMAIL_USER
    gmail_pass = config.GMAIL_APP_PASSWORD

    if not gmail_user or not gmail_pass:
        return jsonify({'success': False, 'message': 'Email not configured. Set GMAIL_USER and GMAIL_APP_PASSWORD.'}), 400

    if not recipients:
        return jsonify({'success': False, 'message': 'No recipients provided.'}), 400

    report_file = DB_PROCESSED_DIR / 'DB_Processed_Latest.xlsx'
    if not report_file.exists():
        return jsonify({'success': False, 'message': 'No processed DB report available.'}), 404

    try:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        server.starttls()
        server.login(gmail_user, gmail_pass)

        from datetime import datetime
        today_str = datetime.now().strftime('%d-%m-%Y')
        sent = 0
        failed = 0

        for recipient in recipients:
            email_addr = recipient.get('email', '').strip()
            if not email_addr:
                continue

            try:
                msg = MIMEMultipart()
                msg['From'] = gmail_user
                msg['To'] = email_addr
                msg['Subject'] = subject or f'Disbursement Report - {today_str}'

                body = f'''<html><body>
<p>Dear Team,</p>
<p>Please find attached the Disbursement Report dated {today_str}.</p>
<p>Regards,<br>Disbursement Reporting System</p>
</body></html>'''
                msg.attach(MIMEText(body, 'html'))

                with open(report_file, 'rb') as f:
                    part = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="DB_Disbursement_Report_{today_str}.xlsx"')
                    msg.attach(part)

                server.send_message(msg)
                sent += 1
                logging.info(f'DB email sent to {email_addr}')
            except Exception as e:
                failed += 1
                logging.error(f'DB email failed for {email_addr}: {e}')

        server.quit()
        return jsonify({
            'success': True,
            'message': f'Sent {sent} email(s)' + (f', {failed} failed' if failed else ''),
            'sent': sent,
            'failed': failed
        })
    except Exception as e:
        logging.error(f'DB email error: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


# ── Sync to Database (dummy) ─────────────────────────────────────


@db_bp.route('/sync-to-dashboard', methods=['POST'])
def sync_to_dashboard():
    """Dummy sync endpoint — returns success with timestamp."""
    from datetime import datetime
    ts = datetime.now().strftime('%d-%b-%Y %I:%M:%S %p')
    return jsonify({
        'success': True,
        'message': f'Synced as of {ts}'
    })


# ── Google Drive endpoints (no API key — public folders only) ─────


@db_bp.route('/gdrive-config', methods=['GET'])
def gdrive_config_get():
    """Return the saved GDrive folder URL for DB module."""
    from services.gdrive import load_gdrive_config
    cfg_path = config.DATA_DIR / 'db_gdrive_config.json'
    cfg = load_gdrive_config(cfg_path)
    return jsonify({'success': True, 'folder_url': cfg.get('folder_url', '')})


@db_bp.route('/gdrive-config', methods=['POST'])
def gdrive_config_save():
    """Save the GDrive folder URL for DB module."""
    from services.gdrive import save_gdrive_config
    body = request.get_json(silent=True) or {}
    folder_url = body.get('folder_url', '').strip()
    cfg_path = config.DATA_DIR / 'db_gdrive_config.json'
    save_gdrive_config(cfg_path, {'folder_url': folder_url})
    return jsonify({'success': True})


@db_bp.route('/gdrive-scan', methods=['POST'])
def gdrive_scan_endpoint():
    """Scan a public Google Drive folder for DB report files (.xlsx/.csv)."""
    try:
        from services.gdrive import (
            parse_folder_id, list_folder_files_public,
            save_gdrive_config,
        )

        body = request.get_json(silent=True) or {}
        folder_url = body.get('folder_url', '').strip()
        if not folder_url:
            return jsonify({'success': False, 'message': 'No folder URL provided.'}), 400

        folder_id = parse_folder_id(folder_url)
        if not folder_id:
            return jsonify({'success': False, 'message': 'Could not parse folder ID from URL.'}), 400

        # Persist the folder URL
        cfg_path = config.DATA_DIR / 'db_gdrive_config.json'
        save_gdrive_config(cfg_path, {'folder_url': folder_url})

        logging.info(f"DB GDrive scan: folder_id={folder_id}")
        all_files = list_folder_files_public(folder_id)

        # Filter for Excel/CSV files
        valid_exts = ('.xlsx', '.csv', '.xls')
        spreadsheet_files = [
            f for f in all_files
            if any(f['name'].lower().endswith(ext) for ext in valid_exts)
        ]

        if not spreadsheet_files:
            return jsonify({
                'success': False,
                'message': 'No spreadsheet files (.xlsx, .csv) found in folder.',
                'available_files': [f['name'] for f in all_files],
            })

        return jsonify({
            'success': True,
            'files': spreadsheet_files,
            'total_files': len(all_files),
        })

    except Exception as e:
        logging.error(f"DB GDrive scan error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@db_bp.route('/gdrive-download', methods=['POST'])
def gdrive_download_endpoint():
    """Download a chosen file from Google Drive for DB processing."""
    try:
        from services.gdrive import download_file as gdrive_download

        body = request.get_json(silent=True) or {}
        file_id = body.get('file_id')
        file_name = body.get('file_name', 'db_report.xlsx')

        if not file_id:
            return jsonify({'success': False, 'message': 'Missing file_id.'}), 400

        gdrive_dir = config.GDRIVE_DOWNLOAD_DIR
        gdrive_dir.mkdir(parents=True, exist_ok=True)

        logging.info(f"DB GDrive: downloading {file_name}")
        file_path = gdrive_download(file_id, gdrive_dir / file_name)
        logging.info(f"DB GDrive: downloaded ({file_path.stat().st_size / (1024*1024):.1f} MB)")

        return jsonify({
            'success': True,
            'file_path': str(file_path),
            'file_name': file_name,
            'file_size': file_path.stat().st_size,
        })

    except Exception as e:
        logging.error(f"DB GDrive download error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@db_bp.route('/gdrive-serve', methods=['GET'])
def gdrive_serve_file():
    """Serve a downloaded GDrive file so the browser can fetch it for SheetJS processing."""
    file_name = request.args.get('name', '')
    if not file_name:
        return jsonify({'error': 'No filename'}), 400

    file_path = config.GDRIVE_DOWNLOAD_DIR / file_name
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_file(
        file_path,
        as_attachment=False,
        download_name=file_name,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ── Static file catch-all (must be LAST) ────────────────────────


@db_bp.route('/<path:filename>')
def serve_static(filename):
    """Serve DB module static files (CSS, JS)."""
    return send_from_directory(str(STATIC_DB_DIR), filename)


def _find_backend_file():
    """Find the backend reference file (xlsx or csv)."""
    for ext in ('xlsx', 'csv'):
        p = config.DB_DIR / ('backend_data.' + ext)
        if p.exists():
            return p
    return None


def _parse_backend_file(path):
    """Parse backend reference file into a BranchID → info lookup dict.

    Expected columns (case-insensitive match):
        BRANCH ID  → key
        NEW REGION → region
        RM NAME    → rmName
        AREA OFFICE / DISTRICT OFFICE → area
        AREA MANAGER NAME / DISTRICT MANAGER NAME → amName

    Returns (lookup_dict, row_count).
    """
    import openpyxl
    import csv

    lookup = {}
    row_count = 0

    ext = path.suffix.lower()

    # Column name mapping (lowercase stripped → json key)
    # Supports new 'Area' columns with fallback to legacy 'District' columns
    COL_MAP = {
        'branch id': 'branchId',
        'new region': 'region',
        'rm name': 'rmName',
        'area office': 'area',
        'district office': 'area',
        'area manager name': 'amName',
        'district manager name': 'amName',
    }

    if ext == '.xlsx':
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header_row = next(rows, None)
        if not header_row:
            wb.close()
            return lookup, 0

        # Map column indices
        col_indices = {}
        for i, h in enumerate(header_row):
            key = str(h).strip().lower() if h else ''
            if key in COL_MAP:
                col_indices[COL_MAP[key]] = i

        branch_idx = col_indices.get('branchId')
        if branch_idx is None:
            wb.close()
            return lookup, 0

        for row in rows:
            branch_val = row[branch_idx] if branch_idx < len(row) else None
            if branch_val is None or str(branch_val).strip() == '':
                continue
            branch_key = str(int(branch_val)) if isinstance(branch_val, (int, float)) else str(branch_val).strip()
            entry = {}
            for json_key, col_idx in col_indices.items():
                if json_key == 'branchId':
                    continue
                val = row[col_idx] if col_idx < len(row) else ''
                entry[json_key] = str(val).strip() if val else ''
            lookup[branch_key] = entry
            row_count += 1

        wb.close()

    elif ext == '.csv':
        with open(str(path), 'r', encoding='utf-8-sig') as fh:
            reader = csv.reader(fh)
            header_row = next(reader, None)
            if not header_row:
                return lookup, 0

            col_indices = {}
            for i, h in enumerate(header_row):
                key = h.strip().lower()
                if key in COL_MAP:
                    col_indices[COL_MAP[key]] = i

            branch_idx = col_indices.get('branchId')
            if branch_idx is None:
                return lookup, 0

            for row in reader:
                branch_val = row[branch_idx] if branch_idx < len(row) else ''
                if not branch_val.strip():
                    continue
                branch_key = branch_val.strip()
                # Normalize numeric branch IDs (remove .0)
                try:
                    branch_key = str(int(float(branch_key)))
                except (ValueError, OverflowError):
                    pass
                entry = {}
                for json_key, col_idx in col_indices.items():
                    if json_key == 'branchId':
                        continue
                    val = row[col_idx] if col_idx < len(row) else ''
                    entry[json_key] = val.strip()
                lookup[branch_key] = entry
                row_count += 1

    return lookup, row_count
