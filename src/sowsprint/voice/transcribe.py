"""Voice scoping: audio capture → transcription → requirement text.

Two input shapes reach this module and both must be handled:

* **An uploaded recording** (``.m4a`` from iOS Safari, ``.webm``/``.ogg`` from Chrome and
  Android). These are self-describing containers and are uploaded as-is, which keeps
  ffmpeg out of the image and removes a whole class of codec failures.
* **The native capture widget**, which streams *headerless 16-bit mono PCM* over the
  socket at the configured sample rate. Raw PCM with a ``.wav`` extension is rejected by
  every transcription API, so :func:`build_audio_file` wraps it in a proper WAV
  container before it leaves the process.

Provider selection mirrors the rest of the platform:

* **self-hosted** (``whisper_base_url``) — any OpenAI-compatible speech-to-text server
  (faster-whisper-server, whisper.cpp ``server``, vLLM, Speaches). Audio never leaves
  the network, which matters when the requirement being dictated is itself confidential.
* **Groq** — ``whisper-large-v3-turbo``, the lowest-latency hosted option and the one
  that makes voice scoping feel instant on a phone.
* **OpenAI** — ``whisper-1``, the default when only an OpenAI key is present.
* **offline** — no provider configured. Real ASR is impossible locally without shipping
  a model, so the pipeline degrades *honestly*: it looks for a sidecar transcript and
  otherwise returns a structured failure the UI renders with instructions, instead of
  pretending to have heard something.
"""

from __future__ import annotations

import contextlib
import io
import wave
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from ..observability.logging import get_logger
from ..telemetry import tracker
from ..telemetry.pricing import transcription_cost

log = get_logger(__name__)

#: Rough bitrate assumption (bytes/second) for compressed audio when the container
#: format does not expose a duration header.
_ASSUMED_BYTES_PER_SECOND = 16_000

SUPPORTED_SUFFIXES = frozenset(
    {".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".wav", ".webm", ".ogg", ".oga", ".flac"}
)

#: Container magic numbers, used to detect what the browser actually sent.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"RIFF", ".wav"),
    (b"OggS", ".ogg"),
    (b"\x1a\x45\xdf\xa3", ".webm"),  # EBML
    (b"ID3", ".mp3"),
    (b"\xff\xfb", ".mp3"),
    (b"fLaC", ".flac"),
)

#: Shape of the PCM produced by the Chainlit capture widget.
_PCM_SAMPLE_WIDTH = 2
_PCM_CHANNELS = 1


@dataclass
class TranscriptionResult:
    """Outcome of one speech-to-text attempt."""

    ok: bool
    text: str = ""
    provider: str = "offline"
    model: str = ""
    duration_seconds: float = 0.0
    cost_usd: float = 0.0
    language: str | None = None
    error: str = ""
    from_sidecar: bool = False

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def summary(self) -> str:
        if not self.ok:
            return f"Transcription unavailable ({self.provider}): {self.error}"
        source = "sidecar transcript" if self.from_sidecar else self.model
        return (
            f"{self.word_count} words from {self.duration_seconds:.1f}s of audio "
            f"via {self.provider}/{source} · ${self.cost_usd:.5f}"
        )


def wrap_pcm_as_wav(
    pcm: bytes,
    *,
    sample_rate: int = 24000,
    sample_width: int = _PCM_SAMPLE_WIDTH,
    channels: int = _PCM_CHANNELS,
) -> bytes:
    """Wrap headerless PCM in a RIFF/WAVE container.

    The Chainlit capture widget streams bare samples; every transcription API expects a
    self-describing container. Writing the 44-byte WAV header here is the difference
    between a working microphone button and a 400 from the provider.
    """
    buffer = io.BytesIO()
    with contextlib.closing(wave.open(buffer, "wb")) as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def build_audio_file(
    chunks: list[bytes],
    *,
    mime_type: str = "",
    sample_rate: int = 24000,
) -> tuple[bytes, str]:
    """Assemble captured chunks into a self-describing audio file.

    Returns ``(payload, suffix)``. A recognised container is passed through untouched;
    anything else is treated as raw PCM from the capture widget and wrapped in WAV.
    """
    payload = b"".join(chunks)
    if not payload:
        return b"", ".wav"

    for magic, suffix in _MAGIC:
        if payload.startswith(magic):
            return payload, suffix

    # A mime type claiming a container we did not detect means the stream is truncated
    # or the widget changed format. Raw PCM is the only remaining interpretation.
    if mime_type and not mime_type.endswith(("wav", "x-wav", "wave")):
        log.warning("voice.unrecognised_container", mime_type=mime_type, bytes=len(payload))

    return wrap_pcm_as_wav(payload, sample_rate=sample_rate), ".wav"


def probe_duration(path: Path) -> float:
    """Best-effort audio duration in seconds.

    PCM WAV exposes an exact frame count; compressed formats generally do not, so the
    file size is used as a fallback. The value only feeds cost accounting and a UI
    label, so an approximation is acceptable — but it must never raise.
    """
    try:
        if path.suffix.lower() == ".wav":
            with contextlib.closing(wave.open(str(path), "rb")) as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or 1
                return frames / float(rate)
    except Exception:
        pass

    try:
        return max(0.0, path.stat().st_size / _ASSUMED_BYTES_PER_SECOND)
    except OSError:
        return 0.0


