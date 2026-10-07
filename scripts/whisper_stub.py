#!/usr/bin/env python
"""OpenAI-compatible speech-to-text test double.

Stands in for a self-hosted Whisper server (faster-whisper-server, whisper.cpp
``server``, Speaches, vLLM) so the voice-scoping path can be exercised end to end
without credentials, a GPU, or a model download.

It is deliberately **not** a yes-man. Before answering it verifies that the upload is a
well-formed audio container and reports a 400 otherwise — which is exactly how a real
Whisper server behaves when handed headerless PCM. That makes this stub a regression
test for the capture pipeline rather than a rubber stamp.

Usage::

    python scripts/whisper_stub.py --port 9000
    # then, in another shell:
    SOWSPRINT_WHISPER_BASE_URL=http://127.0.0.1:9000/v1 chainlit run app.py

Endpoints:
    POST /v1/audio/transcriptions   multipart upload -> {"text": ..., "language": ...}
    GET  /health                    readiness probe
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Returned when the request carries no usable audio. Overridable per request via the
#: ``prompt`` form field, which lets a test drive a specific brief through the graph.
#:
#: Deliberately a *complete* brief — deliverables, business goal, timeline, users,
#: integrations, success metric and budget all present — so the transcript exercises
#: the whole graph rather than stopping at the clarification gate. A caller wanting
#: to test the clarification loop should pass its own `prompt`.
DEFAULT_TRANSCRIPT = (
    "We need a GDPR compliant claims portal for our European brokers. "
    "Build a React front end and a Python FastAPI service with PostgreSQL. "
    "Integrate with Salesforce for policy data. "
    "Our goal is to cut claim processing time for the business. "
    "The primary users are 300 claims handlers across four regional offices. "
    "Success means 40 percent faster claim resolution. "
    "Deliver within 10 weeks for a budget of EUR 90k."
)

_BOUNDARY_RE = re.compile(rb"boundary=([^;]+)")


def _parse_multipart(body: bytes, boundary: bytes) -> dict[str, bytes]:
    """Minimal multipart/form-data parser (stdlib only, no framework)."""
    parts: dict[str, bytes] = {}
    for chunk in body.split(b"--" + boundary):
        if not chunk.strip() or chunk.strip() == b"--":
            continue
        head, _, content = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        match = re.search(rb'name="([^"]+)"', head)
        if not match:
            continue
        parts[match.group(1).decode()] = content.rstrip(b"\r\n-")
    return parts


def _validate_audio(payload: bytes) -> str | None:
    """Return an error message when the upload is not a usable audio container."""
    if len(payload) < 44:
        return f"audio too short: {len(payload)} bytes"

    if payload[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(payload), "rb") as handle:
                if handle.getnframes() <= 0:
                    return "WAV container declares zero frames"
                _ = (handle.getnchannels(), handle.getframerate(), handle.getsampwidth())
            return None
        except wave.Error as exc:
            return f"malformed WAV container: {exc}"

    # Compressed containers a real server would hand to ffmpeg.
    for magic in (b"OggS", b"\x1a\x45\xdf\xa3", b"ID3", b"\xff\xfb", b"fLaC"):
        if payload.startswith(magic):
            return None

    return (
        "could not decode audio: no recognised container header "
        "(is this headerless PCM uploaded as .wav?)"
    )


class WhisperStubHandler(BaseHTTPRequestHandler):
    """Request handler implementing the OpenAI transcription contract."""

    server_version = "WhisperStub/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep the console readable when the container runs this alongside the app.
        sys.stderr.write(f"[whisper-stub] {fmt % args}\n")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/health", "/v1/models"):
            self._json(200, {"status": "ok", "object": "list", "data": [{"id": "whisper-stub"}]})
            return
        self._json(404, {"error": {"message": f"unknown path {self.path}"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/audio/transcriptions"):
            self._json(404, {"error": {"message": f"unknown path {self.path}"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        boundary_match = _BOUNDARY_RE.search(self.headers.get("Content-Type", "").encode())
        if not boundary_match:
            self._json(400, {"error": {"message": "expected multipart/form-data"}})
            return

        parts = _parse_multipart(body, boundary_match.group(1))
        uploaded = parts.get("file", b"")
        if not uploaded:
            self._json(400, {"error": {"message": "no 'file' part in the request"}})
            return

        problem = _validate_audio(uploaded)
        if problem:
            # Mirrors how a real server rejects a bad upload — this is the failure the
            # capture pipeline must never trigger.
            self._json(400, {"error": {"message": problem, "type": "invalid_request_error"}})
            return

        transcript = (parts.get("prompt") or DEFAULT_TRANSCRIPT.encode()).decode("utf-8")
        self._json(
            200,
            {
                "text": transcript,
                "language": "en",
                "duration": 4.2,
                "model": parts.get("model", b"whisper-stub").decode("utf-8"),
            },
        )


def serve(port: int, host: str = "0.0.0.0") -> None:
    httpd = ThreadingHTTPServer((host, port), WhisperStubHandler)
    print(f"[whisper-stub] listening on http://{host}:{port}/v1/audio/transcriptions")
    print(f"[whisper-stub] health: http://{host}:{port}/health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("[whisper-stub] stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAI-compatible Whisper test double")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Validate the audio validator against known-good and known-bad payloads.",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        good = io.BytesIO()
        with wave.open(good, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x00\x01" * 2400)
        checks = [
            ("valid WAV", _validate_audio(good.getvalue()), True),
            ("headerless PCM", _validate_audio(b"\x00\x01" * 24000), False),
            ("truncated", _validate_audio(b"RIFF"), False),
            ("ogg", _validate_audio(b"OggS" + b"\x00" * 200), True),
        ]
        failed = 0
        for label, result, should_pass in checks:
            ok = (result is None) is should_pass
            failed += 0 if ok else 1
            print(f"  [{'OK ' if ok else 'FAIL'}] {label}: {result or 'accepted'}")
        print("SELFTEST PASSED" if not failed else f"SELFTEST FAILED ({failed})")
        return 0 if failed else 1

    serve(args.port, args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
