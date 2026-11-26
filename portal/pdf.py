"""HTML → A4 PDF with headless Chromium (Playwright)."""
import os


class PDFUnavailable(Exception):
    pass


def html_to_pdf(html):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PDFUnavailable("Playwright is not installed") from e
    opts = {"args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]}
    if os.environ.get("CHROMIUM_PATH"):
        opts["executable_path"] = os.environ["CHROMIUM_PATH"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**opts)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                return page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            finally:
                browser.close()
    except Exception as e:  # browser missing, sandbox issues, etc.
        raise PDFUnavailable(str(e).splitlines()[0]) from e
