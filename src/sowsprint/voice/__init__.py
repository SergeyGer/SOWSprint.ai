"""Voice scoping: native audio capture and speech-to-text."""

from __future__ import annotations

from .transcribe import (
    SUPPORTED_SUFFIXES,
    TranscriptionResult,
    build_audio_file,
    probe_duration,
    transcribe_audio,
    wrap_pcm_as_wav,
)

__all__ = [
    "SUPPORTED_SUFFIXES",
    "TranscriptionResult",
    "build_audio_file",
    "probe_duration",
    "transcribe_audio",
    "wrap_pcm_as_wav",
]
