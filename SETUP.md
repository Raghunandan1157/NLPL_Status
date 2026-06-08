# NLPL Status — Setup & Run (standalone)

This project is **self-contained**. The EOD engine (`config.py` + `services/`) is
vendored at `backend/engine/`, so you do **not** need the `unified-collection-report`
project anymore.

## Prerequisites
- Node.js 18+ (for the frontend)
- Python 3.10–3.12 (for the backend)

## 1. Clone
```bash
git clone https://github.com/Raghunandan1157/NLPL_Status.git
cd NLPL_Status
```

## 2. Frontend
```bash
npm install
npm run dev      # dev server on http://127.0.0.1:5174
# or: npm run build   # production build into dist/
```

## 3. Backend
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r backend/requirements.txt

# (optional) only if you use the WhatsApp-send feature:
python -m playwright install chromium

python backend/app.py    # API on http://127.0.0.1:5055
```

Or on Windows just run `start.bat` (starts backend + frontend together).

## 4. Data
- `eod_data/` (inputs, outputs, cache, archive, DuckDB) is **not** in the repo —
  it's created automatically on first run.
- Seed/master files (Demand Sheet, Last Month PAR, etc.) are uploaded through the
  UI per module; the app stores them under `eod_data/` going forward.

## Notes
- Secrets: copy `.env.example` to `.env` and fill in Gmail / Supabase / EC2 values
  if you use email, Supabase sync, or the EC2 disbursement push. The app runs
  without them; those specific features just stay disabled.
- Advanced: to develop against an external engine checkout instead of the vendored
  one, set `UNIFIED_COLLECTION_DIR=/path/to/unified-collection-report`.
