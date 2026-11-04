"""app.core.niche.translate - YouTube Hunter.

Keyword translation for the Niche Finder's Country picker.

The app is English, so people type English keywords. "king" + France found
almost nothing, and that is correct behaviour: French YouTube titles say
"roi", and the Country filter (audience.market_fit) only keeps French titles.
So when a non-English market is picked, the keyword is translated into that
market's main language before it is sent to YouTube.

Rules (kept deliberately simple so nobody has to think about them):
  * Translation is offered whenever the market's MAIN language is not English
    (France, Germany, Turkey, Iran ... yes; US, UK, Canada ... no). Markets
    where English is a co-official SECOND language (India, the UAE, the
    Philippines ...) translate too, and — because English-titled videos there
    are still local content — the original English keyword is searched
    alongside the translation instead of being replaced.
  * A keyword already typed in the market's language comes back unchanged
    without a network round-trip (the offline detector runs first).
  * Each comma-separated keyword is translated on its own; duplicates collapse.
  * Backends, in order: Google Translate's public endpoint (no key, ~100 ms),
    then the AI provider configured in Settings, then give up and search the
    keyword as typed. A failed translation never fails the search.
  * Google is told the source language when it is known. For a short Latin
    keyword it is told "English": left on auto, Google reads the odd Latin
    word as another language ("para" as Portuguese used to come back as a bare
    "à") and answers with a useless one-word translation.
  * Results are memoised for the session: the same keyword + market costs
    one request, ever.
"""

import json
import re
import threading
import urllib.parse
import urllib.request

from app.core.niche import audience as _aud

# audience language code -> the code Google Translate wants
_GT_CODES = {"zh-hans": "zh-CN", "zh-hant": "zh-TW", "he": "iw", "tl": "tl",
             "no": "no", "ku": "ku"}

# Country-specific variants: Brazil does not write "música" like Lisbon, and
# Hong Kong does not write like Taipei. Same language, different YouTube words.
_REGION_GT = {
    ("pt", "PT"): "pt-PT", ("pt", "BR"): "pt-BR",
    ("zh-hant", "HK"): "zh-HK", ("zh-hant", "TW"): "zh-TW",
}

# Targets written in a non-Latin script. A translation for these that comes
# back as bare Latin text is either the unchanged brand name or a bad answer;
# it is only accepted when it IS the original.
_NON_LATIN = {
    "hi", "ar", "fa", "ru", "ja", "ko", "zh-hans", "zh-hant", "th", "bn",
    "ur", "uk", "ta", "te", "mr", "gu", "kn", "ml", "pa", "he", "ps", "ne",
    "si", "my", "km", "ka", "hy", "am", "kk", "sr", "bg", "el",
}

_MEMO = {}
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 500


def google_target(target, region=""):
    """The code Google Translate wants for `target` in `region`."""
    return _REGION_GT.get((target, (region or "").strip().upper()),
                          _GT_CODES.get(target, target))


def search_languages(region):
    """(translate_into, also_english).

    `translate_into` is the market's main language, or '' when no translation
    is wanted (no country, or English is that country's main language).
    `also_english` says English is spoken there too, so the original English
    keyword should be searched alongside the translation — it is local content
    there, not a wasted query."""
    langs = _aud.country_languages(region)
    if not langs or langs[0] == "en":
        return "", False
    return langs[0], ("en" in langs)


def target_language(region):
    """The language a keyword should be translated into for `region`, or ''
    when no translation is wanted."""
    return search_languages(region)[0]


def _source_lang(text):
    """The source language to declare to Google ('' = auto-detect).

    The offline detector is trusted when it is sure. Short Latin keywords are
    the case it cannot read (one word, no function words), and leaving those
    to Google's auto-detect is what produced "para" → Portuguese → "à". The
    app is English-first, so an unreadable ASCII keyword is declared English;
    a name that is not English comes back unchanged, which is handled as
    "already fine" rather than as a wrong translation."""
    code, conf = _aud.detect_text_language(text)
    if code and conf >= 0.5:
        return code
    if text.isascii():
        return "en"
    return ""


def _script_ok(target, out, original):
    """False when the answer cannot be a translation into `target`."""
    letters = [c for c in out if c.isalpha()]
    if not letters or "\ufffd" in out:
        return False
    if target in _NON_LATIN:
        has_target_script = any(
            _aud._script_of(c) not in (None, "latin") for c in letters)
        return has_target_script or out.casefold() == original.casefold()
    return any(_aud._script_of(c) == "latin" for c in letters)


