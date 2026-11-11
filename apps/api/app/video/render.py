"""Build an MP4 from real screenshots or an uploaded recording. Voiceover is not invented."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.errors import AppError


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def render_slideshow(
    frames: list[tuple[str, bytes]],
    script: str,
    *,
    voiceover_requested: bool,
    audio: bytes | None = None,
    source_mode: str = "screenshot_slideshow",
    recording: bytes | None = None,
) -> dict:
    if not ffmpeg_available():
        raise AppError(500, "rendering_error", "ffmpeg and ffprobe are not installed, so video rendering cannot run.")
    if recording:
        source_mode = "uploaded_recording"
    elif not frames:
        raise AppError(422, "rendering_error", "Video rendering needs at least one captured screenshot or an uploaded recording.")
    limitations = []
    if voiceover_requested and not audio:
        limitations.append("Voiceover was requested, but speech synthesis did not return audio. The video is silent and is not voice-enabled.")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        if recording:
            source = root / "recording.bin"
            source.write_bytes(recording)
            silent = root / "silent.mp4"
            _run(["ffmpeg", "-y", "-i", str(source), "-an", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p", str(silent)])
            captions = _srt(script, max(1, int(_duration(silent) // 3) or 1))
            caption_path = root / "captions.srt"
            caption_path.write_text(captions, encoding="utf-8")
            output = root / "tutorial.mp4"
            _attach(root, silent, output, caption_path, audio)
            duration = _duration(output)
            has_audio = _has_audio(output)
            if duration <= 0:
                raise AppError(500, "rendering_error", "The rendered video has no duration.")
            if voiceover_requested and audio and not has_audio:
                limitations.append("Speech audio was returned but the rendered file has no audio stream.")
            thumb = root / "thumb.png"
            _run(["ffmpeg", "-y", "-i", str(output), "-ss", "0.2", "-frames:v", "1", str(thumb)])
            return _package(output, captions, thumb, script, duration, has_audio, limitations, source_mode, voiceover_requested)
        list_path = root / "frames.txt"
        lines: list[str] = []
        image_paths: list[Path] = []
        for index, (_label, data) in enumerate(frames):
            if not data.startswith(b"\x89PNG"):
                raise AppError(422, "rendering_error", "A video frame is not a PNG screenshot.")
            image = root / f"frame-{index}.png"
            image.write_bytes(data)
            image_paths.append(image)
            lines.append(f"file '{image}'")
            lines.append("duration 3")
        lines.append(f"file '{image_paths[-1]}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")
        silent = root / "silent.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                "-r",
                "30",
                str(silent),
            ]
        )
        captions = _srt(script, len(frames))
        caption_path = root / "captions.srt"
        caption_path.write_text(captions, encoding="utf-8")
        output = root / "tutorial.mp4"
        _attach(root, silent, output, caption_path, audio)
        thumb = root / "thumb.png"
        _run(["ffmpeg", "-y", "-i", str(output), "-ss", "0.2", "-frames:v", "1", str(thumb)])
        duration = _duration(output)
        has_audio = _has_audio(output)
        if duration <= 0:
            raise AppError(500, "rendering_error", "The rendered video has no duration.")
        if voiceover_requested and audio and not has_audio:
            limitations.append("Speech audio was returned but the rendered file has no audio stream.")
        return _package(output, captions, thumb, script, duration, has_audio, limitations, source_mode, voiceover_requested)


def _attach(root: Path, silent: Path, output: Path, caption_path: Path, audio: bytes | None) -> None:
    command = ["ffmpeg", "-y", "-i", str(silent)]
    if audio:
        audio_path = root / "narration.bin"
        audio_path.write_bytes(audio)
        command.extend(["-i", str(audio_path)])
    command.extend(["-i", str(caption_path), "-map", "0:v:0"])
    if audio:
        command.extend(["-map", "1:a:0", "-map", "2:0", "-c:v", "copy", "-c:a", "aac", "-c:s", "mov_text", "-shortest"])
    else:
        command.extend(["-map", "1:0", "-c:v", "copy", "-c:s", "mov_text"])
    command.append(str(output))
    _run(command)


def _package(output: Path, captions: str, thumb: Path, script: str, duration: float, has_audio: bool, limitations: list[str], source_mode: str, voiceover_requested: bool) -> dict:
    return {
        "video": output.read_bytes(),
        "captions": captions.encode("utf-8"),
        "thumbnail": thumb.read_bytes(),
        "script": script.encode("utf-8"),
        "duration": duration,
        "has_audio": has_audio,
        "limitations": limitations,
        "metadata": {
            "renderer": "ffmpeg",
            "mode": source_mode,
            "interaction_recording": False,
            "voiceover": bool(voiceover_requested and has_audio),
            "note": "Screenshot slideshow." if source_mode == "screenshot_slideshow" else "Uploaded recording. This is not a new browser interaction capture.",
        },
    }


def _srt(script: str, frames: int) -> str:
    sentences = [line.strip() for line in script.splitlines() if line.strip()] or ["Observed workflow."]
    blocks = []
    for index in range(frames):
        start = index * 3
        end = start + 3
        text = sentences[min(index, len(sentences) - 1)]
        blocks.append(f"{index + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def _stamp(seconds: int) -> str:
    return f"00:00:{seconds:02d},000"


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed")[-500:]
        raise AppError(500, "rendering_error", f"ffmpeg failed: {detail}")


def _duration(path: Path) -> float:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AppError(500, "rendering_error", "ffprobe could not read the rendered video.")
    payload = json.loads(completed.stdout or "{}")
    return float(payload.get("format", {}).get("duration") or 0)


def _has_audio(path: Path) -> bool:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    payload = json.loads(completed.stdout or "{}")
    return bool(payload.get("streams"))
