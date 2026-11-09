"""app.core.captions.pitch - YouTube Hunter.

v1.6: who is speaking, from the voice itself. No API, no request, no quota.

The dub needs two things to cast voices well:
  * how many people talk, and which line is whose;
  * whether each of them is a man or a woman.

Gemini's performance read answers both, but it costs requests, and when the
words come from YouTube's captions (no speaker labels) and Gemini isn't used
(ElevenLabs voices with the quota saver on, or no Gemini key) nobody did.
The result was one male voice for a whole interview between a man and a
woman.

This module reads the original audio once (ffmpeg -> 4 kHz mono PCM) and
estimates each cue's fundamental frequency (F0) with a normalised
autocorrelation on its loudest frames:

    male voices    ~ 85-155 Hz       female voices  ~ 165-255 Hz

  * cues that already carry speaker ids only get a gender per speaker
    (the median F0 of that speaker's cues);
  * cues with no ids are split into two speakers when their F0 falls into
    two clearly separate groups (1-D 2-means, octave scale), so a man and a
    woman get two voices. Two people of the same gender and similar pitch
    stay one speaker: that is the honest limit of pitch alone.

Stdlib only (numpy is used when it happens to be importable). The per-cue F0
list is cached next to the dub's other files, so a second dub of the same
file costs nothing. Everything here is best effort: any failure returns the
performance unchanged and the dub carries on as before.
"""

import hashlib
import json
import math
import operator
import os
import subprocess

_RATE = 4000                  # Hz: plenty for 70-400 Hz voices, tiny to scan
_FRAME = 160                  # 40 ms
_LAG_MIN = _RATE // 400       # 400 Hz
_LAG_MAX = _RATE // 70        # 70 Hz
_MAX_CUES = 300               # sampled evenly on very long videos
_FRAMES_PER_CUE = 6
_VOICED_R = 0.45              # normalised autocorrelation needed to call a frame voiced
_FEMALE_HZ = 160.0            # split point male / female
_MIN_SPLIT_OCT = 0.42         # two groups this far apart (≈ x1.34) are two people

try:                          # optional speed-up, never required
    import numpy as _np       # noqa: F401
except Exception:             # noqa: BLE001
    _np = None


def _log(msg):
    try:
        from app.core.captions import gemini_media as G
        G._log("pitch: " + msg)
    except Exception:         # noqa: BLE001
        pass


# ── audio ────────────────────────────────────────────────────────────────

