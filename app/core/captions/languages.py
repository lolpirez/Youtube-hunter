"""app.core.captions.languages - YouTube Hunter.

The languages Voice Studio offers, with what rendering needs to know about
each: its native name (what the picker shows), ISO 639-2 for track metadata,
right-to-left, whether the script has letter case (Bold Pop upper-cases only
then), and the font libass should use.

Fonts: Latin uses the bundled Hanken Grotesk, Arabic-script languages the
bundled Vazirmatn. Every other script names a font that ships with Windows 10+,
so captions render without anything extra installed.
"""

# code, English name, native name, iso639-2, rtl, cased, script
_L = [
    ("en", "English", "English", "eng", False, True, "latn"),
    ("fa", "Persian", "فارسی", "per", True, False, "arab"),
    ("ar", "Arabic", "العربية", "ara", True, False, "arab"),
    ("tr", "Turkish", "Türkçe", "tur", False, True, "latn"),
    ("es", "Spanish", "Español", "spa", False, True, "latn"),
    ("fr", "French", "Français", "fre", False, True, "latn"),
    ("de", "German", "Deutsch", "ger", False, True, "latn"),
    ("it", "Italian", "Italiano", "ita", False, True, "latn"),
    ("pt", "Portuguese", "Português", "por", False, True, "latn"),
    ("pt-BR", "Portuguese (Brazil)", "Português (Brasil)", "por", False, True, "latn"),
    ("ru", "Russian", "Русский", "rus", False, True, "cyrl"),
    ("uk", "Ukrainian", "Українська", "ukr", False, True, "cyrl"),
    ("pl", "Polish", "Polski", "pol", False, True, "latn"),
    ("nl", "Dutch", "Nederlands", "dut", False, True, "latn"),
    ("sv", "Swedish", "Svenska", "swe", False, True, "latn"),
    ("no", "Norwegian", "Norsk", "nor", False, True, "latn"),
    ("da", "Danish", "Dansk", "dan", False, True, "latn"),
    ("fi", "Finnish", "Suomi", "fin", False, True, "latn"),
    ("cs", "Czech", "Čeština", "cze", False, True, "latn"),
    ("ro", "Romanian", "Română", "rum", False, True, "latn"),
    ("hu", "Hungarian", "Magyar", "hun", False, True, "latn"),
    ("el", "Greek", "Ελληνικά", "gre", False, True, "grek"),
    ("he", "Hebrew", "עברית", "heb", True, False, "hebr"),
    ("ur", "Urdu", "اردو", "urd", True, False, "arab"),
    ("ps", "Pashto", "پښتو", "pus", True, False, "arab"),
    ("ku", "Kurdish (Sorani)", "کوردی", "kur", True, False, "arab"),
    ("az", "Azerbaijani", "Azərbaycanca", "aze", False, True, "latn"),
    ("kk", "Kazakh", "Қазақша", "kaz", False, True, "cyrl"),
    ("uz", "Uzbek", "Oʻzbekcha", "uzb", False, True, "latn"),
    ("tg", "Tajik", "Тоҷикӣ", "tgk", False, True, "cyrl"),
    ("hi", "Hindi", "हिन्दी", "hin", False, False, "deva"),
    ("bn", "Bengali", "বাংলা", "ben", False, False, "beng"),
    ("ta", "Tamil", "தமிழ்", "tam", False, False, "taml"),
    ("te", "Telugu", "తెలుగు", "tel", False, False, "telu"),
    ("mr", "Marathi", "मराठी", "mar", False, False, "deva"),
    ("pa", "Punjabi", "ਪੰਜਾਬੀ", "pan", False, False, "guru"),
    ("zh", "Chinese (Simplified)", "简体中文", "chi", False, False, "hans"),
    ("zh-TW", "Chinese (Traditional)", "繁體中文", "chi", False, False, "hant"),
    ("ja", "Japanese", "日本語", "jpn", False, False, "jpan"),
    ("ko", "Korean", "한국어", "kor", False, False, "kore"),
    ("th", "Thai", "ไทย", "tha", False, False, "thai"),
    ("vi", "Vietnamese", "Tiếng Việt", "vie", False, True, "latn"),
    ("id", "Indonesian", "Bahasa Indonesia", "ind", False, True, "latn"),
    ("ms", "Malay", "Bahasa Melayu", "may", False, True, "latn"),
    ("fil", "Filipino", "Filipino", "fil", False, True, "latn"),
    ("sw", "Swahili", "Kiswahili", "swa", False, True, "latn"),
    ("am", "Amharic", "አማርኛ", "amh", False, False, "ethi"),
]

