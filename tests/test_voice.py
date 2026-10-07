"""Tests for voice scoping.

Every test runs against local files only: the WAV fixtures are synthesised with the
standard library ``wave`` module and each provider test pins ``whisper_provider``
explicitly, so no transcription request can escape to the network. The cloud code path
is additionally guarded by a monkeypatched ``_transcribe_openai_compatible`` that fails
the test if it is ever invoked.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from sowsprint.config import Settings, VectorBackend
from sowsprint.voice import transcribe as transcribe_module
from sowsprint.voice.transcribe import (
    SUPPORTED_SUFFIXES,
    TranscriptionResult,
    probe_duration,
    transcribe_audio,
)

SIDECAR_TEXT = "We need a customer portal that integrates with Salesforce and supports GDPR audit exports."


def make_settings(**overrides) -> Settings:
    """Credential-free settings with every provider decision pinned."""
    base: dict = {
        "vector_backend": VectorBackend.MEMORY,
        "llm_provider": "mock",
        "whisper_provider": "offline",
        "max_audio_mb": 25.0,
        "dry_run_integrations": True,
        "anthropic_api_key": None,
        "openai_api_key": None,
        "groq_api_key": None,
        "cohere_api_key": None,
    }
    base.update(overrides)
    return Settings(**base)


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    """Write a 16-bit mono PCM WAV of the requested duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = round(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path


@pytest.fixture
def forbid_network(monkeypatch) -> list:
    """Record (and forbid) any attempt to reach a transcription provider."""
    calls: list[dict] = []

    def _explode(*args, **kwargs):  # pragma: no cover - only runs on failure
        calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("transcribe_audio attempted a network call")

    monkeypatch.setattr(transcribe_module, "_transcribe_openai_compatible", _explode)
    return calls


# ---------------------------------------------------------------------------------
# duration probing
# ---------------------------------------------------------------------------------


def test_probe_duration_reads_pcm_wav_header(tmp_path):
    path = write_wav(tmp_path / "brief.wav", seconds=1.0)

    assert probe_duration(path) == pytest.approx(1.0, abs=0.2)


def test_probe_duration_scales_with_wav_length(tmp_path):
    short = write_wav(tmp_path / "short.wav", seconds=0.5)
    long = write_wav(tmp_path / "long.wav", seconds=3.0)

    assert probe_duration(short) == pytest.approx(0.5, abs=0.2)
    assert probe_duration(long) == pytest.approx(3.0, abs=0.2)


def test_probe_duration_falls_back_to_size_for_compressed_audio(tmp_path):
    compressed = tmp_path / "brief.m4a"
    compressed.write_bytes(b"\x00" * 32_000)

    # Compressed containers expose no frame count, so size/bitrate is used.
    assert probe_duration(compressed) == pytest.approx(2.0, abs=0.01)


def test_probe_duration_never_raises_for_missing_or_corrupt_files(tmp_path):
    assert probe_duration(tmp_path / "absent.wav") == 0.0

    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"this is definitely not a wav file")
    # Falls back to the size estimate rather than raising.
    assert probe_duration(corrupt) >= 0.0


# ---------------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------------


