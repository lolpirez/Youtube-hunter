"""app.core.captions.dubbing - YouTube Hunter.

AI dubbing that keeps the performance, not just the words.

  1. Performance read   Gemini LISTENS to the original (10-minute slices) with
                        the timed lines in hand and returns, per line: who
                        speaks, how it is delivered ("excited, fast, grinning"),
                        and the line re-written with vocal events where they
                        happen ("No way <laugh> that's insane"). Per speaker:
                        gender, age, timbre, and a cast voice from Gemini's
                        30 prebuilt voices.
  2. Utterances         consecutive lines of one speaker are joined (up to
                        ~10 s), so the voice acts a whole thought instead of
                        restarting every caption.
  3. Adaptation         translate_cues(mode="dub"): each line is written to be
                        SAID in its seconds, tags kept in place.
  4. Voice              Gemini TTS per utterance, 3 at a time, with the line's
                        delivery as speech_metadata.style. Clips are cached by
                        content, so a failed or re-run job resumes instead of
                        paying again.
  5. Timing fit         a line that runs long is first re-written shorter
                        (one AI call), then sped up at most 1.35× (atempo keeps
                        pitch), then trimmed with a fade at the next line.
  6. Mix                the original audio is side-chain ducked under the dub
                        (music and room sound stay, the original voice dips
                        whenever the dub speaks and returns for laughs and
                        reactions in the gaps), or kept as a low bed, or
                        dropped. Loudness-normalised to -16 LUFS.
  7. Mux                dub = default audio track, original kept as a second
                        switchable track; optional burned-in captions of the
                        dub in one encode. The dub WAV and the script (.srt)
                        are written next to the video too.

v1.3.1 "lean dub" (same output, a fraction of the Gemini quota and time):
  * every Gemini call goes through one app-wide gate (gemini_media.GATE):
    no more 429 retry storms, per-minute limits cool the whole app down,
    a per-day "quota used up" stops the job at once instead of grinding;
  * the performance read is cached per 10-min slice at the FILE level (shared
    by previews, full runs and every voice / model setting), a failed slice
    is never cached, thinking is off for it, and "tagged" is only sent back
    for lines that actually got a tag;
  * the script is cached at the file level too, and every line gets a
    character budget, so it is written to fit;
  * lines still predicted too long are tightened in ONE batched call BEFORE
    any voice is recorded (was: record, measure, rewrite, record again, one
    AI call + one TTS call per long line);
  * utterances are a little longer (<=14 s), so fewer TTS calls;
  * voice clips are cached at the file level by content: a job that ran out
    of quota resumes later and only pays for the lines it didn't record;
  * a dub where too many lines failed is reported as failed (with Resume),
    not "done" with silent gaps.

v1.4.4 "tight sync" (the dub follows the original second by second):
  * every recorded line is cleaned first: TTS adds 0.2-0.8 s of silence
    before / after the words, which pushed each line late and, counted as
    "speech", forced speed-ups and the hard trim that cut line endings;
    clips are also normalised to 24 kHz mono 16-bit, so a clip in any other
    format is no longer dropped silently by the assembler
  * timing is planned for the WHOLE track: a line may lean up to 0.45 s
    into the next one instead of being cut, speed-up goes to 1.35x and only
    then (rarely) to 1.5x, and a trim is the very last resort; no line ever
    drifts more than 0.45 s from where it is said in the original
  * utterances are shorter (<= 9 s) so the voice re-anchors to the picture
    more often; non-speech lines ([music], (applause), ♪) are never spoken
  * lines that failed to record get one more try before the dub gives up on
    them, and the result says how many (if any) stayed in the original
  * the dub stream is padded / cut to the exact video length

v1.4.5 "lean fit" (same sound, a fraction of the processes):
  * the TTS lead-in / tail silence is stripped IN MEMORY: a 24 kHz mono
    16-bit take is scanned with `wave` + `array` (20 ms RMS windows,
    -50 dBFS, 10 ms kept on each side). ffmpeg only runs for a take in any
    other format. Was: 1-2 ffmpeg processes per line
  * a line that needs no speed-up and no trim is used as the clean take
    itself (no process, no copy); a trim-only line is cut and faded in
    memory. ffmpeg runs only for the lines that really need atempo
  * a rejected key / refused access / dead network (GeminiFatal) stops the
    job at once with a clear message, in the performance read and in the
    voice recording, instead of being swallowed slice after slice; a
    per-slice glitch still falls back to a plain dub, and is logged


v1.5 "quota saver" (Settings → Captions & Dub, ON by default). Google AI
Studio's free tier counts REQUESTS per day (~20), and a 40-second dub used
to spend 8-15 of them: a transcription, a performance read, a translation,
then one TTS request PER LINE, plus re-records. Now:
  * ONE TAKE PER VOICE: every line a voice says is recorded in one TTS
    request (up to ~18 lines / ~75 s per take), with a long pause between
    lines, then cut back into lines at the pauses. The cut is checked
    against each line's expected length (dynamic programming over the
    silences); a take that doesn't split cleanly falls back to per-line
    recording for just that take, so the dub is never worse than before;
  * when the AI listens to the file, the SAME transcription request also
    returns who speaks and how (sources.ai_transcribe), so the separate
    performance read is skipped (0 requests instead of 1 per 10 min);
  * no re-recording of lines that still run long: they are sped up (max
    1.5x) and fitted by the timing planner instead (each re-record was a
    rewrite request plus one TTS request per line);
  * the request shape each TTS model accepts is remembered on disk, and
    only one worker probes it (gemini_media);
  * the job's note says how many Gemini requests it used.

v1.5 ElevenLabs: Settings → Captions & Dub can switch the dub's voices to
ElevenLabs (your own key). Gemini still listens and adapts the script when
it is set up; the voices cost ElevenLabs credits instead of Gemini requests.
"""

import array
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import wave

from app.core.captions import cues as C
from app.core.captions import gemini_media as G
from app.core.captions import languages as L
from app.core.captions import render as R
from app.core.captions import translate as T
from app.core.captions import gemini_budget as B
from app.core.captions import elevenlabs as E
from app.core.captions import pitch as P

_SLICE = 600.0
_MAX_TEMPO = 1.35
_RATE = 24000
# predicted speech longer than this share of its slot gets tightened before TTS
_PRE_FIT = 1.25
# a dub with more than this share of lines unvoiced fails (resumable) instead
# of finishing as a mostly-original video
_MAX_FAILED = 0.2
_TTS_WORKERS = 4
# v1.4.4 tight sync: how far a line may lean into the next one before it is
# sped up, the last-resort speed-up before a trim, and the breath between lines
_MAX_DRIFT = 0.45
_HARD_TEMPO = 1.5
_GAP = 0.05
_FIT_WORKERS = 4
# a line that is only a sound label: [music], (applause), ♪♪, [Laughter] …
_NON_SPEECH = re.compile(r"^\s*(?:(?:\[[^\]]{0,40}\]|\([^)]{0,40}\)|（[^）]{0,40}）|【[^】]{0,40}】|[♪♫#*~\-–—.…\s])\s*)+$")
_INLINE_LABEL = re.compile(r"\[[^\]]{1,30}\]|♪+")


