"""Flask application factory for NLPL Status.

Thin shell around the reused EOD engine. All EOD routes come from the source
project's ``blueprints.eod`` blueprint (mounted at ``/eod``). We only add a
couple of app-level convenience endpoints (health + module registry) that the
React frontend uses.
"""
import settings  # local settings (NOT the engine's config)

settings.bootstrap()  # must run before importing the engine modules below

import config as engine_config  # noqa: E402  (the source project's config)
from blueprints.eod import eod_bp  # noqa: E402
from blueprints.hourly import hourly_bp  # noqa: E402
from blueprints.quick import quick_bp  # noqa: E402  (migrated: Quick Report)
from blueprints.quick_month_end import quick_month_end_bp  # noqa: E402  (migrated)
from blueprints.ondate import ondate_bp  # noqa: E402  (migrated: On-Date Report)
from blueprints.od_report import od_report_bp  # noqa: E402  (migrated: OD Report)
from blueprints.db import db_bp  # noqa: E402  (migrated: Disbursement Report)
from blueprints.instant import instant_bp  # noqa: E402  (migrated: Instant Report)
from blueprints.disbursement import disbursement_bp  # noqa: E402  (migrated: Disbursement EC2 sync)
from blueprints.growwithme_sync import growwithme_bp  # noqa: E402  (Phase 2: GrowwithmeDB sync — AWS EC2)
from blueprints.analytics import analytics_bp  # noqa: E402  (migrated: Analytics)
from blueprints.employee import employee_bp  # noqa: E402  (migrated: Employee Performance)
from blueprints.emp_login import emp_login_bp  # noqa: E402  (migrated: Employee login)
from blueprints.emp_v3 import emp_v3_bp  # noqa: E402  (migrated: Employee v3)

import retention  # noqa: E402  (local 3-day run-artifact retention)
import email_service  # noqa: E402  (in-app Gmail login — centralized for all modules)
import whatsapp_service  # noqa: E402  (centralized WhatsApp session/contacts)
import report_history  # noqa: E402  (generic per-run report archive engine)
import reports_archive  # noqa: E402  (per-date report archive — EOD)
import hourly_reports_archive  # noqa: E402  (hourly reports archive)
import process_jobs  # noqa: E402  (in-process job registry for status + cancellation)

from flask import Flask, jsonify, request, send_file, send_from_directory  # noqa: E402
from flask_cors import CORS  # noqa: E402


# Static metadata describing the modules the app exposes. Adding a new module
# later is a one-line addition here + a matching frontend entry.
MODULES = [
    {
        "id": "eod",
        "name": "EOD Module",
        "description": "Regular Demand vs Collection — process daily files, generate reports, email and WhatsApp them.",
        "status": "live",
    },
    {
        "id": "hourly",
        "name": "Hourly Module",
        "description": "Merge hourly collection data onto EOD Output — generate hourly reports, download VBA templates, and send via WhatsApp.",
        "status": "live",
    },
    {
        "id": "quick",
        "name": "Quick Report",
        "description": "One-shot PAR + Collection + hourly Collection Report → final hourly fast report, with dashboard sync.",
        "status": "live",
    },
    {
        "id": "quick_month_end",
        "name": "Month-End Report",
        "description": "Demand + Last Month PAR + PAR + Collection → month-end Employee report (regular rules) with EOD output, EOD report and portfolio sync.",
        "status": "live",
    },
    {
        "id": "ondate",
        "name": "On-Date Report",
        "description": "Extract On-Date report sheets per date into a monthly master workbook, preserving full formatting.",
        "status": "live",
    },
    {
        "id": "od_report",
        "name": "OD Report",
        "description": "Overdue (OD) report from PAR + month-end + insurance files — FTOD and Insurance-OD analysis, saved to Downloads.",
        "status": "live",
    },
    {
        "id": "disbursement_report",
        "name": "Disbursement Report",
        "description": "Enrich a disbursement export (Product Name, Region/Area via BranchID, Employee ID), build the report, email and run VBA.",
        "status": "live",
    },
    {
        "id": "instant",
        "name": "Instant Report",
        "description": "PAR + Collection → instant pivot summaries (Regular, DPD buckets, NPA) with per-date history and monthly backend data.",
        "status": "live",
    },
    {
        "id": "disbursement_ec2",
        "name": "Disbursement Sync",
        "description": "Aggregate an ESAF disbursement export by date/branch/officer/product and push it to the Coll_Db EC2 Postgres database.",
        "status": "live",
    },
]