def test_transcribe_audio_rejects_a_missing_file(tmp_path):
    result = transcribe_audio(
        tmp_path / "absent.wav", settings=make_settings(), session_id="voice-missing"
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert "not found" in result.error.lower()
    assert str(tmp_path / "absent.wav") in result.error
    assert result.text == ""


def test_transcribe_audio_rejects_an_unsupported_extension(tmp_path):
    disguised = tmp_path / "notes.xyz"
    disguised.write_text("not audio at all", encoding="utf-8")

    result = transcribe_audio(disguised, settings=make_settings(), session_id="voice-ext")

    assert result.ok is False
    assert "Unsupported" in result.error
    assert ".xyz" in result.error


def test_supported_suffixes_cover_the_mobile_recording_formats():
    assert {".wav", ".m4a", ".webm", ".ogg", ".mp3"} <= SUPPORTED_SUFFIXES
    assert ".txt" not in SUPPORTED_SUFFIXES


def test_transcribe_audio_rejects_files_over_the_size_limit(tmp_path):
    path = write_wav(tmp_path / "long.wav", seconds=1.0)
    size_mb = path.stat().st_size / (1024 * 1024)
    assert size_mb > 0.0001

    result = transcribe_audio(
        path,
        settings=make_settings(max_audio_mb=0.0001),
        session_id="voice-too-big",
    )

    assert result.ok is False
    assert "limit" in result.error
    assert "above the" in result.error
    assert f"{size_mb:.1f}" in result.error


# ---------------------------------------------------------------------------------
# offline degradation + sidecar fallback
# ---------------------------------------------------------------------------------


def test_transcribe_audio_uses_the_sidecar_transcript_offline(tmp_path):
    audio = write_wav(tmp_path / "brief.wav", seconds=1.0)
    (tmp_path / "brief.txt").write_text(f"\n{SIDECAR_TEXT}\n", encoding="utf-8")

    result = transcribe_audio(
        audio, settings=make_settings(whisper_provider="offline"), session_id="voice-sidecar"
    )

    assert result.ok is True
    assert result.from_sidecar is True
    assert result.text == SIDECAR_TEXT
    assert result.provider == "offline"
    assert result.model == "sidecar-transcript"
    assert result.cost_usd == 0.0
    assert result.error == ""
    assert result.duration_seconds == pytest.approx(1.0, abs=0.2)
    assert result.word_count == len(SIDECAR_TEXT.split())
    assert "sidecar" in result.summary()


def test_transcribe_audio_ignores_an_empty_sidecar(tmp_path):
    audio = write_wav(tmp_path / "brief.wav", seconds=1.0)
    (tmp_path / "brief.txt").write_text("   \n", encoding="utf-8")

    result = transcribe_audio(
        audio, settings=make_settings(whisper_provider="offline"), session_id="voice-empty-sidecar"
    )

    assert result.ok is False
    assert result.from_sidecar is False


def test_transcribe_audio_without_sidecar_explains_how_to_enable_voice(
    tmp_path, forbid_network
):
    audio = write_wav(tmp_path / "brief.wav", seconds=1.0)

    result = transcribe_audio(
        audio, settings=make_settings(whisper_provider="offline"), session_id="voice-offline"
    )

    assert result.ok is False
    assert result.from_sidecar is False
    assert result.provider == "offline"
    assert result.text == ""
    assert "Offline mode" in result.error
    assert "SOWSPRINT_OPENAI_API_KEY" in result.error
    assert "SOWSPRINT_GROQ_API_KEY" in result.error
    assert "API key" in result.error or "API_KEY" in result.error
    assert "Transcription unavailable" in result.summary()
    assert forbid_network == []


def test_transcribe_audio_with_openai_provider_and_no_key_makes_no_call(
    tmp_path, forbid_network
):
    audio = write_wav(tmp_path / "brief.wav", seconds=1.0)

    result = transcribe_audio(
        audio,
        settings=make_settings(whisper_provider="openai", openai_api_key=None),
        session_id="voice-openai-nokey",
    )

    assert result.ok is False
    assert result.provider == "openai"
    # The message must name the misconfigured provider and list every way to fix it,
    # including the self-hosted endpoint.
    assert "provider 'openai'" in result.error
    assert "SOWSPRINT_OPENAI_API_KEY" in result.error
    assert "SOWSPRINT_WHISPER_BASE_URL" in result.error
    assert forbid_network == [], "no transcription request may be issued without a key"


def test_transcribe_audio_with_groq_provider_and_no_key_makes_no_call(
    tmp_path, forbid_network
):
    audio = write_wav(tmp_path / "brief.wav", seconds=1.0)

    result = transcribe_audio(
        audio,
        settings=make_settings(whisper_provider="groq", groq_api_key=None),
        session_id="voice-groq-nokey",
    )

    assert result.ok is False
    assert result.provider == "groq"
    assert "provider 'groq'" in result.error
    assert "SOWSPRINT_GROQ_API_KEY" in result.error
    assert forbid_network == []


def test_transcribe_audio_result_summary_renders_for_success_and_failure():
    ok = TranscriptionResult(
        ok=True,
        text="one two three",
        provider="groq",
        model="whisper-large-v3-turbo",
        duration_seconds=12.5,
        cost_usd=0.000146,
    )
    assert "3 words" in ok.summary()
    assert "12.5s" in ok.summary()
    assert "groq/whisper-large-v3-turbo" in ok.summary()

    failed = TranscriptionResult(ok=False, provider="offline", error="nothing to transcribe")
    assert failed.summary() == "Transcription unavailable (offline): nothing to transcribe"
    assert failed.word_count == 0
