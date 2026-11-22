"""Frames + narration + music + captions → MP4 with one ffmpeg call.

Video comes from an ffmpeg concat list of PNGs with per-image durations: stills
are listed once, animations and crossfades as one PNG per frame. Captions are
burned in with libass. Music (if any file is in assets/music/) loops under the
voice and is ducked further while the voice is speaking (sidechaincompress).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MUSIC_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


class RenderError(RuntimeError):
    pass


@dataclass
class FrameList:
    entries: list[tuple[str, float]]  # (relative png path, seconds)

    def write(self, path: Path) -> None:
        lines = ["ffconcat version 1.0"]
        for name, dur in self.entries:
            lines.append(f"file '{name}'")
            lines.append(f"duration {dur:.6f}")
        # The concat demuxer ignores the last duration unless the file is repeated.
        if self.entries:
            lines.append(f"file '{self.entries[-1][0]}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @property
    def duration(self) -> float:
        return sum(d for _n, d in self.entries)


def pick_music(music_dir: Path, video_id: str) -> Path | None:
    tracks = sorted(p for p in music_dir.glob("*") if p.suffix.lower() in MUSIC_EXT)
    if not tracks:
        return None
    idx = int(hashlib.sha256(video_id.encode()).hexdigest(), 16) % len(tracks)
    return tracks[idx]


def ffmpeg_command(
    *,
    frames_list: str,
    narration: str,
    captions: str,
    output: str,
    duration: float,
    fps: int,
    preset: str,
    crf: int,
    music: Path | None,
    music_db: float,
) -> list[str]:
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        frames_list,
        "-i",
        narration,
    ]
    # shaping=complex makes libass use HarfBuzz; the default "simple" shaper puts
    # Devanagari pre-base matras on the wrong side (आधारित -> आधारति).
    vf = f"[0:v]fps={fps},format=yuv420p,ass={captions}:shaping=complex[v]"
    if music is not None:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        af = (
            f"[2:a]volume={music_db}dB,aformat=channel_layouts=stereo[m];"
            "[1:a]aformat=channel_layouts=stereo,asplit=2[n1][n2];"
            "[m][n1]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[duck];"
            "[n2][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-tune",
        "stillimage",
        "-crf",
        str(crf),
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        f"{duration:.3f}",
        output,
    ]
    return cmd


def run_ffmpeg(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed:\n{proc.stderr[-3000:]}")


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return {
        "duration": float(data["format"]["duration"]),
        "width": video["width"],
        "height": video["height"],
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }
