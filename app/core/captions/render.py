"""app.core.captions.render - YouTube Hunter.

The ffmpeg side of Voice Studio: probe a file, burn an ASS script into the
picture, add a switchable subtitle track, pull a small audio copy for the AI,
and run all of it with live progress and cancel.

Burning runs ffmpeg with its working directory set to the job folder and the
script / fonts referenced by RELATIVE name. The subtitles filter's own option
syntax needs ':' and '\\' escaped, which is exactly what every Windows path is
made of; relative names sidestep that whole class of bug.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class Cancelled(Exception):
    pass


# ── binaries ─────────────────────────────────────────────────────────────

def _bin(name):
    exe = name + (".exe" if sys.platform == "win32" else "")
    try:
        from app.core.runtime_deps import ensure_ffmpeg_dir
        d = ensure_ffmpeg_dir()
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    except Exception:                    # noqa: BLE001 — fall back to PATH
        pass
    return shutil.which(name) or exe


def ffmpeg_exe():
    return _bin("ffmpeg")


def ffprobe_exe():
    return _bin("ffprobe")


_FILTERS = {}


def has_filter(name):
    """Whether this ffmpeg build has a filter (libass builds have `subtitles`)."""
    ff = ffmpeg_exe()
    if ff not in _FILTERS:
        try:
            out = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 timeout=15, creationflags=_NO_WINDOW).stdout or ""
        except Exception:                # noqa: BLE001
            out = ""
        _FILTERS[ff] = set(m.group(1) for m in re.finditer(r"^\s*\S+\s+(\w+)\s", out, re.M))
    return name in _FILTERS[ff]


def fonts_dir():
    try:
        from app.paths import asset_path
        d = asset_path("fonts")
        if os.path.isdir(d):
            return d
    except Exception:                    # noqa: BLE001
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "fonts"))


# ── probe ────────────────────────────────────────────────────────────────

def probe(path):
    """{duration, width, height, has_video, has_audio, vcodec, acodec}."""
    info = {"duration": 0.0, "width": 0, "height": 0, "has_video": False,
            "has_audio": False, "vcodec": "", "acodec": "", "audio_streams": 0}
    try:
        out = subprocess.run(
            [ffprobe_exe(), "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=_NO_WINDOW).stdout
        data = json.loads(out or "{}")
    except Exception:                    # noqa: BLE001
        data = {}
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not info["has_video"] \
                and (s.get("disposition") or {}).get("attached_pic") != 1:
            info["has_video"] = True
            info["vcodec"] = s.get("codec_name") or ""
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            rot = 0
            try:
                rot = int((s.get("tags") or {}).get("rotate") or 0)
            except ValueError:
                rot = 0
            for sd in s.get("side_data_list") or []:
                if "rotation" in sd:
                    try:
                        rot = int(sd["rotation"])
                    except (TypeError, ValueError):
                        pass
            if abs(rot) in (90, 270):
                w, h = h, w
            info["width"], info["height"] = w, h
        elif s.get("codec_type") == "audio":
            info["audio_streams"] += 1
            if not info["has_audio"]:
                info["has_audio"] = True
                info["acodec"] = s.get("codec_name") or ""
    try:
        info["duration"] = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass
    if not info["duration"]:
        info["duration"] = _duration_fallback(path)
    return info


def _duration_fallback(path):
    try:
        err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30,
                             creationflags=_NO_WINDOW).stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", err)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:                    # noqa: BLE001
        pass
    return 0.0


def wav_duration(path):
    """Seconds of a PCM WAV (header math, no ffmpeg)."""
    import wave
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:                    # noqa: BLE001
        return probe(path)["duration"]


# ── runner ───────────────────────────────────────────────────────────────

def run(args, duration=0.0, on_progress=None, should_cancel=None, cwd=None):
    """Run ffmpeg with `-progress`, report 0..1, honour cancel.
    Returns (ok, error_tail)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1",
           "-nostats", *args]
    err_f = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_f,
                                stdin=subprocess.DEVNULL, cwd=cwd,
                                creationflags=_NO_WINDOW)
    except OSError as e:
        return False, str(e)
    killer = None
    if should_cancel:
        def watch():
            while proc.poll() is None:
                if should_cancel():
                    try:
                        proc.kill()
                    except Exception:    # noqa: BLE001
                        pass
                    return
                threading.Event().wait(0.25)
        killer = threading.Thread(target=watch, daemon=True)
        killer.start()
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if on_progress and duration > 0 and line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    us = int(line.split("=", 1)[1])
                    on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
                except ValueError:
                    pass
        proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:                # noqa: BLE001
            pass
    if should_cancel and should_cancel():
        err_f.close()
        raise Cancelled()
    err_f.seek(0)
    tail = err_f.read().decode("utf-8", "replace")[-2500:]
    err_f.close()
    return proc.returncode == 0, _clean_err(tail)


