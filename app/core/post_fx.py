"""app.core.post_fx - YouTube Hunter.

v1.4.7: what captions / an AI dub did to a download or a clip.

A finished download (or clip) row points at ONE file: the version the user
made. When captions or a dub finish, the row is moved onto that new file and
remembers two things beside it:

    original_path   the plain download / cut (still on disk, still in the
                    folder, no longer what the list shows)
    post_fx         JSON list of layers, oldest first:
                    [{"kind": "captions", "lang": "en", "mode": "burn"},
                     {"kind": "dub", "lang": "fa"}]

Layers stack: captions on a file that already has a dub keep the dub (and
the other way round). A step the file already has (captions again, in
another style) starts from the original instead, so nothing is drawn twice.

Pure functions, no Qt, no database: the db modules and view-models call them.
"""

import json
import os
import re

KINDS = ("captions", "dub")


def same_path(a, b):
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def load(raw):
    """post_fx column -> clean list of layers ([] for anything odd)."""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out = []
    for x in data if isinstance(data, list) else []:
        if isinstance(x, dict) and x.get("kind") in KINDS:
            out.append({"kind": x["kind"], "lang": str(x.get("lang") or "").lower(),
                        "mode": str(x.get("mode") or "")})
    return out


def dump(layers):
    return json.dumps(load(layers), ensure_ascii=False) if layers else ""


def kinds_of(layers):
    return {x["kind"] for x in load(layers)}


def stack(current_layers, new_layers, base_is_current):
    """The layers of the new file. `base_is_current`: the job ran on the
    row's current (already processed) file, so its layers carry over."""
    new = load(new_layers)
    if not base_is_current:
        return new
    keep = [x for x in load(current_layers) if x["kind"] not in {n["kind"] for n in new}]
    return keep + new


# ".en.captions.mp4", ".fa.dub.mkv", ".de.subs.mp4": what a file name says,
# for rows written before v1.4.7 (no post_fx yet)
_NAME_RX = re.compile(r"\.([a-z]{2,3}(?:-[a-z0-9]{2,8})?)\.(dub|captions|subs)(?:-preview)?(?=\.)", re.I)


def from_name(path):
    layers = []
    for m in _NAME_RX.finditer(os.path.basename(path or "")):
        kind = "dub" if m.group(2).lower() == "dub" else "captions"
        layers = [x for x in layers if x["kind"] != kind]
        layers.append({"kind": kind, "lang": m.group(1).lower(),
                       "mode": "soft" if m.group(2).lower() == "subs" else ("burn" if kind == "captions" else "")})
    return layers


def badge(raw, path=""):
    """What the row's badge shows: {"captions": "EN", "dub": "FA",
    "captionsSoft": bool, "has": bool}. Falls back to the file name."""
    layers = load(raw) or from_name(path)
    cap = next((x for x in reversed(layers) if x["kind"] == "captions"), None)
    dub = next((x for x in reversed(layers) if x["kind"] == "dub"), None)
    def code(x):
        return (x["lang"].split("-")[0].upper() or "ON") if x else ""
    return {"captions": code(cap), "dub": code(dub),
            "captionsSoft": bool(cap and cap.get("mode") == "soft"),
            "has": bool(cap or dub)}


def job_layers(kind, opts, res):
    """Layers one finished Voice Studio job put into its video ([] when it
    made no video: an .srt-only run, a preview, a cues-only step)."""
    opts, res = dict(opts or {}), dict(res or {})
    video = res.get("video") or ""
    if not video or not os.path.isfile(video):
        return []
    if float(opts.get("preview_sec") or 0) > 0 or "-preview." in os.path.basename(video):
        return []
    if kind == "captions":
        mode = "soft" if (opts.get("output") == "soft" or ".subs." in os.path.basename(video)) else "burn"
        return [{"kind": "captions", "lang": res.get("lang") or "", "mode": mode}]
    if kind == "dub":
        tgt = (res.get("lang") or opts.get("target") or "").split("|")[0]
        out = []
        if opts.get("burn_style"):
            out.append({"kind": "captions", "lang": opts.get("burn_lang") or tgt, "mode": "burn"})
        elif opts.get("soft_after") or opts.get("soft_from_captions"):
            out.append({"kind": "captions", "lang": tgt, "mode": "soft"})
        out.append({"kind": "dub", "lang": tgt})
        return out
    return []


