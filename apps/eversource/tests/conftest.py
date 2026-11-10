import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from paperpull_core.browser import sandboxed_launch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = sandboxed_launch(pw.chromium, headless=True)
        context = browser.new_context()
        context.route("**/*", lambda route: route.fulfill(
            content_type="text/html", body="<body>No synthetic route</body>"))
        page = context.new_page()
        yield page
        context.close()
        browser.close()
