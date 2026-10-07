"""Tests for the browser audio-capture contract.

These cover the boundary between the Chainlit capture widget and the transcription
layer — the place where a headerless PCM stream has to become a file that a
speech-to-text API will actually accept.

The bug this suite exists to prevent: the widget streams *bare samples*, and writing
them to ``recording.wav`` without a RIFF header produces a file that every provider
rejects with an opaque 400. That failure is invisible to tests which only exercise
:func:`transcribe_audio` with a well-formed input file.
"""

from __future__ import annotations

import io
import wave

import pytest

from sowsprint.config import Settings
from sowsprint.voice.transcribe import (
    build_audio_file,
    probe_duration,
    wrap_pcm_as_wav,
)

SAMPLE_RATE = 24000


def make_pcm(seconds: float = 1.0, *, rate: int = SAMPLE_RATE) -> bytes:
    """Synthesize 16-bit mono PCM, exactly as the capture widget streams it."""
    return b"\x10\x20" * int(rate * seconds)


def make_container(fmt: str = "wav") -> bytes:
    """Build a real self-describing container for pass-through tests."""
    buffer = io.BytesIO()
    if fmt == "wav":
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(make_pcm(0.25))
        return buffer.getvalue()
    raise ValueError(fmt)


class TestPcmWrapping:
    def test_produces_a_valid_riff_wave_container(self) -> None:
        wrapped = wrap_pcm_as_wav(make_pcm(1.0))
        assert wrapped[:4] == b"RIFF"
        assert wrapped[8:12] == b"WAVE"
        # 44-byte canonical header plus the payload.
        assert len(wrapped) == 44 + len(make_pcm(1.0))

    def test_header_declares_the_right_format(self) -> None:
        wrapped = wrap_pcm_as_wav(make_pcm(1.0), sample_rate=SAMPLE_RATE)
        with wave.open(io.BytesIO(wrapped), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == SAMPLE_RATE
            assert handle.getnframes() == SAMPLE_RATE

    def test_duration_is_recoverable_from_the_wrapped_bytes(self, tmp_path) -> None:
        """The whole point of the header: downstream code can measure the audio."""
        wrapped = wrap_pcm_as_wav(make_pcm(2.0))
        path = tmp_path / "clip.wav"
        path.write_bytes(wrapped)
        assert probe_duration(path) == pytest.approx(2.0, abs=0.05)

    def test_sample_rate_is_honoured(self) -> None:
        wrapped = wrap_pcm_as_wav(make_pcm(1.0, rate=16000), sample_rate=16000)
        path = io.BytesIO(wrapped)
        with wave.open(path, "rb") as handle:
            assert handle.getframerate() == 16000


class TestBuildAudioFile:
    def test_raw_pcm_is_wrapped_and_named_wav(self) -> None:
        payload, suffix = build_audio_file([make_pcm(0.5)], mime_type="audio/wav")
        assert suffix == ".wav"
        assert payload[:4] == b"RIFF"

    def test_multiple_chunks_are_concatenated_in_order(self) -> None:
        first, second = make_pcm(0.5), make_pcm(0.5)
        payload, _ = build_audio_file([first, second], mime_type="audio/wav")
        with wave.open(io.BytesIO(payload), "rb") as handle:
            assert handle.getnframes() == SAMPLE_RATE  # 0.5s + 0.5s

    def test_existing_wav_container_passes_through_untouched(self) -> None:
        original = make_container("wav")
        payload, suffix = build_audio_file([original], mime_type="audio/wav")
        assert suffix == ".wav"
        assert payload == original

    @pytest.mark.parametrize(
        ("magic", "expected_suffix"),
        [
            (b"OggS", ".ogg"),
            (b"\x1a\x45\xdf\xa3", ".webm"),
            (b"ID3", ".mp3"),
            (b"fLaC", ".flac"),
        ],
    )
    def test_compressed_containers_are_detected_and_preserved(
        self, magic: bytes, expected_suffix: str
    ) -> None:
        """Safari sends .m4a and Chrome sends .webm; neither may be re-wrapped."""
        blob = magic + b"\x00" * 128
        payload, suffix = build_audio_file([blob], mime_type="audio/webm")
        assert suffix == expected_suffix
        assert payload == blob

    def test_empty_input_yields_nothing(self) -> None:
        assert build_audio_file([], mime_type="audio/wav") == (b"", ".wav")
        assert build_audio_file([b""], mime_type="audio/wav") == (b"", ".wav")

    def test_unknown_container_is_treated_as_pcm(self) -> None:
        """Better a wrapped guess than a 400 from the provider."""
        payload, suffix = build_audio_file([make_pcm(0.25)], mime_type="application/octet-stream")
        assert suffix == ".wav"
        assert payload[:4] == b"RIFF"

    def test_result_is_accepted_by_transcribe_audio(self, tmp_path) -> None:
        """End-to-end within the module: wrapped PCM survives validation."""
        from sowsprint.voice.transcribe import transcribe_audio

        payload, suffix = build_audio_file([make_pcm(1.0)], mime_type="audio/wav")
        path = tmp_path / f"capture{suffix}"
        path.write_bytes(payload)

        settings = Settings(
            whisper_provider="offline",
            openai_api_key=None,
            groq_api_key=None,
            whisper_base_url=None,
        )
        result = transcribe_audio(path, settings=settings)
        # No provider is configured, so it must fail on *provider selection* — not on
        # a malformed file.
        assert result.ok is False
        assert "Unsupported audio format" not in result.error
        assert result.duration_seconds == pytest.approx(1.0, abs=0.1)


class TestProviderResolution:
    def test_self_hosted_endpoint_wins_over_cloud_keys(self) -> None:
        """An operator who stood up local Whisper did so for a reason."""
        settings = Settings(
            whisper_provider="auto",
            whisper_base_url="http://whisper:8000/v1",
            openai_api_key="sk-test",
            groq_api_key="gsk-test",
        )
        assert settings.resolved_whisper_provider == "local"

    def test_cloud_keys_used_when_no_local_endpoint(self) -> None:
        assert (
            Settings(whisper_provider="auto", groq_api_key="gsk", openai_api_key="sk").resolved_whisper_provider
            == "groq"
        )
        assert (
            Settings(whisper_provider="auto", groq_api_key=None, openai_api_key="sk").resolved_whisper_provider
            == "openai"
        )

    def test_offline_when_nothing_is_configured(self) -> None:
        settings = Settings(
            whisper_provider="auto",
            whisper_base_url=None,
            openai_api_key=None,
            groq_api_key=None,
        )
        assert settings.resolved_whisper_provider == "offline"

    def test_explicit_provider_overrides_auto(self) -> None:
        settings = Settings(
            whisper_provider="openai", whisper_base_url="http://whisper:8000/v1"
        )
        assert settings.resolved_whisper_provider == "openai"

    def test_capability_matrix_reports_the_local_endpoint(self) -> None:
        settings = Settings(
            whisper_provider="auto", whisper_base_url="http://whisper:8000/v1"
        )
        assert settings.capability_matrix()["transcription"] == "local"


class TestLocalProviderTransport:
    def test_local_provider_posts_to_the_configured_endpoint(self, tmp_path, monkeypatch) -> None:
        """The self-hosted path must speak the OpenAI transcription contract."""
        from sowsprint.voice import transcribe as module

        captured: dict[str, object] = {}

        class FakeResponse:
            text = "We need a GDPR compliant claims portal for our European brokers."
            language = "en"

        class FakeTranscriptions:
            def create(self, **kwargs):
                captured.update(kwargs)
                captured["file_name"] = getattr(kwargs.get("file"), "name", "")
                return FakeResponse()

        class FakeAudio:
            transcriptions = FakeTranscriptions()

        class FakeClient:
            audio = FakeAudio()

            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs

        monkeypatch.setattr("openai.OpenAI", FakeClient)

        path = tmp_path / "capture.wav"
        path.write_bytes(make_container("wav"))

        settings = Settings(
            whisper_provider="local",
            whisper_base_url="http://whisper:8000/v1",
            whisper_api_key=None,
            whisper_model="Systran/faster-whisper-small",
        )
        result = module.transcribe_audio(path, settings=settings)

        assert result.ok is True
        assert result.provider == "local"
        assert result.model == "Systran/faster-whisper-small"
        assert "claims portal" in result.text
        # Self-hosted inference is compute the operator already pays for.
        assert result.cost_usd == 0.0

        client_kwargs = captured["client_kwargs"]
        assert client_kwargs["base_url"] == "http://whisper:8000/v1"  # type: ignore[index]
        assert captured["model"] == "Systran/faster-whisper-small"

    def test_local_provider_does_not_send_audio_to_a_cloud_host(self, tmp_path, monkeypatch) -> None:
        """Data-residency guarantee: the local path must never hit api.openai.com."""
        from sowsprint.voice import transcribe as module

        seen: list[object] = []

        class ExplodingClient:
            def __init__(self, **kwargs):
                seen.append(kwargs.get("base_url"))
                raise AssertionError("cloud client must not be constructed")

        monkeypatch.setattr("openai.OpenAI", ExplodingClient)

        path = tmp_path / "capture.wav"
        path.write_bytes(make_container("wav"))

        settings = Settings(
            whisper_provider="local",
            whisper_base_url="http://127.0.0.1:9/v1",  # unreachable on purpose
            openai_api_key="sk-should-not-be-used",
        )
        result = module.transcribe_audio(path, settings=settings)

        # It fails (nothing is listening), but it fails against the local endpoint.
        assert result.ok is False
        assert all(url != "https://api.openai.com/v1" for url in seen if url)