def _read_pcm(media, timeout=900):
    """The whole track as 4 kHz mono signed 16-bit samples (array('h'))."""
    import array
    from app.core.captions import render as R
    cmd = [R.ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", media,
           "-vn", "-ac", "1", "-af", "highpass=f=60,lowpass=f=900",
           "-ar", str(_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000          # CREATE_NO_WINDOW
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=timeout, **kw)
    a = array.array("h")
    a.frombytes(p.stdout[: len(p.stdout) // 2 * 2])
    return a


def _frame_f0(x):
    """F0 of one frame (list of floats, _FRAME + _LAG_MAX long) or None."""
    n = _FRAME
    head = x[:n]
    e0 = sum(map(operator.mul, head, head))
    if e0 <= 1e-6:
        return None
    best_l, best_r, rs = 0, 0.0, {}
    for lag in range(_LAG_MIN, _LAG_MAX + 1):
        tail = x[lag:lag + n]
        num = sum(map(operator.mul, head, tail))
        el = sum(map(operator.mul, tail, tail))
        if el <= 1e-6:
            continue
        r = num / math.sqrt(e0 * el)
        rs[lag] = r
        if r > best_r:
            best_l, best_r = lag, r
    if best_r < _VOICED_R or not best_l:
        return None
    # octave guard: the SHORTEST lag that is nearly as good wins (a female
    # voice also correlates at twice its period)
    for lag in range(_LAG_MIN, best_l):
        r = rs.get(lag, 0.0)
        if r >= 0.86 * best_r and rs.get(lag - 1, 0.0) <= r >= rs.get(lag + 1, 0.0):
            best_l, best_r = lag, r
            break
    # parabolic refinement around the peak
    a, b, c = rs.get(best_l - 1), best_r, rs.get(best_l + 1)
    lag = float(best_l)
    if a is not None and c is not None:
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            lag += max(-0.5, min(0.5, 0.5 * (a - c) / d))
    f0 = _RATE / lag
    return f0 if 60.0 <= f0 <= 420.0 else None


def _frame_f0_np(x):
    np = _np
    x = np.asarray(x, dtype=np.float64)
    n = _FRAME
    head = x[:n]
    e0 = float(head @ head)
    if e0 <= 1e-6:
        return None
    lags = np.arange(_LAG_MIN, _LAG_MAX + 1)
    tails = np.stack([x[l:l + n] for l in lags])
    num = tails @ head
    el = np.einsum("ij,ij->i", tails, tails)
    ok = el > 1e-6
    r = np.where(ok, num / np.sqrt(e0 * np.maximum(el, 1e-6)), 0.0)
    rs = {int(l): float(v) for l, v in zip(lags, r)}
    bi = int(np.argmax(r))
    best_l, best_r = int(lags[bi]), float(r[bi])
    if best_r < _VOICED_R:
        return None
    for lag in range(_LAG_MIN, best_l):
        v = rs.get(lag, 0.0)
        if v >= 0.86 * best_r and rs.get(lag - 1, 0.0) <= v >= rs.get(lag + 1, 0.0):
            best_l, best_r = lag, v
            break
    a, b, c = rs.get(best_l - 1), best_r, rs.get(best_l + 1)
    lag = float(best_l)
    if a is not None and c is not None:
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            lag += max(-0.5, min(0.5, 0.5 * (a - c) / d))
    f0 = _RATE / lag
    return f0 if 60.0 <= f0 <= 420.0 else None


def _cue_f0(pcm, start, end):
    """Median F0 of a cue's loudest voiced frames, or None."""
    s = max(0, int(start * _RATE))
    e = min(len(pcm) - _LAG_MAX - 1, int(end * _RATE))
    if e - s < _FRAME * 2:
        return None
    # candidate frames on a 40 ms grid, the loudest first
    starts = list(range(s, e - _FRAME, _FRAME))
    if len(starts) > 60:                       # long cue: look at most ~2.4 s of it
        step = len(starts) / 60.0
        starts = [starts[int(i * step)] for i in range(60)]
    loud = []
    for f in starts:
        seg = pcm[f:f + _FRAME:4]
        loud.append((sum(abs(v) for v in seg), f))
    loud.sort(reverse=True)
    fn = _frame_f0_np if _np is not None else _frame_f0
    vals = []
    for _en, f in loud[: _FRAMES_PER_CUE * 2]:
        x = [float(v) for v in pcm[f:f + _FRAME + _LAG_MAX + 1]]
        v = fn(x)
        if v:
            vals.append(v)
        if len(vals) >= _FRAMES_PER_CUE:
            break
    if len(vals) < 2:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def cue_pitches(media, cues, cache_dir="", should_cancel=None):
    """[F0 Hz or None] per cue, cached in `cache_dir`."""
    try:
        st = os.stat(media)
        sig = hashlib.sha1(("|".join([os.path.abspath(media), str(st.st_size), str(int(st.st_mtime))]
                                     + [f"{c.start:.2f}-{c.end:.2f}" for c in cues])).encode()).hexdigest()[:16]
    except OSError:
        sig = ""
    path = os.path.join(cache_dir, f"pitch_{sig}.json") if cache_dir and sig else ""
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, list) and len(got) == len(cues):
                return got
        except Exception:     # noqa: BLE001
            pass
    pcm = _read_pcm(media)
    if len(pcm) < _RATE:
        return [None] * len(cues)
    idx = list(range(len(cues)))
    if len(idx) > _MAX_CUES:
        step = len(idx) / float(_MAX_CUES)
        idx = sorted({int(i * step) for i in range(_MAX_CUES)})
    out = [None] * len(cues)
    for i in idx:
        if should_cancel and should_cancel():
            break
        c = cues[i]
        try:
            out[i] = _cue_f0(pcm, float(c.start), float(c.end))
        except Exception:     # noqa: BLE001
            out[i] = None
    if path and not (should_cancel and should_cancel()):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f)
        except OSError:
            pass
    return out


# ── speakers ─────────────────────────────────────────────────────────────

def gender_of(hz):
    if not hz:
        return ""
    return "female" if hz >= _FEMALE_HZ else "male"


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def split_two(f0s):
    """[0 | 1 | None] per cue when the voiced cues fall into two clearly
    separate pitch groups (1 = the higher voice), else None."""
    pts = [(i, math.log2(h)) for i, h in enumerate(f0s) if h]
    if len(pts) < 6:
        return None
    vals = sorted(v for _i, v in pts)
    m1, m2 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
    if m2 - m1 < 1e-3:
        return None
    for _ in range(25):
        lo = [v for v in vals if abs(v - m1) <= abs(v - m2)]
        hi = [v for v in vals if abs(v - m1) > abs(v - m2)]
        if not lo or not hi:
            return None
        n1, n2 = sum(lo) / len(lo), sum(hi) / len(hi)
        if abs(n1 - m1) < 1e-4 and abs(n2 - m2) < 1e-4:
            break
        m1, m2 = n1, n2
    lo = [v for v in vals if abs(v - m1) <= abs(v - m2)]
    hi = [v for v in vals if abs(v - m1) > abs(v - m2)]
    small = min(len(lo), len(hi))
    if small < max(2, int(0.12 * len(vals))):
        return None
    if m2 - m1 < _MIN_SPLIT_OCT:
        return None
    var = (sum((v - m1) ** 2 for v in lo) + sum((v - m2) ** 2 for v in hi)) / max(1, len(vals) - 2)
    if (m2 - m1) / max(1e-3, math.sqrt(var)) < 2.4:
        return None
    lab = [None] * len(f0s)
    for i, v in pts:
        lab[i] = 0 if abs(v - m1) <= abs(v - m2) else 1
    return lab


def _fill_gaps(labels, cues):
    """Unvoiced / unsampled cues take the label of the nearest cue in time."""
    known = [(float(cues[i].start), lab) for i, lab in enumerate(labels) if lab is not None]
    if not known:
        return labels
    out = list(labels)
    for i, lab in enumerate(out):
        if lab is None:
            t = float(cues[i].start)
            out[i] = min(known, key=lambda k: abs(k[0] - t))[1]
    return out


def enrich(media, cues, perf, cache_dir="", should_cancel=None):
    """Give the performance read speakers and genders from the voice
    itself. Returns a NEW perf dict ({"speakers", "lines"}); the input is
    never modified. Best effort: returns the input on any failure."""
    try:
        return _enrich(media, cues, perf or {}, cache_dir, should_cancel)
    except Exception as e:    # noqa: BLE001
        _log(f"skipped ({e!r})")
        return perf or {"speakers": {}, "lines": {}}


def _enrich(media, cues, perf, cache_dir, should_cancel):
    speakers = {str(k): dict(v or {}) for k, v in (perf.get("speakers") or {}).items()}
    lines = {str(k): dict(v or {}) for k, v in (perf.get("lines") or {}).items()}
    out = {"speakers": speakers, "lines": lines}
    if not cues:
        return out
    spk = [str((lines.get(str(k)) or {}).get("spk") or c.speaker or "") for k, c in enumerate(cues)]
    distinct = sorted(set(spk))

    def has_gender(s):
        return str((speakers.get(s) or {}).get("gender") or "").lower() in ("male", "female")

    if len(distinct) > 1 and all(has_gender(s) for s in distinct):
        return out                               # Gemini already said who is who
    f0 = cue_pitches(media, cues, cache_dir, should_cancel)
    voiced = sum(1 for h in f0 if h)
    if not voiced:
        return out

    if len(distinct) <= 1:
        lab = split_two(f0)
        if lab:
            lab = _fill_gaps(lab, cues)
            groups = {0: [], 1: []}
            for h, g in zip(f0, lab):
                if h:
                    groups[g].append(h)
            old = speakers.get(distinct[0] if distinct else "", {})
            new = {}
            for g, sid in ((0, "P1"), (1, "P2")):
                hz = _median(groups[g])
                new[sid] = dict({k: v for k, v in old.items() if k not in ("id", "gender", "cast", "name")},
                                id=sid, gender=gender_of(hz), pitch_hz=round(hz or 0, 1), by="pitch")
            out["speakers"] = new
            for k in range(len(cues)):
                ln = lines.setdefault(str(k), {})
                ln["spk"] = "P1" if lab[k] == 0 else "P2"
            _log(f"two voices found: P1 {new['P1']['pitch_hz']} Hz ({new['P1']['gender']}), "
                 f"P2 {new['P2']['pitch_hz']} Hz ({new['P2']['gender']})")
            return out

    # same speakers, add the genders nobody gave
    for s in distinct:
        if has_gender(s):
            continue
        hz = _median([h for h, x in zip(f0, spk) if x == s and h])
        if not hz:
            continue
        speakers[s] = dict(speakers.get(s) or {}, id=s, gender=gender_of(hz),
                           pitch_hz=round(hz, 1), by="pitch")
        _log(f"speaker {s or '(main)'}: {round(hz)} Hz -> {gender_of(hz)}")
    return out
