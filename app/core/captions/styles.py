"""app.core.captions.styles - YouTube Hunter.

The caption looks, and the ASS script libass burns them with.

Sizes are fractions of the frame's SHORT side, so a 1080p landscape video and a
1080×1920 Short get the same physical caption size, and the script's PlayRes is
the video's own resolution (no scaling surprises).

    youtube  v1.4: YouTube's own CC look: white on a 75 % black box, the
             player's plain sans, regular weight. Auto captions that carry
             word timings "paint on" word by word, exactly like the player.
    clean    Streaming-service subtitles: white, soft shadow, thin outline.
    pop      Shorts / Reels: heavy words, 1–4 at a time, the word being spoken
             lights up in the accent and pops (needs word timings; estimated
             from the line when the source has none).
    boxed    White on a rounded-feel translucent box: readable on any footage.
    neon     White core with an accent glow (two layers: blurred + crisp).
    cinema   Classic film-festival yellow with a firm black edge.

v1.4.6, the 2026 set:

    lyric      Apple-Music-style live lyrics: the line is on screen dimmed and
               each word fills to white as it is spoken (\\kf karaoke), on a
               soft blurred halo instead of a hard outline.
    spotlight  The spoken word sits on a rounded accent pill (a thick
               round-joined \\bord in the accent), 1-4 words at a time.
    punch      One word at a time, huge, slams in with an overshoot; numbers
               and long words land in the accent.
    aura       A calm premium line: white on a soft accent bloom, rises in
               with a tracking pull.

Refreshed: pop (words still to come are dimmed, groups rise in), boxed ("Glass":
lighter frosted box, more air), neon (three layers: bloom, glow, core).

v1.4.7, "no stroke" pass:

    * only Bold Pop keeps a hard outline (the one classic stroke look);
      Clean, Cinema, Spotlight and Punch sit on a soft blurred shade layer
      instead (crisp text, no stroke), Neon and Aura lose their thin edge
    * Spotlight: the spoken word gets a solid accent BOX with softened
      corners (an opaque box per word via BorderStyle 3 + \\3a), not a
      blobby outline pill, and the words sit one plain space apart
    * YouTube: every line is its own event with its own box, stacked with a
      small gap, so two lines no longer crowd into one block

v1.4.12, "one size, real metrics":

    * ONE text size for every look (Punch, one word at a time, is the only
      bigger one). Sizes are set on the font's real em, read from the font
      file: ASS "Fontsize" is the font's whole cell (winAscent+winDescent),
      and Vazirmatn's cell is 1.71 em against Arial's 1.12, so the same
      Fontsize drew Persian ~35 % smaller than English. Now the letters
      come out the same size in every look and every language, in the app
      and from the extension (both burn through build_ass).
    * box looks (YouTube, Glass) put every wrapped row in its own event,
      spaced by the font's REAL line height + padding + a gap, so two
      rows never touch or overlap (Persian rows did, its line is 1.56 em).
    * Glass and Spotlight boxes have truly ROUNDED corners: the box layer
      is the same text squashed to a hairline (\\fscy) and grown back with
      \\xbord / \\ybord, so libass draws a rounded rectangle exactly as wide
      as the words, with no text measuring on our side.
    * Spotlight and Bold Pop go word by word in Persian / Arabic / Hebrew
      too. libass lays out override runs left to right even in an RTL line
      (that scrambled the words before), so RTL lines are written in
      VISUAL order, each word isolated with LRM / RLM marks: every layer
      gets the identical string, so the box always lands on its word.

Every look goes through _no_overlap(): two lines of the same layer are never
on screen at once (libass draws simultaneous events on top of each other).

Colours in ASS are &HAABBGGRR (alpha first, 00 = opaque, blue before red).
"""

from app.core.captions import cues as C
from app.core.captions import languages as L

