#!/usr/bin/env python
"""Edit `.env` safely — one key at a time, never a rewrite.

Why this exists
---------------
A section-rewriting regular expression was used once to update `.env`. It matched a
larger span than intended and silently emptied **ten** credential fields, including
every API key. The damage was invisible until a probe reported every service as
unconfigured, and by then the previous container had been recreated, so the values were
unrecoverable.

A file holding every credential the deployment has should not be edited by a pattern
match. This tool parses the file into lines, replaces exactly one line, preserves
comments, ordering and every other value, and refuses to write if it would change
anything beyond the key it was asked about.

Usage::

    python scripts/setenv.py SOWSPRINT_OPENAI_API_KEY sk-...        # set
    python scripts/setenv.py SOWSPRINT_GROQ_API_KEY --unset         # clear
    python scripts/setenv.py --show                                 # what is empty
    python scripts/setenv.py --check                                # exit 1 if broken
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

#: Fields that must hold a value for the deployment to do anything useful. Anything not
#: listed is optional and its emptiness is not reported as a problem.
REQUIRED_FOR_CLOUD = {
    "SOWSPRINT_ANTHROPIC_API_KEY": "drafting agent (Anthropic)",
    "SOWSPRINT_OPENAI_API_KEY": "judge, embeddings and voice (OpenAI)",
    "SOWSPRINT_AUTH_USERS": "at least one account, or nobody can sign in",
    "CHAINLIT_AUTH_SECRET": "Chainlit refuses to start without it",
    "SOWSPRINT_AUTH_API_KEYS": "machine accounts for the harnesses (optional if unused)",
}
REQUIRED_FOR_INTEGRATIONS = {
    "SOWSPRINT_JIRA_BASE_URL": "Jira site, e.g. https://you.atlassian.net",
    "SOWSPRINT_JIRA_EMAIL": "your Atlassian ACCOUNT address",
    "SOWSPRINT_JIRA_API_TOKEN": "from id.atlassian.com → Security → API tokens",
    "SOWSPRINT_NOTION_API_KEY": "from notion.so/my-integrations",
    "SOWSPRINT_NOTION_PARENT_PAGE_ID": "32 hex characters from the page URL",
}


def read_lines() -> list[str]:
    if not ENV_PATH.is_file():
        raise SystemExit(f"{ENV_PATH} does not exist; copy .env.example to .env first")
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def current_value(key: str, lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :]
    return None


def set_key(key: str, value: str | None, lines: list[str]) -> tuple[list[str], bool]:
    """Replace exactly one line. Returns (lines, changed).

    Refuses to proceed if the key appears more than once, because that is the state that
    made an earlier debugging session report a working credential as missing: two
    entries, one of them empty, and the loader picking the wrong one.
    """
    occurrences = [i for i, line in enumerate(lines) if line.startswith(f"{key}=")]
    if len(occurrences) > 1:
        raise SystemExit(
            f"{key} appears {len(occurrences)} times. Remove the duplicates by hand "
            "first — which one wins depends on the loader, and an empty duplicated key "
            "reads as 'configured' while being useless."
        )
    new_line = f"{key}=" if value is None else f"{key}={value}"
    if occurrences:
        index = occurrences[0]
        if lines[index] == new_line:
            return lines, False
        lines[index] = new_line
        return lines, True
    lines.append(new_line)
    return lines, True


def write_atomically(lines: list[str]) -> None:
    """Write via a temporary file, so an interrupted run cannot truncate `.env`."""
    import os
    import tempfile

    original = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.is_file() else ""
    payload = "\n".join(lines) + "\n"

    # Safety net: never let a credential line lose its value by accident.
    before = _credential_values(original)
    after = _credential_values(payload)
    lost = [k for k, v in before.items() if v and not after.get(k)]
    if lost:
        raise SystemExit(
            "refusing to write: this change would empty " + ", ".join(sorted(lost))
        )

    handle, name = tempfile.mkstemp(dir=str(ENV_PATH.parent), prefix=".env.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.chmod(name, 0o600)  # the file holds every credential in the deployment
        os.replace(name, ENV_PATH)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _credential_values(text: str) -> dict[str, str]:
    pattern = re.compile(r"^([A-Z0-9_]*(?:API_KEY|API_TOKEN|AUTH_USERS|AUTH_API_KEYS|SECRET|PAGE_ID))=(\S+)$", re.M)
    return {m.group(1): m.group(2) for m in pattern.finditer(text)}


def report(lines: list[str], groups: dict[str, dict[str, str]]) -> int:
    problems = 0
    for title, mapping in groups.items():
        print(f"\n  {title}")
        for key, why in mapping.items():
            value = current_value(key, lines)
            if value is None:
                print(f"    ❌ {key:34} absent — {why}")
                problems += 1
            elif not value.strip():
                print(f"    ❌ {key:34} empty  — {why}")
                problems += 1
            else:
                print(f"    ✅ {key:34} {len(value)} chars")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("key", nargs="?", help="Setting name, e.g. SOWSPRINT_OPENAI_API_KEY")
    parser.add_argument("value", nargs="?", help="New value. Omit to read from stdin.")
    parser.add_argument("--unset", action="store_true", help="Clear the value.")
    parser.add_argument("--from-file", metavar="PATH", help="Read the value from a file (keeps it out of shell history).")
    parser.add_argument("--show", action="store_true", help="Report which credentials are present.")
    parser.add_argument("--check", action="store_true", help="Same as --show, but exit non-zero on any gap.")
    args = parser.parse_args(argv)

    lines = read_lines()

    if args.show or args.check:
        print("SOWSprint.ai — credential presence (values are never printed)")
        gaps = report(lines, {"Core": REQUIRED_FOR_CLOUD, "Integrations (optional)": REQUIRED_FOR_INTEGRATIONS})
        print()
        if gaps:
            print(f"  {gaps} gap(s). Fill them with:")
            print("    python scripts/setenv.py SOWSPRINT_OPENAI_API_KEY sk-...")
            print("  or use --from-file so the value never reaches shell history.")
        else:
            print("  every required credential is present")
        # --show is informational; --check is a gate.
        return 1 if (gaps and args.check) else 0

    if not args.key:
        parser.error("provide a key, or use --show / --check")

    value: str | None
    if args.unset:
        value = None
    elif args.from_file:
        value = Path(args.from_file).read_text(encoding="utf-8").strip()
    elif args.value is not None:
        value = args.value.strip()
    else:
        import getpass

        value = getpass.getpass(f"Value for {args.key}: ").strip()

    lines, changed = set_key(args.key, value, lines)
    if not changed:
        print(f"  {args.key} already has that value; nothing written")
        return 0

    write_atomically(lines)
    state = "cleared" if value is None else f"set ({len(value)} chars)"
    print(f"  {args.key} {state}")
    print("  Restart for it to take effect:  docker compose up -d --force-recreate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
