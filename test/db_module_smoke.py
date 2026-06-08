"""Headless smoke test for the DB Module changes.

Verifies:
  - Sidebar order: Home, DB Module, Reports and Downloads, then EOD/Hourly modules
  - DB Module page renders with both master-file cards + DuckDB status panel
  - EOD process page shows the DB status summary + Open DB Module (no upload cards)
  - Hourly process page shows the DB status summary + Open DB Module
  - No console errors / page exceptions throughout

Run (frontend + backend up):
    <unified-venv>/python.exe test/db_module_smoke.py
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
            browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        print("> loading home")
        page.goto(FRONTEND, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)

        # ---- sidebar order ----
        nav_buttons = page.locator("aside.sidebar nav button")
        labels = [nav_buttons.nth(i).inner_text().strip().split("\n")[0] for i in range(nav_buttons.count())]
        print("  sidebar:", labels)
        # Workspace nav: Home, DB Module, Reports and Downloads
        assert labels[0].startswith("Home"), f"expected Home first, got {labels}"
        assert "DB Module" in labels[1], f"expected DB Module second, got {labels}"
        assert "Reports and Downloads" in labels[2], f"expected Reports third, got {labels}"
        assert any("EOD" in l for l in labels), "EOD module missing"
        assert any("Hourly" in l for l in labels), "Hourly module missing"

        # ---- DB Module page ----
        print("> opening DB Module")
        page.get_by_role("button", name="DB Module").first.click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "db_01_module.png"))
        assert page.locator("text=Database & Master Files").first.is_visible(), "DB Module heading missing"
        assert page.locator("text=Demand Master").first.is_visible(), "Demand Master card missing"
        assert page.locator("text=Last Month PAR").first.is_visible(), "Last Month PAR card missing"
        assert page.locator("text=Sync DuckDB").first.is_visible(), "Sync DuckDB button missing"
        assert page.locator("text=DuckDB Status").first.is_visible(), "DuckDB status panel missing"
        # Save to DB buttons (one per card)
        assert page.get_by_role("button", name="Save to DB").count() >= 2, "expected 2 Save to DB buttons"

        # ---- EOD summary ----
        print("> opening EOD module")
        page.get_by_role("button", name="Home").first.click()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="Open EOD Module").first.click()
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "db_02_eod.png"))
        assert page.locator("text=Upload & Process").first.is_visible(), "EOD process panel missing"
        assert page.locator("text=Database Status").first.is_visible(), "EOD DB summary missing"
        assert page.get_by_role("button", name="Open DB Module").first.is_visible(), "EOD Open DB Module link missing"
        # The old full-management Save-to-DB cards must be gone from EOD
        assert page.locator("text=Saved Monthly Data").count() == 0, "EOD still shows old DB panel"

        # ---- Hourly summary ----
        print("> opening Hourly module")
        page.get_by_role("button", name="Home").first.click()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="Hourly Module").first.click()
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "db_03_hourly.png"))
        assert page.locator("text=Hourly Collection Processing").first.is_visible(), "Hourly panel missing"
        assert page.locator("text=Database Status").first.is_visible(), "Hourly DB summary missing"
        assert page.get_by_role("button", name="Open DB Module").first.is_visible(), "Hourly Open DB Module link missing"
        assert page.locator("text=Saved Monthly Data").count() == 0, "Hourly still shows old DB panel"

        browser.close()

    if errors:
        print("\nCONSOLE/RUNTIME ERRORS:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nSUCCESS: DB Module smoke passed. Screenshots in", OUT)


if __name__ == "__main__":
    main()
