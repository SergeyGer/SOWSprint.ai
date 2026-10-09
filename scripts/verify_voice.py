#!/usr/bin/env python
"""Live verification harness for the voice-scoping path.

Streams synthesized audio to the **running Chainlit server** over the same socket.io
protocol the browser uses (``audio_start`` → ``audio_chunk``* → ``audio_end``) and
asserts what comes back.

Two modes, both meaningful:

* **offline** (no provider configured) — the server must report an actionable failure.
  The regression this guards against is a raw ``TypeError`` from the hook signature,
  which is what the feature did before this harness existed.
* **success** — with ``--expect-transcript``, a provider is assumed to be reachable
  (typically ``scripts/whisper_stub.py``) and the harness asserts the transcript
  reached Triage and produced a Statement of Work.

Usage::

    python scripts/verify_voice.py --url http://127.0.0.1:8000
    python scripts/verify_voice.py --url http://127.0.0.1:8000 --expect-transcript
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Raw PCM the Chainlit capture widget streams: 16-bit mono at the configured rate.
#: Synthesized silence is sufficient — the payload shape is what is under test, and a
#: real server would transcribe noise as noise regardless.
CAPTURE_SAMPLE_RATE = 24000
CAPTURE_SECONDS = 2.0


def synthesize_pcm(seconds: float = CAPTURE_SECONDS, rate: int = CAPTURE_SAMPLE_RATE) -> bytes:
    """Produce headerless 16-bit mono PCM, exactly as the widget does."""
    return b"\x00\x01" * int(rate * seconds)


def chunked(payload: bytes, chunks: int = 3) -> list[bytes]:
    """Split the stream the way the widget does: several incremental chunks."""
    size = max(1, len(payload) // chunks)
    return [payload[i : i + size] for i in range(0, len(payload), size)]


class Collector:
    """Merges ``new_message`` / ``update_message`` payloads by id."""

    MESSAGE_EVENTS = ("new_message", "update_message")

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._by_id: dict[str, dict] = {}
        self._order: list[str] = []

    def handler(self, event: str):
        def _record(data: Any) -> None:
            self.events.append((event, data))
            if event not in self.MESSAGE_EVENTS or not isinstance(data, dict):
                return
            message_id = data.get("id")
            if not message_id:
                return
            message_id = str(message_id)
            if message_id not in self._by_id:
                self._by_id[message_id] = dict(data)
                self._order.append(message_id)
            else:
                self._by_id[message_id].update(data)

        return _record

    def texts(self) -> list[str]:
        """Flatten every message.

        A Chainlit *step* carries its title in ``name`` and its detail in ``output``;
        a plain message carries its body in ``output``. Both fields must be included,
        or step titles such as "Scope parsed — COMPLETE" are invisible to assertions.
        """
        out: list[str] = []
        step_types = ("assistant_message", "run", "tool", "llm", "retrieval", "embedding", "error")
        for message_id in self._order:
            message = self._by_id[message_id]
            if message.get("type") not in step_types:
                continue
            for field in ("name", "output"):
                value = message.get(field)
                if value:
                    out.append(str(value))
        return out

    def joined(self) -> str:
        return "\n\n".join(self.texts())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the live voice-scoping path")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("SOWSPRINT_AUTH_API_KEY", ""),
        help="Machine-account key for an authenticated deployment (X-API-Key).",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--expect-transcript",
        action="store_true",
        help="Assert a provider is reachable and the transcript drives the pipeline.",
    )
    args = parser.parse_args(argv)

    import socketio

    collector = Collector()
    client = socketio.Client(logger=False, engineio_logger=False, reconnection=False)
    for event in ("new_message", "update_message", "task_start", "task_end", "audio_chunk"):
        client.on(event, collector.handler(event))

    session_id = str(uuid.uuid4())
    print(f"→ connecting to {args.url} (session {session_id[:8]})")
    client.connect(
        args.url,
        auth={"sessionId": session_id, "clientType": "web", "userEnv": "{}", "threadId": None},
        socketio_path="/ws/socket.io",
        transports=["websocket", "polling"],
    )
    client.emit("connection_successful")
    _wait_for(lambda: "SOWSprint" in collector.joined(), 45, label="on_chat_start")
    print("✓ chat started")

    pcm = synthesize_pcm()
    parts = chunked(pcm)
    print(
        f"→ streaming {len(pcm):,} bytes of headerless PCM "
        f"({CAPTURE_SECONDS:.0f}s @ {CAPTURE_SAMPLE_RATE} Hz) in {len(parts)} chunk(s)"
    )

    client.emit("audio_start")
    time.sleep(0.8)
    for index, part in enumerate(parts):
        client.emit(
            "audio_chunk",
            {
                "isStart": index == 0,
                "mimeType": "audio/wav",
                "elapsedTime": CAPTURE_SECONDS * index / len(parts),
                "data": part,
            },
        )
        time.sleep(0.25)
    client.emit("audio_end")
    print("→ audio_end sent")

    if args.expect_transcript:
        # The pipeline stops at the approval gate unless auto-deploy is on; a complete
        # brief reaches that gate, which is the milestone worth asserting.
        _wait_for(
            lambda: "approval required" in collector.joined().lower()
            or "run finished" in collector.joined().lower(),
            args.timeout,
            "pipeline",
        )
        if "approval required" in collector.joined().lower():
            print("→ approval gate reached; approving")
            client.emit(
                "client_message",
                {
                    "message": {
                        "id": str(uuid.uuid4()),
                        "name": "User",
                        "type": "user_message",
                        "output": "approve",
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    "fileReferences": [],
                },
            )
            _wait_for(
                lambda: "run finished" in collector.joined().lower(),
                args.timeout,
                "provisioning",
            )
    else:
        _wait_for(
            lambda: "transcription unavailable" in collector.joined().lower(),
            args.timeout,
            "transcription verdict",
        )

    text = collector.joined()
    lowered = text.lower()

    checks: list[tuple[str, bool]] = [
        ("audio widget acknowledged the stream", "listening" in lowered),
        ("capture size reported back to the user", "kb" in lowered and "captured" in lowered),
        # The regression this harness exists for: the hook used to raise TypeError.
        ("no TypeError from the audio hook", "missing 1 required positional argument" not in lowered),
        ("no unhandled hook exception", "on_audio_end()" not in lowered),
    ]

    if args.expect_transcript:
        checks += [
            ("transcript produced", "transcript" in lowered),
            ("transcript reached Triage", "scope parsed" in lowered),
            ("statement of work drafted", "statement of work" in lowered),
            ("retrieval used the jurisdiction filter", "jurisdiction filter" in lowered),
        ]
    else:
        checks += [
            (
                "failure is actionable, not silent",
                "enable voice scoping" in lowered or "speech-to-text provider" in lowered,
            ),
            ("nothing was scoped from missing audio", "scope parsed" not in lowered),
        ]

    print("\n" + "=" * 78)
    print("VOICE PATH VERIFICATION")
    print("=" * 78)
    failed = 0
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")
        failed += 0 if ok else 1

    if failed:
        print("\n--- transcript tail ---")
        print(text[-2500:])

    client.disconnect()
    print("\n" + ("✅ ALL VOICE CHECKS PASSED" if failed == 0 else f"❌ {failed} CHECK(S) FAILED"))
    return 0 if failed == 0 else 1


def _wait_for(predicate, timeout: float, label: str = "condition") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    print(f"  ⏱ timeout waiting for {label} after {timeout:.0f}s")
    return False


if __name__ == "__main__":
    raise SystemExit(main())
