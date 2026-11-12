"""app.core.captions.translate - YouTube Hunter.

Translation that reads like a person wrote it, through whichever AI provider
is active in Settings.

How it gets there:
  1. A brief first: one call reads the opening of the transcript and returns
     the topic, tone, audience, how people address each other, and a glossary
     (names, brands, recurring terms → how to render them). Every later batch
     carries it, so batches agree with each other.
  2. Batches of ~28 lines with the lines before and after as context (never
     translated, only read), so a sentence split across captions is
     translated as one thought.
  3. Two registers:
       subtitle  reading speed matters: condense, max 2 short lines
       dub       mouth timing matters: each line says ~what fits in its
                 seconds, spoken register, vocal tags like <laugh> preserved
  4. Strict JSON by id, then validation: a missing / empty / untranslated
     line is retried on its own; anything still missing keeps its original
     text rather than silently vanishing.

v1.4 "lean tokens" (same quality, far fewer tokens):
  * every translation call runs with the reasoning knob turned DOWN
    ("low" on Gemini / OpenAI reasoning models, none on Claude). Left on the
    user's global effort, a Gemini 2.5 call spent up to 12-24k hidden
    thinking tokens per batch of 28 lines, billed as output;
  * no brief for a short transcript (one batch): the batch already carries
    the title and the surrounding lines, and the extra call cost more than
    the lines themselves on a typical Cutter clip;
  * the brief reads 4,000 characters (was 7,000), answers in at most 1,200
    tokens and is cached on disk per transcript + language, so captions,
    a dub and every re-run of the same file share one brief;
  * batches of 40 lines with 4 / 2 context lines (was 28 with 6 / 3): the
    rules + brief header is sent ~30 % less often;
  * the second pass only retries lines that came back missing or clearly
    untranslated (24+ characters identical to the source), not names.

v1.4.9 "no wasted calls":
  * a quota / rate-limit / bad-key / no-network answer on ANY batch stops
    the whole job at once: the batches still waiting are never sent and the
    second pass is skipped (before, three workers kept firing every
    remaining batch into the same refusal, and each one could be billed);
  * every finished batch is cached on disk (per lines + languages + mode),
    so Resume after a failure only pays for the batches that didn't land;
  * the line listing is compact JSON (no spaces after , and :), ~8 % fewer
    input tokens on every batch.

v1.4.12 "fluent from YouTube's words":
  * raw_asr=True: the lines are YouTube's AUTO captions (no punctuation,
    misheard words, a sentence cut across 2-3 lines). The rules say so, so
    the model reads the run of lines as speech, fixes obvious mishearings
    from context and writes each line as a fluent piece of one sentence,
    instead of translating every fragment on its own. Same calls, a few
    dozen extra prompt tokens per batch.
  * mode="polish": same language in and out. Used when YouTube's auto
    captions are kept in the spoken language: punctuation, casing and
    obvious mishearings are fixed, nothing is translated or reworded.
"""

import concurrent.futures as cf
import hashlib
import json
import os
import re
import threading

from app.core.captions import cues as C
from app.core.captions import languages as L

_BATCH = 40
_CTX_BEFORE = 4
_CTX_AFTER = 2
# v1.4: below this many lines the translation runs without a brief
_BRIEF_MIN_LINES = _BATCH + 1

# Natural dubbing pace in characters per second (spaces included). Used to
# give every dub line a character budget up front, so lines are written to
# fit instead of being voiced, found too long, rewritten and voiced again
# (v1.3.1: that second TTS call was the biggest waste in a dub).
_RATE = {"en": 14.5, "es": 15.0, "it": 15.0, "pt": 14.5, "fr": 14.0, "de": 13.0, "nl": 13.5,
         "ru": 13.0, "uk": 13.0, "pl": 13.0, "tr": 13.0, "fa": 12.5, "ar": 12.0, "ur": 12.0,
         "he": 12.5, "hi": 12.0, "id": 14.0, "vi": 12.0, "th": 10.0,
         "ja": 7.5, "zh": 5.5, "ko": 7.0}


def speak_rate(lang):
    return _RATE.get((lang or "").split("-")[0].lower(), 13.5)


# v1.8: marks that change how a word is SAID, not how long it takes:
# harakat / tanvin / sukun / shadda / superscript alef, ZWNJ, vocal tags
_MARKS = re.compile("[\u064b-\u065f\u0670\u200c]")
_VOCAL = re.compile(r"<[a-z][a-z \-]{1,24}>|\[[a-z][a-z \-]{1,24}\]", re.I)
# the short vowels a reader doesn't need on screen (tanvin, shadda and hamza stay)
_SHORT_VOWELS = re.compile("[\u064e\u064f\u0650\u0652]")


def spoken_len(text):
    """v1.8: characters that take time to say. Diacritics added for the voice
    (اعراب) and ZWNJ must not make a line look "too long" (that triggered a
    paid tightening call for nothing)."""
    t = _VOCAL.sub(" ", str(text or ""))
    return len(re.sub(r"\s{2,}", " ", _MARKS.sub("", t)).strip())


def caption_text(text, lang):
    """v1.8: the dub's script as captions. The voice gets the diacritics; the
    screen gets clean Persian (short vowels off, tanvin / shadda kept)."""
    t = str(text or "")
    if str(lang or "").split("-")[0].lower() == "fa":
        t = _SHORT_VOWELS.sub("", t)
    return t


def char_budget(seconds, lang):
    return max(8, int(seconds * speak_rate(lang)))


