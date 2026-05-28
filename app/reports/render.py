"""TERRA report renderer — one template set, two outputs.

render_html(context) -> str
render_pdf(context)  -> bytes   (PDF rendered from the SAME HTML/CSS)

The PDF backend is swappable behind _render_pdf(): WeasyPrint by default (honors
@page / @media print / running headers), Playwright headless Chromium as a fallback
if WeasyPrint's native libs aren't present. Templates never change between backends.
"""

from __future__ import annotations

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TPL_DIR = os.path.join(os.path.dirname(__file__), "templates")

_env = Environment(
    loader=FileSystemLoader(_TPL_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Which sections render at "summary" depth (per brief: 1,2,4,7,8). 12 (citations)
# is mandatory at every depth. "full" renders all 12.
SUMMARY_SECTIONS = {1, 2, 4, 7, 8, 12}


def _section_visible(depth: str, n: int) -> bool:
    if depth == "full":
        return True
    return n in SUMMARY_SECTIONS


_env.globals["section_visible"] = _section_visible


def render_html(context: dict) -> str:
    tpl = _env.get_template("report.html.j2")
    return tpl.render(**context)


# ---------------------------------------------------------------------------
# PDF backends.
# ---------------------------------------------------------------------------
PDF_BACKEND = os.environ.get("TERRA_PDF_BACKEND", "weasyprint")


def _render_pdf_weasyprint(html: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=html, base_url=_TPL_DIR).write_pdf()


def _render_pdf_playwright(html: str) -> bytes:
    # Fallback path — heavier, but bulletproof rendering fidelity. Only used if
    # explicitly selected via TERRA_PDF_BACKEND=playwright.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        pdf = page.pdf(format="Letter", print_background=True,
                       margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()
        return pdf


def render_pdf(context: dict) -> bytes:
    html = render_html(context)
    if PDF_BACKEND == "playwright":
        return _render_pdf_playwright(html)
    return _render_pdf_weasyprint(html)
