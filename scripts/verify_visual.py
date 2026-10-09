#!/usr/bin/env python
"""Verify the running app **as a browser sees it**, not as the socket describes it.

Why this exists
---------------
``verify_ui.py`` drives the socket.io protocol and asserts on the message payloads the
server emits. That is useful, but it cannot tell whether anything was rendered. The
cost dashboard passed every socket-level check for weeks while being completely
invisible in a real browser: the server emitted a ``CustomElement`` with correct props,
and the frontend silently failed to mount it.

This script closes that gap. It runs a real Chromium against the app and asserts on the
DOM, which is the only layer a user experiences.

Running it
----------
Chromium needs system libraries the application image does not carry, so this runs
inside the official Playwright image on the same Docker network:

    docker run --rm --network sowsprint_sowsprint-net \\
      -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \\
      -v "$PWD/demo:/demo" \\
      --entrypoint bash mcr.microsoft.com/playwright/python:v1.49.1-noble \\
      -c "pip install -q playwright==1.49.1; python /demo/verify_visual.py"

Exits non-zero when a check fails, so it can gate CI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

BRIEF = """# Visual check
Build a React dashboard for 120 staff across 3 sites.
Our goal is faster monthly reporting.
Delivery in 10 weeks. Integrate with Slack.
Success means 25 percent faster reporting. Budget: EUR 80k."""

PASS, FAIL = "✅", "❌"

#: Sent when the Triage agent asks for the variables it could not infer.
ANSWER = (
    "Acceptance is a signed UAT sign-off per milestone. Constraints: EU data "
    "residency, no new headcount. Primary users are the operations analysts. "
    "No employment data is processed."
)


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"  {PASS if ok else FAIL} {name}")
        if detail:
            print(f"      {detail}")

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.rows if not r[1]]


def wait_text(page: Page, needle: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if needle.casefold() in page.inner_text("body").casefold():
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return False


def log_in(page: Page, username: str, password: str) -> bool:
    """Complete Chainlit's login form if the deployment requires authentication."""
    try:
        page.wait_for_selector("input[type=password]", timeout=15_000)
    except PlaywrightTimeout:
        return True  # no login form: either open, or already authenticated
    print("  → login form present, signing in", flush=True)
    page.fill("input[type=text], input[name=username]", username)
    page.fill("input[type=password]", password)
    page.keyboard.press("Enter")
    try:
        page.wait_for_selector("input[type=password]", state="detached", timeout=30_000)
    except PlaywrightTimeout:
        return False
    return True


def run(url: str, timeout_s: float, negotiate: bool, username: str, password: str) -> int:
    checks = Checks()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--lang=en-US"])
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
        page = context.new_page()

        console_errors: list[str] = []
        page.on(
            "console",
            lambda m: console_errors.append(m.text[:160]) if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {str(e)[:160]}"))

        # ---- 0. authentication -------------------------------------------------
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        logged_in = log_in(page, username, password)
        checks.add("authentication accepted", logged_in)
        if not logged_in:
            tail = page.inner_text("body")[-200:].replace("\n", " | ")
            print(f"      page tail: {tail}")
            browser.close()
            return 1

        # ---- 1. the shell actually mounts -------------------------------------
        mounted = wait_text(page, "SOWSprint.ai", 90)
        checks.add("React app mounts (no blank page)", mounted)
        if not mounted:
            browser.close()
            return 1

        checks.add(
            "composer is interactive",
            page.locator("textarea").count() > 0,
            f"{page.locator('textarea').count()} textarea(s)",
        )
        checks.add(
            "capability matrix is rendered",
            wait_text(page, "Subsystem", 30) or wait_text(page, "Resolved adapter", 5),
        )

        # ---- 2. a full engagement renders -------------------------------------
        page.locator("textarea").first.fill(BRIEF)
        page.locator("textarea").first.press("Enter")
        checks.add("requirement accepted", wait_text(page, "New engagement", 90))
        # Wait for the pipeline to actually produce a contract before asserting on the
        # dashboard: an early refresh legitimately has no per-agent breakdown yet.
        wait_text(page, "quality audit", timeout_s)

        # A live Triage model may legitimately decide the brief needs clarification
        # before it will draft. That is normal product behaviour, not a failure, so
        # answer one round and carry on to the gate we actually care about.
        # The clarification prompt reads "Reply with your answers in one message",
        # not the word "clarification" — matching the real copy matters, since a
        # missed gate looks exactly like a hung run.
        clarify_needles = ("reply with your answers", "clarification", "before i continue")
        reached = wait_text(page, "approval required", min(timeout_s, 240))
        if not reached:
            body = page.inner_text("body").casefold()
            if any(n in body for n in clarify_needles):
                checks.add("run asked for clarification (answered)", True)
                box = page.locator("textarea").first
                box.fill(ANSWER)
                box.press("Enter")
                reached = wait_text(page, "approval required", timeout_s)

        checks.add("run reaches the approval gate", reached, f"within {timeout_s:.0f}s")
        if not reached:
            tail = page.inner_text("body")[-400:].replace("\n", " | ")
            print(f"      page tail: {tail}")

        # ---- 3. the dashboard is VISIBLE, which is the point -------------------
        html = page.content()
        checks.add(
            "cost dashboard markup is in the DOM",
            "sow-cost-dashboard" in html,
            "the check that verify_ui.py could not make",
        )
        checks.add("dashboard shows a dollar figure", "Session compute cost" in html)
        page.wait_for_timeout(1500)
        html = page.content()
        checks.add(
            "dashboard reports cost by agent",
            "Cost by agent" in html,
            "per-node breakdown rendered",
        )

        box = page.locator(".sow-cost-dashboard").first
        if box.count():
            bbox = box.bounding_box()
            visible = bool(bbox and bbox["width"] > 200 and bbox["height"] > 80)
            checks.add(
                "dashboard has real layout box",
                visible,
                f"{bbox['width']:.0f}x{bbox['height']:.0f}" if bbox else "no box",
            )
        else:
            checks.add("dashboard has real layout box", False, "element not found in DOM")

        # ---- 4. approve and confirm deliverables render ------------------------
        if reached:
            approve = page.locator("button", has_text="Approve")
            if approve.count():
                approve.first.click()
            else:
                page.locator("textarea").first.fill("approve")
                page.locator("textarea").first.press("Enter")
            done = wait_text(page, "run finished", timeout_s)
            checks.add("run completes after approval", done)
            if done:
                body = page.inner_text("body")
                checks.add(
                    "quality audit is reported to the user",
                    "Quality audit" in body,
                )
                checks.add(
                    "deliverables are attached",
                    "Statement of Work" in body,
                )

        # ---- 5. no console errors ---------------------------------------------
        real = [e for e in console_errors if "400" not in e and "Failed to load resource" not in e]
        checks.add("no unexpected console errors", not real, "; ".join(real[:2]))

        browser.close()

    print()
    if checks.failures:
        print(f"{FAIL} {len(checks.failures)} check(s) failed")
        return 1
    print(f"{PASS} all visual checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://sowsprint-app:8000")
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--username", default=os.environ.get("SOWSPRINT_AUTH_USER", ""))
    parser.add_argument("--password", default=os.environ.get("SOWSPRINT_AUTH_PASSWORD", ""))
    args = parser.parse_args(argv)
    print("SOWSprint.ai — visual verification (real browser)\n")
    return run(args.url, args.timeout, True, args.username, args.password)


if __name__ == "__main__":
    sys.exit(main())
