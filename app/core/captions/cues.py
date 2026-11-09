"""app.core.captions.cues - YouTube Hunter.

The timed-text model every caption / dub step shares.

A Cue is one on-screen caption (or one spoken line): start / end in seconds,
the text, and, when the source has them, per-word timings. Word timings are
what the "Bold Pop" style highlights and what the re-segmenter uses to turn
YouTube's rolling ASR fragments into whole sentences, which is also what makes
the translation read like a person wrote it (a translator handed half a
sentence produces half a sentence).
"""

import html
import json
import re

# ── model ────────────────────────────────────────────────────────────────


class Cue:
    __slots__ = ("start", "end", "text", "words", "speaker", "meta")

    def __init__(self, start, end, text, words=None, speaker="", meta=None):
        self.start = float(start)
        self.end = float(end)
        self.text = text or ""
        # [(start, end, word)] in absolute seconds, or []
        self.words = list(words or [])
        self.speaker = speaker or ""
        self.meta = dict(meta or {})

    @property
    def dur(self):
        return max(0.0, self.end - self.start)

    def copy(self, **kw):
        c = Cue(self.start, self.end, self.text, self.words, self.speaker, self.meta)
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def to_dict(self):
        return {"s": round(self.start, 3), "e": round(self.end, 3), "t": self.text,
                "w": [[round(a, 3), round(b, 3), w] for a, b, w in self.words],
                "spk": self.speaker, "m": self.meta}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("s", 0), d.get("e", 0), d.get("t", ""),
                   [tuple(x) for x in (d.get("w") or [])], d.get("spk", ""),
                   d.get("m") or {})

    def __repr__(self):
        return f"Cue({self.start:.2f}-{self.end:.2f} {self.text[:30]!r})"


# ── time helpers ─────────────────────────────────────────────────────────

_TS_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def parse_ts(s):
    m = _TS_RE.search(s or "")
    if not m:
        return None
    h = int(m.group(1) or 0)
    frac = m.group(4)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(frac) / (10 ** len(frac))


