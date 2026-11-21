from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, sync_playwright


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        # The opening plays once per session, and is not what most tests are about.
        context.add_init_script("sessionStorage.setItem('homunculus_booted_v1', '1')")
        yield context.new_page()
        browser.close()