def _clean_err(text):
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    keep = [ln for ln in lines if re.search(r"error|invalid|failed|not found|unable|no such", ln, re.I)]
    return "\n".join((keep or lines)[-4:])


# ── encoders ─────────────────────────────────────────────────────────────

def encoder_chain():
    """[(name, args)] fastest first (hardware when the cutter found one)."""
    try:
        from app.core.cutter import video_encoders
        return video_encoders(ffmpeg_exe())
    except Exception:                    # noqa: BLE001 — headless / tests
        return [("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                             "-pix_fmt", "yuv420p"])]


def out_container(src, prefer=""):
    ext = (prefer or os.path.splitext(src)[1]).lower().lstrip(".")
    return ext if ext in ("mp4", "mkv", "mov", "m4v") else "mp4"


def _audio_args(acodec, container):
    ok_copy = {"mp4": ("aac", "mp3", "ac3", "eac3", "alac", "opus"),
               "m4v": ("aac", "mp3", "ac3", "eac3", "alac"),
               "mov": ("aac", "mp3", "alac", "pcm_s16le"),
               "mkv": ("aac", "mp3", "opus", "vorbis", "flac", "ac3", "eac3", "pcm_s16le")}
    if acodec and acodec in ok_copy.get(container, ()):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]


def stage_fonts(workdir):
    """Copy the bundled caption fonts next to the script (relative fontsdir)."""
    dst = os.path.join(workdir, "fonts")
    os.makedirs(dst, exist_ok=True)
    src = fonts_dir()
    for name in ("HankenGrotesk.ttf", "Vazirmatn-Regular.ttf", "Vazirmatn-Medium.ttf",
                 "Vazirmatn-SemiBold.ttf", "Vazirmatn-Bold.ttf"):
        p = os.path.join(src, name)
        q = os.path.join(dst, name)
        if os.path.isfile(p) and not os.path.isfile(q):
            try:
                shutil.copyfile(p, q)
            except OSError:
                pass
    return "fonts"


def burn(src, ass_text, out_path, workdir, duration=0.0, on_progress=None,
         should_cancel=None, extra_inputs=None, map_args=None, audio_args=None):
    """Re-encode `src` with the captions drawn into the picture.

    `extra_inputs` / `map_args` / `audio_args` let the dub pipeline mux its new
    audio in the same pass (one encode instead of two)."""
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "subs.ass"), "w", encoding="utf-8") as f:
        f.write(ass_text)
    fd = stage_fonts(workdir)
    info = probe(src)
    cont = out_container(out_path)
    vf = f"subtitles=subs.ass:fontsdir={fd}"
    last_err = ""
    for name, venc in encoder_chain():
        args = ["-i", os.path.abspath(src)]
        for x in (extra_inputs or []):
            args += x
        args += ["-vf", vf]
        args += map_args or ["-map", "0:v:0", "-map", "0:a?"]
        args += venc
        args += audio_args or _audio_args(info["acodec"], cont)
        if cont in ("mp4", "m4v", "mov"):
            args += ["-movflags", "+faststart"]
        args += [os.path.abspath(out_path)]
        ok, err = run(args, duration or info["duration"], on_progress, should_cancel, cwd=workdir)
        if ok:
            return True, ""
        last_err = err
    return False, last_err


def soft_mux(src, sub_paths, out_path, duration=0.0, on_progress=None,
             should_cancel=None):
    """Add one or more switchable subtitle tracks without re-encoding.
    `sub_paths` = [(path, iso3, title, default)]."""
    info = probe(src)
    cont = out_container(out_path)
    args = ["-i", os.path.abspath(src)]
    for p, _l, _t, _d in sub_paths:
        args += ["-i", os.path.abspath(p)]
    args += ["-map", "0:v?", "-map", "0:a?"]
    for i in range(len(sub_paths)):
        args += ["-map", f"{i + 1}:0"]
    args += ["-c:v", "copy", "-c:a", "copy",
             "-c:s", "mov_text" if cont in ("mp4", "m4v", "mov") else "srt"]
    for i, (_p, iso, title, default) in enumerate(sub_paths):
        args += [f"-metadata:s:s:{i}", f"language={iso}", f"-metadata:s:s:{i}", f"title={title}",
                 f"-disposition:s:{i}", "default" if default else "0"]
    if cont in ("mp4", "m4v", "mov"):
        args += ["-movflags", "+faststart"]
    args += [os.path.abspath(out_path)]
    return run(args, duration or info["duration"], on_progress, should_cancel)