def _client(feature="captions"):
    """The active provider, with its reasoning turned down for this job.

    Translating subtitle lines is pattern work, not a reasoning problem: the
    global "effort" setting (made for reports and blueprints) must not make
    every batch think. Claude gets no thinking at all (its "low" would
    switch thinking ON with a 1,024-token budget)."""
    from app.core.ai import profiles
    # v1.4.8: captions and the dub each follow their own pick in Settings →
    # Which AI does what (the dub is held to Gemini there)
    c = profiles.make_client(feature)
    try:
        provider = str(getattr(c, "provider", "") or "").lower()
        mode = str(getattr(c, "api_mode", "") or "")
        # v1.4.1: "none" (was "low"): Gemini 2.5 Flash went from a
        # 512-token thinking budget per batch to 0, Pro to its 128 floor.
        c.effort = "auto" if (provider in ("anthropic", "claude")
                              or mode == "anthropic_messages") else "none"
    except Exception:                    # noqa: BLE001 — a plain client is fine
        pass
    return c


def provider_label(feature="captions"):
    try:
        return _client(feature).label
    except Exception:                    # noqa: BLE001
        return ""


def ai_ready(feature="captions"):
    try:
        from app.core.ai import profiles
        return profiles.is_ready(profiles.resolve(feature))
    except Exception:                    # noqa: BLE001
        return False


class NotJSON(ValueError):
    """v1.4.11: the model answered, but not with JSON (prose, a thinking
    block, a cut-off reply). Was a bare "Expecting value: line 1 column 1
    (char 0)" in the activity card."""


_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)
_THINK_OPEN = re.compile(r"^\s*<(think|thinking|reasoning)>", re.I)


def _json(text):
    """The JSON value in a model reply. Copes with ```json fences, a
    <think>...</think> block in front (GLM, Qwen3, DeepSeek-R1 and other
    reasoning models on routers that inline it), a sentence before or after
    the JSON, and several JSON values (the first object/array wins)."""
    t = (text or "").strip()
    t = _THINK_BLOCK.sub("", t).strip()
    t = _THINK_OPEN.sub("", t).strip()          # a thinking block cut off mid-way
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except Exception:                    # noqa: BLE001
        pass
    dec = json.JSONDecoder()
    tries = 0
    for m in re.finditer(r"[\{\[]", t):
        tries += 1
        if tries > 300:
            break
        try:
            obj, _end = dec.raw_decode(t, m.start())
        except ValueError:
            continue
        if isinstance(obj, (dict, list)) and obj:
            return obj
    snippet = re.sub(r"\s+", " ", t)[:80]
    raise NotJSON("not JSON: " + (snippet or "(empty reply)"))


def _lang_label(code):
    l = L.get(code)
    if not l:
        return code or "the original language"
    return f"{l['name']} ({l['native']})" if l["native"] != l["name"] else l["name"]


# ── v2.1: no stray foreign / garbled words ───────────────────────────────
# A model can drop a token from another language into a line ("حالا می‌istem",
# "این écran اصلی است") or half-transliterate a word. On any script that is
# not Latin the intruder is visible at a glance, so such tokens are found
# deterministically and only the affected lines are rewritten once. On
# Latin-script targets the guard looks for non-Latin intruders instead.
_FOREIGN_OK = frozenset("""youtube google instagram telegram whatsapp twitter tiktok facebook
iphone ipad android windows linux macos ios app apple microsoft amazon netflix spotify chatgpt
openai gpt ai api apis gpu cpu ram ssd usb hdmi wifi bluetooth fps hd uhd oled pdf url html css
vpn ok okay vs tv pc email online offline studio pro max plus ultra lite mini claude gemini
anthropic fable premiere photoshop davinci resolve figma notion obs linkedin reddit snapchat
capcut fiverr upwork""".split())
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\S+\.(?:com|org|net|io|dev|app|co|tv|me)\b", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]{1,60}>")
_LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z'’\-]*(?![A-Za-z0-9])")
# a run of non-Latin letters inside a Latin-script line is an intruder
_NON_LATIN_RUN = re.compile(
    "[Ͱ-ϿЀ-ӿ֐-׿؀-ۿ܀-ݏ"
    "ऀ-ൿ฀-๿ሀ-፿-ヿ-鿿"
    "가-힯]{2,}")


def _word_in(needle, haystack):
    if not needle or not haystack:
        return False
    if _LATIN_TOKEN.fullmatch(needle):
        return re.search(r"(?<![A-Za-z])" + re.escape(needle) + r"(?![A-Za-z])",
                         haystack, re.I) is not None
    return needle.casefold() in haystack.casefold()


def foreign_tokens(text, src_text="", brief_txt="", tgt=""):
    """Tokens that cannot belong to `tgt`: Latin words inside a non-Latin
    line (or non-Latin words inside a Latin line) that the source and the
    brief do not contain and that are not a known brand / technical term.
    Uppercase acronyms and URLs are left alone."""
    rec = L.get(tgt)
    if not rec:
        return []
    line = _URL_RE.sub(" ", _ANY_TAG_RE.sub(" ", str(text or "")))
    src, brief = str(src_text or ""), str(brief_txt or "")
    out = []
    if rec["script"] == "latn":
        for m in _NON_LATIN_RUN.finditer(line):
            tok = m.group(0)
            if _word_in(tok, src) or _word_in(tok, brief):
                continue
            out.append(tok)
        return out
    for m in _LATIN_TOKEN.finditer(line):
        tok = m.group(0)
        core = re.sub(r"['’]s$", "", tok)
        low = core.lower()
        if len(core) < 2 or core.isupper() or low in _FOREIGN_OK:
            continue
        if _word_in(low, src) or _word_in(low, brief):
            continue
        out.append(tok)
    return out


