"""Local backend settings for the NLPL Status app.

The heavy EOD processing engine (Excel reading, DuckDB pipeline, report
building, email + WhatsApp services) lives in the sibling
``unified-collection-report`` project. We deliberately REUSE that engine rather
than fork ~4400 lines of working logic. This module's only job is to:

  1. Locate the source engine project.
  2. Load local secrets from a project ``.env`` file (Gmail app password, etc.).
  3. Wire the ``COLLECTION_*`` environment variables the engine's ``config``
     module reads, pointing its data dir at THIS project's ``eod_data`` folder
     so inputs/outputs/cache stay isolated from the source project.

IMPORTANT: this module is intentionally **not** named ``config`` so it never
shadows the engine's own top-level ``config`` module once it is on ``sys.path``.
"""
from pathlib import Path
import os
import sys

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DESKTOP_DIR = PROJECT_DIR.parent

# This project's isolated data directory (inputs, outputs, cache, archive).
DATA_DIR = PROJECT_DIR / "eod_data"

# The EOD engine (config.py + services/) is VENDORED inside this project at
# backend/engine, so nlpl_status runs standalone — no external project needed.
VENDORED_ENGINE_DIR = (BACKEND_DIR / "engine").resolve()

# Optional external engine override: set UNIFIED_COLLECTION_DIR to use a sibling
# 'unified-collection-report' checkout instead of the vendored copy (dev only).
_external_engine = os.environ.get("UNIFIED_COLLECTION_DIR")
ENGINE_DIR = Path(_external_engine).resolve() if _external_engine else VENDORED_ENGINE_DIR

# Kept for .env discovery + backward-compat references.
UNIFIED_COLLECTION_DIR = ENGINE_DIR

HOST = os.environ.get("NLPL_HOST", "127.0.0.1")
PORT = int(os.environ.get("NLPL_PORT", "5055"))

_bootstrapped = False


def _load_env_file(env_path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file without any third-party dep.
    Uses setdefault, so values already in the environment (or loaded from an
    earlier, higher-priority file) win."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _load_dotenv() -> None:
    """Load secrets from .env files.

    Precedence (first wins, via setdefault):
      1. This project's .env            (nlpl_status/.env)   — local override
      2. The engine project's .env      (unified-collection-report/.env)

    Loading the engine's .env means secrets that live with the source project
    (SUPABASE_SERVICE_KEY, EC2_*/COLLDB_URL, Gmail, etc.) are picked up by this
    shell automatically — no need to duplicate them here.
    """
    _load_env_file(PROJECT_DIR / ".env")
    _load_env_file(UNIFIED_COLLECTION_DIR / ".env")


def bootstrap() -> None:
    """Prepare the environment so the source engine is importable and reads/writes
    this project's data dir. Safe to call more than once."""
    global _bootstrapped
    if _bootstrapped:
        return

    if not ENGINE_DIR.exists() or not (ENGINE_DIR / "config.py").exists():
        raise RuntimeError(
            f"EOD engine not found at: {ENGINE_DIR}\n"
            "The engine is normally vendored at backend/engine. If you set "
            "UNIFIED_COLLECTION_DIR, make sure it points at a valid engine checkout."
        )

    _load_dotenv()

    # Engine dir (config.py + services/) then backend dir (blueprints, settings,
    # email/whatsapp services). backend ends up first so its own 'blueprints'
    # package and helper modules take precedence; 'config'/'services' resolve to
    # the engine.
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    os.environ.setdefault("COLLECTION_HOST", HOST)
    os.environ.setdefault("COLLECTION_PORT", str(PORT))
    os.environ.setdefault("COLLECTION_DATA_DIR", str(DATA_DIR))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _bootstrapped = True

