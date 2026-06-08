"""Smoke check for the Hourly upload fix + Reports page (no-blank).

Runs against the temp frontend on 5175 (-> backend 5055). Verifies:
  - Hourly process page shows upload drops for all inputs (not locked/preloaded)
  - Reports and Downloads page renders (never blank), even with the live backend
  - No console errors / page exceptions
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

FRONTEND = "http://127.0.0.1:5175"
OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(exist_ok=True)
errors = []


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1366, "height": 950})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        print("> load app")
        page.goto(FRONTEND, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(900)

        print("> open Hourly")
        page.get_by_role("button", name="Hourly Module").first.click()
        page.wait_for_timeout(1300)
        page.screenshot(path=str(OUT / "fix_01_hourly.png"))
        assert page.locator("text=Upload & Configure").first.is_visible(), "Hourly process page missing"
        # all three inputs are uploadable (filedrops, not locked) — labels say Upload/Replace
        body = page.content()
        assert ("Upload EOD Output" in body or "Replace EOD Output" in body), "EOD Output drop not shown"
        assert ("Upload Collection" in body or "Replace Collection" in body), "Collection drop not shown"
        assert ("Upload Hourly Daily" in body or "Replace Hourly Daily" in body), "Hourly Daily drop not shown"
        # no leftover misleading DB-sync requirement on hourly
        assert "Sync DuckDB" not in body, "Hourly should not require DuckDB sync"
        # Loaded Files status panel present
        assert page.locator("text=Loaded Files").first.is_visible(), "inputs status panel missing"
        print("  hourly upload drops present, no DuckDB gate")

        print("> open Reports and Downloads (must not be blank)")
        page.get_by_role("button", name="Reports and Downloads").first.click()
        page.wait_for_timeout(1300)
        page.screenshot(path=str(OUT / "fix_02_reports.png"))
        # page rendered something (header present) — not blank
        assert page.locator("text=Reports and Downloads").first.is_visible(), "Reports page blank!"
        # the ReportHistory panel header renders
        assert page.locator(".panel").count() >= 1, "no panel rendered on reports page"
        print("  reports page rendered (not blank)")

        # switch to Hourly reports module too
        page.get_by_role("button", name="Hourly Module").first.click()
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / "fix_03_reports_hourly.png"))
        assert page.locator(".panel").count() >= 1, "hourly reports blank"

        browser.close()

    if errors:
        print("\nCONSOLE/RUNTIME ERRORS:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nSUCCESS: Hourly uploads + Reports page OK, no console errors.")


if __name__ == "__main__":
    main()
