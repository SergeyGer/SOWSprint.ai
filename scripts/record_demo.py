"""Record the full engagement: voice brief in, contract and provisioned workspace out.

Produces, in one pass:

* a video of the whole session (Playwright captures the page continuously),
* a timeline of wall-clock marks for the moments worth keeping, so the thinking pauses
  can be cut out afterwards rather than watched,
* screenshots at each of those moments,
* the transcript of what Whisper heard.

The microphone is real: Chromium is launched with a WAV file standing in for the capture
device, so the app receives audio through the same path a person's voice would take and
transcribes it with the same call. Nothing here is simulated at the application level —
only the sound card is.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8000"
OUT = Path("/out")
VIDEO = Path("/video")
WAV = "/audio/brief-voice.wav"


class Timeline:
    """Wall-clock marks, so the edit knows what to keep."""

    def __init__(self) -> None:
        self.t0 = time.time()
        self.marks: list[dict[str, object]] = []

    def mark(self, name: str, **extra: object) -> None:
        entry = {"name": name, "at": round(time.time() - self.t0, 3), **extra}
        self.marks.append(entry)
        print(f"    [{entry['at']:7.2f}s] {name}" + (f"  {extra}" if extra else ""))

    def save(self) -> None:
        (OUT / "timeline.json").write_text(json.dumps(self.marks, indent=2), encoding="utf-8")


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)


def main() -> int:
    tl = Timeline()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--lang=en-US",
                # A WAV file stands in for the sound card. Without this the audio button
                # records silence and Whisper returns nothing.
                f"--use-file-for-fake-audio-capture={WAV}",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="en-US",
            record_video_dir=str(VIDEO),
            record_video_size={"width": 1600, "height": 1000},
            permissions=["microphone"],
        )
        page = context.new_page()

        # ---------------------------------------------------------------- sign in
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4000)
        page.wait_for_selector("input[type=password]", timeout=30_000)
        page.fill("#email, input[name=email], input[type=text], input[name=username]", "sergey")
        page.fill("input[type=password]", "SOWSprint-2026!")
        page.keyboard.press("Enter")
        page.wait_for_selector("input[type=password]", state="detached", timeout=40_000)
        page.wait_for_timeout(8000)
        tl.mark("signed_in")
        shot(page, "01-signed-in")

        # ---------------------------------------------------------------- dictate
        # Chainlit gives its composer buttons no ids, labels or test ids, and the SVG
        # path selector proved unreliable. Position is the stable fact: the microphone is
        # the leftmost of the three buttons in the composer row, which sits below y=880
        # and left of centre. The upload and settings buttons are to its right.
        def mic_handle():
            buttons = page.evaluate(
                """() => [...document.querySelectorAll('button')]
                    .map((b, i) => ({i, r: b.getBoundingClientRect()}))
                    .filter(b => b.r.width > 0 && b.r.y > 880 && b.r.x < 700)
                    .sort((a, b) => a.r.x - b.r.x)
                    .map(b => b.i)"""
            )
            return buttons[0] if buttons else None

        index = mic_handle()
        mic = page.locator("button").nth(index) if index is not None else None
        if mic is not None:
            tl.mark("mic_found", index=index)

        if mic is None:
            tl.mark("mic_missing")
            page.screenshot(path=str(OUT / "no-mic.png"))
            context.close()
            browser.close()
            tl.save()
            return 1

        mic.click()
        tl.mark("recording_started")
        page.wait_for_timeout(3000)
        shot(page, "02-recording")

        # The WAV supplies audio continuously; 62 seconds covers the 56-second brief with
        # margin for Whisper to receive a complete sentence.
        page.wait_for_timeout(62_000)
        mic.click()
        tl.mark("recording_stopped")

        heard = ""
        for _ in range(30):
            page.wait_for_timeout(2000)
            body = page.inner_text("body")
            if "FastAPI" in body or "Nordwind" in body or "logistics" in body.lower():
                heard = body
                break
        tl.mark("transcribed")
        shot(page, "03-transcribed")
        if heard:
            (OUT / "transcript.txt").write_text(heard[:4000], encoding="utf-8")

        page.keyboard.press("Enter")
        tl.mark("brief_sent")

        # ---------------------------------------------------------------- the run
        # Triage may ask more than once. On a capable model it asks sharper questions and
        # often asks a second round after the first answers narrow the scope — so the loop
        # answers whatever it is asked rather than assuming one round is all there is.
        answers = [
            (
                "Aggregate operational metrics only, no individual employee tracking. "
                "Acceptance is 40% faster monthly close and adoption by 150 of the 200 staff."
            ),
            (
                "No automated decision-making — descriptive and predictive analytics for "
                "human review only, no decisions taken about individuals. The vendor "
                "provides and manages the production infrastructure; hosting and database "
                "licences are included in the fixed price."
            ),
            "Fixed price per milestone, invoiced on acceptance. No other constraints.",
            "Confirmed. Proceed on those terms.",
            "Yes, that is correct. Please proceed with the draft.",
            "Agreed on all points. Draft the contract now.",
        ]
        gate = False
        answered = 0
        for _ in range(120):
            page.wait_for_timeout(10_000)
            body = page.inner_text("body")
            # Count occurrences, not presence. The earlier question block stays in the
            # transcript after it is answered, so a presence check fires again on every
            # poll and the script answers the same round four times over.
            asked = body.count("before I can draft")
            if asked > answered and answered < len(answers):
                tl.mark("clarification_asked", round=answered + 1)
                shot(page, f"04-clarification-{answered + 1}")
                box = page.locator("textarea").first
                box.fill(answers[answered])
                page.keyboard.press("Enter")
                answered += 1
                tl.mark("clarification_answered", round=answered)
            if "approval required" in body:
                gate = True
                tl.mark("approval_gate", clarifications=answered)
                shot(page, "05-approval-gate")
                break

        if not gate:
            tl.mark("no_gate")
            shot(page, "no-gate")
            context.close()
            browser.close()
            tl.save()
            return 2

        # Read the gate out loud in the edit: pause so the viewer can see the plan.
        page.wait_for_timeout(6000)
        button = page.get_by_role("button", name="Approve & provision")
        button.first.click()
        tl.mark("approved")

        done = False
        for _ in range(90):
            page.wait_for_timeout(10_000)
            body = page.inner_text("body")
            if "Where the data went" in body:
                done = True
                tl.mark("terminal")
                break
        shot(page, "06-terminal")
        page.keyboard.press("End")
        page.wait_for_timeout(2500)
        shot(page, "07-terminal-bottom")

        # Scroll to each landmark so the screenshots show the parts that matter.
        for label, name in (
            ("AUDIT", "08-verdict"),
            ("Delivery plan", "09-gantt"),
            ("Documents produced", "10-documents"),
        ):
            loc = page.locator(f"text={label}").first
            if loc.count():
                loc.scroll_into_view_if_needed()
                page.wait_for_timeout(2000)
                shot(page, name)
        tl.mark("recorded", completed=done)

        context.close()
        browser.close()

    tl.save()
    print("\n  timeline written to /out/timeline.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