def speakable(cues):
    """v1.4.4: drop sound-label lines and inline labels a voice must not
    read aloud ("[Music]" was spoken as a word)."""
    out = []
    for c in cues:
        t = C.flat(c.text)
        if not t or _NON_SPEECH.match(t):
            continue
        t2 = " ".join(_INLINE_LABEL.sub(" ", t).split())
        if not t2:
            continue
        out.append(c if t2 == t else c.copy(text=t2))
    return out


class QuotaStop(RuntimeError):
    """The job stopped because Gemini's quota ran out; everything recorded so
    far is cached and a re-run only pays for what is missing."""

_PERF_SYS = ("You are a dubbing director with perfect hearing. You listen to HOW every line is "
             "performed: emotion, energy, pace, volume, laughs, sighs, breaths, hesitation. "
             "You describe delivery precisely and briefly, in English.")

_PERF_PROMPT = """This is {dur:.0f} seconds of a video's audio. Below are its subtitle lines with ids and
start/end times in seconds from the start of THIS clip. Listen to each line.

Return JSON only:
{{
 "speakers": [{{"id": "S1", "gender": "male|female", "age": "child|teen|young adult|adult|senior",
               "voice": "timbre & energy in a few words", "cast": "<name from the casting table>"}}],
 "lines": [{{"id": 12, "spk": "S1",
            "style": "delivery in max 6 words, e.g. 'excited, fast, grinning' / 'deadpan, slow' / 'whispered, nervous'",
            "tagged": "ONLY if you add a vocal event or fix a mishearing: the line text with the event where it happens"}}]
}}

Vocal event tags (English, angle brackets) you may insert in "tagged": <laugh> <chuckle> <giggle> <sigh>
<gasp> <breath> <cough> <sniff> <scoff> <hmm> <short pause> <long pause>.
Only add a tag when you actually hear it. Do not rewrite the words (fix only obvious mishearings).
Leave "tagged" out when the line has no event and no fix: the original text is used. Keep it compact.
Every line id must appear once. Reuse speaker ids for the same voice.{known}

Casting table (pick the closest match per speaker; never cast a male voice for a female speaker or vice versa):
{voices}

LINES:
{lines}"""

_MALE = ["Puck", "Charon", "Orus", "Achird", "Fenrir", "Algenib", "Iapetus", "Sadaltager", "Umbriel"]
_FEMALE = ["Kore", "Aoede", "Sulafat", "Leda", "Despina", "Achernar", "Autonoe", "Gacrux", "Vindemiatrix"]
_GENDER = {v[0]: v[1] for v in G.VOICES}


