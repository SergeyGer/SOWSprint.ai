"""Voice scoping: native audio capture and speech-to-text."""

from __future__ import annotations

from .transcribe import (
    SUPPORTED_SUFFIXES,
    TranscriptionResult,
    probe_duration,
    transcribe_audio,
)

__all__ = [
    "SUPPORTED_SUFFIXES",
    "TranscriptionResult",
    "probe_duration",
    "transcribe_audio",
]