STYLES = [
    {"key": "youtube", "name": "YouTube", "desc": "Exactly like YouTube's own captions.",
     "preview": {"fg": "#FFFFFF", "outline": "", "box": "#BF080808", "hi": "", "glow": "", "weight": 500, "upper": False}},
    {"key": "clean", "name": "Clean", "desc": "Streaming-service look. Quiet, sharp, readable.",
     "preview": {"fg": "#FFFFFF", "outline": "", "shade": True, "box": "", "hi": "", "glow": "", "weight": 600, "upper": False}},
    {"key": "pop", "name": "Bold Pop", "desc": "The classic stroke look. Big words, the spoken word lights up.",
     "preview": {"fg": "#FFFFFF", "outline": "#000000", "box": "", "hi": "#FFD400", "glow": "", "weight": 900, "upper": True}},
    {"key": "boxed", "name": "Glass", "desc": "Frosted glass box. Readable on any footage, never heavy.",
     "preview": {"fg": "#FFFFFF", "outline": "", "box": "#8C1A1D26", "hi": "", "glow": "", "weight": 600, "upper": False}},
    {"key": "neon", "name": "Neon", "desc": "Crisp core, layered glow. Gaming and tech energy.",
     "preview": {"fg": "#9FF7FF", "outline": "", "box": "", "hi": "", "glow": "#00E5FF", "weight": 700, "upper": False}},
    {"key": "cinema", "name": "Cinema", "desc": "Classic film yellow with a firm edge.",
     "preview": {"fg": "#FFE14D", "outline": "", "shade": True, "box": "", "hi": "", "glow": "", "weight": 600, "upper": False}},
    # v1.4.6: the 2026 set
    {"key": "lyric", "name": "Lyric", "desc": "Live-lyrics glow. Each word lights up as it's spoken.",
     "preview": {"fg": "#FFFFFF", "outline": "", "box": "", "hi": "#7E8597", "glow": "", "weight": 800, "upper": False}},
    {"key": "spotlight", "name": "Spotlight", "desc": "The spoken word sits on an accent box. Creator favourite.",
     "preview": {"fg": "#FFFFFF", "outline": "", "shade": True, "box": "", "hi": "#FFFFFF", "pill": "#7C5CFF", "glow": "", "weight": 900, "upper": True}},
    {"key": "punch", "name": "Punch", "desc": "One huge word at a time. Numbers hit in the accent.",
     "preview": {"fg": "#FFFFFF", "outline": "", "shade": True, "box": "", "hi": "#C8FF2E", "glow": "", "weight": 900, "upper": True, "single": True}},
    {"key": "aura", "name": "Aura", "desc": "Calm and premium. A soft bloom, rises in gently.",
     "preview": {"fg": "#FFFFFF", "outline": "", "box": "", "hi": "", "glow": "#8B7BFF", "weight": 600, "upper": False}},
]
STYLE_KEYS = [s["key"] for s in STYLES]
SIZES = {"s": 0.85, "m": 1.0, "l": 1.2}
POSITIONS = ("bottom", "middle", "top")

# v1.4.12: the one text size. EM is the letters' real em as a share of the
# frame's short side (YouTube's own CC size, which reads right on both a
# 1080p video and a Short). Every look uses it; Punch shows one word at a
# time and is the only one drawn bigger.
_EM = 0.0412
_SCALE = {"punch": 1.6}

# (cell, line) per family, in em: cell = (winAscent + winDescent) / upm,
# which is what ASS Fontsize means; line = hhea ascender - descender, the
# height libass gives a row (and a BorderStyle 3 box). Bundled fonts are
# read from the file; Windows fonts use their published metrics.
_SYS_METRICS = {
    "arial": (1.117, 1.117), "segoe ui": (1.330, 1.330), "nirmala ui": (1.380, 1.380),
    "microsoft yahei": (1.320, 1.320), "microsoft jhenghei": (1.320, 1.320),
    "yu gothic ui": (1.330, 1.330), "malgun gothic": (1.330, 1.330),
    "leelawadee ui": (1.400, 1.400), "ebrima": (1.330, 1.330),
}
_FONT_FILES = {"hanken grotesk": "HankenGrotesk.ttf", "vazirmatn": "Vazirmatn-Regular.ttf",
               "vazir": "Vazir.ttf"}
_METRICS_CACHE = {}


def _read_metrics(path):
    import struct
    with open(path, "rb") as f:
        d = f.read()
    n = struct.unpack(">H", d[4:6])[0]
    t = {}
    for i in range(n):
        tag, _cs, off, ln = struct.unpack(">4sIII", d[12 + 16 * i:28 + 16 * i])
        t[tag.decode("latin-1")] = off
    upm = struct.unpack(">H", d[t["head"] + 18:t["head"] + 20])[0]
    asc, desc = struct.unpack(">hh", d[t["hhea"] + 4:t["hhea"] + 8])
    wa, wd = struct.unpack(">HH", d[t["OS/2"] + 74:t["OS/2"] + 78])
    cell = (wa + wd) / upm if (wa + wd) else (asc - desc) / upm
    return round(cell, 4), round((asc - desc) / upm, 4)


def font_metrics(family):
    """(cell, line) of a font family in em; see _SYS_METRICS."""
    key = (family or "").strip().lower()
    if key in _METRICS_CACHE:
        return _METRICS_CACHE[key]
    m = _SYS_METRICS.get(key, (1.30, 1.30))
    if key in _FONT_FILES:
        try:
            import os
            from app.core.captions import render as _R
            m = _read_metrics(os.path.join(_R.fonts_dir(), _FONT_FILES[key]))
        except Exception:                # noqa: BLE001 - keep the table value
            pass
    _METRICS_CACHE[key] = m
    return m


def _fs(family, em_px):
    """ASS Fontsize that draws letters `em_px` tall (em) in `family`."""
    return em_px * font_metrics(family)[0]


def _line_px(family, em_px):
    """Height libass gives one row of `family` at that em."""
    return em_px * font_metrics(family)[1]


def _vis_rtl(tokens, sep=" "):
    """v1.4.12: an RTL line as VISUAL order text (see the module notes).
    `tokens` in reading order, may carry override tags. Each word is
    isolated between an LRM and an RLM (so its trailing "." / "،" stays on
    its own left), the words are written right-most first, and the result
    reads right to left whatever libass does with the override runs."""
    return _LRM + (sep + _LRM).join(t + _RLM for t in reversed(tokens))