def _google(text, target, proxy, region="", timeout=6):
    from app.core.net import _urlopen
    sl = _source_lang(text)
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
           "&sl=" + (urllib.parse.quote(_GT_CODES.get(sl, sl)) if sl else "auto")
           + "&tl=" + urllib.parse.quote(google_target(target, region))
           + "&dt=t&q=" + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _urlopen(req, timeout, proxy=proxy or None) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    out = "".join((seg[0] or "") for seg in (data[0] or []) if seg)
    src = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
    return out.strip(), _aud.norm_lang(src)


def _ai(text, target):
    from app.core.ai import profiles
    client = profiles.make_client()
    if not getattr(client, "api_key", ""):
        return ""
    lang = _aud.language_name(target) or target
    prompt = ("Translate this YouTube search keyword into %s, exactly as a "
              "native %s-speaking viewer would type it into YouTube search. "
              "Keep brand names, game titles and people's names as they are. "
              "If it is already %s, return it unchanged. Reply with the "
              "keyword only, no quotes, no explanation.\n\nKeyword: %s"
              % (lang, lang, lang, text))
    out = client.generate(prompt, timeout=20, max_output_tokens=60) or ""
    out = out.strip().splitlines()[0] if out.strip() else ""
    return out.strip().strip('"“”«»\'').strip()


def _clean(s, original, target):
    s = re.sub(r"\s+", " ", s or "").strip()
    # a translator that answers with a sentence is not a keyword
    if not s or len(s) > max(60, len(original) * 4):
        return ""
    # "para" + French used to come back as the bare "à": a one-character answer
    # is never a usable keyword for a longer input. Chinese/Japanese words
    # genuinely are one character (猫 = cat), so those targets are exempt.
    if len(s) < 2 and len(original) > 2 and target not in ("zh-hans", "zh-hant", "ja"):
        return ""
    if not _script_ok(target, s, original):
        return ""
    return s


def translate_keyword(text, target, proxy="", region=""):
    """(translated, backend) — backend is 'google' | 'ai' | 'same' | ''.
    'same' = already in the target language; '' = could not translate (the
    original is returned). Never raises."""
    text = (text or "").strip()
    if not text or not target:
        return text, ""
    key = (text.casefold(), google_target(target, region))
    with _MEMO_LOCK:
        if key in _MEMO:
            return _MEMO[key]
    result = (text, "")
    # The offline detector knows a local-language title when it sees one. Doing
    # that check here skips the request (and a chance of a bad auto-detection)
    # for keywords the user already typed in the market's language.
    guess, conf = _aud.detect_text_language(text)
    if guess and conf >= 0.5 and _aud._family(guess) == _aud._family(target):
        result = (text, "same")
    else:
        try:
            out, src = _google(text, target, proxy, region)
            out = _clean(out, text, target)
            if src and _aud._family(src) == _aud._family(target):
                result = (text, "same")
            elif out:
                result = (out, "same" if out.casefold() == text.casefold()
                          else "google")
        except Exception:
            pass
        # A Google answer that was unusable (wrong script, a sentence) is a
        # failure like any other: give the configured AI provider its turn
        # instead of silently searching the untranslated keyword.
        if not result[1]:
            try:
                out = _clean(_ai(text, target), text, target)
                if out:
                    result = (out, "same" if out.casefold() == text.casefold()
                              else "ai")
            except Exception:
                pass
    if result[1]:                     # only remember answers, not failures
        with _MEMO_LOCK:
            if len(_MEMO) >= _MEMO_MAX:
                _MEMO.clear()
            _MEMO[key] = result
    return result


def translate_queries(queries, region, proxy=""):
    """Translate every keyword for `region`.

    Returns (new_queries, pairs, langs):
      * new_queries — deduplicated search queries
      * pairs — [(original, translated)] for the ones that actually changed
      * langs — relevanceLanguage hint for each new query, parallel to
        new_queries; this is what keeps a translated Hindi query and the kept
        English one from sharing one country-wide hint.

    When English is a co-official language of the market, the original English
    keywords are appended (they are local content there), so the caller
    searches both instead of throwing the English half away."""
    target, also_english = search_languages(region)
    if not target:
        return [q for q in queries if q], [], ["" for q in queries if q]
    target_api = _aud.language_api(target)
    out, pairs, langs, seen = [], [], [], set()
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        t, how = translate_keyword(q, target, proxy, region)
        if how in ("google", "ai") and t != q:
            pairs.append((q, t))
        folded = t.casefold()
        if folded in seen:            # two keywords can translate to the same
            continue
        seen.add(folded)
        out.append(t)
        langs.append(target_api)
    if also_english:
        for q in queries:
            q = (q or "").strip()
            if q and q.casefold() not in seen:
                seen.add(q.casefold())
                out.append(q)
                langs.append("en")
    return out, pairs, langs
