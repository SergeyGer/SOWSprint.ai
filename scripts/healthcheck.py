"""Container health and readiness probe.

Two levels, because "the HTTP server answers" and "the agent can actually retrieve
compliance evidence" are different questions and orchestrators need both:

* **liveness** (default) — the Chainlit HTTP server responds. Cheap, no side effects.
* **readiness** (``--deep``) — the vector store is reachable and the BM25 encoder is
  fitted, i.e. a contract can actually be drafted right now.

Exit codes: 0 healthy, 1 unhealthy.

CLI::

    python scripts/healthcheck.py --url http://127.0.0.1:8000/
    python scripts/healthcheck.py --deep
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def check_http(url: str, timeout: float) -> tuple[bool, str]:
    """Liveness: does the Chainlit server answer with a 2xx/3xx?"""
    request = urllib.request.Request(url, headers={"User-Agent": "sowsprint-healthcheck"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            if 200 <= status < 400:
                return True, f"http {status}"
            return False, f"http {status}"
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_readiness(timeout: float = 15.0) -> tuple[bool, str]:
    """Readiness: can the retriever serve evidence for both jurisdictions?

    Ensures the index first. Ingestion is lazy and idempotent by design, so on a cold
    volume the probe *seeds* the collection rather than declaring the service
    unhealthy — a readiness check that cannot fix a self-healing service is testing
    the wrong thing. On a warm volume this is a single cheap count.
    """
    try:
        from sowsprint.config import get_settings
        from sowsprint.rag.pipeline import get_pipeline

        settings = get_settings()
        pipeline = get_pipeline(settings)
        pipeline.ensure_indexed()
        health = pipeline.health()

        if not health.get("ok"):
            return False, f"vector store empty or unreachable: {health}"

        # Prove the mandatory jurisdiction filter actually narrows results. If both
        # regimes returned the same chunk, the compliance constraint is broken and the
        # container must not be considered ready to serve contracts.
        eu = pipeline.retriever.retrieve("governing law and liability", jurisdiction="EU", final_k=1)
        us = pipeline.retriever.retrieve("governing law and liability", jurisdiction="US", final_k=1)
        if not eu.chunks or not us.chunks:
            return False, "jurisdiction-filtered retrieval returned no evidence"
        if eu.citation_ids() == us.citation_ids():
            return False, "jurisdiction filter is not discriminating between regimes"

        return True, json.dumps(
            {
                "store": health.get("backend"),
                "chunks": health.get("chunks"),
                "bm25_fitted": health.get("bm25_fitted"),
                "eu_probe": eu.citation_ids()[0],
                "us_probe": us.citation_ids()[0],
            }
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SOWSprint.ai health probe")
    parser.add_argument("--url", default="http://127.0.0.1:8000/", help="Chainlit HTTP URL.")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also verify vector-store readiness and jurisdiction filtering.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    ok, detail = check_http(args.url, args.timeout)
    if not args.quiet:
        print(f"[{'OK ' if ok else 'FAIL'}] liveness: {detail}")
    if not ok:
        return 1

    if args.deep:
        ok, detail = check_readiness(args.timeout)
        if not args.quiet:
            print(f"[{'OK ' if ok else 'FAIL'}] readiness: {detail}")
        if not ok:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