def _row_centres(n, pitch, half, geo, mv=0):
    """Centre y of each of `n` stacked rows (top row first) for \\an5."""
    w, h, position, margin_v = geo
    if position == "top":
        top = (mv or margin_v) + half
        return [top + r * pitch for r in range(n)]
    if position == "middle":
        top = h / 2 - ((n - 1) * pitch) / 2
        return [top + r * pitch for r in range(n)]
    bottom = h - margin_v - half
    return [bottom - (n - 1 - r) * pitch for r in range(n)]


def _squash(xb, yb, blur=0.7):
    """Override head that turns a text event into its own rounded box: the
    glyphs are squashed to a hairline and the round border grows them back
    into a rounded rectangle the exact width of the words."""
    # \\u1: the underline runs unbroken under spaces and between letters
    # that don't join, so the squashed strip is one solid bar and the
    # rounded box has clean straight edges (no bumps at the gaps)
    return "\\fscy4\\u1\\1a&HFF&\\xbord%.1f\\ybord%.1f\\blur%.1f" % (xb, yb, blur)
# v1.4.7: looks drawn without a stroke, on a soft blurred shade layer
_SHADED = ("clean", "cinema", "spotlight", "punch")


def _ass_color(hex_rgb, alpha=0):
    h = hex_rgb.lstrip("#")
    if len(h) == 8:                       # AARRGGBB
        alpha = 255 - int(h[0:2], 16)
        h = h[2:]
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ovr_color(hex_rgb):
    """Colour for an override tag: \\c&HBBGGRR&."""
    h = hex_rgb.lstrip("#")[-6:]
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _esc(t):
    # ASS has no escape for braces; keep the meaning, lose the override risk
    return (t or "").replace("{", "(").replace("}", ")").replace("\\", "/")


_RLM = "\u200f"
_LRM = "\u200e"


def _prep(text, lang, upper):
    t = C.flat(text)
    if upper and L.is_cased(lang):
        t = t.upper()
    return t


def _line_text(text, lang, upper, max_line):
    t = _prep(text, lang, upper)
    t = C.wrap_lines(t, max_line)
    parts = [_esc(p) for p in t.split("\n")]
    if L.is_rtl(lang):
        parts = [_RLM + p for p in parts]
    return "\\N".join(parts)


