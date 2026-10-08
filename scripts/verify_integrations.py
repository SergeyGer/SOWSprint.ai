#!/usr/bin/env python
"""Pre-flight check for the Jira and Notion integrations.

Credential problems in these two services surface as opaque 401/403/404 responses
several steps into a run, long after the contract has been drafted. This script
verifies the whole chain up front using **read-only** calls — it never creates a
project, an issue or a page.

Checks performed:

Jira
  1. ``GET /rest/api/3/myself``      — are the email and API token valid?
  2. ``GET /rest/api/3/project/search`` — can the account see projects at all?
  3. ``GET /rest/api/3/field``       — which custom field carries Story Points and,
                                       for company-managed projects, Epic Link?

Notion
  1. ``GET /v1/users/me``            — is the integration secret valid?
  2. ``GET /v1/blocks/{page_id}``    — is the parent page actually shared with the
                                       integration? This is the single most common
                                       failure: a valid key still returns 404 when the
                                       page was never connected.

Usage::

    python scripts/verify_integrations.py
    python scripts/verify_integrations.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def _redact(text: str) -> str:
    """Strip anything that looks like a credential out of a message."""
    import re

    text = re.sub(r"(ATATT|ntn_|secret_|sk-)[A-Za-z0-9_\-]{6,}", r"\1…<redacted>", text)
    return text


class Results:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def add(self, service: str, check: str, ok: bool, detail: str) -> None:
        self.rows.append(
            {"service": service, "check": check, "ok": ok, "detail": _redact(detail)}
        )
        mark = PASS if ok else FAIL
        print(f"  {mark} {check}")
        if detail:
            print(f"      {detail}")

    @property
    def failures(self) -> list[dict[str, object]]:
        return [row for row in self.rows if not row["ok"]]


def check_jira(settings, results: Results) -> None:
    import httpx

    base = (settings.jira_base_url or "").rstrip("/")
    if not (base and settings.jira_email and settings.jira_api_token):
        results.add("jira", "credentials configured", False, "base URL, email and token are all required")
        return

    auth = (settings.jira_email, settings.jira_api_token)
    headers = {"Accept": "application/json"}

    try:
        with httpx.Client(timeout=30.0) as client:
            # ---- 1. identity -------------------------------------------------
            response = client.get(f"{base}/rest/api/3/myself", auth=auth, headers=headers)
            if response.status_code == 401:
                results.add("jira", "authenticate", False, "401 — email or API token is wrong")
                return
            if response.status_code == 403:
                results.add("jira", "authenticate", False, "403 — token valid but lacks permission")
                return
            response.raise_for_status()
            me = response.json()
            results.add(
                "jira",
                "authenticate",
                True,
                f"signed in as {me.get('displayName')} ({me.get('emailAddress')})",
            )

            # ---- 2. project visibility ---------------------------------------
            response = client.get(
                f"{base}/rest/api/3/project/search",
                auth=auth,
                headers=headers,
                params={"maxResults": 5},
            )
            if response.status_code == 200:
                total = response.json().get("total", 0)
                results.add("jira", "browse projects", True, f"{total} project(s) visible")
            else:
                results.add(
                    "jira",
                    "browse projects",
                    False,
                    f"HTTP {response.status_code} — the account may lack the 'Browse Projects' permission",
                )

            # ---- 3. field discovery ------------------------------------------
            response = client.get(f"{base}/rest/api/3/field", auth=auth, headers=headers)
            if response.status_code == 200:
                fields = response.json()
                story = next(
                    (f for f in fields if f.get("name") == "Story Points"), None
                )
                epic_link = next(
                    (f for f in fields if f.get("name") == "Epic Link"), None
                )
                detail = (
                    f"Story Points -> {story['id'] if story else 'not found'}; "
                    f"Epic Link -> {epic_link['id'] if epic_link else 'not found'}"
                )
                results.add("jira", "custom field discovery", True, detail)
                if story and story["id"] != "customfield_10016":
                    results.add(
                        "jira",
                        "story-points field id",
                        False,
                        f"this tenant uses {story['id']}, but the connector writes "
                        f"customfield_10016 — set it in tools/jira.py",
                    )
                if epic_link:
                    results.add(
                        "jira",
                        "epic linking model",
                        True,
                        f"'Epic Link' exists ({epic_link['id']}) — this tenant uses "
                        f"company-managed projects, which link stories via that field, "
                        f"not via 'parent'",
                    )
                else:
                    results.add(
                        "jira",
                        "epic linking model",
                        True,
                        "no 'Epic Link' field — team-managed project, 'parent' linking applies",
                    )
            else:
                results.add("jira", "custom field discovery", False, f"HTTP {response.status_code}")

    except Exception as exc:
        results.add("jira", "connect", False, f"{type(exc).__name__}: {exc}")


def check_notion(settings, results: Results) -> None:
    import httpx

    if not (settings.notion_api_key and settings.notion_parent_page_id):
        results.add(
            "notion", "credentials configured", False, "API key and parent page id are both required"
        )
        return

    headers = {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Notion-Version": "2022-06-28",
    }
    page_id = settings.notion_parent_page_id

    try:
        with httpx.Client(timeout=30.0) as client:
            # ---- 1. identity -------------------------------------------------
            response = client.get("https://api.notion.com/v1/users/me", headers=headers)
            if response.status_code == 401:
                results.add("notion", "authenticate", False, "401 — the integration secret is wrong")
                return
            response.raise_for_status()
            me = response.json()
            name = me.get("name") or me.get("bot", {}).get("owner", {}).get("type")
            results.add("notion", "authenticate", True, f"integration: {name}")

            # ---- 2. is the page shared with the integration? ------------------
            response = client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=headers,
                params={"page_size": 1},
            )
            if response.status_code == 404:
                results.add(
                    "notion",
                    "parent page reachable",
                    False,
                    "404 — the page exists but is NOT shared with this integration. "
                    "Open the page in Notion, use ••• → Connections → connect your "
                    "integration. A valid key alone is not enough.",
                )
            elif response.status_code == 200:
                results.add("notion", "parent page reachable", True, f"page {page_id[:8]}… is shared")
            else:
                results.add(
                    "notion",
                    "parent page reachable",
                    False,
                    f"HTTP {response.status_code}: {response.text[:160]}",
                )

            # ---- 3. can we write at all? -------------------------------------
            response = client.get("https://api.notion.com/v1/search", headers=headers, params={"page_size": 1})
            if response.status_code == 200:
                count = len(response.json().get("results", []))
                results.add(
                    "notion",
                    "search capability",
                    True,
                    f"{count} object(s) visible to the integration",
                )
            else:
                results.add(
                    "notion",
                    "search capability",
                    True,
                    "search not enabled on this integration (optional — page creation "
                    "only needs 'Insert content')",
                )

    except Exception as exc:
        results.add("notion", "connect", False, f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Jira and Notion credentials")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args(argv)

    from sowsprint.config import get_settings

    settings = get_settings()
    results = Results()

    if not args.json:
        print("SOWSprint.ai — integration pre-flight\n")
        print(f"  dry_run_integrations = {settings.dry_run_integrations}")
        if settings.dry_run_integrations:
            print(
                "  (credentials are still verified below; dry-run only affects whether\n"
                "   the agents are allowed to WRITE during an engagement)\n"
            )
        print("Jira")
    check_jira(settings, results)

    if not args.json:
        print("\nNotion")
    check_notion(settings, results)

    if args.json:
        print(json.dumps({"results": results.rows}, indent=2, default=str))
        return 1 if results.failures else 0

    print()
    if results.failures:
        print(f"{FAIL} {len(results.failures)} check(s) failed")
        return 1
    print(f"{PASS} all checks passed — the integrations are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
