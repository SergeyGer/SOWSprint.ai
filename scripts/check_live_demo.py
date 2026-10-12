#!/usr/bin/env python
"""Check that the live Streamlit demo actually works.

Why a browser, and why this is not a link checker's job
------------------------------------------------------
The demo URL answers 303 to a session endpoint, which answers 303 to a login endpoint
carrying a fresh payload — a cycle that never terminates for a client which does not store
cookies. Curl gives up after fifty redirects. Lychee reports the same thing, which is why
the URL is excluded from the link check rather than "accepted": there is no status code
that would mean anything here.

A browser resolves it in one hop, because it keeps the cookie and executes the JavaScript
that Streamlit needs to open its websocket. So the only honest check is the one that uses
a browser — and it may as well verify the thing that matters, which is not that the URL
answers but that the pipeline produces a contract.

This runs on a schedule rather than on pull requests: it depends on an external service,
and an outage at Streamlit should not turn an unrelated pull request red.
"""

from __future__ import annotations

import sys

URL = "https://sowsprintai-tr52wy95c3da5fg2ewqwj3.streamlit.app"

#: What a working run produces. Kept to the few facts that would be false if the pipeline
#: broke: a status, a clause count, a story count.
EXPECTED = ["Statement of Work", "Clauses", "Stories", "Compliance audit"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)

        # A cold Streamlit app boots on first request, which can take a minute.
        app = None
        for _ in range(12):
            page.wait_for_timeout(10_000)
            for frame in page.frames:
                try:
                    if "Turn a client brief" in frame.inner_text("body"):
                        app = frame
                        break
                except Exception:
                    continue
            if app:
                break

        if app is None:
            print("::error::the demo did not load", file=sys.stderr)
            print("No frame contained the application heading.", file=sys.stderr)
            browser.close()
            return 1
        print("  the demo loaded")

        button = app.get_by_role("button", name="Generate")
        if button.count() == 0:
            print("::error::the Generate button is missing", file=sys.stderr)
            browser.close()
            return 1
        button.first.click()
        print("  clicked Generate")

        body = ""
        for _ in range(18):
            page.wait_for_timeout(10_000)
            body = app.inner_text("body")
            if "Statement of Work" in body or "failed" in body.lower():
                break

        missing = [needle for needle in EXPECTED if needle.lower() not in body.lower()]
        browser.close()

        if missing:
            print(f"::error::the demo ran but produced no {missing}", file=sys.stderr)
            print(body[:600], file=sys.stderr)
            return 1

        print("  the pipeline produced a contract, a plan and an audit")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