_SYS_REPAIR = ("You are a meticulous {tgt} subtitle editor. You remove words that are not "
               "{tgt} and keep every line natural, faithful and about the same length.")

_REPAIR_PROMPT = """These {tgt} subtitle lines contain a word that is not {tgt}: an untranslated, half-translated or garbled token (like "istem" inside a Persian sentence). Rewrite ONLY these lines the way a native {tgt} speaker would say them: replace or drop the stray token, keep the meaning, keep names and numbers, keep the line about the same length. A real name or brand that the SOURCE line itself writes in Latin letters stays in Latin letters.

SOURCE → CURRENT ("id", "src", "x"):
{lines}

Return JSON only: {{"t": [{{"id": <id>, "x": "<fixed line>"}}, …]}}"""


def _repair_foreign(out, tgt, client, src="", brief_txt="", cache_dir="", should_cancel=None):
    """One targeted AI pass over ONLY the lines that carry a foreign /
    garbled token. Returns (cues, n_fixed). Never fails the job."""
    if not L.get(tgt) or not out:
        return out, 0
    flagged = []
    for i, c in enumerate(out):
        toks = foreign_tokens(c.text, c.meta.get("src", ""), brief_txt, tgt)
        if toks:
            flagged.append((i, toks))
    if not flagged:
        return out, 0
    res = [c.copy() for c in out]
    fixed = 0
    for k in range(0, len(flagged), 20):
        if should_cancel and should_cancel():
            break
        chunk = flagged[k:k + 20]
        listing = "\n".join(json.dumps({"id": i, "src": C.flat(res[i].meta.get("src", "")),
                                        "x": C.flat(res[i].text)},
                                       ensure_ascii=False, separators=_COMPACT)
                            for i, _t in chunk)
        path = ""
        if cache_dir:
            h = hashlib.sha1((listing + "|" + tgt + "|" + brief_txt).encode("utf-8", "replace")).hexdigest()[:16]
            path = os.path.join(cache_dir, f"fx_{(tgt or 'x').split('-')[0]}_{h}.json")
        got = None
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    got = {int(a): b for a, b in json.load(f).items()}
            except Exception:            # noqa: BLE001
                got = None
        if got is None:
            prompt = _REPAIR_PROMPT.format(tgt=_lang_label(tgt), lines=listing)
            try:
                data = _json(client.generate(prompt, _SYS_REPAIR.format(tgt=_lang_label(tgt)),
                                             timeout=120,
                                             max_output_tokens=min(6000, 200 + 120 * len(chunk))))
            except Exception:            # noqa: BLE001 — keep the lines as they are
                break
            got = {}
            for item in (data.get("t") if isinstance(data, dict) else data) or []:
                try:
                    i = int(item.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                x = C.clean_text(str(item.get("x") or "")).replace("\n", " ")
                if x:
                    got[i] = x
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({str(a): b for a, b in got.items()}, f, ensure_ascii=False)
                except OSError:
                    pass
        for i, toks in chunk:
            x = got.get(i)
            if not x or not (0 <= i < len(res)):
                continue
            old = res[i].text
            if len(foreign_tokens(x, res[i].meta.get("src", ""), brief_txt, tgt)) >= len(toks):
                continue
            if len(C.flat(x)) > max(12, int(len(C.flat(old)) * 1.5)):
                continue
            if x != old:
                res[i] = res[i].copy(text=x, meta=dict(res[i].meta, repaired=True))
                fixed += 1
    return res, fixed


# ── brief ────────────────────────────────────────────────────────────────

_BRIEF_SYS = "You are a senior audiovisual translator preparing a translation brief."
_BRIEF_PROMPT = """Below is the opening of a video transcript (source language: {src}).
The translation target is {tgt}{title_line}.

Write a brief a translator will follow. JSON only:
{{
 "summary": "2 sentences: what the video is about",
 "tone": "e.g. casual and funny / calm tutorial / hype gaming / serious news",
 "audience": "who is watching",
 "source_lang": "BCP-47 code of the transcript's language, e.g. en",
 "address": "how speakers address the viewer and each other, and the matching register in the target language (e.g. Persian: محاوره‌ای و صمیمی، «تو»)",
 "glossary": [{{"src": "term", "dst": "rendering in target language", "note": "keep / translate / transliterate"}}]
}}
Glossary: names, brands, channel-specific terms, technical words. At most 15 entries; keep every field short.

TRANSCRIPT:
{text}"""


_TAG_RE = re.compile(r"<[^<>]{1,24}>")


def _brief_key(cues, src, tgt):
    import hashlib
    # vocal tags and line grouping differ between the caption cues and the
    # dub's utterances; the words don't, so both share one brief
    text = " ".join(_TAG_RE.sub("", C.flat(c.text)) for c in cues)
    text = re.sub(r"\s+", " ", text).strip()[:4000]
    h = hashlib.sha1(f"{src}|{tgt}|{text}".encode("utf-8", "replace")).hexdigest()[:16]
    return f"brief_{(tgt or 'x').split('-')[0]}_{h}.json"


def make_brief(cues, src, tgt, title="", client=None, cache_dir=""):
    path = ""
    if cache_dir:
        import os
        path = os.path.join(cache_dir, _brief_key(cues, src, tgt))
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:            # noqa: BLE001
                pass
    client = client or _client("captions")
    text = "\n".join(C.flat(c.text) for c in cues)[:4000]
    prompt = _BRIEF_PROMPT.format(src=_lang_label(src), tgt=_lang_label(tgt), text=text,
                                  title_line=f' and the video title is "{title}"' if title else "")
    try:
        data = _json(client.generate(prompt, _BRIEF_SYS, timeout=120, max_output_tokens=1200))
        data = data if isinstance(data, dict) else {}
    except Exception as e:               # noqa: BLE001 — a brief is a bonus...
        if _is_fatal(e):
            raise                        # ...but quota / bad key stops the job at once
        return {}
    if path and data:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass
    return data


def _brief_text(brief):
    if not brief:
        return ""
    parts = []
    for k in ("summary", "tone", "audience", "address"):
        if brief.get(k):
            parts.append(f"- {k}: {brief[k]}")
    gl = brief.get("glossary") or []
    if gl:
        parts.append("- glossary:")
        for g in gl[:15]:
            if isinstance(g, dict) and g.get("src"):
                parts.append(f"    {g.get('src')} → {g.get('dst', '')} {('(' + g['note'] + ')') if g.get('note') else ''}")
    return "\n".join(parts)


# ── batches ──────────────────────────────────────────────────────────────

_SYS_SUB = """You are an award-winning subtitle translator and a native speaker of the target language. You never translate word-for-word: you hear what the person MEANS and write how a native speaker would actually SAY it in that moment: everyday words, natural idioms instead of literal ones, the speaker's energy and humour. A viewer must never feel they are reading a translation, and never feel it is bookish or stiff. Names and numbers stay correct; you never add or drop information that matters."""

# v2.0: how the translation talks. "natural" is the default: spoken, never bookish.
REGISTERS = (
    ("natural", "Natural", "Spoken and fluent, the way a native YouTuber says it. Never bookish."),
    ("casual", "Casual", "Chatty and street-level: slang and contractions, like friends talking."),
    ("formal", "Formal", "Clean standard language for news, lectures and documentaries."),
)
_REGISTER_KEYS = tuple(k for k, _n, _d in REGISTERS)

_REG_RULES = {
    "natural": """
- Register: SPOKEN. Write it the way people really talk on YouTube in {tgt}, not the way a book, a dictionary or the news would. Short everyday words over formal ones; active voice; natural word order of {tgt}.
- Kill translationese: no literal calques of English structure ("this is a ... that ...", "in fact" on every line, "you know" / "like" / "so" translated literally); drop filler that a native wouldn't say.""",
    "casual": """
- Register: CASUAL. Friends talking: slang, contractions and colloquial grammar of {tgt} wherever the speaker is relaxed; jokes and swearing keep their strength. Never stiff.""",
    "formal": """
- Register: FORMAL but natural: clean standard {tgt}, correct grammar, no slang; still fluent and modern, never archaic or bureaucratic.""",
}

_FA_SUB = {
    "natural": """
- Persian (فارسی): محاوره‌ای و روان، مثل یک یوتیوبر ایرانی که خودش حرف می‌زند؛ نه کتابی و نه خبری. Use spoken forms where they read naturally: می‌خوام، می‌تونم، نمی‌دونم، اینو، اونجا، یه، چی، الان، خیلی، واقعاً. NEVER bookish words: «می‌باشد، گردید، نمودن، جهت، لذا، اینجانب، فلذا، همچنین» (use «هست، شد، کردن، برای، پس، من، تازه/هم»). Idioms by meaning: "piece of cake" → «مثل آب خوردن»، "no way" → «امکان نداره» / «عمراً»، "guys" → «بچه‌ها»، "Oh my god" → «وای خدای من» / «وای». Persian punctuation «؟ ، !»، نیم‌فاصله in می‌/نمی‌ and ‌ها. Keep Latin brand names as they are.""",
    "casual": """
- Persian (فارسی): کاملاً خودمونی و شکسته، مثل حرف زدن دو تا رفیق: می‌خوام، نمی‌دونم، چیکار، اینجوری، خفن، باحال، داداش (when it fits). Strong reactions stay strong. Persian punctuation «؟ ، !»، نیم‌فاصله in می‌/نمی‌ and ‌ها.""",
    "formal": """
- Persian (فارسی): فارسیِ معیار و روان (نوشتاری ولی امروزی)، نه شکسته و نه کتابی‌ـاداری. Avoid «می‌باشد، گردید، نمودن، جهت، لذا». Persian punctuation «؟ ، ؛»، نیم‌فاصله where Persian spelling needs it.""",
}

# v2.0: the biggest cause of stiff subtitles is translating fragments
_SENTENCE = """
- Think in SENTENCES, not lines: when one spoken sentence runs across several ids, translate the whole sentence first in natural {tgt} word order, then split it back across those ids at natural break points. A word may move to a neighbouring id when {tgt} grammar needs it; never leave an id empty."""


def register_rules(tgt, register):
    reg = register if register in _REGISTER_KEYS else "natural"
    out = _REG_RULES[reg].format(tgt=_lang_label(tgt)) + _SENTENCE.format(tgt=_lang_label(tgt))
    if str(tgt or "").split("-")[0].lower() == "fa":
        out += _FA_SUB[reg]
    return out


_SYS_REFINE = """You are a native {tgt} subtitle editor with a great ear. You get subtitles that were translated from another language. You fix every line that still sounds translated, stiff, bookish or unnatural, so it reads exactly like a native speaker talking. You keep the meaning, names and numbers. You leave good lines alone."""

_REFINE_PROMPT = """These {tgt} subtitles were translated from {src}{title_line}.

BRIEF:
{brief}

Rules:
- Rewrite ONLY the lines that sound translated, literal, bookish or stiff in {tgt}. Leave natural lines out of your answer.{reg}
- Same meaning, same speaker intent. Keep names, numbers and brand names exactly.
- About the same length (never more than ~15% longer): these are subtitles.
- A sentence that runs across several ids stays split across the same ids.
- Never introduce a word that is not {tgt}: no foreign, half-translated or garbled token, no transliteration in the wrong script.
- Never cut a line down to a fragment or drop its words; leave short lines (a word or two) exactly as they are.
- No full stop at the end of a line; when the sentence continues in the next id, end with no punctuation or a comma.

LINES ("src" = original, "x" = current subtitle):
{lines}

Return JSON only, with the changed lines only (empty list when all are fine): {{"t": [{{"id": <id>, "x": "<better line>"}}, …]}}"""

# v2.1: a rewrite is only accepted when it cannot have damaged the line.
_REFINE_MIN_CHARS = 12


def _refine_ok(old, new, src, brief_txt, tgt):
    if foreign_tokens(new, src, brief_txt, tgt):
        return False
    if len(C.flat(new)) < max(6, int(len(C.flat(old)) * 0.55)):
        return False
    if set(re.findall(r"\d+", old)) - set(re.findall(r"\d+", new)):
        return False
    old_lat = {t.lower() for t in _LATIN_TOKEN.findall(old)
               if len(t) > 1 and (t.lower() in _FOREIGN_OK or _word_in(t, src))}
    new_lat = {t.lower() for t in _LATIN_TOKEN.findall(new)}
    if old_lat - new_lat:
        return False
    return True


def refine_cues(out, src, tgt, register="natural", title="", client=None, brief_txt="",
                cache_dir="", should_cancel=None, on_progress=None):
    """v2.0 native-editor pass. Returns (cues, n_changed). Never fails the
    job: any error keeps the first-pass lines. v2.1: batches run in parallel
    and a rewrite that adds a foreign token, guts the line or drops a
    number / brand is rejected."""
    client = client or _client("captions")
    reg = register_rules(tgt, register)
    res = [c.copy() for c in out]
    texts = [C.flat(c.text) for c in res]
    srcs = [C.flat(c.meta.get("src", "")) for c in res]
    untr = [bool(c.meta.get("untranslated")) for c in res]
    batches = [list(range(i, min(len(res), i + _BATCH))) for i in range(0, len(res), _BATCH)]
    lock = threading.Lock()
    changed = [0]
    done = [0]

    def work(ids):
        if should_cancel and should_cancel():
            return
        listing = "\n".join(json.dumps({"id": i, "src": srcs[i], "x": texts[i]},
                                       ensure_ascii=False, separators=_COMPACT)
                            for i in ids if not untr[i] and len(texts[i]) >= _REFINE_MIN_CHARS)
        if not listing:
            with lock:
                done[0] += 1
            return
        path = ""
        if cache_dir:
            h = hashlib.sha1((listing + "|" + str(register) + "|" + brief_txt).encode("utf-8", "replace")).hexdigest()[:16]
            path = os.path.join(cache_dir, f"rf2_{(tgt or 'x').split('-')[0]}_{h}.json")
        got = None
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    got = {int(k): v for k, v in json.load(f).items()}
            except Exception:            # noqa: BLE001
                got = None
        if got is None:
            prompt = _REFINE_PROMPT.format(tgt=_lang_label(tgt), src=_lang_label(src),
                                           title_line=f' (video: "{title}")' if title else "",
                                           brief=brief_txt or "(none)", reg=reg, lines=listing)
            try:
                data = _json(client.generate(prompt, _SYS_REFINE.format(tgt=_lang_label(tgt)), timeout=180,
                                             max_output_tokens=min(8000, 300 + 120 * len(ids))))
            except Exception:            # noqa: BLE001 - the first pass stands
                return
            got = {}
            for item in (data.get("t") if isinstance(data, dict) else data) or []:
                try:
                    i = int(item.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                x = C.clean_text(str(item.get("x") or "")).replace("\n", " ")
                if (i in ids and x and len(x) <= max(12, int(len(texts[i]) * 1.35))
                        and _refine_ok(texts[i], x, srcs[i], brief_txt, tgt)):
                    got[i] = x
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({str(k): v for k, v in got.items()}, f, ensure_ascii=False)
                except OSError:
                    pass
        with lock:
            for i, x in got.items():
                if 0 <= i < len(res) and x != res[i].text:
                    res[i] = res[i].copy(text=x, meta=dict(res[i].meta, refined=True))
                    changed[0] += 1
            done[0] += 1
            if on_progress:
                on_progress(done[0] / len(batches))

    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(work, batches))
    return res, changed[0]

_SYS_DUB = """You are a dubbing adapter for film and YouTube. You rewrite lines in the target language so a voice actor can SAY them naturally in the time the original line takes. Spoken register, natural rhythm, the same emotion and intent. You keep meaning faithful and keep names and numbers correct."""

_RULES_SUB = """Rules:
- Natural, fluent {tgt} as a native speaker would write it for this audience. No literal calques.
- Keep the speaker's tone ({tone}). Jokes become jokes in {tgt}.
- Each line must stay readable in its time: about {cps} characters per second at most; condense filler if needed, never drop meaning.
- One translation per id. Do not merge or split ids. Do not translate the CONTEXT lines.
- No quotes around lines, no notes, no romanisation.{rtl_rule}"""

_RULES_DUB = """Rules:
- Spoken, natural {tgt} that a native voice actor would say. Contractions and colloquial grammar where the original is casual.
- "sec" is how long the original line lasts and "max" is its character budget in {tgt} (tags not counted): stay at or under "max" so it can be spoken in that time at a natural pace. Shorter is fine, longer is not. Drop filler before meaning.
- Keep emotion and intent ({tone}).
- Keep vocal tags exactly where they belong in the sentence: <laugh>, <sigh>, <gasp>, <short pause>, <breath> … (English tags, angle brackets).
- One translation per id. Do not merge or split ids. Do not translate the CONTEXT lines.
- No stage directions in words, no notes.{rtl_rule}"""

# v1.8: a Persian dub is READ by a TTS voice: the spelling is the pronunciation
_FA_DUB = """
- Persian script for the voice (a TTS voice reads this line, so the spelling IS the pronunciation):
  * Persian punctuation that carries the intonation: «،» for a breath, «.» to end, «؟» on every question (without it the question is read flat), «!» only for real excitement, «…» for a trailing or hesitant end. Never Latin ? , ;
  * نیم‌فاصله (ZWNJ) wherever Persian spelling needs it: می‌خوام، نمی‌دونم، کتاب‌ها، خونه‌ات، بزرگ‌تر. Never a plain space there.
  * اعراب ONLY where a word would otherwise be misread: homographs (کُشت / کِشت، مُرد / مَرد، گُل / گِل)، the ezafe kasre when the phrase is ambiguous (دستِ راست)، tanvin as it is spelled (واقعاً، حتماً). Do not vocalise every word. Diacritics and ZWNJ don't count toward "max".
  * Numbers, symbols and abbreviations written the way they are said: ۲۵٪ → بیست‌وپنج درصد، $10 → ده دلار.
  * When the register is casual, colloquial Persian spelled as it is pronounced (می‌خوام، نمی‌دونم، اینجوری)."""

_FA_SHORT = (" Keep the Persian punctuation (، ؟ !)، the نیم‌فاصله and the few diacritics that fix "
             "pronunciation; diacritics don't count toward \"max\".")


def _script_hint(tgt):
    return _FA_SHORT if str(tgt or "").split("-")[0].lower() == "fa" else ""


def _script_rule(tgt):
    """v2.1: applies to EVERY target language, not only RTL ones."""
    return ("\n- Write ONLY {tgt}. Never leave a word from another language, a half-translated or "
            "garbled token, or a transliteration in the wrong script; a name/brand the source writes "
            "in Latin letters stays exactly as the source writes it."
            "\n- No full stop at the end of a line; when the sentence continues in the next id, end "
            "with no punctuation or a comma.").format(tgt=_lang_label(tgt))


# v1.4.12: YouTube's auto captions as the source
_RAW_ASR = """
- The source lines are AUTOMATIC captions from speech recognition: no punctuation, some misheard words, and one spoken sentence is often cut across 2-3 lines. Read the lines (and the context) as continuous speech. Fix obvious mishearings from context (names, terms in the brief). Write each id as a fluent part of the sentence it belongs to, in natural {tgt} word order, with proper punctuation, so the lines read as clean subtitles one after another. A word may move to a neighbouring id when {tgt} grammar needs it; never leave an id empty.
- Never invent or leave a foreign, half-translated or garbled token; every word is a real {tgt} word (or a name/brand the source itself writes)."""

_SYS_POLISH = """You are a meticulous subtitle editor. You turn raw automatic captions into clean, fluent subtitles in the SAME language: correct punctuation and casing, obvious mishearings fixed from context. You never translate, summarise or add anything."""

_RULES_POLISH = """Rules:
- Same language ({tgt}). Do NOT translate and do not reword what was said.
- Add punctuation and casing; fix words that were clearly misheard (use the context and the brief); drop stutters and doubled words.
- The lines are automatic captions: one sentence often runs across 2-3 ids. Keep each id's words in that id (a word may move to a neighbouring id only to finish a sentence cleanly); never leave an id empty.
- One line per id. Do not merge or split ids. Do not edit the CONTEXT lines.
- Never invent or leave a foreign, half-translated or garbled token; keep every word a real {tgt} word.
- No full stop at the end of a line; when the sentence continues in the next id, end with no punctuation or a comma.{rtl_rule}"""

_PROMPT = """Translate from {src} to {tgt}.

BRIEF:
{brief}

{rules}

CONTEXT BEFORE (already handled, for meaning only):
{before}

LINES TO TRANSLATE:
{lines}

CONTEXT AFTER (for meaning only):
{after}

Return JSON only: {{"t": [{{"id": <id>, "x": "<translation>"}}, …]}}"""


_COMPACT = (",", ":")


def _fmt_lines(items, dub, tgt=""):
    out = []
    for i, c in items:
        if dub:
            out.append(json.dumps({"id": i, "sec": round(c.dur, 1), "max": char_budget(c.dur, tgt),
                                   "text": C.flat(c.text)}, ensure_ascii=False, separators=_COMPACT))
        else:
            out.append(json.dumps({"id": i, "text": C.flat(c.text)}, ensure_ascii=False,
                                  separators=_COMPACT))
    return "\n".join(out) or "(none)"


# v1.4.9: errors no retry can fix within this job
_FATAL_WORDS = ("quota", "rate-limit", "rate limit", "too many requests", "(429)", "(401)",
                "(403)", "api key", "unauthorized", "forbidden", "can't reach", "network error",
                "getaddrinfo", "secure connection")


def _is_fatal(msg):
    low = str(msg or "").lower()
    return any(w in low for w in _FATAL_WORDS)


def _batch_cache(cache_dir, cues, ids, src, tgt, dub, brief_txt, flavour=""):
    if not cache_dir:
        return ""
    blob = json.dumps([[C.flat(cues[i].text), round(cues[i].dur, 1)] for i in ids],
                      ensure_ascii=False) + f"|{src}|{tgt}|{int(dub)}|{brief_txt}" \
        + (f"|{flavour}" if flavour else "")
    h = hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(cache_dir, f"tb_{(tgt or 'x').split('-')[0]}_{h}.json")


def _ctx(cues, idxs):
    return "\n".join(f"- {C.flat(cues[j].text)}" for j in idxs) or "(none)"


_JSON_NUDGE = ("\n\nIMPORTANT: your last answer could not be read. Reply with the JSON "
               "object ONLY, exactly in the shape asked for above: no thinking, no "
               "explanation, no markdown.")


def _translate_batch(client, cues, ids, src, tgt, brief_txt, tone, dub, title="", raw_asr=False,
                     polish=False, register="natural"):
    lo, hi = ids[0], ids[-1]
    before = range(max(0, lo - _CTX_BEFORE), lo)
    after = range(hi + 1, min(len(cues), hi + 1 + _CTX_AFTER))
    cps = 12 if L.is_cjk(tgt) else 17
    rtl_rule = _script_rule(tgt)
    if polish:
        rules = _RULES_POLISH.format(tgt=_lang_label(tgt), rtl_rule=rtl_rule)
    else:
        rules = (_RULES_DUB if dub else _RULES_SUB).format(tgt=_lang_label(tgt), tone=tone or "as in the original",
                                                            cps=cps, rtl_rule=rtl_rule)
        if not dub:
            rules += register_rules(tgt, register)
        if raw_asr:
            rules += _RAW_ASR.format(tgt=_lang_label(tgt))
        if dub and str(tgt or "").split("-")[0].lower() == "fa":
            rules += _FA_DUB
    if not brief_txt and title:
        brief_txt = f"- video title: {title}"
    head = _PROMPT
    if polish:
        head = _PROMPT.replace("Translate from {src} to {tgt}.",
                               "Clean up these {src} automatic captions (same language, no translation).") \
                      .replace("LINES TO TRANSLATE", "LINES TO CLEAN UP")
    prompt = head.format(src=_lang_label(src), tgt=_lang_label(tgt), brief=brief_txt or "(none)",
                            rules=rules, before=_ctx(cues, before),
                            lines=_fmt_lines([(i, cues[i]) for i in ids], dub, tgt),
                            after=_ctx(cues, after))
    sys_ = _SYS_POLISH if polish else (_SYS_DUB if dub else _SYS_SUB)
    cap = min(8000, 400 + 160 * len(ids))
    raw = client.generate(prompt, sys_, timeout=180, max_output_tokens=cap)
    try:
        data = _json(raw)
    except NotJSON:
        # v1.4.11: one stricter retry with more room (a reasoning model may
        # have spent the cap thinking, or wrapped the answer in prose)
        raw = client.generate(prompt + _JSON_NUDGE, sys_, timeout=180,
                              max_output_tokens=min(8000, cap * 2))
        try:
            data = _json(raw)
        except NotJSON as e:
            who = " · ".join(x for x in (str(getattr(client, "label", "") or ""),
                                         str(getattr(client, "model", "") or "")) if x) or "The model"
            raise RuntimeError(
                f"{who} answered, but not in the format captions need (JSON). Press Resume, "
                f"or pick another model in Settings → Captions → Captions.") from e
    got = {}
    for item in (data.get("t") if isinstance(data, dict) else data) or []:
        try:
            i = int(item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        x = C.clean_text(str(item.get("x") or "")).replace("\n", " ")
        if i in ids and x:
            got[i] = x
    return got


def _looks_untranslated(src_text, out_text, src, tgt):
    if not out_text:
        return True
    if src and tgt and src.split("-")[0] == tgt.split("-")[0]:
        return False
    a, b = C.flat(src_text).lower(), C.flat(out_text).lower()
    return len(a) >= 24 and a == b


def translate_cues(cues, tgt, src="", title="", mode="subtitle", on_progress=None,
                   should_cancel=None, client=None, workers=3, cache_dir="", raw_asr=False,
                   register="natural", refine=False):
    """New cue list (same timings) in `tgt`. Returns (cues, brief).
    mode: "subtitle" | "dub" | "polish" (v1.4.12: same language, clean-up
    only). raw_asr: the lines are YouTube's automatic captions."""
    if not cues:
        return [], {}
    # v1.7: the dub's lines follow Settings → Captions → Script
    client = client or _client("script" if mode == "dub" else "captions")
    dub = mode == "dub"
    polish = mode == "polish"
    if polish:
        tgt = tgt or src
    register = register if register in _REGISTER_KEYS else "natural"
    flavour = "polish2" if polish else (("asr-" if raw_asr else "") + "nat3-" + register)
    brief = (make_brief(cues, src, tgt, title, client, cache_dir)
             if len(cues) >= _BRIEF_MIN_LINES else {})
    brief_txt = _brief_text(brief)
    tone = (brief or {}).get("tone", "")
    batches = [list(range(i, min(len(cues), i + _BATCH))) for i in range(0, len(cues), _BATCH)]
    result = {}
    done = [0]
    lock = threading.Lock()
    errors = []
    abort = threading.Event()

    def run_batch(ids):
        path = _batch_cache(cache_dir, cues, ids, src, tgt, dub, brief_txt, flavour)
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return {ids[int(k)]: v for k, v in json.load(f).items()
                            if 0 <= int(k) < len(ids) and v}
            except Exception:            # noqa: BLE001 — re-translate
                pass
        got = _translate_batch(client, cues, ids, src, tgt, brief_txt, tone, dub, title,
                               raw_asr=raw_asr, polish=polish, register=register)
        if path and got:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({str(ids.index(i)): x for i, x in got.items()}, f, ensure_ascii=False)
            except OSError:
                pass
        return got

    def work(ids):
        if (should_cancel and should_cancel()) or abort.is_set():
            return
        try:
            got = run_batch(ids)
        except Exception as e:           # noqa: BLE001 — retried below
            errors.append(str(e))
            if _is_fatal(e):
                abort.set()              # don't send the rest into the same wall
            got = {}
        with lock:
            result.update(got)
            done[0] += 1
            if on_progress:
                on_progress(done[0] / (len(batches) + 1))

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(work, batches))
    if should_cancel and should_cancel():
        from app.core.captions.render import Cancelled
        raise Cancelled()

    # v1.4.1: every batch failed (bad key, provider down, quota): stop here.
    # The old second pass then re-sent the WHOLE transcript 10 lines at a
    # time, one doomed (and sometimes billed) call per 10 lines.
    if (not result and errors) or abort.is_set():
        # v1.4.9: a fatal answer stops here, even with some batches done:
        # they are cached, so Resume picks up exactly where this stopped
        raise RuntimeError(errors[-1] if errors else "Translation stopped.")

    # second pass: anything missing or left in the source language
    missing = [i for i in range(len(cues))
               if _looks_untranslated(cues[i].text, result.get(i, ""), src, tgt)]
    # a whole batch that came back unusable is retried once as one batch,
    # stray lines in groups of 10; if the retry fails too, stop retrying
    step = 10 if len(missing) <= 20 else _BATCH
    for k in range(0, len(missing), step):
        if should_cancel and should_cancel():
            break
        ids = missing[k:k + step]
        try:
            result.update(run_batch(ids))
        except Exception as e:           # noqa: BLE001
            errors.append(str(e))
            break
    if not result and errors:
        raise RuntimeError(errors[-1])
    out = []
    for i, c in enumerate(cues):
        x = result.get(i) or c.text
        out.append(c.copy(text=x, words=[], meta=dict(c.meta, src=c.text,
                                                       untranslated=i not in result)))
    if refine and not polish and not dub and out:
        out, _n = refine_cues(out, src, tgt, register, title, client, brief_txt, cache_dir,
                              should_cancel)
        if brief is not None and isinstance(brief, dict):
            brief = dict(brief, refined=_n)
    # v2.1: one targeted pass over any line that still carries a foreign /
    # garbled token (first pass, refine or polish). No call when none do.
    if out:
        out, _fx = _repair_foreign(out, tgt, client, src, brief_txt, cache_dir, should_cancel)
        if _fx and isinstance(brief, dict):
            brief = dict(brief, repaired=_fx)
    if on_progress:
        on_progress(1.0)
    return out, brief


def shorten_line(text, seconds, tgt, client=None):
    """One dub line that ran long: ask for a tighter version that fits."""
    client = client or _client("script")
    prompt = (f"This {_lang_label(tgt)} dubbing line must be spoken in {seconds:.1f} seconds at a "
              f"natural pace but it is too long:\n{text}\n\n"
              "Rewrite it shorter with the same meaning, emotion and register. Keep any <tags>."
              + _script_hint(tgt) + ' Return JSON only: {"x": "<line>"}')
    try:
        data = _json(client.generate(prompt, _SYS_DUB, timeout=60, max_output_tokens=400))
        x = C.clean_text(str(data.get("x") or "")).replace("\n", " ")
        return x or text
    except Exception:                    # noqa: BLE001
        return text


def shorten_lines(items, tgt, client=None, should_cancel=None, batch=25):
    """Many too-long dub lines in a few calls instead of one call each.
    items = [(key, text, seconds)] -> {key: shorter text}. Lines the model
    skips are simply left out (the caller keeps the long take)."""
    if not items:
        return {}
    client = client or _client("script")
    out = {}
    for k in range(0, len(items), batch):
        if should_cancel and should_cancel():
            break
        chunk = items[k:k + batch]
        listing = "\n".join(json.dumps({"id": n, "sec": round(sec, 1), "max": char_budget(sec, tgt),
                                        "text": C.flat(text)}, ensure_ascii=False, separators=_COMPACT)
                            for n, (_key, text, sec) in enumerate(chunk))
        prompt = (f"These {_lang_label(tgt)} dubbing lines are too long to be spoken in their time "
                  f"(\"sec\") at a natural pace. Rewrite each one at or under its \"max\" characters "
                  "(tags not counted) with the same meaning, emotion and register. Keep any <tags> "
                  "where they belong. One line per id." + _script_hint(tgt) + "\n\nLINES:\n" + listing +
                  '\n\nReturn JSON only: {"t": [{"id": <id>, "x": "<line>"}, ...]}')
        try:
            data = _json(client.generate(prompt, _SYS_DUB, timeout=120,
                                         max_output_tokens=min(6000, 300 + 120 * len(chunk))))
        except Exception:                # noqa: BLE001 — keep the long takes
            continue
        for item in (data.get("t") if isinstance(data, dict) else data) or []:
            try:
                n = int(item.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            x = C.clean_text(str(item.get("x") or "")).replace("\n", " ")
            if 0 <= n < len(chunk) and x:
                out[chunk[n][0]] = x
    return out
