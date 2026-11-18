"""FFmpeg assembly: per-scene clips -> concat -> captions + voice + music + logo -> 9:16 mp4.

All ffmpeg calls run with cwd=job_dir and relative file names, so the subtitles filter
never has to deal with path escaping.
"""
import logging
import random
import subprocess
from pathlib import Path

import config

W, H, FPS = 1080, 1920, 30
PAD = 0.35  # silence after each scene's voiceover
log = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{p.stderr[-900:]}")
    return p


def probe_duration(path: Path) -> float:
    p = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-an"]


def _render_clip(visual: dict, dur: float, out: Path, idx: int, brand_hex: str, cwd: Path) -> None:
    kind, src = visual["kind"], visual["path"]
    if kind == "video":
        clip_len = probe_duration(Path(src))
        # a short generated clip is slowed down up to 1.3x, then its last frame is held to fill the scene
        slow = min(max(dur / clip_len, 1.0), 1.3) if clip_len > 0 else 1.0
        vf = (f"setpts={slow:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,tpad=stop_mode=clone:stop_duration={dur:.3f},format=yuv420p")
        _run(["ffmpeg", "-y", "-i", src, "-t", f"{dur:.3f}", "-vf", vf, *_ENC, str(out)], cwd)
    elif kind == "image":
        frames = int(dur * FPS) + 1
        # alternate slow zoom-in / zoom-out so consecutive photos don't look identical
        z = "min(1+0.0009*on,1.3)" if idx % 2 == 0 else "max(1.3-0.0009*on,1)"
        fc = (
            "[0:v]split=2[a][b];"
            "[a]scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,boxblur=40:6[bg];"
            "[b]scale=1620:2880:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
            "setsar=1,format=yuv420p[v]"
        )
        _run(["ffmpeg", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]",
              "-frames:v", str(frames), *_ENC, str(out)], cwd)
    else:  # card
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
              f"color=c=0x{brand_hex}:s={W}x{H}:r={FPS}:d={dur:.3f}",
              "-vf", "vignette=PI/4,format=yuv420p", *_ENC, str(out)], cwd)


def _ass_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _build_ass(scenes: list[dict], durations: list[float], path: Path) -> None:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,DejaVu Sans,84,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,"
        "100,100,0,0,1,7,2,2,60,60,420,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines, t0 = [], 0.0
    for scene, dur in zip(scenes, durations):
        words = scene["voiceover"].replace("{", "(").replace("}", ")").split()
        chunks = [" ".join(words[i:i + 3]) for i in range(0, len(words), 3)]
        speak = max(dur - PAD, 0.5)
        total_chars = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            span = speak * len(c) / total_chars
            lines.append(f"Dialogue: 0,{_ass_time(t)},{_ass_time(t + span)},Default,,0,0,0,,{c.upper()}")
            t += span
        t0 += dur
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def render_video(job_dir: Path, scenes: list[dict], visuals: list[dict], audio_files: list[Path],
                 brand_hex: str, logo: Path | None = None) -> Path:
    # 1) pad each scene's voiceover, measure it
    wavs, durations = [], []
    for i, mp3 in enumerate(audio_files):
        wav = job_dir / f"a{i}.wav"
        _run(["ffmpeg", "-y", "-i", str(mp3), "-af", f"apad=pad_dur={PAD}",
              "-ar", "44100", "-ac", "1", str(wav)], job_dir)
        wavs.append(wav)
        durations.append(probe_duration(wav))
    total = sum(durations)

    # 2) one silent clip per scene, exactly as long as its voiceover
    clips = []
    for i, (vis, dur) in enumerate(zip(visuals, durations)):
        out = job_dir / f"c{i}.mp4"
        _render_clip(vis, dur, out, i, brand_hex, job_dir)
        clips.append(out)

    # 3) concat clips and voiceover
    (job_dir / "clips.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    (job_dir / "voices.txt").write_text("".join(f"file '{w.name}'\n" for w in wavs))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "clips.txt", "-c", "copy", "video.mp4"], job_dir)
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "voices.txt", "-c", "copy", "voice.wav"], job_dir)

    # 4) captions
    _build_ass(scenes, durations, job_dir / "captions.ass")

    # 5) final mix
    music_files = sorted(config.MUSIC.glob("*.mp3"))
    music = random.choice(music_files) if music_files else None

    cmd = ["ffmpeg", "-y", "-i", "video.mp4", "-i", "voice.wav"]
    next_idx = 2
    music_idx = logo_idx = None
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx, next_idx = next_idx, next_idx + 1
    if logo:
        cmd += ["-i", str(logo)]
        logo_idx, next_idx = next_idx, next_idx + 1

    fc = ["[0:v]subtitles=captions.ass[v0]"]
    if logo_idx is not None:
        fc.append(f"[{logo_idx}:v]scale=220:-1[lg]")
        fc.append("[v0][lg]overlay=W-w-50:70[vout]")
    else:
        fc.append("[v0]null[vout]")
    if music_idx is not None:
        fc.append(f"[{music_idx}:a]volume=0.10,afade=t=in:d=1[m]")
        fc.append("[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                  "loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    else:
        fc.append("[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

    final = job_dir / "final.mp4"
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)]
    _run(cmd, job_dir)
    return final