def pipeline_layers(entry, summary, raw, final):
    """Layers of a queued captions + dub run (download / cut pipeline)."""
    if not final or same_path(final, raw) or not os.path.isfile(final):
        return []
    entry, summary = dict(entry or {}), dict(summary or {})
    cap = dict(entry.get("captions") or {})
    out = []
    steps = summary.get("steps") or []
    done = [s for s in steps if s.get("state") == "done"]
    dub_done = next((s for s in done if s.get("kind") == "dub"), None)
    cap_done = next((s for s in done if s.get("kind") == "captions"), None)
    cap_mode = cap.get("output") or "burn"
    if cap and cap_mode != "srt" and (cap_done or (dub_done and summary.get("merged"))):
        lang = (cap_done or dub_done or {}).get("lang") or ""
        out.append({"kind": "captions", "lang": lang, "mode": "soft" if cap_mode == "soft" else "burn"})
    if dub_done:
        out.append({"kind": "dub", "lang": dub_done.get("lang") or ""})
    return out or from_name(final)


# ── v1.4.8: what each processed file holds, by path ─────────────────────
# A download row points at ONE file, so once the same video is downloaded or
# processed again its `post_fx` describes the NEW file and the older copies
# are only described by their names. A name can't say everything ("x.fa.dub.mp4"
# may also have captions burned in), so every adopted file is remembered here
# too. The YouTube panel's Play / Show in folder uses it to open the copy that
# matches what the user picked (plain, captions, dub or both).

_INDEX_NAME = "post_variants.json"
_INDEX_MAX = 600


def _index_path():
    try:
        from app.core.db.ideas import DATA_DB_PATH
        return os.path.join(os.path.dirname(DATA_DB_PATH), _INDEX_NAME)
    except Exception:                     # noqa: BLE001
        return ""


def _index_key(path):
    try:
        return os.path.normcase(os.path.abspath(path))
    except (TypeError, ValueError):
        return ""


def _index_load():
    p = _index_path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(path, layers):
    """Record the layers of one finished file (best effort, never raises)."""
    clean = load(layers)
    p, k = _index_path(), _index_key(path)
    if not p or not k or not clean:
        return
    try:
        data = _index_load()
        data.pop(k, None)
        data[k] = clean                       # newest last
        if len(data) > _INDEX_MAX:
            for old in list(data)[:len(data) - _INDEX_MAX]:
                data.pop(old, None)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def recall(path):
    """The layers remembered for this file ([] when unknown)."""
    k = _index_key(path)
    return load(_index_load().get(k)) if k else []


# "x.fa.dub (2).mp4": unique_path() numbers a repeat; the name still counts
_COPY_RX = re.compile(r" \(\d+\)(?=\.[^.]+$)")


def layers_of(path, row=None):
    """Best knowledge of what one file holds: the index, then the row's own
    post_fx (only if the row points at this very file), then its name."""
    got = recall(path)
    if got:
        return got
    row = row or {}
    if row.get("filepath") and same_path(row.get("filepath"), path):
        got = load(row.get("post_fx"))
        if got:
            return got
    return from_name(_COPY_RX.sub("", os.path.basename(path or "")))


def pick(variants, want):
    """Choose the copy to open. `variants`: [(path, layers)] with the plain
    file FIRST (layers []). `want`: {"captions": bool, "dub": bool,
    "capLang": str, "dubLang": str}.

    Nothing wanted -> the plain file. Otherwise the copy that has everything
    asked for and the fewest extras (language match, then newest, breaks a
    tie); failing that, the copy with the most of it; failing that, plain.
    Returns (path, layers, exact)."""
    if not variants:
        return "", [], False
    plain = variants[0]
    want = dict(want or {})
    kinds = {k for k in KINDS if want.get(k)}
    if not kinds:
        return plain[0], [], True
    langs = {"captions": str(want.get("capLang") or "").lower().split("-")[0],
             "dub": str(want.get("dubLang") or "").lower().split("-")[0]}

    def score(item):
        path, layers = item
        have = kinds_of(layers)
        hit = sum(1 for x in load(layers)
                  if x["kind"] in kinds and langs.get(x["kind"])
                  and x["lang"].split("-")[0] == langs[x["kind"]])
        try:
            mt = os.path.getmtime(path)
        except OSError:
            mt = 0
        return (len(kinds & have), -len(have - kinds), hit, mt)

    rest = [v for v in variants[1:] if kinds_of(v[1]) & kinds]
    full = [v for v in rest if kinds <= kinds_of(v[1])]
    if full:
        best = max(full, key=score)
        return best[0], best[1], True
    if rest:
        best = max(rest, key=score)
        return best[0], best[1], False
    return plain[0], [], False