def create_app() -> Flask:
    # static_folder = engine static dir (matches unified-collection-report) so the
    # migrated module UIs' shared assets (/static/common/*, /static/fonts/*,
    # /static/logo.png, …) are served exactly as in the original project.
    app = Flask(__name__, static_folder=str(engine_config.STATIC_DIR), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = engine_config.MAX_CONTENT_LENGTH
    app.config["MAX_FORM_MEMORY_SIZE"] = engine_config.MAX_FORM_MEMORY_SIZE
    CORS(app, resources={r"/*": {"origins": "*"}})

    app.register_blueprint(eod_bp, url_prefix="/eod")
    app.register_blueprint(hourly_bp, url_prefix="/hourly")
    app.register_blueprint(quick_bp, url_prefix="/quick")
    app.register_blueprint(quick_month_end_bp, url_prefix="/quick-month-end")
    app.register_blueprint(ondate_bp, url_prefix="/ondate")
    app.register_blueprint(od_report_bp, url_prefix="/od-report")
    app.register_blueprint(db_bp, url_prefix="/db")
    app.register_blueprint(instant_bp, url_prefix="/instant")
    app.register_blueprint(disbursement_bp, url_prefix="/disbursement")
    app.register_blueprint(growwithme_bp, url_prefix="/growwithme")  # Phase 2: GrowwithmeDB sync (AWS EC2)
    app.register_blueprint(analytics_bp, url_prefix="/analytics")  # migrated: Analytics
    app.register_blueprint(employee_bp, url_prefix="/employee")  # migrated: Employee Performance
    app.register_blueprint(emp_login_bp, url_prefix="/emp-login")  # migrated: Employee login
    app.register_blueprint(emp_v3_bp, url_prefix="/emp-v3")  # migrated: Employee v3

    # Start the 3-day run-artifact retention daemon (sweeps now + on a timer).
    retention.start(engine_config.DATA_DIR)

    @app.get("/api/health")
    def health():
        email_ready = bool(engine_config.GMAIL_USER and engine_config.GMAIL_APP_PASSWORD)
        return jsonify(
            {
                "success": True,
                "service": "nlpl_status",
                "modules": [m["id"] for m in MODULES],
                "enginePath": str(settings.UNIFIED_COLLECTION_DIR),
                "dataPath": str(engine_config.DATA_DIR),
                "backendDataPath": str(engine_config.BACKEND_DATA_DIR),
                "email": {
                    "configured": email_ready,
                    "sender": engine_config.GMAIL_USER if email_ready else "",
                },
                "retention": retention.status(),
            }
        )

    @app.get("/api/modules")
    def modules():
        return jsonify({"modules": MODULES})

    @app.get("/api/sync-config")
    def sync_config():
        """Report which sync credentials are present (values are never returned),
        so the UI/operator can confirm Supabase + EC2 wiring."""
        import os as _os
        from pathlib import Path as _Path
        try:
            from blueprints.disbursement import EC2_KEY, EC2_HOST
        except Exception:
            EC2_KEY, EC2_HOST = "", ""
        return jsonify({
            "supabase": {
                "url": getattr(engine_config, "SUPABASE_URL", ""),
                "serviceKeyConfigured": bool(getattr(engine_config, "SUPABASE_SERVICE_KEY", "")),
            },
            "ec2": {
                "host": EC2_HOST,
                "keyPath": EC2_KEY,
                "keyPresent": bool(EC2_KEY and _Path(EC2_KEY).exists()),
            },
            "colldbUrl": getattr(engine_config, "COLLDB_URL", ""),
            "ec2UploadUrlConfigured": bool(getattr(engine_config, "EC2_UPLOAD_URL", "")),
            "engineEnvPath": str(settings.UNIFIED_COLLECTION_DIR / ".env"),
            "localEnvPath": str(settings.PROJECT_DIR / ".env"),
        })

    @app.get("/api/retention")
    def retention_status():
        return jsonify(retention.status())

    @app.get("/api/retention/run")
    @app.post("/api/retention/run")
    def retention_run():
        return jsonify(retention.run_once())

    # --- in-app Gmail login -------------------------------------------------
    @app.get("/api/email/config")
    def email_config():
        return jsonify(email_service.get_config())

    @app.post("/api/email/login")
    def email_login():
        data = request.get_json(silent=True) or {}
        result = email_service.login(
            data.get("user", ""),
            data.get("appPassword", ""),
            data.get("host", "smtp.gmail.com"),
            data.get("port", 587),
        )
        return jsonify(result), (200 if result.get("success") else 400)

    @app.post("/api/email/logout")
    def email_logout():
        return jsonify(email_service.logout())

    # --- centralized WhatsApp (shared session + contacts across all modules) -
    # Login once (scan the QR) from any module and the session is shared by
    # every module; only the file being sent is module-specific.
    @app.get("/api/whatsapp/contacts")
    def whatsapp_contacts_get():
        return jsonify({"contacts": whatsapp_service.get_contacts()})

    @app.post("/api/whatsapp/contacts")
    def whatsapp_contacts_save():
        data = request.get_json(silent=True) or {}
        saved = whatsapp_service.save_contacts(data.get("contacts", []))
        return jsonify({"success": True, "contacts": saved})

    @app.post("/api/whatsapp/open")
    def whatsapp_open():
        result = whatsapp_service.open_session()
        return jsonify(result), (200 if result.get("success") else 500)

    @app.post("/api/whatsapp/send")
    def whatsapp_send():
        data = request.get_json(silent=True) or {}
        result = whatsapp_service.send_file(
            data.get("bundle_path", ""), data.get("filename", "")
        )
        return jsonify(result), (200 if result.get("success") else 500)

    # --- generic process job status + cancellation -------------------------
    # Cancellation is cooperative: cancel sets a flag the processing code checks
    # at phase boundaries (see process_jobs.py). It cannot kill an in-flight
    # pandas/Excel call mid-operation, but stops a multi-phase run promptly.
    @app.post("/api/<module>/process/<job_id>/cancel")
    def process_cancel(module, job_id):
        # Only set the cancel flag here. Temp-file cleanup happens inside the
        # running /process handler's JobCancelled path — deleting them now would
        # race a still-writing process. The frontend then polls /status until the
        # job reaches 'cancelled' (set in the handler's finally, after the lock
        # is released), guaranteeing the server is free before a restart.
        result = process_jobs.request_cancel(module, job_id)
        code = 200 if result.get("found") else 404
        return jsonify(result), code

    @app.get("/api/<module>/process/<job_id>/status")
    def process_status(module, job_id):
        return jsonify(process_jobs.status_of(module, job_id))

    # --- generic per-run report history (shared engine for migrated modules) -
    # New modules use /api/reports/<module>/... mirroring the EOD/Hourly trio.
    @app.post("/api/reports/<module>/snapshot")
    def module_snapshot_reports(module):
        data = request.get_json(silent=True) or {}
        return jsonify(report_history.snapshot(
            engine_config.DATA_DIR, module, data.get("date", ""), data.get("time", ""),
        ))

    @app.get("/api/reports/<module>/archive")
    def module_report_archive(module):
        return jsonify({"dates": report_history.list_history(engine_config.DATA_DIR, module)})

    @app.get("/api/reports/<module>/file")
    def module_report_archive_file(module):
        path = report_history.file_path(
            engine_config.DATA_DIR, module,
            request.args.get("date", ""),
            request.args.get("run", ""),
            request.args.get("type", ""),
        )
        if not path:
            return jsonify({"error": "Report not found for that run."}), 404
        return send_file(str(path), as_attachment=True, download_name=path.name)

    # --- per-run report history (EOD) --------------------------------------
    # Every generation snapshots its own run folder; history is grouped by date
    # -> runs so previous runs stay downloadable within the 3-day window.
    @app.post("/api/eod/snapshot-reports")
    def snapshot_reports():
        data = request.get_json(silent=True) or {}
        date = data.get("date", "")
        # Prefer the report's OWN date (the "as on" date persisted at processing
        # time) so the archived filename matches the date printed inside the
        # report, not the date the snapshot was taken.
        try:
            from pathlib import Path as _Path
            td_file = _Path(engine_config.BACKEND_DATA_DIR) / ".target_date"
            if td_file.exists():
                td = td_file.read_text().strip()
                if td:
                    date = td
        except Exception:
            pass
        return jsonify(reports_archive.snapshot(engine_config.DATA_DIR, date))

    @app.get("/api/eod/report-archive")
    def report_archive():
        return jsonify({"dates": reports_archive.list_history(engine_config.DATA_DIR)})

    @app.get("/api/eod/report-archive/file")
    def report_archive_file():
        path = reports_archive.file_path(
            engine_config.DATA_DIR,
            request.args.get("date", ""),
            request.args.get("run", ""),
            request.args.get("type", ""),
        )
        if not path:
            return jsonify({"error": "Report not found for that run."}), 404
        return send_file(str(path), as_attachment=True, download_name=path.name)

    # --- per-run report history (Hourly) -----------------------------------
    @app.post("/api/hourly/snapshot-reports")
    def hourly_snapshot_reports():
        data = request.get_json(silent=True) or {}
        return jsonify(hourly_reports_archive.snapshot(
            engine_config.DATA_DIR,
            data.get("date", ""),
            data.get("time", ""),
        ))

    @app.get("/api/hourly/report-archive")
    def hourly_report_archive():
        return jsonify({"dates": hourly_reports_archive.list_history(engine_config.DATA_DIR)})

    @app.get("/api/hourly/report-archive/file")
    def hourly_report_archive_file():
        path = hourly_reports_archive.file_path(
            engine_config.DATA_DIR,
            request.args.get("date", ""),
            request.args.get("run", ""),
            request.args.get("type", ""),
        )
        if not path:
            return jsonify({"error": "Report not found for that run."}), 404
        return send_file(str(path), as_attachment=True, download_name=path.name)

    return app


app = create_app()
