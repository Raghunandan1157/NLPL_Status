"""
End-to-end test script for nlpl_status.
Starts backend, uploads files, runs EOD, downloads outputs.
"""
import subprocess
import sys
import time
import requests
import os
from pathlib import Path

# Configuration
BASE_URL = "http://127.0.0.1:5055"
PROJECT_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = Path.home() / "Downloads"

INPUT_FILES = {
    "collection": DOWNLOADS_DIR / "collection as on 31-05-2026.xlsx",
    "par": DOWNLOADS_DIR / "Par as on 31-05-2026.xlsx",
    "demand": DOWNLOADS_DIR / "Demand_Sheet_Master_Demand_Sheet_Master_May (1).xlsx",
}

OUTPUT_NAMES = {
    "output": DOWNLOADS_DIR / "Regular Demand Vs Collection (1).xlsx",
    "report": DOWNLOADS_DIR / "EOD_Report (2).xlsx",
}

VENV_PYTHON = Path.home() / "Desktop" / "unified-collection-report" / "venv" / "Scripts" / "python.exe"
SERVER_PY = PROJECT_DIR / "backend" / "server.py"

def log(msg):
    print(f"[TEST] {msg}")

def wait_for_server(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{BASE_URL}/api/health", timeout=2)
            if r.status_code == 200:
                log("Backend is ready.")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def main():
    # Verify inputs exist
    for key, p in INPUT_FILES.items():
        if not p.exists():
            log(f"ERROR: Missing input file: {p}")
            sys.exit(1)
        log(f"Found input [{key}]: {p.name} ({p.stat().st_size / 1024 / 1024:.1f} MB)")

    # Start backend server
    log("Starting backend server...")
    env = os.environ.copy()
    env["COLLECTION_DATA_DIR"] = str(PROJECT_DIR / "eod_data")
    proc = subprocess.Popen(
        [str(VENV_PYTHON), str(SERVER_PY)],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not wait_for_server(timeout=60):
            log("ERROR: Backend failed to start within 60s")
            sys.exit(1)

        # Step 1: Upload Demand Master to backend
        log("Uploading Demand Master to backend...")
        with open(INPUT_FILES["demand"], "rb") as f:
            r = requests.post(
                f"{BASE_URL}/eod/save-backend-file",
                files={"file": (INPUT_FILES["demand"].name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"type": "masterDemand"},
                timeout=120,
            )
        log(f"save-backend-file response: {r.status_code} — {r.text[:200]}")
        if r.status_code not in (200, 201):
            log("ERROR: Demand Master upload failed")
            sys.exit(1)

        # Step 2: Cache PAR and Collection
        log("Caching PAR file...")
        with open(INPUT_FILES["par"], "rb") as f:
            r = requests.post(
                f"{BASE_URL}/eod/cache-file",
                files={"file": (INPUT_FILES["par"].name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"type": "par"},
                timeout=120,
            )
        log(f"cache-file (par) response: {r.status_code} — {r.text[:200]}")

        log("Caching Collection file...")
        with open(INPUT_FILES["collection"], "rb") as f:
            r = requests.post(
                f"{BASE_URL}/eod/cache-file",
                files={"file": (INPUT_FILES["collection"].name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"type": "collection"},
                timeout=120,
            )
        log(f"cache-file (collection) response: {r.status_code} — {r.text[:200]}")

        # Step 3: Run EOD process
        log("Running EOD process (this may take a while)...")
        with open(INPUT_FILES["par"], "rb") as par_f, \
             open(INPUT_FILES["collection"], "rb") as coll_f, \
             open(INPUT_FILES["demand"], "rb") as dem_f:
            r = requests.post(
                f"{BASE_URL}/eod/process",
                files={
                    "par": (INPUT_FILES["par"].name, par_f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    "collection": (INPUT_FILES["collection"].name, coll_f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    "demand": (INPUT_FILES["demand"].name, dem_f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                },
                data={
                    "targetDate": "31-05-2026",
                    "useBackendDemand": "true",
                    "useLastCache": "false",
                    "cachePar": "true",
                    "cacheCollection": "true",
                    "autoFixSheets": "false",
                },
                timeout=None,
            )
        log(f"process response: {r.status_code} — {r.text[:500]}")
        if r.status_code not in (200, 201):
            log("ERROR: EOD processing failed")
            sys.exit(1)

        # Step 4: Download outputs
        log("Downloading EOD Output (Regular Demand Vs Collection)...")
        r = requests.get(f"{BASE_URL}/eod/download-output", timeout=60)
        if r.status_code == 200:
            OUTPUT_NAMES["output"].write_bytes(r.content)
            log(f"Saved: {OUTPUT_NAMES['output']} ({len(r.content)} bytes)")
        else:
            log(f"ERROR downloading output: {r.status_code} — {r.text[:200]}")

        log("Downloading EOD Report...")
        r = requests.get(f"{BASE_URL}/eod/download-report", timeout=60)
        if r.status_code == 200:
            OUTPUT_NAMES["report"].write_bytes(r.content)
            log(f"Saved: {OUTPUT_NAMES['report']} ({len(r.content)} bytes)")
        else:
            log(f"ERROR downloading report: {r.status_code} — {r.text[:200]}")

        # Final verification
        ok = True
        for key, p in OUTPUT_NAMES.items():
            if p.exists():
                size_mb = p.stat().st_size / 1024 / 1024
                log(f"VERIFIED [{key}]: {p} ({size_mb:.2f} MB)")
            else:
                log(f"MISSING [{key}]: {p}")
                ok = False

        if ok:
            log("SUCCESS: All outputs generated and saved correctly.")
        else:
            log("FAILURE: Some outputs are missing.")
            sys.exit(1)

    finally:
        log("Stopping backend server...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log("Backend stopped.")

if __name__ == "__main__":
    main()
