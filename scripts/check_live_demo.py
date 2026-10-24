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
# What proves the pipeline ran AND that the result is presented the way the application
# presents it.
#
# These are not arbitrary words: each one is a section the demo must render for the page to be
# the product. An earlier version of this list named "Clauses" and "Stories", which were bar
# metrics of a layout that has since been replaced by the verdict-first one — so the check
# failed for a week while the demo worked, which is the worst kind of red: it teaches people
# to ignore the badge.
EXPECTED = [
    "AUDIT",              # the verdict, which now comes first
    "Statement of Work",  # the contract tab
    "Compliance audit",   # the findings tab
    "Grounding",          # the measured ratio, not the estimated one
    "Documents produced", # the artefact table
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)

        # A Streamlit Community Cloud app SLEEPS after a period of inactivity, and a sleeping
        # app renders a "Zzzz ... wake it back up?" page instead of the application. The check
        # treated that as "the demo is broken" and failed on every run after a quiet spell —
        # a check that is red when nothing is wrong, which teaches everyone to ignore it.
        #
        # So wake it first, then wait. The boot after waking is the same cold start as any
        # other, which is why the loop below is generous.
        app = None
        woken = False
        for attempt in range(36):
            page.wait_for_timeout(5_000)

            if not woken:
                for frame in page.frames:
                    try:
                        body = frame.inner_text("body")
                    except Exception:
                        continue
                    if "gone to sleep" in body or "wake it back up" in body:
                        print("  the demo is asleep — waking it")
                        for label in ("Yes, get this app back up", "get this app back up"):
                            button = frame.get_by_role("button", name=label)
                            if button.count():
                                button.first.click()
                                woken = True
                                break
                        break

            for frame in page.frames:
                try:
                    if "Turn a client brief" in frame.inner_text("body"):
                        app = frame
                        break
                except Exception:
                    continue
            if app:
                break
            if attempt and attempt % 6 == 0:
                print(f"  still loading ({attempt * 5}s)")

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
        for _ in range(30):
            page.wait_for_timeout(10_000)
            body = app.inner_text("body")
            if "AUDIT" in body or "failed" in body.lower():
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
