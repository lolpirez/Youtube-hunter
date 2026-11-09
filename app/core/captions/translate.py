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

_SYS_SUB = """You are an award-winning subtitle translator. You translate meaning, intent, humour and tone, never word-for-word. Your subtitles sound like a native speaker of the target language wrote them for this exact video: natural idioms instead of literal ones, the right register, names and numbers kept correct. You are accurate: you never add or drop information that matters."""

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

# v1.4.12: YouTube's auto captions as the source
_RAW_ASR = """
- The source lines are AUTOMATIC captions from speech recognition: no punctuation, some misheard words, and one spoken sentence is often cut across 2-3 lines. Read the lines (and the context) as continuous speech. Fix obvious mishearings from context (names, terms in the brief). Write each id as a fluent part of the sentence it belongs to, in natural {tgt} word order, with proper punctuation, so the lines read as clean subtitles one after another. A word may move to a neighbouring id when {tgt} grammar needs it; never leave an id empty."""

_SYS_POLISH = """You are a meticulous subtitle editor. You turn raw automatic captions into clean, fluent subtitles in the SAME language: correct punctuation and casing, obvious mishearings fixed from context. You never translate, summarise or add anything."""

_RULES_POLISH = """Rules:
- Same language ({tgt}). Do NOT translate and do not reword what was said.
- Add punctuation and casing; fix words that were clearly misheard (use the context and the brief); drop stutters and doubled words.
- The lines are automatic captions: one sentence often runs across 2-3 ids. Keep each id's words in that id (a word may move to a neighbouring id only to finish a sentence cleanly); never leave an id empty.
- One line per id. Do not merge or split ids. Do not edit the CONTEXT lines.{rtl_rule}"""

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
                     polish=False):
    lo, hi = ids[0], ids[-1]
    before = range(max(0, lo - _CTX_BEFORE), lo)
    after = range(hi + 1, min(len(cues), hi + 1 + _CTX_AFTER))
    cps = 12 if L.is_cjk(tgt) else 17
    rtl_rule = ("\n- Use the target script only; keep Latin brand names as they are."
                if L.is_rtl(tgt) else "")
    if polish:
        rules = _RULES_POLISH.format(tgt=_lang_label(tgt), rtl_rule=rtl_rule)
    else:
        rules = (_RULES_DUB if dub else _RULES_SUB).format(tgt=_lang_label(tgt), tone=tone or "as in the original",
                                                            cps=cps, rtl_rule=rtl_rule)
        if raw_asr:
            rules += _RAW_ASR.format(tgt=_lang_label(tgt))
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
                f"or pick another model in Settings → Captions & Dub → Captions.") from e
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
                   should_cancel=None, client=None, workers=3, cache_dir="", raw_asr=False):
    """New cue list (same timings) in `tgt`. Returns (cues, brief).
    mode: "subtitle" | "dub" | "polish" (v1.4.12: same language, clean-up
    only). raw_asr: the lines are YouTube's automatic captions."""
    if not cues:
        return [], {}
    # v1.7: the dub's lines follow Settings → Captions & Dub → Script
    client = client or _client("script" if mode == "dub" else "captions")
    dub = mode == "dub"
    polish = mode == "polish"
    if polish:
        tgt = tgt or src
    flavour = "polish" if polish else ("asr" if raw_asr else "")
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
                               raw_asr=raw_asr, polish=polish)
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
    if on_progress:
        on_progress(1.0)
    return out, brief


def shorten_line(text, seconds, tgt, client=None):
    """One dub line that ran long: ask for a tighter version that fits."""
    client = client or _client("script")
    prompt = (f"This {_lang_label(tgt)} dubbing line must be spoken in {seconds:.1f} seconds at a "
              f"natural pace but it is too long:\n{text}\n\n"
              "Rewrite it shorter with the same meaning, emotion and register. Keep any <tags>. "
              'Return JSON only: {"x": "<line>"}')
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
                  "where they belong. One line per id.\n\nLINES:\n" + listing +
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