def _sidecar_transcript(path: Path) -> str | None:
    """Return a sibling ``.txt`` transcript, if one exists.

    Enables deterministic tests and offline demos: drop ``brief.m4a`` and
    ``brief.txt`` next to each other and the offline pipeline behaves as if ASR ran.
    """
    candidate = path.with_suffix(".txt")
    if candidate.is_file():
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            return text or None
        except OSError:
            return None
    return None


def _transcribe_openai_compatible(
    path: Path, *, api_key: str, base_url: str | None, model: str, timeout: float
) -> tuple[str, str | None]:
    """Call an OpenAI-compatible ``/audio/transcriptions`` endpoint."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)
    with path.open("rb") as handle:
        response = client.audio.transcriptions.create(
            model=model,
            file=handle,
            response_format="verbose_json",
        )
    text = getattr(response, "text", "") or ""
    language = getattr(response, "language", None)
    return text, language


def transcribe_audio(
    audio_path: str | Path,
    *,
    settings: Settings | None = None,
    session_id: str = "voice",
    node: str = "voice",
) -> TranscriptionResult:
    """Transcribe a captured audio file, degrading gracefully at every step."""
    resolved = settings or get_settings()
    path = Path(audio_path)

    if not path.is_file():
        return TranscriptionResult(ok=False, error=f"Audio file not found: {path}")

    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return TranscriptionResult(
            ok=False,
            error=(
                f"Unsupported audio format '{path.suffix}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
            ),
        )

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > resolved.max_audio_mb:
        return TranscriptionResult(
            ok=False,
            error=f"Audio is {size_mb:.1f} MB, above the {resolved.max_audio_mb:.0f} MB limit.",
        )

    duration = probe_duration(path)
    provider = resolved.resolved_whisper_provider

    # ---------------------------------------------------------------- speech-to-text
    # One OpenAI-compatible contract covers hosted and self-hosted servers alike; only
    # the base URL, credential and price model differ.
    if provider in ("openai", "groq", "local"):
        fallback_reason = ""
        if provider == "groq":
            model = resolved.groq_whisper_model
            api_key = resolved.groq_api_key
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "local":
            model = resolved.whisper_model
            # Many self-hosted servers ignore auth entirely; the SDK just needs a
            # non-empty string.
            api_key = resolved.whisper_api_key or "not-required"
            base_url = resolved.whisper_base_url
        else:
            model = resolved.whisper_model
            api_key = resolved.openai_api_key
            base_url = resolved.openai_base_url

        # `api_key` is the single gate: the cloud providers supply it from their own
        # key, and the local provider defaults it to a placeholder because a
        # self-hosted server typically ignores the Authorization header.
        if api_key:
            try:
                text, language = _transcribe_openai_compatible(
                    path,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=resolved.request_timeout_s,
                )
                # Self-hosted inference has no per-minute list price: the cost is
                # compute the operator already paid for.
                cost = 0.0 if provider == "local" else transcription_cost(model, duration)
                if cost:
                    with contextlib.suppress(Exception):
                        tracker.get_ledger(session_id).record_transcription(
                            model, duration, node=node
                        )
                log.info(
                    "voice.transcribed",
                    provider=provider,
                    model=model,
                    seconds=round(duration, 1),
                    words=len(text.split()),
                    cost_usd=round(cost, 6),
                )
                return TranscriptionResult(
                    ok=bool(text.strip()),
                    text=text.strip(),
                    provider=provider,
                    model=model,
                    duration_seconds=duration,
                    cost_usd=cost,
                    language=language,
                    error="" if text.strip() else "Provider returned an empty transcript.",
                )
            except Exception as exc:
                log.warning("voice.transcription_failed", provider=provider, error=str(exc))
                fallback_reason = f"{type(exc).__name__}: {exc}"
        else:
            fallback_reason = f"No endpoint or API key configured for provider '{provider}'."
    else:
        fallback_reason = "Offline mode: no speech-to-text provider is configured."

    # ---------------------------------------------------------------- offline
    sidecar = _sidecar_transcript(path)
    if sidecar:
        log.info("voice.sidecar_used", path=str(path), words=len(sidecar.split()))
        return TranscriptionResult(
            ok=True,
            text=sidecar,
            provider="offline",
            model="sidecar-transcript",
            duration_seconds=duration,
            cost_usd=0.0,
            from_sidecar=True,
        )

    return TranscriptionResult(
        ok=False,
        provider=provider,
        duration_seconds=duration,
        error=(
            f"{fallback_reason} Set SOWSPRINT_WHISPER_BASE_URL (self-hosted), "
            f"SOWSPRINT_GROQ_API_KEY or SOWSPRINT_OPENAI_API_KEY to enable voice scoping, "
            f"or type the requirement instead."
        ),
    )