LANGUAGES = [
    {"code": c, "name": n, "native": nat, "iso3": i3, "rtl": rtl,
     "cased": cased, "script": sc}
    for c, n, nat, i3, rtl, cased, sc in _L
]
_BY_CODE = {l["code"].lower(): l for l in LANGUAGES}

# script → libass font family
_SCRIPT_FONT = {
    "latn": "Hanken Grotesk",
    "arab": "Vazirmatn",
    "cyrl": "Segoe UI",
    "grek": "Segoe UI",
    "hebr": "Segoe UI",
    "deva": "Nirmala UI",
    "beng": "Nirmala UI",
    "taml": "Nirmala UI",
    "telu": "Nirmala UI",
    "guru": "Nirmala UI",
    "hans": "Microsoft YaHei",
    "hant": "Microsoft JhengHei",
    "jpan": "Yu Gothic UI",
    "kore": "Malgun Gothic",
    "thai": "Leelawadee UI",
    "ethi": "Ebrima",
}


def get(code):
    """The language record for a code ('en', 'en-US', 'fa-IR', 'pt-BR' …)."""
    if not code:
        return None
    k = str(code).strip().lower().replace("_", "-")
    if k in _BY_CODE:
        return _BY_CODE[k]
    base = k.split("-")[0]
    if base in ("iw",):
        base = "he"
    if base == "zh" and any(x in k for x in ("tw", "hk", "hant")):
        return _BY_CODE["zh-tw"]
    return _BY_CODE.get(base)


def name_of(code):
    l = get(code)
    return l["name"] if l else (code or "")


def native_of(code):
    l = get(code)
    return l["native"] if l else (code or "")


def is_rtl(code):
    l = get(code)
    return bool(l and l["rtl"])


def is_cased(code):
    l = get(code)
    return True if l is None else bool(l["cased"])


def iso3(code):
    l = get(code)
    return l["iso3"] if l else "und"


def font_for(code):
    l = get(code)
    return _SCRIPT_FONT.get(l["script"] if l else "latn", "Hanken Grotesk")


def is_cjk(code):
    l = get(code)
    return bool(l and l["script"] in ("hans", "hant", "jpan", "kore", "thai"))


def line_limit(code):
    """Readable characters per caption line for the script."""
    return 18 if is_cjk(code) else 42


def guess_from_text(text):
    """Cheap script sniff for a file whose language nobody told us."""
    t = text or ""
    counts = {
        "fa": sum(1 for ch in t if ch in "پچژگک"),
        "ar": sum(1 for ch in t if "\u0600" <= ch <= "\u06ff"),
        "ru": sum(1 for ch in t if "\u0400" <= ch <= "\u04ff"),
        "zh": sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff"),
        "ja": sum(1 for ch in t if "\u3040" <= ch <= "\u30ff"),
        "ko": sum(1 for ch in t if "\uac00" <= ch <= "\ud7af"),
        "hi": sum(1 for ch in t if "\u0900" <= ch <= "\u097f"),
        "he": sum(1 for ch in t if "\u0590" <= ch <= "\u05ff"),
        "th": sum(1 for ch in t if "\u0e00" <= ch <= "\u0e7f"),
    }
    if counts["fa"] > 3:
        return "fa"
    best = max(counts, key=counts.get)
    if counts[best] > max(10, len(t) * 0.1):
        return best
    return ""
