"""Capture Jira's own views for the demo, and why the obvious approach does not work.

The Timeline view rendered as an empty shell for eight attempts across two sessions. It was not
headless mode, and it was not the GPU. The cause was the authentication method.

`browser.new_context(extra_http_headers={"Authorization": ...})` attaches that header to **every**
request the context makes — including the cross-origin loads of Atlassian's own scripts and fonts
from `jira-fe.prod-east.frontend.public.atl-paas.net` and `ds-cdn.prod-east.frontend...`. A
cross-origin request carrying credentials is refused by CORS, so the JavaScript that draws the
timeline never arrived. The page rendered its navigation, then stopped:

    error: Access to script at 'https://jira-fe.../shared~vendor~atlaskit~ar.cda6eb81.js'
           from origin 'https://serger.atlassian.net' has been blocked by CORS policy
    failed: https://ds-cdn.prod-east.frontend.public.atl-paas.net/assets/fonts/...woff2

Signing in instead is not available to a scripted browser: Atlassian guards the login form with
reCAPTCHA, and defeating that is not something this project does.

So the header stays and the requests are routed through Playwright, which sends it at the network
layer where CORS does not apply. The same board then renders in six seconds.

Usage:

    python scripts/capture_jira.py --board 58 --out docs/media
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

#: The views the release video needs, in the order it shows them.
VIEWS: tuple[tuple[str, str, str], ...] = (
    ("jira-backlog", "/jira/software/c/projects/{project}/boards/{board}/backlog", "Backlog"),
    (
        "jira-timeline",
        "/jira/software/c/projects/{project}/boards/{board}/timeline?rangeMode=MONTHS",
        "Timeline",
    ),
)


def _auth_header(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


def capture(
    *,
    base: str,
    project: str,
    board: str,
    email: str,
    token: str,
    out: Path,
    width: int = 1600,
    height: int = 900,
) -> list[Path]:
    """Capture each view. Returns the files written."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - exercised only without the optional dependency
        raise SystemExit("playwright is required: pip install playwright") from None

    jira_host = urlparse(base).hostname
    header = _auth_header(email, token)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--lang=en-US"])
        context = browser.new_context(
            viewport={"width": width, "height": height}, locale="en-US"
        )

        def route_handler(route, request):  # type: ignore[no-untyped-def]
            # The header goes on Jira's own requests and nowhere else. Attaching it to the CDN
            # loads is what broke the timeline; stripping it there is what fixes it.
            headers = dict(request.headers)
            if urlparse(request.url).hostname == jira_host:
                headers["authorization"] = header
            else:
                headers.pop("authorization", None)
            route.continue_(headers=headers)

        context.route("**/*", route_handler)
        page = context.new_page()

        for name, template, marker in VIEWS:
            url = base.rstrip("/") + template.format(project=project, board=board)
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)

            # The cookie banner sits over the top of the board and would appear in every take.
            # "Only necessary" rather than "Accept all": the capture needs no tracking, and a
            # demo recording is not a reason to grant it.
            for label in ("Only necessary", "Accept all"):
                with suppress(Exception):
                    page.get_by_role("button", name=label).first.click(timeout=4000)
                    page.wait_for_timeout(1200)
                    break

            for _ in range(10):
                page.wait_for_timeout(6000)
                if len(page.inner_text("body")) > 1800:
                    break
            else:
                print(f"  ⚠ {name}: the page did not fill in", file=sys.stderr)

            text = page.inner_text("body")
            if marker not in text:
                print(f"  ⚠ {name}: '{marker}' not on the page", file=sys.stderr)

            target = out / f"{name}.png"
            page.screenshot(path=str(target))
            written.append(target)
            print(f"  {name}: {target}")

        context.close()
        browser.close()

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_jira", description=__doc__)
    parser.add_argument("--base", default=os.environ.get("SOWSPRINT_JIRA_BASE_URL", ""))
    parser.add_argument("--project", default="EUSTORE")
    parser.add_argument("--board", default="58")
    parser.add_argument("--out", type=Path, default=Path("docs/media"))
    parser.add_argument("--email", default=os.environ.get("SOWSPRINT_JIRA_EMAIL", ""))
    parser.add_argument("--token", default=os.environ.get("SOWSPRINT_JIRA_API_TOKEN", ""))
    args = parser.parse_args(argv)

    if not (args.base and args.email and args.token):
        parser.error("Jira base URL, email and API token are required")
    if not args.base.startswith("http"):
        # A bare host is the common case in .env files.
        args.base = f"https://{args.base}"

    capture(
        base=args.base,
        project=args.project,
        board=args.board,
        email=args.email,
        token=args.token,
        out=args.out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