def _sha(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def media_key(path):
    try:
        st = os.stat(path)
        return _sha(os.path.abspath(path), st.st_size, int(st.st_mtime))
    except OSError:
        return _sha(path)


# ── 1. performance read ──────────────────────────────────────────────────

def read_performance(media, cues, work, creds, on_progress=None, should_cancel=None):
    """Who speaks each line and how. Cached per slice in `work/perf/`, keyed by
    the slice's lines, so a preview, a full run and every voice/model setting
    share the same paid listening. A slice that failed is NOT cached (it is
    retried next run); a used-up quota stops listening and the dub goes on
    plainly for the remaining lines."""
    pdir = os.path.join(work, "perf")
    os.makedirs(pdir, exist_ok=True)
    voices = "\n".join(f"- {n}: {g}, {c}" for n, g, c in G.VOICES)
    speakers, lines = {}, {}
    total = cues[-1].end if cues else 0
    n = max(1, int((total + _SLICE - 1) // _SLICE))
    t, i = 0.0, 0
    stop_listening = False
    while t < total - 0.1:
        if should_cancel and should_cancel():
            raise R.Cancelled()
        end = min(total, t + _SLICE)
        ids = [k for k, c in enumerate(cues) if c.start >= t and c.start < end]
        if ids:
            seg_end = max(end, max(cues[k].end for k in ids))
            key = _sha(media_key(media), round(t, 2), round(seg_end, 2),
                       *[C.flat(cues[k].text) for k in ids])
            cache = os.path.join(pdir, key + ".json")
            data = None
            if os.path.isfile(cache):
                try:
                    with open(cache, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:        # noqa: BLE001
                    data = None
            if data is None and not stop_listening:
                audio, _err = R.extract_audio(media, os.path.join(work, f"perf_{i}.ogg"), t, seg_end - t,
                                              should_cancel=should_cancel)
                if not audio:
                    G._log(f"performance read: slice {i} ({t:.0f}-{seg_end:.0f} s) audio extraction "
                           f"failed, dubbing it plainly: {_err}")
                if audio:
                    # v1.4.12: compact JSON (no spaces), ~8 % fewer input tokens
                    listing = "\n".join(json.dumps({"id": k, "s": round(cues[k].start - t, 1),
                                                    "e": round(cues[k].end - t, 1),
                                                    "text": C.flat(cues[k].text)}, ensure_ascii=False,
                                                   separators=(",", ":"))
                                        for k in ids)
                    known = ("\nSpeakers identified earlier: " + "; ".join(
                        f"{sid} ({s.get('gender', '?')}, {s.get('voice', '')})" for sid, s in speakers.items())
                        if speakers else "")
                    try:
                        data = G.listen_json(creds, audio, _PERF_PROMPT.format(
                            dur=seg_end - t, known=known, voices=voices, lines=listing), _PERF_SYS,
                            should_cancel=should_cancel, max_tokens=12000)
                        if isinstance(data, dict) and data.get("lines"):
                            with open(cache, "w", encoding="utf-8") as f:
                                json.dump(data, f, ensure_ascii=False)
                    except R.Cancelled:
                        raise
                    except G.GeminiFatal as e:
                        # v1.4.5: a bad key / no network can't be fixed by
                        # the next slice either: stop now, say why
                        G._log(f"performance read: fatal on slice {i}: {e}")
                        _remove(audio)
                        raise
                    except G.QuotaExhausted:
                        stop_listening = True
                        data = None
                    except Exception as e:   # noqa: BLE001 — a dub without acting is still a dub
                        if should_cancel and should_cancel():
                            _remove(audio)
                            raise R.Cancelled() from e
                        G._log(f"performance read: slice {i} ({t:.0f}-{seg_end:.0f} s) failed, "
                               f"dubbing it plainly: {e!r}")
                        data = None
                    _remove(audio)
            if isinstance(data, dict):
                for sp in data.get("speakers") or []:
                    if isinstance(sp, dict) and sp.get("id") and str(sp["id"]) not in speakers:
                        speakers[str(sp["id"])] = sp
                for ln in data.get("lines") or []:
                    try:
                        lines[int(ln.get("id"))] = ln
                    except (TypeError, ValueError, AttributeError):
                        continue
        i += 1
        t = end
        if on_progress:
            on_progress(min(1.0, i / n))
    return {"speakers": speakers, "lines": {str(k): v for k, v in lines.items()}}


def can_dub(cfg=None, engine=""):
    """v1.5: the dub can run: a Gemini key, or ElevenLabs picked with a key.
    v1.6: `engine` = the voices picked for this one dub ("" = Settings')."""
    eng = engine if engine in ("gemini", "elevenlabs") else E.engine(cfg)
    if eng == "elevenlabs":
        return E.ready(cfg)
    return G.has_gemini(cfg)


def why_cant_dub(engine=""):
    """The one-line reason can_dub() said no, for the engine in use."""
    eng = engine if engine in ("gemini", "elevenlabs") else E.engine()
    if eng == "elevenlabs":
        return ("ElevenLabs voices are picked, but there is no ElevenLabs key yet. "
                "Add it in Settings → Captions & Dub, or pick Gemini voices.")
    return ("Gemini voices need a Google Gemini key (free from Google AI Studio, Settings → AI Providers). "
            "Or switch the dub to ElevenLabs voices in Settings → Captions & Dub.")


def perf_from_cues(cues):
    """v1.5: the performance read the transcription already did. When the AI
    listened with the quota saver on, every cue carries meta["perf"] plus
    its delivery, vocal-event text and the speaker's gender / age. None when
    any cue lacks it (YouTube captions, a subtitle file, an older cache)."""
    if not cues or not all(isinstance(c.meta, dict) and c.meta.get("perf") for c in cues):
        return None
    speakers, lines = {}, {}
    for k, c in enumerate(cues):
        sid = c.speaker or "S1"
        if sid not in speakers:
            speakers[sid] = {"id": sid, "gender": c.meta.get("gender") or "",
                             "age": c.meta.get("age") or ""}
        ln = {"id": k, "spk": sid, "style": str(c.meta.get("style") or "")}
        if c.meta.get("tagged"):
            ln["tagged"] = str(c.meta["tagged"])
        lines[str(k)] = ln
    return {"speakers": speakers, "lines": lines}


def cast_voices(speakers, mode="auto", single_voice="Kore"):
    """speaker id → voice name. Distinct voices per speaker, gender respected."""
    if mode == "single":
        return {sid: single_voice for sid in speakers} or {"": single_voice}
    out, used = {}, set()
    for sid, s in speakers.items():
        g = str(s.get("gender") or "").lower()
        pick = s.get("cast") if s.get("cast") in G.VOICE_NAMES else ""
        if pick and g in ("male", "female") and _GENDER.get(pick) != g:
            pick = ""
        if pick in used:
            pick = ""
        if not pick:
            pool = _FEMALE if g == "female" else _MALE
            pick = next((v for v in pool if v not in used), pool[0])
        out[sid] = pick
        used.add(pick)
    return out


# ── 2. utterances ────────────────────────────────────────────────────────

def build_utterances(cues, perf, acting=True, max_dur=9.0, max_chars=220, gap=0.4):
    lines = (perf or {}).get("lines") or {}
    marked = []
    for k, c in enumerate(cues):
        ln = lines.get(str(k)) or {}
        text = C.flat(ln.get("tagged") or c.text) if acting else C.flat(c.text)
        if not acting:
            text = G._strip_tags(text)
        marked.append(c.copy(text=text, speaker=str(ln.get("spk") or c.speaker or ""),
                             meta={"style": str(ln.get("style") or "").strip()}))
    out = []
    for c in marked:
        if out:
            u = out[-1]
            if (c.speaker == u.speaker and c.start - u.end <= gap
                    and c.end - u.start <= max_dur and len(u.text) + len(c.text) < max_chars):
                styles = u.meta["styles"]
                if c.meta["style"] and c.meta["style"] not in styles:
                    styles.append(c.meta["style"])
                out[-1] = u.copy(end=c.end, text=u.text + " " + c.text,
                                 meta={"styles": styles})
                continue
        out.append(c.copy(meta={"styles": [c.meta["style"]] if c.meta["style"] else []}))
    for u in out:
        u.meta["style"] = "; then ".join(u.meta.pop("styles")[:2])
    return out


# ── 5. timing fit ────────────────────────────────────────────────────────

def _fit_clip(src_wav, dst_wav, avail, should_cancel=None):
    """Speed up (<= _MAX_TEMPO) and/or trim a clip into `avail` seconds.
    Kept for callers outside the dub pipeline; dub_media uses the planned
    _prep_clip / plan_timing / _render_clip path below."""
    d = R.wav_duration(src_wav)
    tempo = 1.0 if d <= avail + 0.02 else min(_MAX_TEMPO, d / max(0.1, avail))
    trim = avail if d / tempo > avail + 0.02 else 0.0
    return _render_clip(src_wav, dst_wav, tempo, trim, should_cancel), tempo


_PCM_OUT = ["-ac", "1", "-ar", str(_RATE), "-c:a", "pcm_s16le"]
# leading + trailing silence off a TTS take (-50 dB keeps soft breaths).
# Only used for a take that isn't 24 kHz mono s16 (see _prep_clip).
_TRIM_SIL = ("silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,"
             "areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,areverse")

# v1.4.5 in-memory silence scan (same -50 dB as the ffmpeg filter above;
# 20 ms is silenceremove's own RMS window)
_SIL_DB = -50.0
_SIL_AMP = 32768.0 * (10.0 ** (_SIL_DB / 20.0))      # ≈ 104 of 32768
_SIL_WIN = 0.020
_EDGE_PAD = 0.010                                    # keep the onset / release
_MIN_TAKE = 0.12


# ── PCM helpers (stdlib only: wave + array) ──────────────────────────────

def _read_wav16(path):
    """(array('h'), rate) of a plain 16-bit mono PCM WAV, or None for any
    other format (the caller then lets ffmpeg normalise it)."""
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getcomptype() != "NONE":
                return None
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        return None
    a = array.array("h")
    a.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":                       # WAV is little-endian
        a.byteswap()
    return a, rate


def _write_wav16(path, samples, rate):
    if sys.byteorder == "big":
        samples = array.array("h", samples)
        samples.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _voiced_edges(a, rate, amp=_SIL_AMP):
    """(first, end) sample indexes of the voiced part of a take: from the
    first to the last 20 ms window whose RMS is above `amp`, plus 10 ms on
    each side. None when the whole take is below it. Scans inward from both
    edges only, so a normal take costs a few hundred windows at most."""
    n = len(a)
    if not n:
        return None
    win = max(1, int(rate * _SIL_WIN))
    pad = int(rate * _EDGE_PAD)
    lim = amp * amp

    def loud(i):
        seg = a[i:i + win]
        return bool(seg) and sum(x * x for x in seg) > lim * len(seg)

    i = 0
    while i < n and not loud(i):
        i += win
    if i >= n:
        return None
    first = max(0, i - pad)
    j = max(i, n - win)
    while j > i and not loud(j):
        j -= win
    end = min(n, j + win + pad)
    return first, end


def _trim_fade(a, rate, trim):
    """First `trim` seconds with a linear fade-out (afade=t=out, tri curve)."""
    n = min(len(a), max(0, int(round(trim * rate))))
    out = a[:n]
    f = min(n, int(min(0.12, trim * 0.2) * rate))
    if f > 0:
        base = n - f
        for k in range(f):
            out[base + k] = int(out[base + k] * (1.0 - (k + 1) / f))
    return out


def _prep_clip(src_wav, should_cancel=None):
    """v1.4.4: a clean take: 24 kHz mono s16, the TTS's lead-in / tail
    silence removed. Cached next to the take. Returns (path, seconds).

    v1.4.5: a take that already is 24 kHz mono s16 (every Gemini TTS take)
    is trimmed in memory, no process. Anything else goes through ffmpeg,
    which also normalises the format."""
    dst = src_wav[:-4] + ".tight.wav" if src_wav.lower().endswith(".wav") else src_wav + ".tight.wav"
    if os.path.isfile(dst) and os.path.getsize(dst) > 44:
        return dst, R.wav_duration(dst)
    if should_cancel and should_cancel():
        raise R.Cancelled()
    tmp = f"{dst}.{threading.get_ident()}.part.wav"
    got = _read_wav16(src_wav)
    if got and got[1] == _RATE:
        a, rate = got
        edges = _voiced_edges(a, rate)
        if edges and edges[1] - edges[0] >= int(_MIN_TAKE * rate):
            a = a[edges[0]:edges[1]]
        # else: all "silence" by the scan (a whisper?): keep the whole take
        _write_wav16(tmp, a, rate)
        os.replace(tmp, dst)
        return dst, len(a) / float(rate)
    ok, _err = R.run(["-i", src_wav, "-af", _TRIM_SIL, *_PCM_OUT, tmp], 0, None, should_cancel)
    d = R.wav_duration(tmp) if ok and os.path.isfile(tmp) else 0.0
    if not ok or d < _MIN_TAKE:
        # all "silence" by the detector (a whisper?) or a filter problem:
        # keep the whole take, only normalise the format
        ok, err = R.run(["-i", src_wav, *_PCM_OUT, tmp], 0, None, should_cancel)
        if not ok:
            raise RuntimeError("Couldn't read a recorded line: " + err)
        d = R.wav_duration(tmp)
    os.replace(tmp, dst)
    return dst, d


# ── v1.5: one take per voice ─────────────────────────────────────────────
#
# A batch take is the lines of ONE voice, read in order with a long pause
# between them. It is cut back into lines at the pauses: every silence of
# 0.18 s+ is a candidate cut, and dynamic programming picks the n-1 cuts
# that (a) sit in the longest silences and (b) give each line a length
# close to what its text predicts. A take that can't be cut cleanly (a
# line skipped, two merged, pauses missing) is rejected and those lines
# are recorded one by one, exactly as before.

_BATCH_LINES = 18          # lines per take
_BATCH_CHARS = 1400        # characters per take (the TTS prompt stays small)
_BATCH_SEC = 75.0          # predicted speech per take (long takes drift)
_BATCH_WORKERS = 2
_CAND_GAP = 0.18           # shortest silence that may be a cut
_MIN_CUT_GAP = 0.28        # every chosen cut must be at least this long
_MAX_RATIO = 2.4           # a line may be at most this far off its prediction
_GAP_WEIGHT = 1.6          # how strongly long silences win
_BATCH_SEP = "\n\n<long pause>\n\n"


def _window_rms(a, rate, win_s=_SIL_WIN):
    win = max(1, int(rate * win_s))
    out = []
    for i in range(0, len(a), win):
        seg = a[i:i + win]
        if not seg:
            break
        out.append((sum(x * x for x in seg) / len(seg)) ** 0.5)
    return out, win


def split_take(wav_path, expected, rate_hint=_RATE):
    """Cut a batch take into len(expected) clips. `expected` = predicted
    speech seconds per line. Returns [(start_sample, end_sample)] or None
    when the take doesn't split cleanly."""
    n = len(expected)
    got = _read_wav16(wav_path)
    if not got or n < 1:
        return None
    a, rate = got
    if n == 1:
        return [(0, len(a))]
    rms, win = _window_rms(a, rate)
    if not rms:
        return None
    loud = sorted(rms)
    ref = loud[int(len(loud) * 0.9)] if loud else 0
    thr = max(_SIL_AMP, ref * 0.06)
    quiet = [v <= thr for v in rms]
    # voiced region of the whole take
    first = next((k for k, q in enumerate(quiet) if not q), None)
    last = next((k for k in range(len(quiet) - 1, -1, -1) if not quiet[k]), None)
    if first is None or last is None or last <= first:
        return None
    # candidate gaps: silent runs strictly inside the voiced region
    cands = []
    k = first
    while k <= last:
        if quiet[k]:
            j = k
            while j <= last and quiet[j]:
                j += 1
            length = (j - k) * win / rate
            if length >= _CAND_GAP:
                cands.append((k, j, length))     # [k, j) windows are silent
            k = j
        else:
            k += 1
    m = len(cands)
    if m < n - 1:
        return None
    # voiced seconds between two cut positions (silence excluded)
    pre = [0]
    for q in quiet:
        pre.append(pre[-1] + (0 if q else 1))

    def voiced(w0, w1):
        return max(0, pre[w1] - pre[w0]) * win / rate

    total_voiced = voiced(first, last + 1)
    exp_total = sum(max(0.2, e) for e in expected) or 1.0
    scale = total_voiced / exp_total

    import math

    def seg_cost(w0, w1, idx):
        d = voiced(w0, w1)
        e = max(0.2, expected[idx]) * scale
        if d <= 0.05:
            return 1e9, 99.0
        r = d / e
        return math.log(r) ** 2, r

    INF = float("inf")
    # dp[j][c]: best cost for lines 0..j with line j ending at candidate c
    dp = [[INF] * m for _ in range(n - 1)]
    back = [[-1] * m for _ in range(n - 1)]
    for c in range(m):
        cost, _r = seg_cost(first, cands[c][0], 0)
        dp[0][c] = cost - _GAP_WEIGHT * min(1.5, cands[c][2])
    for j in range(1, n - 1):
        for c in range(j, m):
            best, arg = INF, -1
            for p in range(j - 1, c):
                if dp[j - 1][p] == INF:
                    continue
                cost, _r = seg_cost(cands[p][1], cands[c][0], j)
                v = dp[j - 1][p] + cost
                if v < best:
                    best, arg = v, p
            if arg >= 0:
                dp[j][c] = best - _GAP_WEIGHT * min(1.5, cands[c][2])
                back[j][c] = arg
    best, arg = INF, -1
    for c in range(n - 2, m):
        if dp[n - 2][c] == INF:
            continue
        cost, _r = seg_cost(cands[c][1], last + 1, n - 1)
        v = dp[n - 2][c] + cost
        if v < best:
            best, arg = v, c
    if arg < 0:
        return None
    picks = [arg]
    for j in range(n - 2, 0, -1):
        picks.append(back[j][picks[-1]])
    picks.reverse()
    # validate: every cut a real pause, every line close to its prediction
    if min(cands[c][2] for c in picks) < _MIN_CUT_GAP:
        return None
    bounds = [first] + [x for c in picks for x in (cands[c][0], cands[c][1])] + [last + 1]
    for idx in range(n):
        _cost, r = seg_cost(bounds[2 * idx], bounds[2 * idx + 1], idx)
        if r > _MAX_RATIO or r < 1.0 / _MAX_RATIO:
            return None
    # cut in the middle of each chosen silence
    cuts = [0] + [((cands[c][0] + cands[c][1]) // 2) * win for c in picks] + [len(a)]
    return [(cuts[i], cuts[i + 1]) for i in range(n)]


def _batch_style(styles, lang_name, acting):
    """One delivery for a take: the most common cues of its lines."""
    if not acting:
        base = "natural, clear, conversational"
    else:
        words = {}
        for st in styles:
            for w in str(st or "").replace(";", ",").split(","):
                w = w.strip().lower()
                if w and not w.startswith("then"):
                    words[w] = words.get(w, 0) + 1
        top = [w for w, _n in sorted(words.items(), key=lambda kv: -kv[1])[:3]]
        base = ", ".join(top) if top else "natural and conversational"
    return (f"{base}. Native {lang_name} speaker, natural dubbing performance. These are "
            f"separate lines of one video: read each as its own take and leave a clear "
            f"two-second silence at every long pause").strip()


def plan_batches(ids, voice_of, text_of, seconds_of):
    """[(voice, [line ids])]: lines grouped per voice, in order, cut to the
    take limits. Single-line groups are left to the per-line path."""
    by = {}
    order = []
    for i in ids:
        v = voice_of(i)
        if v not in by:
            by[v] = []
            order.append(v)
        by[v].append(i)
    out = []
    for v in order:
        cur, chars, sec = [], 0, 0.0
        for i in by[v]:
            t, d = len(text_of(i)), seconds_of(i)
            if cur and (len(cur) >= _BATCH_LINES or chars + t > _BATCH_CHARS or sec + d > _BATCH_SEC):
                out.append((v, cur))
                cur, chars, sec = [], 0, 0.0
            cur.append(i)
            chars += t
            sec += d
        if cur:
            out.append((v, cur))
    return [(v, g) for v, g in out if len(g) >= 2]


def plan_timing(starts, durs, total, max_drift=_MAX_DRIFT, gap=_GAP,
                max_tempo=_MAX_TEMPO, hard_tempo=_HARD_TEMPO):
    """v1.4.4: place every line on the timeline at once.

    starts[i] = where the original says it, durs[i] = clean take length
    (None = no take). Returns [(at, tempo, trim_to)] (None for no take):
    trim_to = 0 means "no trim". Rules, in order of preference:
      1. play at 1.0x where the original said it;
      2. lean up to `max_drift` s into the next line's slot (the next line
         then starts that much later, never more than `max_drift` late);
      3. speed up to `max_tempo`, then to `hard_tempo`;
      4. only then trim (with a short fade).
    The last line never runs past the end of the video."""
    n = len(starts)
    out = [None] * n
    cursor = 0.0
    nxt_voiced = [None] * n
    nv = None
    for i in range(n - 1, -1, -1):
        nxt_voiced[i] = nv
        if durs[i]:
            nv = starts[i]
    end_cap = max(0.3, total - 0.04)
    for i in range(n):
        d = durs[i]
        if not d:
            continue
        at = min(max(starts[i], cursor), max(0.0, end_cap - 0.25))
        nxt = nxt_voiced[i]
        deadline = end_cap if nxt is None else min(end_cap, nxt + max_drift)
        room = max(0.25, deadline - at - gap)
        need = d / room
        if need <= 1.0:
            tempo, trim = 1.0, 0.0
        elif need <= max_tempo + 1e-6:
            tempo, trim = need, 0.0
        elif need <= hard_tempo + 1e-6:
            tempo, trim = need, 0.0
        else:
            tempo, trim = hard_tempo, room
        # a tiny speed-up is inaudible but a tiny one rounds badly: snap
        if tempo < 1.01:
            tempo = 1.0
        length = min(d / tempo, room) if trim else d / tempo
        out[i] = (round(at, 3), round(tempo, 4), round(trim, 3))
        cursor = at + length + gap
    return out


def _needs_render(tempo, trim):
    return tempo > 1.005 or bool(trim and trim > 0)


def _render_clip(src_wav, dst_wav, tempo, trim, should_cancel=None):
    """One planned clip: tempo (atempo keeps pitch) and the rare trim.

    v1.4.5: nothing to do -> the take is copied (no process); trim only ->
    cut + faded in memory; only a real speed-up runs ffmpeg."""
    if not _needs_render(tempo, trim):
        if os.path.abspath(src_wav) != os.path.abspath(dst_wav):
            shutil.copyfile(src_wav, dst_wav)
        return R.wav_duration(dst_wav)
    if tempo <= 1.005:
        got = _read_wav16(src_wav)
        if got and got[1] == _RATE:
            a, rate = got
            out = _trim_fade(a, rate, trim)
            _write_wav16(dst_wav, out, rate)
            return len(out) / float(rate)
    filt = []
    if tempo > 1.005:
        filt.append(f"atempo={tempo:.4f}")
    if trim and trim > 0:
        fade = min(0.12, trim * 0.2)
        filt.append(f"atrim=0:{trim:.3f}")
        filt.append(f"afade=t=out:st={max(0.0, trim - fade):.3f}:d={fade:.3f}")
    args = ["-i", src_wav]
    if filt:
        args += ["-af", ",".join(filt)]
    ok, err = R.run(args + [*_PCM_OUT, dst_wav], 0, None, should_cancel)
    if not ok:
        raise RuntimeError("Timing fit failed: " + err)
    return R.wav_duration(dst_wav)


def _assemble(clips, total, out_wav):
    """clips = [(start_sec, wav_path)] in time order, non-overlapping →
    one mono 24 kHz track of `total` seconds (bytes only, no per-sample work)."""
    import wave
    frame = 2
    total_frames = int(total * _RATE)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        pos = 0
        for start, path in clips:
            at = int(start * _RATE)
            if at > pos:
                w.writeframes(b"\x00" * ((at - pos) * frame))
                pos = at
            try:
                pcm, rate = G.read_pcm(path)
            except Exception as e:       # noqa: BLE001
                G._log(f"assemble: skipped unreadable clip {os.path.basename(path)}: {e!r}")
                continue
            if rate != _RATE:
                G._log(f"assemble: skipped {os.path.basename(path)} at {rate} Hz")
                continue
            n = len(pcm) // frame
            if pos > at:                  # overlap guard: skip what's already written
                skip = pos - at
                pcm = pcm[skip * frame:]
                n -= skip
            if n <= 0:
                continue
            if pos + n > total_frames:
                n = max(0, total_frames - pos)
                pcm = pcm[:n * frame]
            w.writeframes(pcm)
            pos += n
        if total_frames > pos:
            w.writeframes(b"\x00" * ((total_frames - pos) * frame))


# ── pipeline ─────────────────────────────────────────────────────────────

def dub_media(media, cues, src_lang, tgt_lang, out_path, work, opts=None, report=None,
              should_cancel=None, title=""):
    """Full dub. `report(stage_text, fraction)`. Returns {video, wav, srt}."""
    opts = dict(opts or {})
    report = report or (lambda s, p: None)
    os.makedirs(work, exist_ok=True)
    # file-level cache shared by every dub of this file (jobs.py puts each
    # setting combo in a sub-folder of the file's folder)
    shared = opts.get("shared_dir") or os.path.dirname(os.path.abspath(work))
    os.makedirs(shared, exist_ok=True)
    t0 = time.monotonic()
    snap = B.snapshot()
    saver = B.saver_on() if "saver" not in opts else bool(opts.get("saver"))
    # v1.5: the voices come from Gemini (default) or ElevenLabs
    engine = opts.get("voice_engine") or E.engine()
    if engine not in ("gemini", "elevenlabs"):
        engine = E.engine()
    # v1.6: ElevenLabs picked but no key is a clear stop, never a silent
    # switch to Gemini voices (that read as "it keeps using Gemini")
    if engine == "elevenlabs" and not E.ready():
        raise G.GeminiFatal("ElevenLabs voices are picked for this dub, but there is no ElevenLabs "
                            "key. Add it in Settings → Captions & Dub, or pick Gemini voices.",
                            "key", 0, "elevenlabs")
    eleven = engine == "elevenlabs"
    if eleven:
        # Gemini is optional here: it still listens and adapts when it is set
        # up, the voices themselves come from ElevenLabs
        try:
            creds = G.credentials(feature="dub")
        except (G.GeminiUnavailable, RuntimeError):
            creds = None
    else:
        creds = G.credentials(feature="dub")
    # v1.7: the script (translate + adapt + tighten) has its own pick in
    # Settings → Captions & Dub → Script. Default: Gemini, else the Captions AI.
    text_client = None
    if T.ai_ready("script"):
        try:
            text_client = T._client("script")
        except Exception:                # noqa: BLE001 - same language still works
            text_client = None
    info = R.probe(media)
    total = info["duration"] or (cues[-1].end + 1 if cues else 0)
    preview = float(opts.get("preview_sec") or 0)
    if preview > 0:
        cues = [c for c in cues if c.start < preview]
        total = min(total, preview)
    cues = speakable(cues)
    if not cues:
        raise RuntimeError("Nothing to dub: no spoken lines were found.")
    acting = bool(opts.get("acting", True))
    model = opts.get("tts_model") or G.TTS_MODELS[0]["key"]
    cancel = should_cancel or (lambda: False)

    # 1. performance
    report("Listening to the performance", 0.02)
    perf = {"speakers": {}, "lines": {}}
    heard = perf_from_cues(cues)
    if heard is not None and (acting or opts.get("voice_mode", "auto") == "auto"):
        # v1.5: the transcription already heard who speaks and how
        perf = heard
    elif (creds is not None and (acting or opts.get("voice_mode", "auto") == "auto")
          and not (eleven and saver)):
        # v1.6: with ElevenLabs voices + the quota saver, Gemini isn't asked
        # to read the performance; the voice itself tells who speaks (below)
        perf = read_performance(media, cues, shared, creds,
                                lambda p: report("Listening to the performance", 0.02 + 0.13 * p),
                                cancel)
    # v1.6: speakers and genders from the voice itself (pitch), free and
    # offline. Fills what the performance read didn't say: a man and a woman
    # on YouTube captions get two voices, and a woman a female voice.
    perf = P.enrich(media, cues, perf, shared, cancel)
    utts = build_utterances(cues, perf, acting)
    speakers = perf.get("speakers") or {}
    for u in utts:
        if u.speaker and u.speaker not in speakers:
            speakers[u.speaker] = {"id": u.speaker}
    if not speakers:
        speakers = {"": {"id": ""}}
    if eleven:
        pick = str(opts.get("voice") or "")
        single = pick if pick and pick not in G.VOICE_NAMES else E.default_voice()
        weights = {}
        for u in utts:
            weights[u.speaker] = weights.get(u.speaker, 0.0) + max(0.1, u.end - u.start)
        if opts.get("voice_mode") == "single" and pick and pick not in G.VOICE_NAMES:
            # a voice hand-picked in the Voice Studio: exactly that one
            voices = {sid: pick for sid in (speakers or {"": {}})}
        else:
            voices = E.cast(speakers, opts.get("voice_mode", "auto"), single, weights=weights)
    else:
        voices = cast_voices(speakers, opts.get("voice_mode", "auto"), opts.get("voice") or "Kore")
    default_voice = next(iter(voices.values()))

    # 3. adaptation (cached at the file level: changing voices or the TTS
    #    model never pays for the translation again)
    report("Adapting the script", 0.15)
    utt_sig = _sha(tgt_lang, int(acting), *[f"{u.start:.2f}|{u.end:.2f}|{u.speaker}|{u.text}" for u in utts])
    tr_cache = os.path.join(shared, f"script_{tgt_lang}_{utt_sig}.json")
    lines = None
    if os.path.isfile(tr_cache):
        try:
            with open(tr_cache, encoding="utf-8") as f:
                lines = C.load_json(f.read())
        except Exception:                # noqa: BLE001
            lines = None
        if lines is not None and len(lines) != len(utts):
            lines = None
    if lines is None:
        if src_lang and src_lang.split("-")[0] == tgt_lang.split("-")[0]:
            lines = [u.copy() for u in utts]
        else:
            # v1.4.12: YouTube's auto captions (no punctuation) are read as
            # speech, so the dub script comes out as fluent sentences
            lines, _brief = T.translate_cues(utts, tgt_lang, src_lang, title, mode="dub",
                                             on_progress=lambda p: report("Adapting the script", 0.15 + 0.17 * p),
                                             should_cancel=cancel, cache_dir=shared,
                                             raw_asr=C.looks_raw_asr(cues), client=text_client)
        for a, b in zip(lines, utts):
            a.speaker, a.meta = b.speaker, dict(a.meta, style=b.meta.get("style", ""))

        # 3b. tighten what will clearly run long, in one batched call, BEFORE
        #     paying for any voice
        def slot(i):
            nxt = lines[i + 1].start if i + 1 < len(lines) else total
            return max(0.3, min(nxt, total) - lines[i].start - 0.06)
        rate = T.speak_rate(tgt_lang)
        long_ones = [(i, lines[i].text, slot(i)) for i in range(len(lines))
                     if slot(i) > 0.8 and len(G._strip_tags(lines[i].text)) / rate > slot(i) * _PRE_FIT]
        if long_ones:
            report("Tightening long lines", 0.33)
            try:
                short = T.shorten_lines(long_ones, tgt_lang, client=text_client, should_cancel=cancel)
            except Exception:            # noqa: BLE001 — timing fit still copes
                short = {}
            for i, x in short.items():
                if x and len(x) < len(lines[i].text):
                    lines[i] = lines[i].copy(text=x)
        if cancel():
            raise R.Cancelled()
        with open(tr_cache, "w", encoding="utf-8") as f:
            f.write(C.dump_json(lines))

    # 4. voice (clips cached by content at the file level: resumable, and
    #    shared between preview / full run / setting changes)
    tts_dir = os.path.join(shared, "tts")
    fit_dir = os.path.join(work, "fit")
    os.makedirs(tts_dir, exist_ok=True)
    os.makedirs(fit_dir, exist_ok=True)
    lang_name = L.name_of(tgt_lang)
    done = [0]
    lock = threading.Lock()
    quota = []                             # first QuotaExhausted seen
    fatal = []                             # v1.4.5: first GeminiFatal seen

    def style_for(u):
        base = u.meta.get("style") or ("natural and conversational" if acting else "")
        if not acting:
            return "natural, clear, conversational"
        return f"{base}. Native {lang_name} speaker, natural dubbing performance".strip(". ")

    e_model = E.model() if eleven else ""
    voice_model = ("11:" + e_model) if eleven else model

    def clip_path(idx, text=None):
        u = lines[idx]
        txt = text or u.text
        if not acting:
            txt = G._strip_tags(txt)
        voice = voices.get(u.speaker, default_voice)
        sty = style_for(u)
        return txt, voice, sty, os.path.join(tts_dir, _sha(txt, voice, sty, voice_model) + ".wav")

    def speak(idx, text=None):
        txt, voice, sty, path = clip_path(idx, text)
        if not os.path.isfile(path):
            tmp = f"{path}.{threading.get_ident()}.part"
            if eleven:
                E.tts(txt, tmp, voice, e_model, tgt_lang, sty, cancel)
            else:
                G.tts(creds, txt, tmp, voice, sty, model, tgt_lang, cancel)
            os.replace(tmp, path)
        return path

    rate = T.speak_rate(tgt_lang)

    def speak_batch(voice, ids):
        """v1.5: one TTS request for many lines of one voice. Returns the
        ids that were recorded; the rest go to the per-line path."""
        items = [clip_path(i) for i in ids]
        text = _BATCH_SEP.join(it[0] for it in items)
        sty = _batch_style([lines[i].meta.get("style", "") for i in ids], lang_name, acting)
        key = _sha("batch", text, voice, sty, model)
        take = os.path.join(tts_dir, "batch_" + key + ".wav")
        if not os.path.isfile(take):
            tmp = f"{take}.{threading.get_ident()}.part"
            G.tts(creds, text, tmp, voice, sty, model, tgt_lang, cancel,
                  timeout=max(120, 30 + 4 * len(ids)))
            os.replace(tmp, take)
        expected = [max(0.3, len(G._strip_tags(it[0])) / rate) for it in items]
        parts = split_take(take, expected)
        if not parts:
            G._log(f"batch take of {len(ids)} lines didn't split cleanly; recording them one by one")
            return []
        got = _read_wav16(take)
        if not got:
            return []
        a, r = got
        for (s0, s1), it in zip(parts, items):
            seg = a[s0:s1]
            tmp = f"{it[3]}.{threading.get_ident()}.part"
            _write_wav16(tmp, seg, r)
            os.replace(tmp, it[3])
        return list(ids)

    raw = [None] * len(lines)
    errors = []
    todo = []
    for i in range(len(lines)):
        p = clip_path(i)[3]
        if os.path.isfile(p):
            raw[i] = p                     # recorded in an earlier run: free
        else:
            todo.append(i)
    n_all = len(lines)
    done[0] = n_all - len(todo)

    def rec_label():
        return f"Recording voices {done[0]}/{n_all}"

    report(rec_label(), 0.36 + 0.46 * done[0] / max(1, n_all))

    def job(i):
        if cancel() or quota or fatal:
            return
        try:
            raw[i] = speak(i)
        except G.GeminiFatal as f:
            # bad key / no network: every other line would fail the same way
            with lock:
                if not fatal:
                    fatal.append(f)
                    G._log(f"voice recording stopped at line {i}: {f}")
            return
        except G.QuotaExhausted as q:
            with lock:
                if not quota:
                    quota.append(q)
        except Exception as e:           # noqa: BLE001
            errors.append(str(e))
        with lock:
            done[0] += 1
            report(rec_label(), 0.36 + 0.46 * done[0] / max(1, n_all))

    # v1.5 quota saver: one take per voice first (Gemini voices only;
    # ElevenLabs bills characters, so it gains nothing from batching)
    if todo and saver and not eleven and creds is not None:
        groups = plan_batches(todo, lambda i: clip_path(i)[1], lambda i: clip_path(i)[0],
                              lambda i: len(G._strip_tags(clip_path(i)[0])) / rate)

        def batch_job(g):
            voice, ids = g
            if cancel() or quota or fatal:
                return
            try:
                ok_ids = speak_batch(voice, ids)
            except G.GeminiFatal as f:
                with lock:
                    if not fatal:
                        fatal.append(f)
                return
            except G.QuotaExhausted as q:
                with lock:
                    if not quota:
                        quota.append(q)
                return
            except Exception as e:       # noqa: BLE001 - per-line path takes over
                G._log(f"batch take failed ({e!r}); recording its lines one by one")
                return
            with lock:
                for i in ok_ids:
                    raw[i] = clip_path(i)[3]
                done[0] += len(ok_ids)
                report(rec_label(), 0.36 + 0.46 * done[0] / max(1, n_all))

        if groups:
            report("Recording voices (one take per voice)", 0.36 + 0.46 * done[0] / max(1, n_all))
            with cf.ThreadPoolExecutor(max_workers=_BATCH_WORKERS) as ex:
                list(ex.map(batch_job, groups))
        todo = [i for i in todo if not raw[i]]
    if todo and not quota and not fatal:
        with cf.ThreadPoolExecutor(max_workers=2 if eleven else _TTS_WORKERS) as ex:
            list(ex.map(job, todo))
    # v1.4.4: a line that failed (timeout, a busy server) gets one calmer
    # second try before it is left as a gap in the dub
    retry = [i for i in todo if not raw[i]]
    if retry and not quota and not fatal and not cancel():
        done[0] -= len(retry)
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(job, retry))
    if cancel():
        raise R.Cancelled()
    if fatal:
        raise fatal[0]
    voiced = sum(1 for r in raw if r)
    if quota:
        if eleven:
            raise QuotaStop(
                f"ElevenLabs credits ran out after {voiced} of {n_all} lines. Everything recorded is "
                f"saved: top up (or switch to Gemini voices in Settings → Captions & Dub) and press "
                f"Resume; only the {n_all - voiced} missing lines are recorded.")
        raise QuotaStop(
            f"Gemini's voice quota ran out after {voiced} of {n_all} lines. Everything recorded is "
            f"saved: press Resume later (or switch to the Fast voice / another key / ElevenLabs) "
            f"and only the {n_all - voiced} missing lines are recorded.")
    if not voiced:
        raise RuntimeError(errors[-1] if errors else "No voice lines were produced.")
    if n_all - voiced > max(1, int(n_all * _MAX_FAILED)):
        raise RuntimeError(
            f"Only {voiced} of {n_all} lines could be voiced ({errors[-1] if errors else 'unknown error'}). "
            f"The recorded lines are saved: press Resume to record the rest.")

    # 5. fit (v1.4.4 tight sync): clean every take, re-write what still
    #    can't fit even when sped up and leaning into the next line, then
    #    plan the whole timeline at once and render each clip to its plan
    report("Fitting the timing", 0.82)
    tight = [None] * len(lines)
    tdur = [None] * len(lines)

    def prep(i):
        if raw[i] and not cancel():
            tight[i], tdur[i] = _prep_clip(raw[i], cancel)

    with cf.ThreadPoolExecutor(max_workers=_FIT_WORKERS) as ex:
        list(ex.map(prep, range(len(lines))))
    if cancel():
        raise R.Cancelled()

    def slot_of(i):
        nxt = next((lines[j].start for j in range(i + 1, len(lines)) if tdur[j]), total)
        return max(0.3, min(nxt + _MAX_DRIFT, total) - lines[i].start - _GAP)

    redo = [(i, lines[i].text, slot_of(i)) for i in range(len(lines))
            if tdur[i] and slot_of(i) > 0.8 and tdur[i] > slot_of(i) * _MAX_TEMPO + 0.15]
    if saver:
        # v1.5: a rewrite + a fresh recording per long line is the most
        # expensive fix there is; the planner speeds it up / fits it instead
        redo = []
    if redo:
        try:
            short = T.shorten_lines(redo, tgt_lang, client=text_client, should_cancel=cancel)
        except Exception:                # noqa: BLE001
            short = {}

        def redo_one(item):
            i, x = item
            if not x or x == lines[i].text or cancel():
                return
            try:
                p = speak(i, x)
                tp, td = _prep_clip(p, cancel)
                if td < tdur[i]:                   # only keep a take that is shorter
                    raw[i], tight[i], tdur[i] = p, tp, td
                    lines[i] = lines[i].copy(text=x)
            except (R.Cancelled, G.GeminiFatal):
                raise
            except Exception as e:       # noqa: BLE001 — keep the long take
                G._log(f"re-record of long line {i} failed, keeping the long take: {e!r}")
        with cf.ThreadPoolExecutor(max_workers=_TTS_WORKERS) as ex:
            list(ex.map(redo_one, list(short.items())))

    plan = plan_timing([u.start for u in lines], tdur, total)
    clips = [None] * len(lines)

    def render_one(i):
        if plan[i] is None or cancel():
            return
        at, tempo, trim = plan[i]
        if not _needs_render(tempo, trim):
            clips[i] = (at, tight[i])      # v1.4.5: the clean take as is
            return
        dst = os.path.join(fit_dir, f"{i:05d}.wav")
        _render_clip(tight[i], dst, tempo, trim, cancel)
        clips[i] = (at, dst)

    with cf.ThreadPoolExecutor(max_workers=_FIT_WORKERS) as ex:
        list(ex.map(render_one, range(len(lines))))
    if cancel():
        raise R.Cancelled()
    clips = [c for c in clips if c]
    clips.sort(key=lambda c: c[0])
    trimmed = sum(1 for p in plan if p and p[2])
    dub_wav = os.path.join(work, "dub_track.wav")
    _assemble(clips, total, dub_wav)

    # 6–7. mix + mux
    report("Mixing", 0.86)
    bg = opts.get("bg_mode", "duck")
    has_orig = info["has_audio"]
    keep_orig = bool(opts.get("keep_original", True)) and has_orig
    # v1.4.4: loudnorm works at 192 kHz inside: back to 48 kHz, and the dub
    # stream is padded / cut to exactly the video's length
    dub_chain = (f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                 f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                 f"apad,atrim=0:{total:.3f}")
    if not has_orig or bg == "replace":
        fc = dub_chain + "[mix]"
    elif bg == "bed":
        fc = (dub_chain + "[dub];[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
              "volume=0.14[bed];[bed][dub]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        fc = (dub_chain + ",asplit=2[dub][sc];"
              "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[orig];"
              "[orig][sc]sidechaincompress=threshold=0.012:ratio=20:attack=12:release=380:knee=4[duck];"
              "[duck][dub]amix=inputs=2:duration=first:normalize=0[mix]")
    iso = L.iso3(tgt_lang)
    maps = ["-map", "0:v:0?", "-map", "[mix]"] + (["-map", "0:a:0"] if keep_orig else [])
    a_args = ["-c:a", "aac", "-b:a", "192k",
              "-metadata:s:a:0", f"language={iso}",
              "-metadata:s:a:0", f"title={lang_name} (AI dub)", "-disposition:a:0", "default"]
    if keep_orig:
        a_args += ["-metadata:s:a:1", f"language={L.iso3(src_lang) if src_lang else 'und'}",
                   "-metadata:s:a:1", "title=Original", "-disposition:a:1", "0"]
    t_args = ["-t", f"{total:.3f}"] if preview > 0 else []
    progress = (lambda p: report("Mixing", 0.86 + 0.13 * p))

    script = [c.copy(text=G._strip_tags(c.text)) for c in lines]
    script = C.split_long(script, 84, 7.0)
    burn = opts.get("burn_style") or ""
    # v1.4: captions made by the job before this one (e.g. YouTube's own
    # track, or the original language under the dub) are burned in THIS
    # encode instead of a second one
    burn_cues, burn_lang = script, tgt_lang
    bc = opts.get("burn_cues_json") or ""
    if burn and bc and os.path.isfile(bc):
        try:
            with open(bc, encoding="utf-8") as f:
                loaded = C.load_json(f.read())
            if loaded:
                burn_cues = loaded
                burn_lang = opts.get("burn_lang") or tgt_lang
        except Exception:                # noqa: BLE001 — fall back to the dub's lines
            burn_cues, burn_lang = script, tgt_lang
    if burn and info["has_video"] and R.has_filter("subtitles"):
        from app.core.captions import styles as S
        if preview > 0:
            burn_cues = [c for c in burn_cues if c.start < preview]
        ass = S.build_ass(burn_cues, burn, burn_lang, info["width"], info["height"],
                          opts.get("size", "m"), opts.get("position", "bottom"))
        ok, err = R.burn(media, ass, out_path, os.path.join(work, "burn"), total, progress, cancel,
                         extra_inputs=[["-i", os.path.abspath(dub_wav)]],
                         map_args=["-filter_complex", fc] + maps,
                         audio_args=a_args + t_args)
    else:
        # v1.4.7: keep any subtitle track the source carries (a dub stacked
        # on soft captions used to drop them). Only when the container stays
        # the same, so the track can always be copied as it is.
        s_args = []
        if os.path.splitext(media)[1].lower() == os.path.splitext(out_path)[1].lower():
            s_args = ["-map", "0:s?", "-c:s", "copy"]
        args = ["-i", os.path.abspath(media), "-i", os.path.abspath(dub_wav),
                "-filter_complex", fc, *maps, *s_args, "-c:v", "copy", *a_args, *t_args]
        if R.out_container(out_path) in ("mp4", "m4v", "mov"):
            args += ["-movflags", "+faststart"]
        args += [os.path.abspath(out_path)]
        ok, err = R.run(args, total, progress, cancel)
    if not ok:
        raise RuntimeError("Mixing failed: " + err)

    base = os.path.splitext(out_path)[0]
    srt_path = base + ".srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(C.to_srt(script, L.line_limit(tgt_lang)))
    wav_out = base + ".voice.wav"
    try:
        shutil.copyfile(dub_wav, wav_out)
    except OSError:
        wav_out = ""
    report("Done", 1.0)
    failed = sum(1 for r in raw if not r)
    notes = []
    if failed:
        notes.append(f"{failed} of {len(lines)} lines couldn't be voiced; the original plays there "
                     f"(Resume records them)")
    if trimmed:
        notes.append(f"{trimmed} long line(s) were shortened to stay in sync")
    used = B.since(snap)
    if eleven:
        names = ", ".join(dict.fromkeys(E.voice_name(v) for v in voices.values()))
        notes.append(f"voiced with ElevenLabs ({names})"
                     + (f" · {used} Gemini request(s) for listening / the script" if used
                        else " · no Gemini requests"))
    else:
        notes.append(f"{used} Gemini request(s)" if used else "no Gemini requests (all cached)")
    return {"video": out_path, "wav": wav_out, "srt": srt_path,
            "voices": voices, "lines": len(lines), "failed_lines": failed,
            "trimmed_lines": trimmed, "note": " · ".join(notes),
            "seconds": round(time.monotonic() - t0, 1)}
