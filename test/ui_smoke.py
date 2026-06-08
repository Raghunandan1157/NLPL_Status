"""Headless UI smoke test for NLPL Status.

Loads the running frontend, navigates Home -> EOD -> each tab, and asserts there
are no console errors or uncaught exceptions. Saves screenshots for inspection.

Run (frontend must be up, e.g. `npm run dev`):
    <unified-venv>/python.exe test/ui_smoke.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND = "http://127.0.0.1:5174"
OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(exist_ok=True)

errors = []


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            # Playwright's bundled chromium may not be installed; fall back to
            # the system Edge (always present on Windows 10).
            browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1366, "height": 900})

        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        print("> loading home")
        page.goto(FRONTEND, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "01_home.png"))
        assert page.locator("text=Modules").first.is_visible(), "home modules not visible"

        print("> opening EOD module")
        page.get_by_role("button", name="Open EOD Module").first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "02_eod_process.png"))
        assert page.locator("text=Upload & Process").first.is_visible(), "EOD process panel not visible"

        for tab, shot in [
            ("Reports & Downloads", "03_reports.png"),
            ("Email", "04_email.png"),
            ("WhatsApp", "05_whatsapp.png"),
            ("Process", "06_process_again.png"),
        ]:
            print(f"> tab: {tab}")
            page.get_by_role("button", name=tab).first.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / shot))

        print("> opening Hourly module")
        page.get_by_role("button", name="Home").first.click()
        page.wait_for_timeout(1000)
        page.get_by_role("button", name="Hourly Module").first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "07_hourly_process.png"))
        assert page.locator("text=Hourly Collection Processing").first.is_visible(), "Hourly process panel not visible"

        for tab, shot in [
            ("VBA Runner", "08_hourly_vba.png"),
            ("WhatsApp", "09_hourly_whatsapp.png"),
            ("Process", "10_hourly_process_again.png"),
        ]:
            print(f"> hourly tab: {tab}")
            page.get_by_role("button", name=tab).first.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / shot))

        browser.close()

    if errors:
        print("\nCONSOLE/RUNTIME ERRORS:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nSUCCESS: no console errors. Screenshots in", OUT)


if __name__ == "__main__":
    main()
