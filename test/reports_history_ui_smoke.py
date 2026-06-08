"""Headless UI check for the multi-run Reports and Downloads.

Points at the temp frontend (5175 -> backend 5056) which already has >=2 EOD
archived runs. Verifies the date->runs view shows multiple runs with Latest /
Previous Run tags and working per-run download links, with no console errors.
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

        print("> loading app")
        page.goto(FRONTEND, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(800)

        print("> open Reports and Downloads")
        page.get_by_role("button", name="Reports and Downloads").first.click()
        page.wait_for_timeout(1200)
        # EOD is the default selected module
        page.screenshot(path=str(OUT / "rep_01_eod_runs.png"))

        # date rail present
        assert page.locator(".date-item").count() >= 1, "no dates in rail"
        # multiple run groups visible for the selected date
        run_groups = page.locator(".run-group")
        n = run_groups.count()
        print("  run groups:", n)
        assert n >= 2, f"expected >=2 runs, got {n}"
        # latest + previous tags
        assert page.locator(".run-tag.latest").count() >= 1, "no Latest tag"
        assert page.get_by_text("Previous Run").count() >= 1, "no Previous Run tag"
        # per-run download links (anchor hrefs include run= param)
        dls = page.locator("a.btn-primary")
        assert dls.count() >= 2, f"expected per-run download links, got {dls.count()}"
        href = dls.first.get_attribute("href") or ""
        assert "run=" in href and "type=" in href, f"download href missing run/type: {href}"
        print("  sample download href:", href)

        # switch to Hourly module (may have 0 runs — just must not crash)
        print("> switch to Hourly")
        page.get_by_role("button", name="Hourly Module").first.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT / "rep_02_hourly.png"))

        browser.close()

    if errors:
        print("\nCONSOLE/RUNTIME ERRORS:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nSUCCESS: multi-run Reports and Downloads renders correctly.")


if __name__ == "__main__":
    main()
