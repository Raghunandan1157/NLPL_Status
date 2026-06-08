"""In-app Gmail login for the EOD email feature.

The reused engine reads SMTP credentials from ``config.GMAIL_USER`` /
``config.GMAIL_APP_PASSWORD`` at send time. This module lets the UI log in:
it validates the credentials against Gmail's SMTP server, persists them to the
project ``.env`` (so they survive restarts), and updates the running engine
config in memory (so sending works immediately, no restart needed).
"""
from __future__ import annotations

import os
import smtplib
import ssl
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def get_config() -> dict:
    import config as ec

    configured = bool(ec.GMAIL_USER and ec.GMAIL_APP_PASSWORD)
    return {
        "configured": configured,
        "sender": ec.GMAIL_USER if configured else "",
        "host": ec.SMTP_HOST,
        "port": ec.SMTP_PORT,
    }


def _write_env(updates: dict) -> None:
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
    done = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    for key, val in updates.items():
        if key not in done:
            lines.append(f"{key}={val}")
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_runtime(user: str, password: str, host: str, port: int) -> None:
    os.environ.update(
        {"GMAIL_USER": user, "GMAIL_APP_PASSWORD": password, "SMTP_HOST": host, "SMTP_PORT": str(port)}
    )
    import config as ec

    ec.GMAIL_USER = user
    ec.GMAIL_APP_PASSWORD = password
    ec.SMTP_HOST = host
    ec.SMTP_PORT = int(port)


def login(user: str, password: str, host: str = "smtp.gmail.com", port: int = 587) -> dict:
    user = (user or "").strip()
    # App passwords are often shown with spaces ("abcd efgh ijkl mnop").
    password = (password or "").replace(" ", "").strip()
    host = (host or "smtp.gmail.com").strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 587

    if not user or not password:
        return {"success": False, "error": "Email and app password are required."}

    try:
        server = smtplib.SMTP(host, port, timeout=20)
        try:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(user, password)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError:
        return {
            "success": False,
            "error": "Gmail rejected the login. Use a 16-character App Password "
            "(myaccount.google.com/apppasswords) with 2-Step Verification enabled — not your normal password.",
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Could not reach the mail server: {e}"}

    _write_env({"GMAIL_USER": user, "GMAIL_APP_PASSWORD": password, "SMTP_HOST": host, "SMTP_PORT": str(port)})
    _apply_runtime(user, password, host, port)
    return {"success": True, "sender": user}


def logout() -> dict:
    _write_env({"GMAIL_USER": "", "GMAIL_APP_PASSWORD": ""})
    os.environ["GMAIL_USER"] = ""
    os.environ["GMAIL_APP_PASSWORD"] = ""
    import config as ec

    ec.GMAIL_USER = ""
    ec.GMAIL_APP_PASSWORD = ""
    return {"success": True}
