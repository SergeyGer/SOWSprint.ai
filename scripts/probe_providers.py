#!/usr/bin/env python
"""Probe every configured cloud service with one minimal, real call.

Answers the question "does my .env actually work?" before you spend an evening
debugging a pipeline that was never going to start.

Each probe performs the smallest request that proves the credential, the endpoint and
the model id are all correct, and reports the latency and the cost of that call. Probes
run independently: one failing service does not stop the others, and you get a complete
picture in one pass rather than fixing one thing at a time.

Usage::

    python scripts/probe_providers.py            # everything that is configured
    python scripts/probe_providers.py --json
    python scripts/probe_providers.py --only anthropic,openai
    python scripts/probe_providers.py --write    # actually write to Jira/Notion

Cost: a full pass is well under one cent — each LLM probe asks for a single short word.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL, SKIP, WARN = "✅", "❌", "⏭️ ", "⚠️ "


class Report:
    def __init__(self, *, as_json: bool) -> None:
        self.rows: list[dict] = []
        self.as_json = as_json

    def add(self, service: str, ok: bool | None, detail: str, ms: float = 0.0, cost: float = 0.0) -> None:
        self.rows.append(
            {"service": service, "ok": ok, "detail": detail, "ms": round(ms), "cost_usd": round(cost, 6)}
        )
        if self.as_json:
            return
        mark = SKIP if ok is None else (PASS if ok else FAIL)
        timing = f"  [{ms:.0f} ms]" if ms else ""
        price = f"  ${cost:.6f}" if cost else ""
        print(f"  {mark} {service:26}{timing}{price}")
        if detail:
            print(f"       {detail}")

    def summary(self) -> int:
        failed = [r for r in self.rows if r["ok"] is False]
        passed = [r for r in self.rows if r["ok"] is True]
        skipped = [r for r in self.rows if r["ok"] is None]
        spent = sum(r["cost_usd"] for r in self.rows)
        if self.as_json:
            print(json.dumps({"results": self.rows, "spent_usd": round(spent, 6)}, indent=2, default=str))
        else:
            print()
            print(f"  {len(passed)} working · {len(failed)} failing · {len(skipped)} not configured")
            print(f"  total spend for this probe: ${spent:.6f}")
        return 1 if failed else 0


def probe_anthropic(settings, report: Report) -> None:
    if not settings.anthropic_api_key:
        report.add("anthropic", None, "no SOWSPRINT_ANTHROPIC_API_KEY")
        return
    from anthropic import Anthropic

    model = settings.reasoning_model or "claude-haiku-5-5"
    client = Anthropic(api_key=settings.anthropic_api_key)
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except Exception as exc:
        message = str(exc)
        hint = ""
        if "temperature" in message:
            hint = " (the app retries without temperature; harmless)"
        if "credit" in message.lower() or "quota" in message.lower():
            hint = " (billing problem, not a credential problem)"
        report.add("anthropic", False, f"{type(exc).__name__}: {message[:150]}{hint}")
        return

    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    usage = response.usage
    from sowsprint.telemetry.pricing import lookup

    cost = lookup(model).cost(usage.input_tokens, usage.output_tokens)
    report.add(
        "anthropic",
        True,
        f"{model} replied {text.strip()[:20]!r} · in={usage.input_tokens} out={usage.output_tokens}",
        (time.perf_counter() - started) * 1000,
        cost,
    )


def probe_openai(settings, report: Report) -> None:
    if not settings.openai_api_key:
        report.add("openai", None, "no SOWSPRINT_OPENAI_API_KEY")
        return
    from openai import OpenAI

    model = settings.critic_model or "gpt-5.1"
    client = OpenAI(api_key=settings.openai_api_key, timeout=90)
    started = time.perf_counter()
    # Reasoning models reject `temperature` and need `max_completion_tokens`.
    reasoning = model.startswith(("o1", "o3", "o4", "gpt-5"))
    kwargs = {"max_completion_tokens": 64} if reasoning else {"max_tokens": 16, "temperature": 0}
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            **kwargs,
        )
    except Exception as exc:
        message = str(exc)
        if "must be verified" in message:
            message = (
                "organisation verification required for this model — verify at "
                "https://platform.openai.com/settings/organization/general, or pick "
                "another model such as gpt-5.1 / gpt-4.1-mini / o4-mini"
            )
        report.add("openai", False, f"{type(exc).__name__}: {message[:200]}")
        return

    usage = response.usage
    from sowsprint.telemetry.pricing import lookup

    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    cost = lookup(model).cost(usage.prompt_tokens, usage.completion_tokens, cached)
    report.add(
        "openai",
        True,
        f"{model} replied {(response.choices[0].message.content or '').strip()[:20]!r} "
        f"· in={usage.prompt_tokens} out={usage.completion_tokens} cached={cached}",
        (time.perf_counter() - started) * 1000,
        cost,
    )


def probe_embeddings(settings, report: Report) -> None:
    provider = (settings.embedding_provider or "hash").lower()
    if provider in ("hash", "auto"):
        report.add(
            "embeddings",
            None,
            f"provider is {provider!r}: local feature hashing, no cloud call and no cost. "
            "Set SOWSPRINT_EMBEDDING_PROVIDER=openai for semantic embeddings, then `make reingest`.",
        )
        return
    if not settings.openai_api_key:
        report.add("embeddings", False, "provider is openai but SOWSPRINT_OPENAI_API_KEY is empty")
        return
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, timeout=60)
    started = time.perf_counter()
    try:
        response = client.embeddings.create(model=settings.embedding_model, input="compliance clause")
    except Exception as exc:
        report.add("embeddings", False, f"{type(exc).__name__}: {str(exc)[:160]}")
        return
    dim = len(response.data[0].embedding)
    report.add(
        "embeddings",
        True,
        f"{settings.embedding_model} returned {dim} dimensions"
        + (
            f" — MISMATCH: SOWSPRINT_EMBEDDING_DIM is {settings.embedding_dim}"
            if dim != settings.embedding_dim
            else ""
        ),
        (time.perf_counter() - started) * 1000,
    )


def probe_whisper(settings, report: Report) -> None:
    provider = (settings.whisper_provider or "auto").lower()
    if provider == "offline":
        report.add("transcription", None, "provider is 'offline': voice input is disabled")
        return
    if not settings.openai_api_key and provider in ("auto", "openai"):
        report.add("transcription", None, "no OpenAI key; voice falls back to offline")
        return

    # A real transcription bills by the minute, so probe with one second of silence.
    # transcribe_audio takes a PATH, because the provider SDKs stream a file handle.
    import struct
    import tempfile
    import wave

    from sowsprint.voice.transcribe import transcribe_audio

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        probe_path = Path(handle.name)
    try:
        with wave.open(str(probe_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(settings.audio_sample_rate or 16000)
            writer.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))

        started = time.perf_counter()
        result = transcribe_audio(probe_path, settings=settings, session_id="probe")
        report.add(
            "transcription",
            True,
            f"provider={getattr(result, 'provider', provider)} accepted a 1 s silent WAV "
            "— the endpoint, the key and the request shape all work",
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        report.add("transcription", False, f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        probe_path.unlink(missing_ok=True)


def probe_qdrant(settings, report: Report) -> None:
    if settings.vector_backend.value != "qdrant":
        report.add("qdrant", None, f"vector backend is {settings.vector_backend.value!r}, not qdrant")
        return
    import httpx

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=10.0) as client:
            # healthz proves the service answers; the collection lookup proves the
            # index exists. `/collections` is not needed for either.
            client.get(f"{settings.qdrant_url.rstrip('/')}/healthz")
            info = client.get(
                f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}"
            )
    except Exception as exc:
        report.add(
            "qdrant",
            False,
            f"{type(exc).__name__}: cannot reach {settings.qdrant_url}. Start it with "
            "`docker compose up -d sowsprint-qdrant`.",
        )
        return

    if info.status_code == 404:
        report.add(
            "qdrant",
            False,
            f"reachable, but collection {settings.qdrant_collection!r} does not exist — "
            "run `make ingest`",
        )
        return
    points = (info.json().get("result") or {}).get("points_count")
    report.add(
        "qdrant",
        True,
        f"{settings.qdrant_url} · collection {settings.qdrant_collection!r} holds {points} vectors",
        (time.perf_counter() - started) * 1000,
    )


def probe_retrieval(settings, report: Report) -> None:
    """End-to-end: embed a query, filter by jurisdiction, return passages."""
    from sowsprint.rag.pipeline import RagPipeline

    started = time.perf_counter()
    try:
        pipeline = RagPipeline(settings)
        pipeline.ensure_indexed()
        result = pipeline.retriever.retrieve("data protection obligations", final_k=3)
    except Exception as exc:
        report.add("retrieval", False, f"{type(exc).__name__}: {str(exc)[:160]}")
        return
    if not result.chunks:
        report.add("retrieval", False, "the index returned no passages — is the corpus ingested?")
        return
    regimes = sorted({c.jurisdiction for c in result.chunks})
    report.add(
        "retrieval",
        True,
        f"{len(result.chunks)} passage(s), regimes={regimes}, "
        f"best={result.chunks[0].chunk_id} ({result.chunks[0].jurisdiction})",
        (time.perf_counter() - started) * 1000,
    )


def probe_integrations(settings, report: Report, write: bool) -> None:
    """Authenticate against Jira and Notion with a real read-only request.

    A configuration check would only restate what .env says. These are live calls, so a
    revoked token or an unshared Notion page is reported here rather than surfacing as
    an opaque failure several steps into an engagement.
    """
    import httpx

    # ---- Jira ---------------------------------------------------------------
    if not settings.jira_configured:
        report.add("jira", None, "not configured — fill the SOWSPRINT_JIRA_* settings")
    else:
        started = time.perf_counter()
        base = (settings.jira_base_url or "").rstrip("/")
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{base}/rest/api/3/myself",
                    auth=(settings.jira_email, settings.jira_api_token),
                    headers={"Accept": "application/json"},
                )
            if response.status_code == 200:
                me = response.json()
                report.add(
                    "jira",
                    True,
                    f"signed in as {me.get('displayName')} · mode="
                    f"{settings.integration_mode(True)}"
                    + ("" if write else " (dry-run: no writes)"),
                    (time.perf_counter() - started) * 1000,
                )
            else:
                hint = ""
                if response.status_code == 401:
                    hint = (
                        " — the email must be the Atlassian ACCOUNT address, which is "
                        "often not the company domain. Check "
                        "https://id.atlassian.com/manage-profile/security"
                    )
                report.add(
                    "jira", False, f"HTTP {response.status_code} from /rest/api/3/myself{hint}"
                )
        except Exception as exc:
            report.add("jira", False, f"{type(exc).__name__}: {str(exc)[:160]}")

    # ---- Notion -------------------------------------------------------------
    if not settings.notion_configured:
        report.add("notion", None, "not configured — fill the SOWSPRINT_NOTION_* settings")
        return
    started = time.perf_counter()
    headers = {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Notion-Version": "2022-06-28",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            who = client.get("https://api.notion.com/v1/users/me", headers=headers)
            if who.status_code != 200:
                report.add("notion", False, f"HTTP {who.status_code} authenticating")
                return
            page = client.get(
                f"https://api.notion.com/v1/blocks/{settings.notion_parent_page_id}/children",
                headers=headers,
                params={"page_size": 1},
            )
        if page.status_code == 404:
            report.add(
                "notion",
                False,
                "the key is valid but the parent page is NOT shared with the integration — "
                "open it in Notion, use ••• → Connections, and connect the integration",
            )
            return
        report.add(
            "notion",
            True,
            f"integration {who.json().get('name')!r} can write to the parent page · mode="
            f"{settings.integration_mode(True)}"
            + ("" if write else " (dry-run: no writes)"),
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        report.add("notion", False, f"{type(exc).__name__}: {str(exc)[:160]}")


PROBES = {
    "anthropic": probe_anthropic,
    "openai": probe_openai,
    "embeddings": probe_embeddings,
    "transcription": probe_whisper,
    "qdrant": probe_qdrant,
    "retrieval": probe_retrieval,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Comma-separated subset, e.g. anthropic,openai,qdrant")
    parser.add_argument("--skip-retrieval", action="store_true", help="Skip the index round-trip.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true", help="Allow integration probes to write.")
    args = parser.parse_args(argv)

    from sowsprint.config import get_settings

    settings = get_settings()
    report = Report(as_json=args.json)

    if not args.json:
        print("SOWSprint.ai — cloud service probe\n")
        print("  resolved configuration")
        for key, value in settings.capability_matrix().items():
            print(f"    {key:14} {value}")
        print()

    wanted = [s.strip() for s in args.only.split(",")] if args.only else list(PROBES)
    if args.skip_retrieval and "retrieval" in wanted:
        wanted.remove("retrieval")

    for name in wanted:
        probe = PROBES.get(name)
        if probe is None:
            report.add(name, False, f"unknown probe; choose from {', '.join(PROBES)}")
            continue
        probe(settings, report)

    probe_integrations(settings, report, args.write)
    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
