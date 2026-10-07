"""Voice scoping: audio capture → transcription → requirement text.

Mobile browsers record in formats the Whisper family accepts directly (``.m4a`` on
iOS Safari, ``.webm``/``.ogg`` on Chrome and Android), so the pipeline uploads the
captured file as-is rather than shelling out to ffmpeg — which keeps the container slim
and removes a whole class of codec failures.

Provider selection mirrors the rest of the platform:

* **Groq** — ``whisper-large-v3-turbo``, the lowest-latency option and the one that
  makes voice scoping feel instant on a phone.
* **OpenAI** — ``whisper-1``, the default when only an OpenAI key is present.
* **offline** — no credentials. Real ASR is impossible locally without shipping a model,
  so the pipeline degrades *honestly*: it looks for a sidecar transcript, and otherwise
  returns a structured failure the UI renders with instructions instead of pretending to
  have heard something.
"""

from __future__ import annotations

import contextlib
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

    # ---------------------------------------------------------------- cloud ASR
    if provider in ("openai", "groq"):
        model = (
            resolved.groq_whisper_model if provider == "groq" else resolved.whisper_model
        )
        api_key = resolved.groq_api_key if provider == "groq" else resolved.openai_api_key
        base_url = "https://api.groq.com/openai/v1" if provider == "groq" else resolved.openai_base_url

        if api_key:
            try:
                text, language = _transcribe_openai_compatible(
                    path,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=resolved.request_timeout_s,
                )
                cost = transcription_cost(model, duration)
                with contextlib.suppress(Exception):
                    tracker.get_ledger(session_id).record_transcription(model, duration, node=node)
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
            fallback_reason = f"No API key configured for provider '{provider}'."
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
            f"{fallback_reason} Set SOWSPRINT_OPENAI_API_KEY or SOWSPRINT_GROQ_API_KEY "
            f"to enable voice scoping, or type the requirement instead."
        ),
    )
