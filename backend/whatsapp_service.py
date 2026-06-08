"""Centralized WhatsApp service shared across all modules.

WhatsApp Web is automated through the engine's ``services.whatsapp_sender``
(one persistent Chromium profile + one worker thread). Because that profile is
process-wide, logging in once (scanning the QR) is shared by EVERY module — we
never create a separate login per module. Contacts live in a single shared CSV
(``<data_dir>/whatsapp_contacts.csv``), so the recipient list is also shared.

Sending stays module-specific: callers pass the exact file (a module's own
report) to send, while the session/login/contacts remain centralized.
"""
from __future__ import annotations

import csv
from pathlib import Path

import config


def _contacts_csv() -> Path:
    return Path(config.DATA_DIR) / "whatsapp_contacts.csv"


def get_contacts() -> list:
    """Read the shared WhatsApp contact list (column ``name``)."""
    path = _contacts_csv()
    names = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                n = (row.get("name") or "").strip()
                if n:
                    names.append(n)
    return names


def save_contacts(names) -> list:
    """Persist the shared WhatsApp contact list."""
    cleaned = [n.strip() for n in (names or []) if n and n.strip()]
    path = _contacts_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name"])
        for n in cleaned:
            writer.writerow([n])
    return cleaned


def open_session() -> dict:
    """Open (or re-attach to) the shared WhatsApp Web session."""
    from services.whatsapp_sender import open_whatsapp
    return open_whatsapp()


def send_file(bundle_path: str, filename: str) -> dict:
    """Send one file (module-specific report) via the shared session.

    ``bundle_path`` + ``filename`` identify the exact report to deliver; the
    session and contacts used are the centralized ones.
    """
    if not bundle_path or not filename:
        return {"success": False, "error": "Missing bundle_path or filename"}
    file_path = Path(bundle_path) / filename
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    from services.whatsapp_sender import send_file_to_contact
    return send_file_to_contact(str(file_path))
