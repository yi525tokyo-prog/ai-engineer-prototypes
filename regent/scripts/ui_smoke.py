"""UI smoke test through a real browser.

Opens the cockpit, follows the "Blocked by you" link to the client portal, solves the
challenge as a human would, and checks that the cockpit shows the plan change with no
further input. Requires the API on :8000 (background loop on) and the cockpit on :3000
with the case study loaded. Writes docs/screens/cockpit-replanned.png.
"""

import os
import time

from playwright.sync_api import sync_playwright

COCKPIT = os.environ.get("REGENT_COCKPIT_URL", "http://localhost:3000/")
exe = os.environ.get("REGENT_BROWSER_EXECUTABLE")

with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(COCKPIT)
    pg.wait_for_selector("text=Blocked by you")
    href = pg.get_attribute("a:has-text('Open page')", "href")
    print("interrupt link:", href)
    portal = b.new_page()
    portal.goto(href)
    assert portal.locator("#captcha").count() == 1, "portal should show a human-verification challenge"
    portal.fill("input[name=code]", "7KQ2")
    portal.click("button[type=submit]")
    print("portal after human:", portal.inner_text("#slot-status"), "/", portal.inner_text("#budget-status"))
    t0 = time.time()
    pg.wait_for_selector("text=plan changed", timeout=30000)
    print(f"cockpit shows the plan change {time.time() - t0:.1f}s later, with no further input")
    pg.wait_for_timeout(3000)
    pg.screenshot(path="docs/screens/cockpit-replanned.png", full_page=True)
    b.close()