def extract_audio(src, out_path, start=0.0, dur=0.0, rate=16000, kbps=32,
                  should_cancel=None, codec="opus"):
    """A small mono Opus copy for the AI to listen to (~240 KB/min).
    v1.4.9: codec="mp3" writes 16 kHz mono MP3 straight away (what
    OpenAI-compatible input_audio accepts)."""
    if codec == "mp3":
        args = []
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", os.path.abspath(src)]
        if dur > 0:
            args += ["-t", f"{dur:.3f}"]
        out_mp3 = os.path.splitext(out_path)[0] + ".mp3"
        args += ["-vn", "-ac", "1", "-ar", str(rate), "-c:a", "libmp3lame", "-b:a", f"{kbps}k",
                 os.path.abspath(out_mp3)]
        ok, err = run(args, 0, None, should_cancel)
        if ok and not _nonempty(out_mp3):
            ok, err = False, err or "the MP3 encode produced an empty file"
        return (out_mp3 if ok else ""), err
    args = []
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", os.path.abspath(src)]
    if dur > 0:
        args += ["-t", f"{dur:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(rate), "-c:a", "libopus", "-b:a", f"{kbps}k",
             "-application", "voip", os.path.abspath(out_path)]
    ok, err = run(args, 0, None, should_cancel)
    # v1.4.5: an encode that "succeeded" but wrote nothing counts as failed
    if ok and not _nonempty(out_path):
        ok, err = False, err or "the Opus encode produced an empty file"
    if not ok:
        # builds without libopus: plain mp3 (same 16 kHz mono, same bitrate)
        alt = os.path.splitext(out_path)[0] + ".mp3"
        args2 = [a for a in args[:-1]]
        i = args2.index("libopus")
        args2[i] = "libmp3lame"
        args2 = [a for a in args2 if a not in ("-application", "voip")]
        ok, err = run(args2 + [os.path.abspath(alt)], 0, None, should_cancel)
        if ok and not _nonempty(alt):
            ok, err = False, err or "the MP3 encode produced an empty file"
        return (alt if ok else ""), err
    return out_path, ""


def _nonempty(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def speech_edges(src, start=0.0, end=0.0, noise_db=-32, min_sil=0.18, timeout=600):
    """v1.4.4: where the voice starts and stops, from ffmpeg's silencedetect
    on the speech band (200 Hz - 3.5 kHz). Returns (onsets, offsets) in
    seconds from `start`, sorted. Empty lists when the audio has no clear
    pauses (music under the voice, a noisy room): callers then keep their
    timings as they are."""
    args = [ffmpeg_exe(), "-hide_banner", "-nostdin"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", os.path.abspath(src)]
    if end > start:
        args += ["-t", f"{end - start:.3f}"]
    args += ["-vn", "-ac", "1", "-af",
             f"highpass=f=200,lowpass=f=3500,silencedetect=noise={noise_db}dB:d={min_sil}",
             "-f", "null", "-"]
    try:
        err = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout,
                             creationflags=_NO_WINDOW).stderr or ""
    except Exception:                    # noqa: BLE001
        return [], []
    sil_start = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    sil_end = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    if not sil_start and not sil_end:
        return [], []
    onsets = sorted(max(0.0, t) for t in sil_end)
    offsets = sorted(max(0.0, t) for t in sil_start)
    if not sil_start or (sil_start and sil_start[0] > 0.05):
        onsets.insert(0, 0.0)            # the audio opens on speech
    return onsets, offsets


def extract_wav(src, out_path, rate=24000, start=0.0, dur=0.0, should_cancel=None):
    args = []
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", os.path.abspath(src)]
    if dur > 0:
        args += ["-t", f"{dur:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", os.path.abspath(out_path)]
    return run(args, 0, None, should_cancel)