def fmt_srt_ts(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_ts(t):
    return fmt_srt_ts(t).replace(",", ".")


def fmt_ass_ts(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ── text cleanup ─────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
_WS_RE = re.compile(r"[ \t\u00a0]+")
# YouTube ASR noise worth keeping out of a translation
_NOISE_RE = re.compile(r"^\s*\[(?:music|applause|laughter|موسیقی|музыка|musique|música|musik)\]\s*$", re.I)


def clean_text(s):
    s = html.unescape(_TAG_RE.sub("", s or ""))
    s = s.replace("\r", "")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def flat(s):
    return " ".join((s or "").split())


# ── parsers ──────────────────────────────────────────────────────────────

def parse_srt(text):
    out = []
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip())
    for b in blocks:
        lines = b.strip("\n").split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if idx < 0:
            continue
        a, _, z = lines[idx].partition("-->")
        s, e = parse_ts(a), parse_ts(z)
        if s is None or e is None:
            continue
        body = clean_text("\n".join(lines[idx + 1:]))
        if body:
            out.append(Cue(s, e, body))
    return out


_VTT_INLINE_TS = re.compile(r"<(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})>")


def parse_vtt(text):
    """WebVTT, including YouTube's auto-caption flavour (inline <00:00:01.230>
    word stamps, and each cue repeating the previous line above the new one)."""
    out = []
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    for b in blocks:
        lines = b.strip("\n").split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if idx < 0:
            continue
        a, _, z = lines[idx].partition("-->")
        s, e = parse_ts(a), parse_ts(z.strip().split(" ")[0])
        if s is None or e is None:
            continue
        body_lines = lines[idx + 1:]
        words = []
        stamped = [ln for ln in body_lines if _VTT_INLINE_TS.search(ln)]
        if stamped:
            # YouTube auto: the stamped line is the NEW text; the plain one is
            # the previous cue scrolled up. Keep only the new words.
            ln = stamped[-1]
            parts = _VTT_INLINE_TS.split(ln)
            # parts = [word0, ts1, word1, ts2, word2, ...]
            t_cur = s
            seq = [(t_cur, parts[0])]
            for i in range(1, len(parts) - 1, 2):
                seq.append((parse_ts(parts[i]) or t_cur, parts[i + 1]))
            for j, (t0, chunk) in enumerate(seq):
                w = clean_text(chunk)
                if not w:
                    continue
                t1 = seq[j + 1][0] if j + 1 < len(seq) else e
                for k, piece in enumerate(w.split()):
                    words.append((t0, t1, piece))
            body = " ".join(w for _, _, w in words)
        else:
            body = clean_text("\n".join(body_lines))
        if body and not _NOISE_RE.match(body):
            out.append(Cue(s, e, body, words))
    return _dedupe_rolling(out)


def clamp_words(c):
    """v1.4.6: keep a cue's word stamps inside the cue and in order.

    YouTube's ASR events overlap their successor, and the LAST word of each
    event used to keep the event's full duration (seconds past the next
    line's start). Bold Pop built its word groups from those stamps, so two
    groups were on screen at once and libass drew them over each other."""
    if not c.words:
        return c
    ws = sorted(c.words, key=lambda w: w[0])
    out = []
    for i, (a, b, w) in enumerate(ws):
        a = min(max(float(a), c.start), c.end)
        nxt = ws[i + 1][0] if i + 1 < len(ws) else c.end
        b = min(float(b), max(a, float(nxt)), c.end)
        if b < a:
            b = a
        out.append((a, b, w))
    c.words = out
    return c


def _dedupe_rolling(cues):
    """Drop the 10 ms 'hold' cues YouTube emits between rolling lines and exact
    repeats of the previous cue."""
    out = []
    for c in cues:
        if c.dur < 0.05:
            continue
        if out and flat(out[-1].text) == flat(c.text):
            out[-1].end = max(out[-1].end, c.end)
            continue
        out.append(c)
    return out


def parse_ass(text):
    out = []
    fmt = None
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln.lower().startswith("format:") and fmt is None and "text" in ln.lower():
            fmt = [x.strip().lower() for x in ln.split(":", 1)[1].split(",")]
            continue
        if not ln.lower().startswith("dialogue:"):
            continue
        cols = fmt or ["layer", "start", "end", "style", "name", "marginl",
                       "marginr", "marginv", "effect", "text"]
        parts = ln.split(":", 1)[1].split(",", len(cols) - 1)
        if len(parts) < len(cols):
            continue
        d = dict(zip(cols, parts))
        s, e = parse_ts(d.get("start", "") + "0"), parse_ts(d.get("end", "") + "0")
        if s is None or e is None:
            continue
        body = _ASS_OVERRIDE_RE.sub("", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n")
        body = clean_text(body)
        if body:
            out.append(Cue(s, e, body, speaker=(d.get("name") or "").strip()))
    return out


def parse_json3(raw):
    """YouTube's json3 timed text. Auto captions carry per-word offsets."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception:
        return []
    out = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        t0 = (ev.get("tStartMs") or 0) / 1000.0
        dur = (ev.get("dDurationMs") or 0) / 1000.0
        words = []
        text_parts = []
        for i, sg in enumerate(segs):
            u = sg.get("utf8") or ""
            if not u.strip():
                if "\n" in u:
                    text_parts.append("\n")
                continue
            ws = t0 + (sg.get("tOffsetMs") or 0) / 1000.0
            words.append([ws, None, u.strip()])
            text_parts.append(u)
        body = clean_text("".join(text_parts))
        if not body or _NOISE_RE.match(body):
            continue
        end = t0 + dur if dur > 0 else t0 + 2.0
        for i, w in enumerate(words):
            w[1] = words[i + 1][0] if i + 1 < len(words) else end
        # only real per-word stamps are worth keeping (manual captions come as
        # one seg per line)
        wl = [tuple(w) for w in words] if len(words) > 1 else []
        out.append(Cue(t0, end, body, wl))
    # ASR events overlap their successor (the line stays on screen while the
    # next one types in): clamp so cues never overlap.
    out.sort(key=lambda c: c.start)
    for i in range(len(out) - 1):
        if out[i].end > out[i + 1].start:
            out[i].end = max(out[i].start + 0.2, out[i + 1].start)
    for c in out:
        clamp_words(c)
    return _dedupe_rolling(out)


def parse_any(text, ext=""):
    ext = (ext or "").lower().lstrip(".")
    t = text or ""
    if ext == "json3" or t.lstrip().startswith("{"):
        c = parse_json3(t)
        if c:
            return c
    if ext in ("ass", "ssa") or "[Events]" in t:
        return parse_ass(t)
    if ext == "vtt" or t.lstrip().startswith("WEBVTT"):
        return parse_vtt(t)
    return parse_srt(t)


def load_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-16", "cp1256", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return normalize(parse_any(text, ext))


# ── shaping ──────────────────────────────────────────────────────────────

def normalize(cues, min_dur=0.35):
    cues = sorted((c for c in cues if flat(c.text)), key=lambda c: c.start)
    out = []
    for c in cues:
        c = c.copy()
        if c.end <= c.start:
            c.end = c.start + max(min_dur, 0.06 * len(c.text))
        if out and c.start < out[-1].end:
            out[-1].end = max(out[-1].start + 0.1, c.start)
        out.append(c)
    for i, c in enumerate(out):
        if c.dur < min_dur:
            nxt = out[i + 1].start if i + 1 < len(out) else c.start + min_dur
            c.end = min(c.start + min_dur, max(c.end, nxt))
        clamp_words(c)
    return out


def snap_to_speech(cues, onsets, offsets, reach=0.6):
    """v1.4.4: pull each line's start onto the nearest moment the voice
    really starts (and its end onto where it stops), when one is within
    `reach` seconds. AI-heard timestamps drift by a few tenths of a second;
    the audio itself does not. Lines with no clear edge nearby keep their
    times."""
    import bisect
    if not cues or (not onsets and not offsets):
        return cues

    def near(arr, t):
        if not arr:
            return None
        i = bisect.bisect_left(arr, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(arr) and abs(arr[j] - t) <= reach:
                if best is None or abs(arr[j] - t) < abs(best - t):
                    best = arr[j]
        return best

    out = []
    for c in cues:
        s = near(onsets, c.start)
        e = near(offsets, c.end)
        ns = c.start if s is None else s
        ne = c.end if e is None else e
        if ne - ns < 0.3:
            ns, ne = c.start, c.end
        out.append(c.copy(start=max(0.0, ns), end=ne))
    return normalize(out)


def window(cues, start=0.0, end=0.0):
    """Cues re-timed to a clip cut out of [start, end) of the source."""
    if start <= 0 and end <= 0:
        return [c.copy() for c in cues]
    end = end if end > start else float("inf")
    out = []
    for c in cues:
        if c.end <= start or c.start >= end:
            continue
        s = max(c.start, start) - start
        e = min(c.end, end) - start
        words = [(max(a, start) - start, min(b, end) - start, w)
                 for a, b, w in c.words if b > start and a < end]
        if e - s >= 0.15:
            out.append(c.copy(start=s, end=e, words=words))
    return out


def estimate_words(cue):
    """Per-word timings spread by length when the source has none."""
    if cue.words:
        return list(cue.words)
    toks = flat(cue.text).split(" ")
    toks = [t for t in toks if t]
    if not toks:
        return []
    weights = [max(1, len(t)) + 1 for t in toks]
    total = float(sum(weights))
    t = cue.start
    out = []
    for tok, w in zip(toks, weights):
        d = cue.dur * w / total
        out.append((t, t + d, tok))
        t += d
    return out


_SENT_END = re.compile(r"[.!?…。！？؟]$")
_SOFT_END = re.compile(r"[,;:،؛、，]$")


def resegment(cues, max_chars=84, max_dur=6.5, pause=0.55):
    """Turn fragments (YouTube ASR rolls 3–6 words at a time) into sentence-ish
    cues: break on sentence punctuation, on a real pause, or when the line gets
    too long to read. Cues that already look like sentences pass through."""
    if not cues:
        return []
    frag = sum(1 for c in cues if len(c.text) < 45 and not _SENT_END.search(c.text))
    if frag < len(cues) * 0.5:
        return normalize(cues)
    words = []
    for c in cues:
        for w in estimate_words(clamp_words(c.copy())):
            words.append((w[0], w[1], w[2], c.speaker))
    words.sort(key=lambda w: w[0])
    for i in range(len(words) - 1):
        if words[i][1] > words[i + 1][0]:
            a, _b, t, spk = words[i]
            words[i] = (a, max(a, words[i + 1][0]), t, spk)
    out = []
    cur = []

    def flush():
        if not cur:
            return
        text = " ".join(w[2] for w in cur)
        out.append(Cue(cur[0][0], cur[-1][1], text, [(a, b, t) for a, b, t, _ in cur], cur[0][3]))
        cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w[0] - cur[-1][1]
            length = len(" ".join(x[2] for x in cur)) + 1 + len(w[2])
            span = w[1] - cur[0][0]
            last = cur[-1][2]
            if (gap >= pause or length > max_chars or span > max_dur
                    or (w[3] != cur[-1][3] and w[3])
                    or (_SENT_END.search(last) and len(cur) >= 2)
                    or (_SOFT_END.search(last) and length > max_chars * 0.6)):
                flush()
        cur.append(w)
    flush()
    return normalize(out)


_PUNCT = re.compile(r"[.!?…,;:،؛؟。！？、，]")


def looks_raw_asr(cues):
    """v1.4.12: True for automatic captions (speech recognition with no
    punctuation): under one punctuation mark per 25 words. Uploaded tracks
    and anything an AI wrote are punctuated and return False."""
    text = " ".join(flat(c.text) for c in (cues or [])[:400])
    words = len(text.split())
    if words < 12:
        return False
    return len(_PUNCT.findall(text)) < words / 25.0


def wrap_lines(text, max_line=42, max_lines=2):
    """Balanced line breaks: two lines of similar length instead of a long
    top line and a widow underneath."""
    t = flat(text)
    if len(t) <= max_line:
        return t
    words = t.split(" ")
    if len(words) == 1:
        return t
    best, best_score = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b)) + (100 if max(len(a), len(b)) > max_line * 1.25 else 0)
        # prefer breaking after punctuation
        if _SOFT_END.search(a) or _SENT_END.search(a):
            score -= 6
        if best_score is None or score < best_score:
            best, best_score = (a, b), score
    if max_lines > 2 and len(best[1]) > max_line:
        return best[0] + "\n" + wrap_lines(best[1], max_line, max_lines - 1)
    return best[0] + "\n" + best[1]


def split_long(cues, max_chars=84, max_dur=7.0):
    """After translation a line can outgrow its slot; split it in two at the
    best word boundary, sharing the time by length."""
    out = []
    for c in cues:
        t = flat(c.text)
        if len(t) <= max_chars * 1.15 and c.dur <= max_dur:
            out.append(c)
            continue
        words = t.split(" ")
        if len(words) < 4:
            out.append(c)
            continue
        mid = len(t) / 2
        best_i, best_d = 1, None
        pos = 0
        for i, w in enumerate(words[:-1]):
            pos += len(w) + 1
            d = abs(pos - mid) - (8 if _SOFT_END.search(w) or _SENT_END.search(w) else 0)
            if best_d is None or d < best_d:
                best_i, best_d = i + 1, d
        a, b = " ".join(words[:best_i]), " ".join(words[best_i:])
        cut = c.start + c.dur * len(a) / max(1, len(a) + len(b))
        out.append(c.copy(end=cut, text=a, words=[]))
        out.append(c.copy(start=cut, text=b, words=[]))
    return out


# ── writers ──────────────────────────────────────────────────────────────

def to_srt(cues, wrap=42):
    lines = []
    for i, c in enumerate(cues, 1):
        body = wrap_lines(c.text, wrap) if wrap else c.text
        lines.append(f"{i}\n{fmt_srt_ts(c.start)} --> {fmt_srt_ts(c.end)}\n{body}\n")
    return "\n".join(lines)


def to_vtt(cues, wrap=42):
    lines = ["WEBVTT", ""]
    for c in cues:
        body = wrap_lines(c.text, wrap) if wrap else c.text
        lines.append(f"{fmt_vtt_ts(c.start)} --> {fmt_vtt_ts(c.end)}\n{body}\n")
    return "\n".join(lines)


def dump_json(cues):
    return json.dumps([c.to_dict() for c in cues], ensure_ascii=False)


def load_json(text):
    return [Cue.from_dict(d) for d in json.loads(text or "[]")]
