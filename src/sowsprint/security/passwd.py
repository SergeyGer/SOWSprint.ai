"""Generate password hashes and API keys for ``SOWSPRINT_AUTH_*`` configuration.

Usage::

    # Prompt for the password (never on the command line, where it lands in history)
    python -m sowsprint.security.passwd --user alice

    # Non-interactive, for provisioning scripts
    python -m sowsprint.security.passwd --user alice --password 'hunter2' --json

    # A machine account for the verification harnesses and CI
    python -m sowsprint.security.passwd --api-key ci-runner
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from .auth import generate_api_key, hash_api_key, hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", help="Username to create a password hash for.")
    parser.add_argument(
        "--password",
        help="Password. Omit to be prompted; passing it here leaves it in shell history.",
    )
    parser.add_argument("--display-name", default="", help="Shown in the interface.")
    parser.add_argument("--role", default="member", choices=["member", "admin"])
    parser.add_argument("--api-key", metavar="NAME", help="Generate an API key for a machine account.")
    parser.add_argument("--json", action="store_true", help="Emit a JSON entry ready to paste.")
    args = parser.parse_args(argv)

    if args.api_key:
        key = generate_api_key()
        if args.json:
            print(json.dumps({"name": args.api_key, "key": key, "sha256": hash_api_key(key)}, indent=2))
        else:
            print("API key generated. It is shown once — store it now.\n")
            print(f"  SOWSPRINT_AUTH_API_KEYS={args.api_key}:{key}")
            print("\nUse it as a header:")
            print(f"  X-API-Key: {key}")
        return 0

    if not args.user:
        parser.error("either --user or --api-key is required")

    password = args.password
    if not password:
        password = getpass.getpass(f"Password for {args.user}: ")
        confirm = getpass.getpass("Repeat: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
    if len(password) < 8:
        print("Refusing a password shorter than 8 characters.", file=sys.stderr)
        return 1

    entry = {
        "username": args.user,
        "display_name": args.display_name or args.user,
        "role": args.role,
        "password_hash": hash_password(password),
    }

    if args.json:
        print(json.dumps([entry], indent=2))
    else:
        print("\nAdd to your .env (single line):\n")
        print(f"SOWSPRINT_AUTH_USERS={json.dumps([entry], separators=(',', ':'))}")
        print("\nMultiple users: put a JSON array containing every entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
