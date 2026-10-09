#!/usr/bin/env python
"""Capture screenshots and a screen recording of a live SOWSprint engagement.

Runs a real browser against the running application, drives one complete
scoping-to-contract cycle, and writes the assets used in the README.

Designed to run **inside a Playwright container** on the same Docker network as the
app, because Chromium needs system libraries the host image does not carry:

    docker run --rm --network sowsprint_sowsprint-net \\
      -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \\
      -v "$PWD/demo:/demo" \\
      --entrypoint bash mcr.microsoft.com/playwright/python:v1.49.1-noble \\
      -c "pip install -q playwright==1.49.1; python /demo/capture_demo.py"

Notes
-----
* The browser locale is pinned to ``en-US``. Chainlit forwards the raw browser locale
  as a BCP-47-validated query parameter, and a POSIX-flavoured locale gives the app a
  blank page (see the note in ``.chainlit/config.toml``).
* Waits are text-driven, not timer-driven, so the capture stays correct whatever the
  model latency happens to be.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}  # iPhone 14/15 logical viewport

BRIEF = """# Project Aurora Data Platform
Nordwind Logistics GmbH (Berlin) requires a customer analytics platform for our
operations organisation.

- Build a React dashboard used by 200 warehouse staff across 6 distribution centres
- Develop a Python FastAPI backend with PostgreSQL as the system of record
- Integrate with Salesforce for account data and SAP for shipment events
- Deliver GDPR-compliant audit logging that satisfies our Data Protection Officer

Budget: EUR 120k. Delivery within 12 weeks. Success means 40% faster monthly close.
The platform processes personal data of EU employees."""


class Capture:
    def __init__(self, page: Page, out: Path) -> None:
        self.page = page
        self.out = out
        self.shots: list[str] = []

    def shot(self, name: str, *, settle: float = 1.0, full: bool = False) -> None:
        self.page.wait_for_timeout(int(settle * 1000))
        path = self.out / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=full)
        self.shots.append(name)
        print(f"    📸 {name}.png", flush=True)

    def wait_for_text(self, needle: str, timeout_s: float = 480, label: str = "") -> bool:
        """Poll the rendered text until it contains ``needle``."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if needle.casefold() in self.page.inner_text("body").casefold():
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(1000)
        print(f"    ⏱ timeout waiting for {label or needle!r}", flush=True)
        return False

    def send(self, text: str) -> None:
        box = self.page.locator("textarea").first
        box.click()
        box.fill(text)
        self.page.wait_for_timeout(400)
        box.press("Enter")


def capture(url: str, out: Path, *, mobile_shot: bool = True) -> int:
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--lang=en-US"])

        # ---------------------------------------------------------------- desktop
        context = browser.new_context(
            viewport=DESKTOP,
            locale="en-US",
            timezone_id="Europe/Berlin",
            record_video_dir=str(out / "video"),
            record_video_size={"width": 1280, "height": 800},
            device_scale_factor=1,
        )
        page = context.new_page()
        cap = Capture(page, out)

        print("  → opening the app", flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)

        if not cap.wait_for_text("SOWSprint.ai", timeout_s=90, label="welcome"):
            print("  ✗ the app never rendered — see the locale note in the docstring")
            context.close()
            browser.close()
            return 1

        cap.wait_for_text("How it works", timeout_s=60, label="welcome body")
        cap.shot("01-welcome", settle=2.0)

        # Cost dashboard widget, before any spend.
        cap.shot("02-dashboard-empty", settle=0.5)

        # ---------------------------------------------------------------- run
        print("  → sending the requirement", flush=True)
        cap.send(BRIEF)
        cap.wait_for_text("New engagement", timeout_s=60, label="run start")

        # Grab the step trace while it is filling in — that is the interesting frame.
        cap.wait_for_text("Scope parsed", timeout_s=180, label="triage")
        cap.shot("03-pipeline-running", settle=1.5)

        at_gate = cap.wait_for_text("approval required", timeout_s=300, label="approval gate")
        asked = False
        if not at_gate:
            asked = cap.wait_for_text("Reply with your answers", timeout_s=20) or cap.wait_for_text(
                "before I continue", timeout_s=5
            )
        if not at_gate and asked:
            # Answer the clarification round, then wait for the real gate.
            print("  → answering the clarification round", flush=True)
            cap.send(
                "Acceptance is a signed UAT sign-off per milestone. Constraints: EU "
                "data residency, no new headcount. Primary users are operations "
                "analysts. No employment data is processed."
            )
            at_gate = cap.wait_for_text("approval required", timeout_s=420, label="approval gate")
        if not at_gate:
            # A live Triage model may ask for clarification instead of drafting.
            settled = cap.wait_for_text("run finished", timeout_s=30) or cap.wait_for_text(
                "clarification", timeout_s=20
            )
            if not settled:
                print("  ✗ the engagement did not reach a terminal state")
                context.close()
                browser.close()
                return 1

        cap.shot("04-quality-audit", settle=2.0)

        # ---------------------------------------------------------------- approve
        # Chainlit renders the actions as buttons; fall back to typing the verdict if
        # the markup ever changes, so the capture degrades rather than fails.
        approve = page.locator("button", has_text="Approve")
        print(f"  → approving ({approve.count()} button(s) matched)", flush=True)
        if approve.count():
            approve.first.click()
        else:
            cap.send("approve")

        cap.wait_for_text("run finished", timeout_s=420, label="completion")
        cap.shot("05-deliverables", settle=2.5)
        cap.shot("06-dashboard-cost", settle=0.5, full=True)

        # ---------------------------------------------------------------- mobile
        if mobile_shot:
            print("  → capturing a phone viewport", flush=True)
            mobile = browser.new_context(
                viewport=MOBILE,
                locale="en-US",
                is_mobile=True,
                has_touch=True,
                device_scale_factor=2,
            )
            mpage = mobile.new_page()
            mpage.goto(url, wait_until="domcontentloaded", timeout=90_000)
            try:
                mpage.wait_for_function(
                    "() => document.body.innerText.includes('SOWSprint.ai')",
                    timeout=90_000,
                )
                mpage.wait_for_timeout(3500)
                mpage.screenshot(path=str(out / "07-mobile.png"))
                print("    📸 07-mobile.png", flush=True)
            except PlaywrightTimeout:
                print("    ⏱ mobile capture timed out", flush=True)
            mobile.close()

        video = page.video
        context.close()  # closes and flushes the recording
        if video is not None:
            try:
                target = out / "session.webm"
                Path(video.path()).replace(target)
                print(f"    🎬 session.webm ({(target.stat().st_size / 1e6):.1f} MB)", flush=True)
            except Exception as exc:
                print(f"    ⚠️ could not keep the recording: {exc}", flush=True)

        browser.close()

    print(f"  ✓ capture finished in {time.time() - started:.0f}s", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://sowsprint-app:8000")
    parser.add_argument("--out", default="/demo/out")
    parser.add_argument("--no-mobile", action="store_true")
    args = parser.parse_args(argv)
    return capture(args.url, Path(args.out), mobile_shot=not args.no_mobile)


if __name__ == "__main__":
    sys.exit(main())
