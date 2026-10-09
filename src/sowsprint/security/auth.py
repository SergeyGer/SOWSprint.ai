"""Authentication and authorisation for the SOWSprint web application.

Threat model
------------
The app spends real money on LLM calls and writes to Jira and Notion. Deployed on a
LAN — which is exactly how it is meant to be reached from a phone — anything that can
route to the port would otherwise be able to start engagements and provision
workspaces on the owner's accounts. Authentication is therefore on by default.

Two credential types, because two kinds of caller exist:

* **Humans** sign in with a username and password through Chainlit's login form.
  Passwords are stored as scrypt digests, never in plaintext.
* **Automation** (the verification harnesses, CI) presents an API key in a header.
  A key is a bearer credential for a machine account, so it is treated as a secret:
  never logged, never echoed in an error, compared in constant time.

Passwords are hashed with :func:`hashlib.scrypt` from the standard library — a
memory-hard KDF — rather than bcrypt or argon2 so that the application carries no
native build dependency. The stored format is self-describing, so the cost parameters
can be raised later without invalidating existing digests::

    scrypt.16384.8.1.<salt-b64>.<hash-b64>

The separator is a dot, not the conventional dollar sign, and that is deliberate.
Digests live in ``.env``, which is read by Docker Compose; Compose performs variable
interpolation on ``$``, so ``scrypt$16384$8$...`` arrives inside the container as
``scrypt`` followed by silence, and every login fails with a correct password. The
failure is invisible — Compose only emits a warning about unset variables — and a dot
appears in neither the base64 alphabet, shell expansion, nor Compose interpolation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field

from ..observability.logging import get_logger

log = get_logger(__name__)

#: scrypt cost parameters. n=2**14 with r=8 costs ~16 MB per hash, which is a sensible
#: balance for a container that must also serve inference and retrieval.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

#: Failed logins tolerated per identity before a cooldown engages.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300


class AuthConfigError(RuntimeError):
    """Raised when authentication is enabled but not usable.

    Startup fails loudly rather than silently downgrading to an open application: an
    authentication layer that quietly disables itself is worse than none, because the
    operator believes the deployment is protected.
    """


# --------------------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------------------
def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """Return a self-describing scrypt digest of ``password``."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=132 * n * r
    )
    return ".".join(
        [
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Check ``password`` against a digest produced by :func:`hash_password`.

    Returns ``False`` rather than raising on a malformed digest, so a corrupted entry
    in configuration denies access instead of crashing the login endpoint.
    """
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split(".")
        if scheme != "scrypt":
            log.error("auth.unknown_hash_scheme", scheme=scheme)
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=132 * int(n) * int(r),
        )
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Account:
    """A principal known to the application."""

    username: str
    display_name: str = ""
    role: str = "member"
    password_hash: str = ""
    #: Set for machine accounts authenticated by an API key rather than a password.
    api_key_hash: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_service(self) -> bool:
        """True for automation accounts, which never sign in interactively."""
        return bool(self.api_key_hash) and not self.password_hash

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_api_key(key: str) -> str:
    """Digest an API key.

    A single SHA-256 is appropriate here, unlike for passwords: the key is 32 bytes of
    CSPRNG output, so there is no low-entropy guess space to slow an attacker down.
    Storing the digest rather than the key means a configuration leak does not yield a
    usable credential.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Generate a new API key, prefixed so it is recognisable in logs and secret scans."""
    return "sowsprint_" + secrets.token_urlsafe(32)


# --------------------------------------------------------------------------------------
# Authenticator
# --------------------------------------------------------------------------------------
class Authenticator:
    """Resolves credentials to accounts and throttles repeated failures."""

    def __init__(self, accounts: list[Account], *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.accounts: dict[str, Account] = {a.username: a for a in accounts}
        self._failures: dict[str, list[float]] = {}

    # -- construction ---------------------------------------------------------------
    @classmethod
    def from_settings(cls, settings) -> Authenticator:
        """Build from ``SOWSPRINT_AUTH_*`` configuration.

        Raises :class:`AuthConfigError` when authentication is enabled but no usable
        account exists, so a misconfigured deployment fails at startup instead of
        running wide open.
        """
        enabled = bool(getattr(settings, "auth_enabled", True))
        accounts: list[Account] = []

        raw_users = (getattr(settings, "auth_users", "") or "").strip()
        if raw_users:
            for entry in _parse_user_entries(raw_users):
                accounts.append(entry)

        raw_keys = (getattr(settings, "auth_api_keys", "") or "").strip()
        if raw_keys:
            for item in raw_keys.replace("\n", ";").split(";"):
                item = item.strip()
                if not item:
                    continue
                name, _, key = item.partition(":")
                name, key = name.strip(), key.strip()
                if not key:
                    # Allow "name" alone only when it is itself the key (anonymous
                    # machine account); otherwise the entry is malformed.
                    name, key = "service", item
                accounts.append(
                    Account(
                        username=name,
                        display_name=f"{name} (service)",
                        role="service",
                        api_key_hash=hash_api_key(key),
                    )
                )

        if enabled and not accounts:
            raise AuthConfigError(
                "Authentication is enabled but no accounts are configured, so nobody "
                "could sign in.\n"
                "Create one with:\n"
                "    python -m sowsprint.security.passwd --user you --password '...'\n"
                "and put the result in SOWSPRINT_AUTH_USERS in your .env. For unattended "
                "harnesses set SOWSPRINT_AUTH_API_KEYS instead. To run an intentionally "
                "open deployment set SOWSPRINT_AUTH_ENABLED=false."
            )

        if enabled:
            log.info(
                "auth.enabled",
                accounts=len(accounts),
                password_accounts=sum(1 for a in accounts if a.password_hash),
                service_accounts=sum(1 for a in accounts if a.is_service),
            )
        else:
            log.warning(
                "auth.disabled",
                detail="the application is open to anyone who can reach the port",
            )
        return cls(accounts, enabled=enabled)

    # -- throttling -----------------------------------------------------------------
    def _throttled(self, username: str) -> bool:
        now = time.monotonic()
        attempts = [t for t in self._failures.get(username, []) if now - t < LOCKOUT_SECONDS]
        self._failures[username] = attempts
        return len(attempts) >= MAX_FAILED_ATTEMPTS

    def _record_failure(self, username: str) -> None:
        self._failures.setdefault(username, []).append(time.monotonic())

    # -- authentication -------------------------------------------------------------
    def authenticate_password(self, username: str, password: str) -> Account | None:
        """Validate a username and password.

        Every failure path returns ``None`` and takes a comparable amount of time, so
        the response does not reveal whether the username exists.
        """
        if not self.enabled:
            return Account(username=username or "anonymous", display_name="Anonymous", role="admin")

        account = self.accounts.get((username or "").strip())
        if self._throttled(username or ""):
            log.warning("auth.throttled", username=username)
            # Still perform a hash so timing does not distinguish throttle from failure.
            verify_password(password or "", hash_password("decoy"))
            return None

        if account is None or not account.password_hash:
            verify_password(password or "", hash_password("decoy"))
            self._record_failure(username or "")
            log.warning("auth.failed", username=username, reason="unknown_account")
            return None

        if not verify_password(password or "", account.password_hash):
            self._record_failure(username)
            log.warning("auth.failed", username=username, reason="bad_password")
            return None

        self._failures.pop(username, None)
        log.info("auth.success", username=account.username, method="password")
        return account

    def authenticate_api_key(self, key: str) -> Account | None:
        """Validate a bearer API key against the service accounts."""
        if not self.enabled:
            return Account(username="anonymous", display_name="Anonymous", role="admin", api_key_hash="")
        if not key:
            return None
        digest = hash_api_key(key.strip())
        for account in self.accounts.values():
            if account.api_key_hash and hmac.compare_digest(account.api_key_hash, digest):
                log.info("auth.success", username=account.username, method="api_key")
                return account
        log.warning("auth.failed", method="api_key", reason="unknown_key")
        return None

    def authenticate_headers(self, headers) -> Account | None:
        """Extract and validate an API key from request headers."""
        for name in ("x-api-key", "authorization", "x-sowsprint-key"):
            value = headers.get(name) if hasattr(headers, "get") else None
            if not value:
                continue
            token = value.split(" ", 1)[1] if " " in value else value
            account = self.authenticate_api_key(token)
            if account is not None:
                return account
        return None

    def to_chainlit_user(self, account: Account):
        """Convert to a Chainlit ``User`` without exposing credential material."""
        import chainlit as cl

        return cl.User(
            identifier=account.username,
            display_name=account.display_name or account.username,
            metadata={"role": account.role, "service": account.is_service},
        )


def _parse_user_entries(raw: str) -> list[Account]:
    """Parse the ``SOWSPRINT_AUTH_USERS`` setting.

    Accepts either a JSON array (readable, and what the passwd tool prints) or a
    compact ``user:hash`` list separated by semicolons. The JSON form is preferred in
    documentation because a bcrypt-style digest itself contains ``$`` and ``:``
    characters that make the delimited form easy to get wrong.
    """
    raw = raw.strip()
    if raw.startswith("["):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthConfigError(f"SOWSPRINT_AUTH_USERS is not valid JSON: {exc}") from exc
        accounts = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("username"):
                raise AuthConfigError("each SOWSPRINT_AUTH_USERS entry needs a 'username'")
            digest = str(item.get("password_hash", ""))
            if digest and not digest.startswith("scrypt."):
                hint = ""
                if digest.startswith("scrypt$"):
                    hint = (
                        " This looks like the dollar-separated form, which Docker "
                        "Compose interpolates as shell variables; re-generate it."
                    )
                raise AuthConfigError(
                    f"entry {item['username']!r} does not look like a scrypt digest; "
                    f"generate one with `python -m sowsprint.security.passwd`.{hint}"
                )
            accounts.append(
                Account(
                    username=str(item["username"]),
                    display_name=str(item.get("display_name", "")),
                    role=str(item.get("role", "member")),
                    password_hash=digest,
                )
            )
        return accounts

    accounts = []
    for entry in raw.replace("\n", ";").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        username, _, digest = entry.partition("=")
        if not digest:
            username, _, digest = entry.partition(":")
        if not digest.strip():
            raise AuthConfigError(f"entry {entry!r} is missing a password hash")
        accounts.append(Account(username=username.strip(), password_hash=digest.strip()))
    return accounts
