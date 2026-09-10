"""
Dashboard smoke test.

Drives the running Streamlit app with a headless browser, visits every page,
captures a screenshot of each, and fails if Streamlit rendered an exception
anywhere. This is the check that the dashboard actually WORKS, as opposed to
merely importing.

    streamlit run dashboard/app.py --server.port 8501 --server.headless true &
    python tests/smoke_dashboard.py
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("CT_URL", "http://localhost:8501")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "screenshots")

PAGES = [
    "Overview",
    "1 · Understand Requirements",
    "2 · Prepare Input Data",
    "3 · Demand Forecasting",
    "4 · Inventory Planning",
    "5 · Master Production Scheduling",
    "6 · BOM Explosion",
    "7 · Material Requirements Planning",
    "8 · Production Order Release",
    "9 · Routing & Capacity Preparation",
    "10 · Shop-Floor Scheduling",
    "11 · Performance Comparison",
    "12 · Exception Management",
    "13 · Scenario Simulation",
    "All Data",
    "Agent Layer",
]


def settle(page, timeout=45000):
    """Wait until Streamlit stops showing its running indicator."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        running = page.locator('[data-testid="stStatusWidget"]').count()
        if running == 0:
            break
        time.sleep(0.4)
    page.wait_for_timeout(1200)


def main():
    os.makedirs(OUT, exist_ok=True)
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=90000)
        settle(page, 90000)

        for i, name in enumerate(PAGES):
            label = name.split(" · ")[-1]
            try:
                if i > 0:
                    page.get_by_text(name, exact=False).first.click(timeout=15000)
                    settle(page)

                errors = page.locator('[data-testid="stException"]').count()
                alerts = page.locator(".stAlert").count()
                body = page.inner_text("body")

                if errors:
                    detail = page.locator('[data-testid="stException"]').first.inner_text()
                    failures.append(f"{name}: exception rendered\n{detail[:800]}")
                    status = "FAIL"
                elif "Traceback" in body or "StreamlitAPIException" in body:
                    failures.append(f"{name}: traceback text present on page")
                    status = "FAIL"
                else:
                    status = "ok"

                slug = (label.lower().replace(" ", "-").replace("–", "-")
                        .replace("·", "").replace("&", "and"))
                shot = os.path.join(OUT, f"{i:02d}-{slug}.png")
                page.screenshot(path=shot, full_page=True)
                print(f"  [{status:4}] {name:32} -> {os.path.basename(shot)} "
                      f"({alerts} info box(es))")
            except Exception as e:                       # noqa: BLE001
                failures.append(f"{name}: {type(e).__name__}: {e}")
                print(f"  [FAIL] {name}: {e}")

        browser.close()

    print()
    if failures:
        print(f"{len(failures)} page(s) failed:")
        for f in failures:
            print(f"\n--- {f}")
        sys.exit(1)
    print(f"All {len(PAGES)} pages rendered without errors.")


if __name__ == "__main__":
    main()
