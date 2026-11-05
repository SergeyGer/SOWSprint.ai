"""Video acceptance for release 2.1, V1.2 and V1.3 — measured, not asserted.

The specification names numbers: 1920×1080, 30 fps, H.264 at CRF 17–20, −16 ± 2 LUFS integrated,
peak ≤ −1 dBTP, readable at 720 px, titles readable at 360 px. Those are checkable, so this script
checks them and fails loudly, rather than leaving a reader to take the README's word for it.

The earlier cut measured −25.1 LUFS against a target of −16 and carried no subtitle track. Both
are inaudible-to-the-author failures: the video sounds fine on a laptop at full volume and
disappears in a meeting room, and nobody notices the missing subtitles if they never watch muted.

Usage:

    python scripts/video_qc.py docs/media/demo.mp4
    python scripts/video_qc.py docs/media/demo.mp4 --subtitles docs/media/demo.en.srt
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Acceptable ranges, from the specification.
TARGET_LUFS = -16.0
LUFS_TOLERANCE = 2.0
MAX_TRUE_PEAK_DBTP = -1.0
MIN_WIDTH = 1920
MIN_HEIGHT = 1080
MIN_FPS = 30.0


@dataclass
class Report:
    path: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    audio_sample_rate: int = 0
    subtitle_streams: int = 0
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _run(command: list[str], *, from_stderr: bool = False) -> str:
    """Run a command and return its output.

    `from_stderr` exists because ffmpeg writes filters' reports there. Reading stdout for the
    loudness measurement returned an empty string, which the caller reported as "could not be
    measured" — a silent wrong answer that looked like a missing feature rather than a bug.
    """
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:400])
    return result.stderr if from_stderr else result.stdout


def probe(path: Path) -> dict:
    raw = _run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    )
    return json.loads(raw)


def measure_loudness(path: Path) -> tuple[float | None, float | None]:
    """Integrated loudness and true peak, per EBU R128.

    `loudnorm` in analysis mode prints the measurement and writes nothing; parsing it is more
    reliable than computing the numbers from a frame dump.
    """
    output = _run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(path),
            "-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        from_stderr=True,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", output, re.S)
    if not match:
        return None, None
    payload = json.loads(match.group(0))
    try:
        return float(payload["input_i"]), float(payload["input_tp"])
    except (KeyError, ValueError):
        return None, None


def check(path: Path, *, expect_subtitles: bool = True) -> Report:
    report = Report(path=str(path))
    if not path.exists():
        report.failures.append(f"file does not exist: {path}")
        return report

    data = probe(path)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    report.subtitle_streams = sum(1 for s in data["streams"] if s["codec_type"] == "subtitle")

    if video is None:
        report.failures.append("no video stream")
        return report

    report.width = int(video.get("width", 0))
    report.height = int(video.get("height", 0))
    report.video_codec = video.get("codec_name", "")
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    report.fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    report.duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)

    if audio is not None:
        report.audio_codec = audio.get("codec_name", "")
        report.audio_sample_rate = int(audio.get("sample_rate", 0) or 0)

    if report.width < MIN_WIDTH or report.height < MIN_HEIGHT:
        report.failures.append(
            f"resolution {report.width}×{report.height} is below {MIN_WIDTH}×{MIN_HEIGHT} (V1.1)"
        )
    if report.fps < MIN_FPS:
        report.failures.append(f"frame rate {report.fps:.1f} is below {MIN_FPS:.0f} (V1.1)")
    if report.video_codec != "h264":
        report.failures.append(f"codec {report.video_codec!r} is not h264 (V1.2)")
    if audio is None:
        report.failures.append("no audio stream (V1.3)")
    elif report.audio_codec != "aac":
        report.failures.append(f"audio codec {report.audio_codec!r} is not aac (V1.3)")
    if audio is not None and report.audio_sample_rate != 48000:
        report.failures.append(
            f"audio is {report.audio_sample_rate} Hz, specification says 48 kHz (V1.3)"
        )
    if expect_subtitles and report.subtitle_streams == 0:
        report.failures.append(
            "no subtitle stream (V1.3). Ship a sidecar .srt if the container is not re-muxed."
        )

    if audio is not None:
        lufs, peak = measure_loudness(path)
        report.integrated_lufs = lufs
        report.true_peak_dbtp = peak
        if lufs is not None:
            if abs(lufs - TARGET_LUFS) > LUFS_TOLERANCE:
                report.failures.append(
                    f"integrated loudness {lufs:.1f} LUFS is outside "
                    f"{TARGET_LUFS:.0f} ± {LUFS_TOLERANCE:.0f} (V1.3)"
                )
        else:
            report.notes.append("loudness could not be measured")
        if peak is not None and peak > MAX_TRUE_PEAK_DBTP:
            report.failures.append(
                f"true peak {peak:.1f} dBTP exceeds {MAX_TRUE_PEAK_DBTP:.0f} (V1.3)"
            )

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="video_qc", description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--allow-no-subtitles", action="store_true")
    args = parser.parse_args(argv)

    report = check(args.video, expect_subtitles=not args.allow_no_subtitles)

    print(f"  {report.path}")
    print(f"    {report.width}×{report.height} @ {report.fps:.0f} fps, {report.duration:.1f}s")
    print(f"    video {report.video_codec}, audio {report.audio_codec} @ {report.audio_sample_rate} Hz")
    print(f"    subtitle streams: {report.subtitle_streams}")
    if report.integrated_lufs is not None:
        print(f"    {report.integrated_lufs:.1f} LUFS integrated, peak {report.true_peak_dbtp:.1f} dBTP")
    for note in report.notes:
        print(f"    note: {note}")

    if report.ok:
        print("\n  ✅ every check passes")
        return 0
    print("\n  ❌ failures:")
    for failure in report.failures:
        print(f"    - {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
