# NLPL Status — Operations Console

A clean, scalable, module-based console for daily collection operations.
The first (and currently only live) module is **EOD — Regular Demand vs Collection**,
which recreates the full EOD experience from the `unified-collection-report`
project with a fresh, polished UI and a one-command startup.

> The heavy EOD processing engine (Excel reading, DuckDB pipeline, report
> building, email + WhatsApp services) is **reused** from the sibling
> `unified-collection-report` project rather than reimplemented. This app is a
> modern frontend + a thin Flask shell that mounts that engine's `eod`
> blueprint.

---

## Run it (one command)

```bash
npm install      # first time only
npm run dev
```

`npm run dev` starts the **backend**, the **frontend**, waits until both are
healthy, then **opens the browser** automatically at <http://127.0.0.1:5174>.
Press `Ctrl+C` to stop both.

- Backend (Flask): <http://127.0.0.1:5055>
- Frontend (Vite/React): <http://127.0.0.1:5174>

The launcher (`scripts/dev.mjs`) prefers the `unified-collection-report/venv`
Python (which already has every dependency), falling back to system `python`.

---

## Project structure

```
nlpl_Status/
├── index.html                 # SPA entry
├── vite.config.js             # React plugin; ignores data dirs in the watcher
├── package.json               # npm scripts (dev / build / preview)
├── .env.example               # copy to .env for Gmail / engine overrides
├── scripts/
│   └── dev.mjs                # one-command launcher (backend + frontend + browser)
├── backend/                   # thin Flask shell (reuses the engine)
│   ├── settings.py            # locates engine, loads .env, wires data dir
│   ├── app.py                 # app factory: mounts /eod blueprint + /api/health
│   ├── server.py              # run entry (`python backend/server.py`)
│   └── requirements.txt
├── src/                       # frontend
│   ├── main.jsx
│   ├── App.jsx                # app shell: sidebar + topbar + routing
│   ├── styles/global.css      # design system (tokens, buttons, cards, modals…)
│   ├── lib/                   # apiClient (fetch + SSE helpers), format helpers
│   ├── components/ui.jsx      # shared UI: Button, Modal, Toast, FileDrop, …
│   ├── modules/registry.js    # module registry (add new modules here)
│   ├── pages/                 # HomePage + home.css
│   └── eod/                   # ALL EOD UI lives here
│       ├── EodModule.jsx      # EOD dashboard shell (tabbed)
│       ├── api.js             # every EOD endpoint call
│       ├── eod.css
│       └── components/        # UploadProcess / Reports / Email / WhatsApp panels
├── eod_data/                  # engine data dir for THIS project (isolated)
│   ├── backend/               # demand master, latest outputs, extracted sheets
│   ├── db/                    # DuckDB + cache (PAR/Collection)
│   └── archive/               # per-session input/output/log archive
└── test/
    ├── ui_smoke.py            # headless UI smoke test (no console errors)
    └── screenshots/           # captured by the smoke test
```

### Adding a new module (scalability)

1. Build the module UI under `src/<module>/` exposing a default component.
2. Add one entry to `src/modules/registry.js` (`status: "live"` + `Component`).
   The home grid and sidebar pick it up automatically.

---

## EOD features (migrated from `unified-collection-report`)

All buttons are wired to real backend endpoints (the reused `eod` blueprint):

| Area | What it does | Endpoint(s) |
|------|--------------|-------------|
| **Upload & Process** | Upload PAR/Collection/Demand, cache files, run the 7-step pipeline with a live log + progress | `/eod/cache-file`, `/eod/process`, `/eod/events` (SSE) |
| **Demand Master** | Upload the master demand file to backend + DuckDB; clear DB | `/eod/save-backend-file`, `/eod/ingest-single-to-db`, `/eod/clear-db`, `/eod/db-status` |
| **Reports** | Generate Employee and Daily/Hourly reports | `/eod/generate-employee-report`, `/eod/generate-daily-hourly-report` |
| **Downloads** | Output, EOD report, employee, accounts, daily, hourly | `/eod/download-*` |
| **Email** | Pick sheets per recipient (combined/separate), auto-assign from config, save, and **batch-send with live per-recipient progress** | `/eod/report-sheet-names`, `/eod/email-config`, `/eod/auto-assign-branches`, `/eod/send-batch-email` (SSE) |
| **WhatsApp** | Open WhatsApp Web, manage contacts, send the latest report to all contacts | `/eod/whatsapp-open`, `/eod/whatsapp-contacts`, `/eod/whatsapp-send` |
| **Status** | Backend / DB / email-config health on Home + EOD | `/api/health`, `/eod/db-status`, `/eod/last-cache`, `/eod/backend-files-status` |

### Reports & Downloads (by date)

After each run the UI snapshots both reports into
`eod_data/reports_archive/<date>/`. The **Reports & Downloads** tab shows a date
rail and, per date, exactly two downloads: **Regular Demand vs Collection
Report** and **EOD Report**. These archives are auto-deleted after 3 days
(same retention job).

### Enabling Email & WhatsApp

- **Email** — click **Connect Gmail** on the Email tab and sign in with a Gmail
  **App Password** (`myaccount.google.com/apppasswords`, needs 2-Step
  Verification). Credentials are validated against Gmail's SMTP server, then
  saved to `.env` and applied immediately (no restart). You can also pre-fill
  `.env` from `.env.example`. **Sign out** clears them.
- **WhatsApp** — the Playwright Chromium browser is required (already installed
  in this project). Click **Connect** on the WhatsApp tab and scan the QR code
  once; the session persists in `eod_data/whatsapp-profile`. If you ever need to
  reinstall the browser:
  ```bash
  "C:\Users\<you>\Desktop\unified-collection-report\venv\Scripts\python.exe" -m playwright install chromium
  ```

---

## Automatic data retention (3 days)

Run artifacts are **physically deleted** (not hidden) after 3 days by a
background daemon in the backend (`backend/retention.py`). It runs one sweep at
startup and then every 12 hours.

- **Purged when older than 3 days:** per-session archives (`archive/**/Session_*`),
  cached PAR/Collection (`db/cache/*`), generated outputs
  (`backend/EOD_Output_*`, `EOD_Report_*`, `Employee_Report_*`), extracted email
  sheets (`backend/sheets/*`), the rendered email image, and `temp/` + `reports/`.
- **Always kept (so EOD keeps working):** the Demand Master file, the DuckDB
  database, `email_config.csv`, `whatsapp_contacts.csv`, auto-assign config
  (`email_sheet_config.xlsx`/`branch_emails.xlsx`), and `*.json` config.

Tunable via env: `NLPL_RETENTION_DAYS` (default `3`),
`NLPL_RETENTION_SWEEP_HOURS` (default `12`). Status is exposed at
`GET /api/retention`; `POST /api/retention/run` triggers a sweep immediately.
Verified by `test/retention_test.py` (runs against a throwaway dir).

## Testing

- `npm run build` — production build (verifies no import/JSX errors).
- `test/ui_smoke.py` — headless render of Home → EOD → all tabs; fails on any
  console/runtime error. Run while `npm run dev` is up:
  ```bash
  "<unified-venv>/python.exe" test/ui_smoke.py
  ```
- `test_run.py` / `backend/run_eod_test.py` — end-to-end processing tests
  (require the input Excel files in your Downloads folder).
