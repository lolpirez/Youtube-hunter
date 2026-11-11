"""app.core.captions.polish - YouTube Hunter (v2.0).

Deterministic finishing for caption lines: no AI, no tokens, runs on every job.

  * fa_typography(): Persian that looks professionally typeset: Persian
    ye / kaf (not Arabic ي ك), «؟ ، ؛» instead of Latin ? , ;, no space before
    punctuation, نیم‌فاصله in می‌/نمی‌ and ‌ها/‌تر/‌ترین, Persian digits.
  * reading_hold(): every line stays up long enough to be READ (characters
    per second for the script), borrowing time from the gap after it, and
    tiny gaps between lines are closed so captions don't flicker.
"""

import re

from app.core.captions import cues as C
from app.core.captions import languages as L

_FA = "\u0600-\u06FF"
_DIG = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_typography(text):
    t = str(text or "")
    t = t.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    t = re.sub(r"(?<=[" + _FA + r"])\s*\?", "؟", t)
    t = re.sub(r"(?<=[" + _FA + r"]),", "،", t)
    t = re.sub(r"(?<=[" + _FA + r"]);", "؛", t)
    t = re.sub(r"[ \t]+([،؛؟!.:…])", r"\1", t)
    t = re.sub(r"(?<![" + _FA + r"])(ن?می) (?=[" + _FA + r"]{2,})", "\\1\u200c", t)
    t = re.sub(r"(?<=[" + _FA + r"]) (ها|های|هایی|هایم|هایش|تر|ترین)(?![\u0620-\u064A\u067E-\u06D3])", "\u200c\\1", t)
    t = re.sub(r"(?<![A-Za-z0-9])\d+(?![A-Za-z])", lambda m: m.group(0).translate(_DIG), t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _cps(lang):
    if L.is_cjk(lang):
        return 7.0
    return 14.0 if str(lang or "").split("-")[0].lower() in ("fa", "ar", "ur", "he") else 15.0


def reading_hold(cues, lang, min_dur=0.9, max_extra=1.2, gap=0.3):
    out = [c.copy() for c in cues]
    cps = _cps(lang)
    for i, c in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else None
        need = max(min_dur, len(C.flat(c.text)) / cps)
        if c.dur < need:
            limit = (nxt - 0.08) if nxt is not None else c.end + max_extra
            c.end = max(c.end, min(c.start + need, c.end + max_extra, limit))
        if nxt is not None and 0 < nxt - c.end < gap:
            c.end = max(c.end, nxt - 0.03)        # no flicker between lines
    return out


def finish(cues, lang):
    """Every caption job's last touch before the .srt is written."""
    if str(lang or "").split("-")[0].lower() == "fa":
        cues = [c.copy(text=fa_typography(c.text)) for c in cues]
    return reading_hold(cues, lang)
