"""Tests for authentication.

The properties that matter here are security properties, not features: a wrong password
must never authenticate, a malformed digest must deny rather than crash, a failure must
not reveal whether a username exists, and an enabled-but-unconfigured deployment must
refuse to start rather than quietly serve everyone.
"""

from __future__ import annotations

import time

import pytest

from sowsprint.security import (
    Account,
    AuthConfigError,
    Authenticator,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_the_right_password_verifies(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", digest) is True

    def test_a_wrong_password_does_not(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert verify_password("Correct horse battery staple", digest) is False
        assert verify_password("", digest) is False

    def test_the_same_password_produces_different_digests(self) -> None:
        """A per-hash salt, so identical passwords are not identifiable in storage."""
        assert hash_password("same") != hash_password("same")

    def test_the_digest_is_self_describing(self) -> None:
        scheme, n, r, p, salt, digest = hash_password("x").split(".")
        assert scheme == "scrypt"
        assert (int(n), int(r), int(p)) == (16384, 8, 1)
        assert salt and digest

    def test_the_plaintext_never_appears_in_the_digest(self) -> None:
        assert "hunter2" not in hash_password("hunter2")

    @pytest.mark.parametrize(
        "malformed",
        ["", "garbage", "scrypt$1$2$3", "bcrypt$16384$8$1$AAAA$BBBB", "scrypt$x$8$1$AA$BB"],
    )
    def test_a_malformed_digest_denies_rather_than_raising(self, malformed: str) -> None:
        assert verify_password("anything", malformed) is False

    def test_hashing_is_deliberately_slow(self) -> None:
        """A fast KDF would make an offline attack on a leaked digest cheap."""
        started = time.perf_counter()
        hash_password("timing")
        assert time.perf_counter() - started > 0.01

    def test_an_empty_password_is_rejected_at_creation(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")


class TestApiKeys:
    def test_a_generated_key_is_prefixed_and_long(self) -> None:
        key = generate_api_key()
        assert key.startswith("sowsprint_")
        assert len(key) > 32

    def test_keys_are_unique(self) -> None:
        assert len({generate_api_key() for _ in range(50)}) == 50

    def test_the_digest_is_stable_and_not_the_key(self) -> None:
        key = generate_api_key()
        assert hash_api_key(key) == hash_api_key(key)
        assert key not in hash_api_key(key)


class TestAuthenticator:
    @pytest.fixture
    def auth(self) -> Authenticator:
        return Authenticator(
            [
                Account("alice", display_name="Alice", role="admin", password_hash=hash_password("alice-pw")),
                Account("bob", password_hash=hash_password("bob-pw")),
                Account("ci", role="service", api_key_hash=hash_api_key("ci-key")),
            ]
        )

    def test_password_login(self, auth: Authenticator) -> None:
        account = auth.authenticate_password("alice", "alice-pw")
        assert account is not None
        assert account.username == "alice"
        assert account.is_admin

    def test_wrong_password_is_rejected(self, auth: Authenticator) -> None:
        assert auth.authenticate_password("alice", "nope") is None

    def test_unknown_user_is_rejected(self, auth: Authenticator) -> None:
        assert auth.authenticate_password("mallory", "whatever") is None

    def test_a_service_account_cannot_log_in_with_a_password(self, auth: Authenticator) -> None:
        """Machine accounts hold no password; the API key is their only credential."""
        assert auth.authenticate_password("ci", "ci-key") is None
        assert auth.authenticate_password("ci", "") is None

    def test_api_key_login(self, auth: Authenticator) -> None:
        account = auth.authenticate_api_key("ci-key")
        assert account is not None and account.username == "ci"
        assert account.is_service

    def test_a_wrong_api_key_is_rejected(self, auth: Authenticator) -> None:
        assert auth.authenticate_api_key("not-the-key") is None
        assert auth.authenticate_api_key("") is None

    def test_headers_accept_several_conventional_names(self, auth: Authenticator) -> None:
        assert auth.authenticate_headers({"x-api-key": "ci-key"}) is not None
        assert auth.authenticate_headers({"authorization": "Bearer ci-key"}) is not None
        assert auth.authenticate_headers({"x-sowsprint-key": "ci-key"}) is not None
        assert auth.authenticate_headers({"authorization": "Bearer wrong"}) is None
        assert auth.authenticate_headers({}) is None

    def test_repeated_failures_engage_a_cooldown(self) -> None:
        auth = Authenticator([Account("alice", password_hash=hash_password("pw"))])
        for _ in range(10):
            auth.authenticate_password("alice", "wrong")
        # Even the correct password is refused while the identity is throttled.
        assert auth.authenticate_password("alice", "pw") is None

    def test_a_success_clears_the_failure_count(self) -> None:
        """Behavioural: a legitimate login resets the throttle, not just the counter."""
        auth = Authenticator([Account("alice", password_hash=hash_password("pw"))])
        for _ in range(7):
            auth.authenticate_password("alice", "wrong")
        assert auth.authenticate_password("alice", "pw") is not None
        # Seven more failures must not lock the account, because the success reset it.
        for _ in range(7):
            auth.authenticate_password("alice", "wrong")
        assert auth.authenticate_password("alice", "pw") is not None

    def test_disabled_auth_admits_everyone(self) -> None:
        """Explicit opt-out, for a laptop demo. It must be loudly reported elsewhere."""
        auth = Authenticator([], enabled=False)
        account = auth.authenticate_password("anyone", "anything")
        assert account is not None
        assert auth.authenticate_api_key("").username == "anonymous"


class TestConfiguration:
    def test_enabled_without_accounts_refuses_to_start(self) -> None:
        """Failing closed beats serving an open application the operator believes is locked."""

        class S:
            auth_enabled = True
            auth_users = ""
            auth_api_keys = ""

        with pytest.raises(AuthConfigError) as excinfo:
            Authenticator.from_settings(S())
        message = str(excinfo.value)
        assert "passwd" in message
        assert "SOWSPRINT_AUTH_ENABLED=false" in message

    def test_json_user_configuration(self) -> None:
        import json

        class S:
            auth_enabled = True
            auth_users = json.dumps(
                [
                    {
                        "username": "alice",
                        "display_name": "Alice",
                        "role": "admin",
                        "password_hash": hash_password("pw"),
                    }
                ]
            )
            auth_api_keys = ""

        auth = Authenticator.from_settings(S())
        account = auth.authenticate_password("alice", "pw")
        assert account is not None and account.is_admin
        assert account.display_name == "Alice"

    def test_a_plaintext_password_in_config_is_rejected(self) -> None:
        """Catches pasting a password where a digest belongs."""

        class S:
            auth_enabled = True
            auth_users = '[{"username": "alice", "password_hash": "hunter2"}]'
            auth_api_keys = ""

        with pytest.raises(AuthConfigError, match="scrypt digest"):
            Authenticator.from_settings(S())

    def test_delimited_user_configuration(self) -> None:
        class S:
            auth_enabled = True
            auth_users = f"alice={hash_password('pw')};bob={hash_password('pw2')}"
            auth_api_keys = ""

        auth = Authenticator.from_settings(S())
        assert auth.authenticate_password("alice", "pw") is not None
        assert auth.authenticate_password("bob", "pw2") is not None

    def test_api_key_configuration(self) -> None:
        class S:
            auth_enabled = True
            auth_users = ""
            auth_api_keys = "ci:secret-key;nightly:other-key"

        auth = Authenticator.from_settings(S())
        assert auth.authenticate_api_key("secret-key").username == "ci"
        assert auth.authenticate_api_key("other-key").username == "nightly"
        assert auth.authenticate_api_key("nope") is None

    def test_disabled_auth_with_no_accounts_is_allowed(self) -> None:
        class S:
            auth_enabled = False
            auth_users = ""
            auth_api_keys = ""

        auth = Authenticator.from_settings(S())
        assert auth.enabled is False


class TestDeploymentSafety:
    """Regressions for faults that only appear once the thing is actually deployed."""

    def test_the_digest_contains_no_dollar_sign(self) -> None:
        """Docker Compose interpolates $ in .env, silently corrupting the digest.

        This shipped once: `scrypt$16384$8$1$...` reached the container as `scrypt`,
        because Compose read the cost parameters as unset variables. Every login then
        failed with a correct password and the only signal was a warning about an
        unset variable that nobody reads.
        """
        digest = hash_password("anything")
        assert "$" not in digest

    def test_a_dollar_format_digest_is_rejected_with_a_pointer(self) -> None:
        class S:
            auth_enabled = True
            auth_users = '[{"username": "alice", "password_hash": "scrypt$16384$8$1$AA$BB"}]'
            auth_api_keys = ""

        with pytest.raises(AuthConfigError) as excinfo:
            Authenticator.from_settings(S())
        assert "Docker" in str(excinfo.value)

    def test_the_digest_survives_a_round_trip_through_json(self) -> None:
        """The documented configuration path stores digests inside a JSON array."""
        import json

        digest = hash_password("round-trip")
        assert json.loads(json.dumps({"h": digest}))["h"] == digest

    def test_the_digest_survives_a_shell_echo(self) -> None:
        """No character in the digest may be interpreted by a shell."""
        import subprocess

        digest = hash_password("shell-safe")
        echoed = subprocess.run(
            ["sh", "-c", f'printf %s "{digest}"'], capture_output=True, text=True, check=True
        ).stdout
        assert echoed == digest


class TestStartupValidation:
    """A deployment that cannot authenticate anyone must not report itself healthy."""

    def test_app_startup_builds_the_authenticator_eagerly(self) -> None:
        """Regression: the authenticator used to be built on first login.

        With authentication enabled and no accounts, the container started, passed its
        health check, served the login form, and rejected every credential. The
        docstring claimed startup validation that did not exist. Assert the wiring
        rather than the intention.
        """
        import ast
        from pathlib import Path

        source = Path("app.py").read_text()
        tree = ast.parse(source)
        startup = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_app_startup"
        )
        body = ast.dump(startup)
        assert "_authenticator" in body, (
            "on_app_startup must construct the authenticator so a bad auth "
            "configuration fails at boot rather than at the first login"
        )

    def test_auth_config_error_names_the_remedy(self) -> None:
        class S:
            auth_enabled = True
            auth_users = ""
            auth_api_keys = ""

        with pytest.raises(AuthConfigError) as excinfo:
            Authenticator.from_settings(S())
        message = str(excinfo.value)
        # The message has to be actionable without reading the source.
        assert "passwd" in message
        assert "SOWSPRINT_AUTH_ENABLED=false" in message
