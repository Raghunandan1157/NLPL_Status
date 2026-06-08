from pathlib import Path
import shutil
import time

from werkzeug.datastructures import FileStorage

from server import app


DOWNLOADS = Path.home() / "Downloads"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = DOWNLOADS / "nlpl_Status_EOD_Test_31-05-2026"

FILES = {
    "par": DOWNLOADS / "Par as on 31-05-2026.xlsx",
    "collection": DOWNLOADS / "collection as on 31-05-2026.xlsx",
    "demand": DOWNLOADS / "Demand_Sheet_Master_Demand_Sheet_Master_May.xlsx",
}


def post_file(client, path, file_key, form_type, source):
    with source.open("rb") as handle:
        data = {
            "type": form_type,
            file_key: FileStorage(
                stream=handle,
                filename=source.name,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        return client.post(path, data=data, content_type="multipart/form-data")


def main():
    for label, path in FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing {label} file: {path}")

    client = app.test_client()
    started = time.time()

    print("Saving Demand Master to backend DB...")
    demand_resp = post_file(client, "/eod/save-backend-file", "file", "masterDemand", FILES["demand"])
    print("Demand status:", demand_resp.status_code, demand_resp.get_json())
    if demand_resp.status_code >= 400:
        raise SystemExit(1)

    print("Running EOD process...")
    with FILES["par"].open("rb") as par_handle, FILES["collection"].open("rb") as collection_handle:
        data = {
            "targetDate": "31-05-2026",
            "useBackendDemand": "true",
            "useLastCache": "false",
            "cachePar": "true",
            "cacheCollection": "true",
            "autoFixSheets": "false",
            "par": FileStorage(
                stream=par_handle,
                filename=FILES["par"].name,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "collection": FileStorage(
                stream=collection_handle,
                filename=FILES["collection"].name,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        process_resp = client.post("/eod/process", data=data, content_type="multipart/form-data")

    payload = process_resp.get_json(silent=True)
    print("Process status:", process_resp.status_code, payload)
    if process_resp.status_code >= 400:
        raise SystemExit(1)

    backend_dir = PROJECT_ROOT / "eod_data" / "backend"
    outputs = [
        backend_dir / "EOD_Output_Latest.xlsx",
        backend_dir / "EOD_Report_Latest.xlsx",
        backend_dir / "Employee_Report_Latest.xlsx",
        backend_dir / "Employee_Report_Accounts_Latest.xlsx",
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in outputs:
        if source.exists():
            target = OUTPUT_DIR / source.name
            shutil.copy2(source, target)
            copied.append((target.name, target.stat().st_size))

    print("Copied reports:")
    for name, size in copied:
        print(f"  {name} ({size / 1024 / 1024:.2f} MB)")

    print(f"Output folder: {OUTPUT_DIR}")
    print(f"Elapsed: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