def build_ass(cues, style="clean", lang="en", width=1920, height=1080,
              size="m", position="bottom", secondary=None, secondary_lang="",
              accent=None):
    """The full .ass script. `secondary` (optional, same length as `cues`) is
    the original-language line shown small above each caption (bilingual)."""
    st = style if style in STYLE_KEYS else "clean"
    w, h = max(16, int(width or 1920)), max(16, int(height or 1080))
    base = min(w, h)
    k = SIZES.get(size, 1.0)
    vertical = h > w
    font = L.font_for(lang)
    sec_font = L.font_for(secondary_lang or lang)
    max_line = L.line_limit(lang)
    if vertical:
        max_line = int(max_line * 0.62)

    # alignment (numpad): 2 bottom-centre, 5 middle, 8 top
    align = {"bottom": 2, "middle": 5, "top": 8}.get(position, 2)
    margin_v = int(base * (0.075 if not vertical else 0.20))
    if st in ("pop", "spotlight", "punch") and position == "bottom":
        margin_v = int(base * (0.12 if not vertical else 0.34))
    margin_h = int(w * 0.06)

    hi = accent or "#FFD400"
    neon = accent or "#00E5FF"
    pill = accent or "#7C5CFF"
    punch = accent or "#C8FF2E"
    aura = accent or "#8B7BFF"

    # v1.4.12: one em for every look (see _EM); Fontsize per font from it
    em = base * _EM * k * _SCALE.get(st, 1.0)
    fs = _fs(font, em)
    line_h = _line_px(font, em)

    # box looks: each wrapped row its own event, one `pitch` apart
    yt_font = _yt_font(lang)
    yt_fs = _fs(yt_font, em)
    yt_pad = em * 0.15
    # v1.4.12: on a Short the bottom fifth is under the player's buttons
    yt_margin = int(base * (0.055 if not vertical else 0.20))
    # a BorderStyle 3 box is the font's whole CELL tall (win metrics, = Fontsize)
    yt_pitch = yt_fs + 2 * yt_pad + em * 0.22
    # Glass: rounded box, half a row of air above and below the letters
    gl_x = em * 0.46
    gl_y = line_h * 0.5 + em * 0.06
    gl_pitch = 2 * gl_y + em * 0.26

    styles = []

    def add(name, fontname, fs_, primary, outline_c, back_c, bold, outline, shadow,
            border_style=1, spacing=0, an=align, mv=margin_v):
        styles.append(
            f"Style: {name},{fontname},{int(round(fs_))},{primary},&H000000FF,{outline_c},{back_c},"
            f"{-1 if bold else 0},0,0,0,100,100,{spacing},0,{border_style},{outline:.1f},{shadow:.1f},"
            f"{an},{margin_h},{margin_h},{mv},-1")

    if st == "youtube":
        # BorderStyle 3 paints the box in OutlineColour, padded by Outline.
        # 0x40 alpha = 75 % opaque, the player's default background.
        add("Main", yt_font, yt_fs, _ass_color("#FFFFFF"),
            _ass_color("#080808", 0x40), _ass_color("#000000", 0xFF), False,
            yt_pad, 0, border_style=3, mv=yt_margin)
    elif st == "clean":
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0)
    elif st == "pop":
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000"),
            _ass_color("#000000", 0x70), True, em * 0.11, em * 0.05, spacing=1)
    elif st == "boxed":
        # v1.4.12 "Glass": a rounded frosted box per row. "GlassBox" is the
        # row squashed to a hairline (\fscy) and grown back by a round
        # \xbord/\ybord: a rounded rectangle exactly as wide as the words.
        add("GlassBox", font, fs, _ass_color("#FFFFFF", 0xFF), _ass_color("#1A1D26", 0x66),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=0.4)
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=0.4)
    elif st == "neon":
        # v1.4.6: bloom (wide, faint) + glow (tight) + crisp core
        add("Bloom", font, fs, _ass_color(neon, 0xFF), _ass_color(neon, 0xB4),
            _ass_color("#000000", 0xFF), True, em * 0.40, 0)
        add("Glow", font, fs, _ass_color("#FFFFFF"), _ass_color(neon, 0x30),
            _ass_color("#000000", 0xFF), True, em * 0.15, 0)
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color(neon, 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0)
    elif st == "lyric":
        # Primary = spoken (white), Secondary = still to come (dimmed): \kf
        # sweeps between them. A thin black edge that _lyric_events blurs
        # into a soft halo.
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000", 0x50),
            _ass_color("#000000", 0x8C), True, em * 0.07, 0)
        styles[-1] = styles[-1].replace(",&H000000FF,", "," + _ass_color("#FFFFFF", 0x9A) + ",", 1)
    elif st == "spotlight":
        # v1.4.12: "Box" draws the spoken word's rounded accent pill (the
        # squashed-text trick, see Glass); "Main" the crisp words on top.
        add("Box", font, fs, _ass_color("#FFFFFF", 0xFF), _ass_color(pill),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=0.5)
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=0.5)
    elif st == "punch":
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#000000", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=1)
    elif st == "aura":
        add("Bloom", font, fs, _ass_color(aura, 0xFF), _ass_color(aura, 0x9C),
            _ass_color("#000000", 0xFF), True, em * 0.34, 0, spacing=0.6)
        add("Main", font, fs, _ass_color("#FFFFFF"), _ass_color("#0B0B14", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0, spacing=0.6)
    elif st == "cinema":
        add("Main", font, fs, _ass_color("#FFE14D"), _ass_color("#000000", 0xFF),
            _ass_color("#000000", 0xFF), True, 0, 0)
    shade_fs = 0.0
    if st in _SHADED:
        shade_fs = fs
        add("Shade", font, shade_fs, _ass_color("#000000", 0x9C), _ass_color("#000000", 0x9C),
            _ass_color("#000000", 0xFF), True, em * 0.045, 0,
            spacing={"spotlight": 0.5, "punch": 1}.get(st, 0))
    sec_em = em * 0.8 / _SCALE.get(st, 1.0)
    add("Secondary", sec_font, _fs(sec_font, sec_em), _ass_color("#FFFFFF", 0x30),
        _ass_color("#000000", 0x50), _ass_color("#000000", 0xA0), False,
        sec_em * 0.06, 0)

    events = []

    def ev(layer, s, e, sty, text, mv=0):
        events.append(f"Dialogue: {layer},{C.fmt_ass_ts(s)},{C.fmt_ass_ts(e)},{sty},,0,0,{int(mv)},,{text}")

    gap = em * 0.34
    sec_line = _line_px(sec_font, sec_em)
    box_geo = (w, h, position, margin_v)
    for i, c in enumerate(cues):
        if c.dur <= 0.02 or not C.flat(c.text):
            continue
        if secondary and i < len(secondary) and C.flat(secondary[i]):
            # Its own event (own paragraph → own text direction), stacked
            # against the main block, so a Latin original never flips the
            # base direction of a Persian / Arabic / Hebrew caption.
            sec_text = _line_text(secondary[i], secondary_lang, False, int(max_line * 1.25))
            n_main = 1 if st in ("pop", "spotlight", "punch") else _line_text(c.text, lang, False, max_line).count("\\N") + 1
            pitch = {"youtube": yt_pitch, "boxed": gl_pitch}.get(st, line_h)
            if st == "spotlight":
                pitch = line_h + em * 0.2
            block = n_main * pitch + gap
            if position == "top":
                mv_s = margin_v
            elif position == "middle":
                mv_s = int(h / 2 + block / 2)
            else:
                mv_s = int((yt_margin if st == "youtube" else margin_v) + block)
            an_s = 8 if position == "top" else 2
            pos_tag = "{\\an%d\\fad(60,40)}" % an_s
            events.append(
                f"Dialogue: 2,{C.fmt_ass_ts(c.start)},{C.fmt_ass_ts(c.end)},Secondary,,0,0,{int(mv_s)},,"
                + pos_tag + sec_text)
            if position == "top":
                # main moves under the original
                sec_push = int(margin_v + (sec_text.count("\\N") + 1) * sec_line + gap)
                c = c.copy(meta=dict(c.meta, _mv=sec_push))
        mv = c.meta.get("_mv", 0)
        if st == "pop":
            _pop_events(ev, c, lang, hi, mv)
        elif st == "youtube":
            _youtube_events(ev, c, lang, max_line, mv,
                            geo=(w, h, yt_pitch, yt_pad, mv or yt_margin, position))
        elif st == "neon":
            body = _line_text(c.text, lang, False, max_line)
            ev(0, c.start, c.end, "Bloom", "{\\blur" + f"{max(4, em * 0.33):.0f}" + "\\fad(120,80)}" + body, mv)
            ev(1, c.start, c.end, "Glow", "{\\blur" + f"{max(2, em * 0.09):.0f}" + "\\fad(80,60)}" + body, mv)
            ev(2, c.start, c.end, "Main", "{\\fad(80,60)}" + body, mv)
        elif st == "lyric":
            _lyric_events(ev, c, lang, max_line, base, mv)
        elif st == "spotlight":
            _spotlight_events(ev, c, lang, pill, em, line_h, box_geo, mv)
        elif st == "punch":
            _punch_events(ev, c, lang, punch, mv)
        elif st == "aura":
            _aura_events(ev, c, lang, max_line, fs, mv)
        elif st == "boxed":
            _glass_events(ev, c, lang, max_line, em, (gl_x, gl_y, gl_pitch), box_geo, mv)
        else:
            body = _line_text(c.text, lang, False, max_line)
            ev(0, c.start, c.end, "Main", "{\\fad(60,40)}" + body, mv)

    if shade_fs:
        events = _with_shade(events, shade_fs)
    events = _no_overlap(events)
    head = [
        "[Script Info]",
        "; Generated by YouTube Hunter · Voice Studio",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ]
    return "\n".join(head)


def _chunks(words, lang, max_words=4, max_chars=22):
    """Bold Pop shows a few words at a time, breaking on punctuation."""
    if L.is_cjk(lang):
        max_words, max_chars = 8, 12
    out, cur = [], []
    for w in words:
        cur_len = sum(len(x[2]) + 1 for x in cur)
        if cur and (len(cur) >= max_words or cur_len + len(w[2]) > max_chars):
            out.append(cur)
            cur = []
        cur.append(w)
        if C._SENT_END.search(w[2]) or C._SOFT_END.search(w[2]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


# Scripts whose letters join or reorder (Arabic, Hebrew, Indic, Thai…) must
# not be split into per-word override runs: libass shapes and orders each run
# on its own, which disjoins Persian letters and scrambles word order. Those
# get a whole-phrase pop instead (the phrase lands in the accent and settles to
# white), which keeps the energy without breaking the text.
_JOINING_SCRIPTS = ("arab", "hebr", "deva", "beng", "taml", "telu", "guru", "thai", "ethi")


def _pop_events(ev, c, lang, hi, mv=0):
    words = C.estimate_words(c)
    if not words:
        return
    hi_c = _ovr_color(hi)
    l = L.get(lang)
    rtl = L.is_rtl(lang)
    # v1.4.12: RTL goes word by word too (visual-order text, _vis_rtl);
    # only other joining scripts keep the whole-phrase pop
    joining = bool(l and l["script"] in _JOINING_SCRIPTS) and not rtl
    for group in _chunks(words, lang, max_words=3 if (joining or rtl) else 4):
        g_start, g_end = group[0][0], max(group[-1][1], group[0][0] + 0.25)
        if joining:
            text = _esc(" ".join(w[2] for w in group))
            if L.is_rtl(lang):
                text = _RLM + text
            ev(0, g_start, g_end, "Main",
               "{\\fad(40,0)\\c" + hi_c + "\\fscx114\\fscy114"
               "\\t(0,140,\\fscx100\\fscy100)\\t(120,320,\\c&HFFFFFF&)}" + text, mv)
            continue
        toks = [_esc(_prep(w[2], lang, True)) for w in group]
        for j, w in enumerate(group):
            s = w[0] if j else g_start
            e = group[j + 1][0] if j + 1 < len(group) else g_end
            if e - s < 0.02:
                continue
            parts = []
            for k2, t in enumerate(toks):
                if k2 == j:
                    # the spoken word: accent colour + a small pop that settles
                    parts.append("{\\c" + hi_c + "\\fscx112\\fscy112\\t(0,90,\\fscx104\\fscy104)}"
                                 + t + "{\\c&HFFFFFF&\\fscx100\\fscy100}")
                elif k2 > j:
                    # v1.4.6: words still to come wait a step back
                    parts.append("{\\1a&H55&}" + t + "{\\1a&H00&}")
                else:
                    parts.append(t)
            body = _vis_rtl(parts) if rtl else " ".join(parts)
            # v1.4.6: the group rises in (scale from 88 % with a soft settle)
            intro = "{\\fad(60,0)\\fscy88\\t(0,140,0.5,\\fscy100)}" if j == 0 else ""
            ev(0, s, e, "Main", intro + body, mv)


# ── v1.4: the YouTube look ───────────────────────────────────────────────

def _yt_font(lang):
    """The player draws captions in a plain sans (Roboto / Arial). Arial
    ships with every Windows and covers Latin, Cyrillic, Greek and Hebrew;
    every other script keeps the font that really has its glyphs."""
    l = L.get(lang)
    if (l["script"] if l else "latn") in ("latn", "cyrl", "grek", "hebr"):
        return "Arial"
    return L.font_for(lang)


def _yt_row_tags(n, geo):
    """v1.4.7: an {\\an\\pos} tag per wrapped row, top row first. Each
    row is its own event (own box), one `pitch` apart, so the boxes keep a
    small gap instead of crowding into one block."""
    w, h, pitch, pad, margin, position = geo
    x = int(round(w / 2))
    tags = []
    for r in range(n):
        if position == "top":
            an, y = 8, margin + r * pitch
        elif position == "middle":
            block = n * pitch
            an, y = 8, h / 2 - block / 2 + pad + r * pitch
        else:
            an, y = 2, h - margin - (n - 1 - r) * pitch
        tags.append("{\\an%d\\pos(%d,%d)}" % (an, x, int(round(y))))
    return tags


def _youtube_events(ev, c, lang, max_line, mv=0, geo=None):
    """A whole line, or (auto captions with real word timings) the line
    painting on word by word as it is spoken, the way YouTube's player shows
    auto captions. The line is wrapped ONCE for its full text and the words
    still to come are drawn fully transparent, so nothing reflows or jumps
    while it paints. Scripts whose letters join keep whole-row events: a
    transparency run inside a word would break their shaping.

    v1.4.7: every wrapped row is its own event on its own layer (see
    _yt_row_tags), so two lines get two boxes with air between them."""
    rtl = L.is_rtl(lang)
    wrapped = C.wrap_lines(_prep(c.text, lang, False), max_line).split("\n")
    rows = [r.split() for r in wrapped if r.strip()]
    if not rows:
        return
    if geo is None:
        body = _line_text(c.text, lang, False, max_line)
        ev(0, c.start, c.end, "Main", body, mv)
        return
    tags = _yt_row_tags(len(rows), geo)

    def row_text(r, toks):
        line = " ".join(toks)
        return tags[r] + ((_RLM + line) if rtl else line)

    words = list(c.words or [])
    flat = [t for r in rows for t in r]
    if _joining(lang) or len(words) < 2 or len(flat) != len(words):
        for r, toks in enumerate(rows):
            ev(r, c.start, c.end, "Main", row_text(r, [_esc(t) for t in toks]))
        return
    hide = "{\\alpha&HFF&}"
    n = len(flat)
    starts, i = [], 0
    for r in rows:
        starts.append(i)
        i += len(r)
    runs = [[] for _ in rows]               # per row: [start, end, text]
    for j in range(n):
        s = c.start if j == 0 else max(c.start, float(words[j][0]))
        e = c.end if j == n - 1 else max(s + 0.02, float(words[j + 1][0]))
        e = min(e, c.end)
        if e - s < 0.02:
            continue
        for r, toks in enumerate(rows):
            if starts[r] > j:
                break                       # rows not reached yet stay off
            parts = []
            for q, tok in enumerate(toks):
                t = _esc(tok)
                parts.append((hide + t) if starts[r] + q == j + 1 else t)
            text = row_text(r, parts)
            run = runs[r]
            if run and run[-1][2] == text and abs(run[-1][1] - s) < 0.005:
                run[-1][1] = e
            else:
                run.append([s, e, text])
    for r, run in enumerate(runs):
        for s, e, text in run:
            ev(r, s, e, "Main", text)


# ── v1.4.7: the soft shade under the no-stroke looks ─────────────────────

_SHADE_STRIP = __import__("re").compile(
    r"\\(?:[1-4]?c&H[0-9A-Fa-f]+&|[1-4]a&H[0-9A-Fa-f]+&|alpha&H[0-9A-Fa-f]+&"
    r"|[xy]?bord-?[\d.]+|[xy]?shad-?[\d.]+|blur[\d.]+|be[\d.]+)")
_EMPTY_T = __import__("re").compile(r"\\t\([\d.,\s]*\)")


def _with_shade(events, fs):
    """Under every Main line, the same text in a soft blurred black: the
    words read on any footage without a hard outline. Same font, size,
    tracking and scale tags, so it sits exactly under the letters; colours,
    borders and alpha runs are stripped (they belong to the top layer).
    Layers: Main at layer L becomes Shade 3L, (Spotlight's Box 3L+1), Main 3L+2."""
    import re as _re
    rx = _re.compile(r"^Dialogue: (\d+),([^,]+),([^,]+),Main,([^,]*),(\d+),(\d+),(\d+),([^,]*),(.*)$")
    out = []
    blur = "{\\blur%.1f}" % max(1.5, fs * 0.075)
    for line in events:
        m = rx.match(line)
        if not m:
            out.append(line)
            continue
        layer = int(m.group(1))
        text = _EMPTY_T.sub("", _SHADE_STRIP.sub("", m.group(9))).replace("{}", "")
        out.append(f"Dialogue: {layer * 3},{m.group(2)},{m.group(3)},Shade,{m.group(4)},"
                   f"{m.group(5)},{m.group(6)},{m.group(7)},{m.group(8)},{blur}{text}")
        out.append(f"Dialogue: {layer * 3 + 2},{m.group(2)},{m.group(3)},Main,{m.group(4)},"
                   f"{m.group(5)},{m.group(6)},{m.group(7)},{m.group(8)},{m.group(9)}")
    return out


# ── v1.4.6: overlap guard ────────────────────────────────────────────────

def _no_overlap(events):
    """Two events of the same layer + style must never share the screen.

    libass does NOT stack simultaneous events that share a position here:
    it draws them on top of each other (the "WE'350,000" mess). Each event
    is clipped to end when the next one of its track starts. Secondary
    (bilingual) lines are their own track and keep their timing."""
    import re as _re
    rx = _re.compile(r"^Dialogue: (\d+),([^,]+),([^,]+),([^,]*),")
    parsed = []
    for i, line in enumerate(events):
        m = rx.match(line)
        if not m:
            parsed.append((None, i, line))
            continue
        s, e = C.parse_ts(m.group(2) + "0"), C.parse_ts(m.group(3) + "0")
        parsed.append(((m.group(1), m.group(4)), i, line, s or 0.0, e or 0.0))
    tracks = {}
    for p in parsed:
        if p[0] is not None:
            tracks.setdefault(p[0], []).append(p)
    fixed = {}
    for key, rows in tracks.items():
        rows.sort(key=lambda r: (r[3], r[1]))
        for n, r in enumerate(rows):
            _k, i, line, s, e = r
            if n + 1 < len(rows) and rows[n + 1][3] < e:
                e = rows[n + 1][3]
            if e - s < 0.02:
                fixed[i] = None              # swallowed by its successor
                continue
            if e != r[4]:
                m = rx.match(line)
                line = (f"Dialogue: {m.group(1)},{m.group(2)},{C.fmt_ass_ts(e)},{m.group(4)},"
                        + line[m.end():])
            fixed[i] = line
    out = []
    for p in parsed:
        if p[0] is None:
            out.append(p[2])
        elif fixed.get(p[1]) is not None:
            out.append(fixed[p[1]])
    return out


# ── v1.4.6: the 2026 looks ───────────────────────────────────────────────

def _joining(lang):
    l = L.get(lang)
    return bool(l and l["script"] in _JOINING_SCRIPTS)


def _cs(sec):
    return max(1, int(round(sec * 100)))


def _lyric_events(ev, c, lang, max_line, base, mv=0):
    """Live lyrics: the whole line waits dimmed, each word fills to white
    as it is spoken (\\kf). The edge is blurred into a soft halo."""
    blur = "\\blur%.1f" % max(1.5, base * 0.0045)
    intro = "{\\fad(140,90)" + blur + "\\fscx98\\fscy98\\t(0,220,0.5,\\fscx100\\fscy100)}"
    body = _line_text(c.text, lang, False, max_line)
    if _joining(lang):
        # \\k splits the line into runs, which breaks joined scripts: the
        # whole line brightens instead
        ev(0, c.start, c.end, "Main", intro + "{\\1a&H66&\\t(0,260,\\1a&H00&)}" + body, mv)
        return
    rows = C.wrap_lines(_prep(c.text, lang, False), max_line).split("\n")
    toks = [t for r in rows for t in r.split()]
    words = list(c.words or [])
    if len(words) != len(toks):
        words = C.estimate_words(c.copy(words=[]))
    if len(words) != len(toks):
        ev(0, c.start, c.end, "Main", intro + body, mv)
        return
    lead = max(0.0, float(words[0][0]) - c.start)
    out_rows, i = [], 0
    for r in rows:
        parts = []
        for tok in r.split():
            a = float(words[i][0])
            b = float(words[i + 1][0]) if i + 1 < len(words) else max(a + 0.2, float(words[i][1]))
            dur = max(0.04, min(b, c.end) - a)
            pre = ("{\\k%d}" % _cs(lead)) if (i == 0 and lead > 0.01) else ""
            parts.append(pre + "{\\kf%d}" % _cs(dur) + _esc(tok))
            i += 1
        out_rows.append(" ".join(parts))
    ev(0, c.start, c.end, "Main", intro + "\\N".join(out_rows), mv)


def _spotlight_events(ev, c, lang, pill, em, line_h, geo, mv=0):
    """1-4 words at a time; the spoken word sits on a rounded accent box.

    v1.4.12: two events per step, both \\an5 at the same \\pos with the
    SAME string. "Box" is that string squashed into rounded boxes
    (_squash), visible (\\3a 00) only under the spoken word; "Main" draws
    the crisp words on top. The box pops in from a slightly narrower pill
    on every step. RTL scripts run word by word as well (_vis_rtl): before,
    Persian lit the whole phrase at once."""
    words = C.estimate_words(c)
    if not words:
        return
    rtl = L.is_rtl(lang)
    xb, yb = em * 0.26, line_h * 0.42 + em * 0.04
    x = int(round(geo[0] / 2))
    y = int(round(_row_centres(1, 0, yb, geo, mv)[0]))
    pos = "\\an5\\pos(%d,%d)" % (x, y)
    # starts a touch narrower and grows to full width: the pill "lands"
    box_head = "{" + pos + _squash(xb * 0.6, yb) + "\\t(0,90,\\xbord%.1f)}" % xb
    on, off = "{\\3a&H00&}", "{\\3a&HFF&}"
    sep = " {\\fscx45}\\h{\\fscx100}"
    for group in _chunks(words, lang, max_words=3 if rtl else 4, max_chars=20):
        g_start = group[0][0]
        g_end = min(c.end, max(group[-1][1], g_start + 0.25))
        toks = [_esc(_prep(w[2], lang, True)) for w in group]

        def line(parts):
            return _vis_rtl(parts, sep) if rtl else sep.join(parts)
        plain = line(toks)
        for j, w in enumerate(group):
            s = w[0] if j else g_start
            e = group[j + 1][0] if j + 1 < len(group) else g_end
            if e - s < 0.02:
                continue
            fade = "{\\fad(70,0)}" if j == 0 else ""
            boxed = line([(on + t + off) if k2 == j else t for k2, t in enumerate(toks)])
            ev(1, s, e, "Box", fade + box_head + off + boxed, mv)
            ev(0, s, e, "Main", fade + "{" + pos + "}" + plain, mv)


def _glass_events(ev, c, lang, max_line, em, box, geo, mv=0):
    """v1.4.12 Glass: every wrapped row gets its own rounded frosted box
    (GlassBox, _squash) and its own crisp text event, \\an5 at the row's
    centre, one `pitch` apart: rows never touch, whatever the script."""
    gl_x, gl_y, pitch = box
    rtl = L.is_rtl(lang)
    rows = [r for r in C.wrap_lines(_prep(c.text, lang, False), max_line).split("\n") if r.strip()]
    if not rows:
        return
    x = int(round(geo[0] / 2))
    ys = _row_centres(len(rows), pitch, gl_y, geo, mv)
    fade = "\\fad(120,80)"
    for r, (row, y) in enumerate(zip(rows, ys)):
        t = _esc(row)
        if rtl:
            t = _RLM + t
        pos = "\\an5\\pos(%d,%d)" % (x, int(round(y)))
        ev(r * 2, c.start, c.end, "GlassBox", "{" + pos + fade + _squash(gl_x, gl_y, 0.9) + "}" + t, mv)
        ev(r * 2 + 1, c.start, c.end, "Main", "{" + pos + fade + "}" + t, mv)


_PUNCH_HOT = __import__("re").compile(r"[0-9$€£%]|^.{8,}$|!$")


def _punch_events(ev, c, lang, accent, mv=0):
    """One word at a time, huge, slamming in. A word spoken in under a
    quarter second shares the screen with the next one, so it never
    strobes. Numbers, money and long words land in the accent."""
    words = C.estimate_words(c)
    if not words:
        return
    ac = _ovr_color(accent)
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if (w[1] - cur[0][0]) >= 0.24 or len(cur) >= 2:
            groups.append(cur)
            cur = []
    if cur:
        if groups and len(groups[-1]) == 1 and (cur[-1][1] - cur[0][0]) < 0.24:
            groups[-1] = groups[-1] + cur
        else:
            groups.append(cur)
    rtl = L.is_rtl(lang)
    for n, g in enumerate(groups):
        s = g[0][0]
        nxt = groups[n + 1][0][0] if n + 1 < len(groups) else c.end
        e = min(c.end, nxt, max(g[-1][1] + 0.18, s + 0.3)) if nxt - g[-1][1] > 0.6 else min(c.end, nxt)
        if e - s < 0.04:
            continue
        parts = []
        for w in g:
            t = _esc(_prep(w[2], lang, True))
            parts.append(("{\\c" + ac + "}" + t + "{\\c&HFFFFFF&}") if _PUNCH_HOT.search(w[2]) else t)
        text = " ".join(parts)
        if rtl:
            text = _RLM + text
        ev(0, s, e, "Main", "{\\fscx128\\fscy128\\t(0,90,\\fscx96\\fscy96)\\t(90,170,\\fscx100\\fscy100)}"
           + text, mv)


def _aura_events(ev, c, lang, max_line, fs, mv=0):
    """White line on a soft accent bloom; rises in with a tracking pull."""
    body = _line_text(c.text, lang, False, max_line)
    pull = "" if _joining(lang) else "\\fsp%.1f\\t(0,260,0.5,\\fsp0.6)" % (fs * 0.08)
    rise = "\\fad(160,110)\\fscx96\\fscy96\\t(0,260,0.5,\\fscx100\\fscy100)"
    ev(0, c.start, c.end, "Bloom", "{" + rise + pull + "\\blur%.1f}" % max(4.0, fs * 0.35) + body, mv)
    ev(1, c.start, c.end, "Main", "{" + rise + pull + "\\blur0.6}" + body, mv)
