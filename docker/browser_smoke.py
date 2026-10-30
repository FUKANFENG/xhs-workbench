"""Offline Chromium smoke check; safe in a read-only, non-root container."""
import asyncio
from patchright.async_api import async_playwright


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, channel="chromium")
        try:
            page = await browser.new_page()
            await page.set_content("<title>Workbench browser check</title><p>Ready</p>")
            assert await page.title() == "Workbench browser check"
        finally:
            await browser.close()
    print("browser_ready=true")


asyncio.run(main())
