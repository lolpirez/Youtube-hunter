"""app.core.dossier - YouTube Hunter.

The Video Dossier: everything the app can learn about ONE video, gathered
for the dossier card (components/VideoDossierCard.qml) and its CSV export.

    meta        title, thumbnails, links, stats, engagement, outlier score,
                tags, description, category, language, flags
    comments    the top comments, MOST LIKED FIRST (v2.2: the best 100 YouTube
                offers, re-ranked by likes), and the newest ones,
                COMMENT_COUNT of each; fewer when the video has fewer
    transcript  the WHOLE caption track (app.core.transcripts, never trimmed)

Sources, best first:
  * YouTube Data API v3 with the user's keys (videos 1 unit + channels 1 unit
    + commentThreads 2 x 1 unit = 4 units per dossier), the key with the most
    quota left first, the next one on "quota exceeded";
  * yt-dlp when there is no key, the quota is gone or the API fails. It needs
    no key at all (comments too), only more time.

Pure functions, no Qt: the ViewModel (app.qml_backend.vm.dossier_vm) runs
them on worker threads.
"""

import csv
import os
import re
import time
from datetime import datetime, timezone

from app.core.utils import parse_iso8601_duration

COMMENT_COUNT = 30           # top AND latest: "20 or 30" -> 30, fewer if fewer exist
# v2.2: "top" means most liked. YouTube's own top order (relevance) mixes in
# recency and replies, so a 40-like comment could sit above a 9k-like one.
# We read a wider pool (one API page of 100 costs the same 1 unit as 30) and
# rank it by likes, so the 30 we keep really are the 30 most liked we saw.
TOP_POOL = 100
_CSV_CELL = 30000            # Excel breaks a CSV cell at ~32,758 characters

_CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music",
    "15": "Pets & Animals", "17": "Sports", "18": "Short Movies",
    "19": "Travel & Events", "20": "Gaming", "21": "Videoblogging",
    "22": "People & Blogs", "23": "Comedy", "24": "Entertainment",
    "25": "News & Politics", "26": "Howto & Style", "27": "Education",
    "28": "Science & Technology", "29": "Nonprofits & Activism",
    "30": "Movies", "43": "Shows", "44": "Trailers",
}


class CommentsOff(Exception):
    """The uploader turned comments off for this video."""


# ── small helpers ─────────────────────────────────────────────────────────
def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _iso_from_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _iso_from_ymd(s):
    s = str(s or "")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}T00:00:00Z"
    return ""


def _parse_iso(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_text(iso):
    dt = _parse_iso(iso)
    if not dt:
        return ""
    days = max(0, (datetime.now(timezone.utc) - dt).days)
    if days < 1:
        return "today"
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 365:
        m = days // 30
        return f"{m} month{'s' if m != 1 else ''} ago"
    y = days / 365.0
    return f"{y:.1f} years ago" if y < 10 else f"{int(y)} years ago"


def _date_text(iso):
    dt = _parse_iso(iso)
    return dt.strftime("%d %b %Y") if dt else ""


def fmt_count(n):
    n = float(n or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.0f}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")) + suf
    return str(int(n))


def fmt_duration(secs):
    s = max(0, int(secs or 0))
    h, m, x = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def _tier(mult):
    if mult >= 10:
        return "Mega outlier"
    if mult >= 5:
        return "Strong outlier"
    if mult >= 2:
        return "Outlier"
    if mult >= 1:
        return "Above its audience"
    if mult > 0:
        return "Within its audience"
    return ""


def _finish_meta(m):
    """Derived numbers every source shares: engagement, rates, outlier."""
    views, likes, comments = m["views"], m["likes"], m["comments"]
    m["engagement"] = (likes + comments) / views if views else 0.0
    m["likeRate"] = likes / views if views else 0.0
    m["commentRate"] = comments / views if views else 0.0
    dt = _parse_iso(m.get("publishedAt"))
    days = max(1.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0) if dt else 0
    m["ageDays"] = float(days)
    m["viewsPerDay"] = views / days if days else 0.0
    subs = m.get("subs") or 0
    # The app's outlier score (Niche Finder / Archive): views per subscriber
    m["outlier"] = views / subs if subs else 0.0
    ch_avg = (m.get("channelViews") or 0) / m["channelVideos"] if m.get("channelVideos") else 0.0
    m["channelAvgViews"] = ch_avg
    m["vsChannelAvg"] = views / ch_avg if ch_avg else 0.0
    m["tier"] = _tier(m["outlier"])
    m["isShort"] = bool(m.get("isShort")) or (0 < m["durationS"] <= 60)
    m["durationText"] = fmt_duration(m["durationS"])
    m["publishedText"] = _date_text(m.get("publishedAt"))
    m["ageText"] = _age_text(m.get("publishedAt"))
    m["tagsText"] = ", ".join(m.get("tags") or [])
    for k in ("views", "likes", "comments", "subs", "channelViews"):
        m[k + "Text"] = fmt_count(m.get(k) or 0)
    return m


def _thumbs_api(th):
    order = ("maxres", "standard", "high", "medium", "default")
    out = []
    for k in order:
        t = (th or {}).get(k) or {}
        if t.get("url"):
            out.append({"label": k, "url": t["url"], "w": _int(t.get("width")), "h": _int(t.get("height"))})
    return out


# ── YouTube Data API ──────────────────────────────────────────────────────
def meta_from_api(yt, video_id, stop_check=None):
    from app.core.youtube_api import execute_with_retry
    resp = execute_with_retry(yt.videos().list(
        part="snippet,statistics,contentDetails,status,topicDetails", id=video_id), stop_check)
    items = resp.get("items") or []
    if not items:
        raise LookupError("YouTube did not return this video (deleted, private or a wrong link).")
    it = items[0]
    sn, st, cd, ss = (it.get("snippet") or {}), (it.get("statistics") or {}), \
        (it.get("contentDetails") or {}), (it.get("status") or {})
    ch_id = sn.get("channelId") or ""
    ch = {}
    if ch_id:
        try:
            cr = execute_with_retry(yt.channels().list(part="snippet,statistics", id=ch_id), stop_check)
            ch = (cr.get("items") or [{}])[0]
        except Exception:                   # noqa: BLE001 — the video still stands
            ch = {}
    cst, csn = (ch.get("statistics") or {}), (ch.get("snippet") or {})
    cth = (csn.get("thumbnails") or {})
    m = {
        "source": "api",
        "videoId": video_id,
        "title": sn.get("title") or "",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "channelTitle": sn.get("channelTitle") or csn.get("title") or "",
        "channelId": ch_id,
        "channelUrl": f"https://www.youtube.com/channel/{ch_id}" if ch_id else "",
        "channelHandle": csn.get("customUrl") or "",
        "channelThumbUrl": ((cth.get("medium") or cth.get("default") or {}).get("url") or ""),
        "subs": float(_int(cst.get("subscriberCount"))),
        "subsHidden": bool(cst.get("hiddenSubscriberCount")),
        "channelViews": float(_int(cst.get("viewCount"))),
        "channelVideos": float(_int(cst.get("videoCount"))),
        "publishedAt": sn.get("publishedAt") or "",
        "durationS": int(parse_iso8601_duration(cd.get("duration", "")) or 0),
        "views": float(_int(st.get("viewCount"))),
        "likes": float(_int(st.get("likeCount"))),
        "comments": float(_int(st.get("commentCount"))),
        "favorites": float(_int(st.get("favoriteCount"))),
        "likesHidden": "likeCount" not in st,
        "commentsHidden": "commentCount" not in st,
        "tags": list(sn.get("tags") or []),
        "categoryId": str(sn.get("categoryId") or ""),
        "category": _CATEGORIES.get(str(sn.get("categoryId") or ""), ""),
        "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "",
        "definition": (cd.get("definition") or "").upper(),
        "dimension": (cd.get("dimension") or "").upper(),
        "hasCaptions": str(cd.get("caption") or "").lower() == "true",
        "licensed": bool(cd.get("licensedContent")),
        "madeForKids": bool(ss.get("madeForKids")),
        "privacy": ss.get("privacyStatus") or "",
        "embeddable": bool(ss.get("embeddable", True)),
        "license": ss.get("license") or "",
        "liveContent": sn.get("liveBroadcastContent") or "none",
        "topics": [t.rsplit("/", 1)[-1].replace("_", " ")
                   for t in ((it.get("topicDetails") or {}).get("topicCategories") or [])],
        "description": sn.get("description") or "",
        "thumbnails": _thumbs_api(sn.get("thumbnails")),
    }
    return _finish_meta(m)


def _api_comment(item):
    sn = item.get("snippet") or {}
    top = ((sn.get("topLevelComment") or {}).get("snippet")) or {}
    return {
        "author": top.get("authorDisplayName") or "",
        "authorThumbUrl": top.get("authorProfileImageUrl") or "",
        "authorChannelUrl": top.get("authorChannelUrl") or "",
        "text": top.get("textOriginal") or top.get("textDisplay") or "",
        "likes": float(_int(top.get("likeCount"))),
        "replies": float(_int(sn.get("totalReplyCount"))),
        "publishedAt": top.get("publishedAt") or "",
        "edited": bool(top.get("updatedAt") and top.get("updatedAt") != top.get("publishedAt")),
        "pinned": False,
    }


def by_likes(comments):
    """Most liked first; ties go to the one with more replies, then keep
    YouTube's order (sorted() is stable)."""
    return sorted(comments or [], key=lambda c: (-float(c.get("likes") or 0),
                                                  -float(c.get("replies") or 0)))


def comments_from_api(yt, video_id, order, n=COMMENT_COUNT, stop_check=None):
    """order: "relevance" (top comments, returned MOST LIKED FIRST) or
    "time" (newest)."""
    from googleapiclient.errors import HttpError
    from app.core.youtube_api import execute_with_retry, _http_error_reasons
    top = order == "relevance"
    ask = max(n, TOP_POOL) if top else n
    try:
        resp = execute_with_retry(yt.commentThreads().list(
            part="snippet", videoId=video_id, order=order, maxResults=max(1, min(100, ask)),
            textFormat="plainText"), stop_check)
    except HttpError as exc:
        if "commentsDisabled" in _http_error_reasons(exc):
            raise CommentsOff()
        raise
    out = [_shape_comment(_api_comment(i)) for i in (resp.get("items") or [])]
    if top:
        out = by_likes(out)
    return out[:n]


# ── yt-dlp (no key / no quota) ────────────────────────────────────────────
def _ydl(url, extra=None):
    from app.config import load_app_config
    cfg = load_app_config()
    proxy = (cfg.get("ai_proxy", "") or "").strip()
    cookies = (cfg.get("yt_cookies_browser", "") or "").strip().lower()
    from app.core.utils import ensure_yt_dlp_ready
    yt_dlp, _ff = ensure_yt_dlp_ready()
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "extractor_retries": 2}
    try:
        from app.core.runtime_deps import js_runtime_opts
        opts.update(js_runtime_opts())
    except Exception:                       # noqa: BLE001
        pass
    if proxy:
        opts["proxy"] = proxy
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    try:
        from app.core.download import _QuietLogger
        opts["logger"] = _QuietLogger()
    except Exception:                       # noqa: BLE001
        pass
    opts.update(extra or {})
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def meta_from_ytdlp(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    info = _ydl(url) or {}
    thumbs = []
    seen = set()
    for t in sorted(info.get("thumbnails") or [], key=lambda t: -(_int(t.get("width")) * _int(t.get("height")))):
        u = t.get("url") or ""
        if not u or u in seen or ".webp" in u:
            continue
        seen.add(u)
        w, h = _int(t.get("width")), _int(t.get("height"))
        thumbs.append({"label": t.get("id") or f"{w}x{h}", "url": u, "w": w, "h": h})
        if len(thumbs) >= 5:
            break
    if not thumbs:
        thumbs = [{"label": "maxres", "url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg", "w": 1280, "h": 720},
                  {"label": "high", "url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "w": 480, "h": 360}]
    ch_id = info.get("channel_id") or ""
    pub = _iso_from_ts(info.get("timestamp")) if info.get("timestamp") else _iso_from_ymd(info.get("upload_date"))
    m = {
        "source": "ytdlp",
        "videoId": video_id,
        "title": info.get("title") or "",
        "url": info.get("webpage_url") or url,
        "channelTitle": info.get("channel") or info.get("uploader") or "",
        "channelId": ch_id,
        "channelUrl": info.get("channel_url") or (f"https://www.youtube.com/channel/{ch_id}" if ch_id else ""),
        "channelHandle": info.get("uploader_id") or "",
        "channelThumbUrl": "",
        "subs": float(_int(info.get("channel_follower_count"))),
        "subsHidden": info.get("channel_follower_count") is None,
        "channelViews": 0.0,
        "channelVideos": 0.0,
        "publishedAt": pub,
        "durationS": _int(info.get("duration")),
        "views": float(_int(info.get("view_count"))),
        "likes": float(_int(info.get("like_count"))),
        "comments": float(_int(info.get("comment_count"))),
        "favorites": 0.0,
        "likesHidden": info.get("like_count") is None,
        "commentsHidden": info.get("comment_count") is None,
        "tags": list(info.get("tags") or []),
        "categoryId": "",
        "category": ", ".join(info.get("categories") or []),
        "language": info.get("language") or "",
        "definition": "HD" if _int(info.get("height")) >= 720 else ("SD" if info.get("height") else ""),
        "dimension": "",
        "hasCaptions": bool(info.get("subtitles")),
        "licensed": False,
        "madeForKids": False,
        "privacy": info.get("availability") or "",
        "embeddable": True,
        "license": info.get("license") or "",
        "liveContent": "live" if info.get("is_live") else ("upcoming" if info.get("live_status") == "is_upcoming" else "none"),
        "topics": [],
        "description": info.get("description") or "",
        "thumbnails": thumbs,
        "isShort": "/shorts/" in str(info.get("original_url") or ""),
    }
    return _finish_meta(m)


def comments_from_ytdlp(video_id, sort="top", n=COMMENT_COUNT):
    """yt-dlp's comment reader (no API key). sort: "top" (returned MOST
    LIKED FIRST, from a wider pool) | "new"."""
    ask = max(n, TOP_POOL) if sort == "top" else n
    info = _ydl(f"https://www.youtube.com/watch?v={video_id}", {
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(ask), str(ask), "0", "0"],
                                       "comment_sort": [sort]}},
    }) or {}
    raw = info.get("comments")
    if raw is None and info.get("comment_count") == 0:
        return []
    out = []
    for c in raw or []:
        if str(c.get("parent") or "root") != "root":
            continue
        out.append(_shape_comment({
            "author": c.get("author") or "",
            "authorThumbUrl": c.get("author_thumbnail") or "",
            "authorChannelUrl": c.get("author_url") or "",
            "text": c.get("text") or "",
            "likes": float(_int(c.get("like_count"))),
            "replies": 0.0,
            "publishedAt": _iso_from_ts(c.get("timestamp")) if c.get("timestamp") else "",
            "edited": bool(c.get("edited")),
            "pinned": bool(c.get("is_pinned")),
        }))
    if sort == "new":
        out.sort(key=lambda c: c.get("publishedAt") or "", reverse=True)
    else:
        out = by_likes(out)
    return out[:n]


def _shape_comment(c):
    c["ageText"] = _age_text(c.get("publishedAt"))
    c["likesText"] = fmt_count(c.get("likes") or 0)
    c["initial"] = (c.get("author") or "?").lstrip("@")[:1].upper() or "?"
    return c


# ── CSV export ────────────────────────────────────────────────────────────
_CSV_HEADERS = ["section", "field", "value", "author", "likes", "replies", "published_at"]


def _parts(text, limit=_CSV_CELL):
    text = str(text or "")
    if len(text) <= limit:
        return [text]
    out = []
    while len(text) > limit:
        cut = text.rfind(" ", int(limit * 0.8), limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def _safe(v):
    """Neutralise spreadsheet formulas in user text (=, +, -, @ leads)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"[-+]?\d[\d.,]*", v):
        return "'" + v
    return v


def _pct(x):
    return f"{float(x or 0) * 100:.2f}%"


def dossier_rows(meta, top, latest, transcript, seed=None):
    """Every fact in the dossier as (section, field, value, author, likes,
    replies, published_at) rows. Long text (description, transcript) is split
    over several rows at word boundaries, never cut."""
    m, seed = meta or {}, seed or {}
    R = []

    def add(section, field, value="", author="", likes="", replies="", pub=""):
        R.append([section, field, value, author, likes, replies, pub])

    for k, val in _video_fields(m, seed):
        add("video", k, "" if val is None else val)
    for t in m.get("thumbnails") or []:
        add("thumbnail", f"{t.get('label')} ({t.get('w')}x{t.get('h')})", t.get("url"))
    for i, t in enumerate(m.get("tags") or [], start=1):
        add("tag", f"tag {i}", t)
    d = _parts(m.get("description"))
    for i, part in enumerate(d, start=1):
        add("description", "description" if len(d) == 1 else f"description (part {i}/{len(d)})", part)

    tx = transcript or {}
    add("transcript", "status", tx.get("status") or "Not Available")
    if tx.get("text"):
        add("transcript", "words", tx.get("words"))
        add("transcript", "characters", tx.get("chars"))
        p = _parts(tx.get("text"))
        for i, part in enumerate(p, start=1):
            add("transcript", "transcript" if len(p) == 1 else f"transcript (part {i}/{len(p)})", part)

    for label, lst in (("top_comment", top), ("latest_comment", latest)):
        for i, c in enumerate(lst or [], start=1):
            add(label, f"#{i}" + (" (pinned)" if c.get("pinned") else ""), c.get("text"),
                c.get("author"), int(c.get("likes") or 0), int(c.get("replies") or 0),
                c.get("publishedAt"))
    return R


def _video_fields(m, seed):
    """The dossier's per-video facts as ordered (column, value) pairs. Shared
    by the one-video dossier CSV and the Archive's one-row-per-video export,
    so both always carry the same fields under the same names."""
    m, seed = m or {}, seed or {}
    v = [
        ("title", m.get("title")), ("video_id", m.get("videoId")), ("url", m.get("url")),
        ("channel", m.get("channelTitle")), ("channel_handle", m.get("channelHandle")),
        ("channel_id", m.get("channelId")), ("channel_url", m.get("channelUrl")),
        ("subscribers", "hidden" if m.get("subsHidden") else int(m.get("subs") or 0)),
        ("channel_total_views", int(m.get("channelViews") or 0) or ""),
        ("channel_video_count", int(m.get("channelVideos") or 0) or ""),
        ("published_at", m.get("publishedAt")), ("age_days", round(m.get("ageDays") or 0, 1)),
        ("duration", m.get("durationText")), ("duration_seconds", m.get("durationS")),
        ("is_short", "yes" if m.get("isShort") else "no"),
        ("views", int(m.get("views") or 0)),
        ("likes", "hidden" if m.get("likesHidden") else int(m.get("likes") or 0)),
        ("comments", "hidden" if m.get("commentsHidden") else int(m.get("comments") or 0)),
        ("engagement_rate", _pct(m.get("engagement"))),
        ("like_rate", _pct(m.get("likeRate"))),
        ("comment_rate", _pct(m.get("commentRate"))),
        ("views_per_day", round(m.get("viewsPerDay") or 0, 1)),
        ("outlier_views_per_subscriber", f"{m.get('outlier') or 0:.2f}x" if m.get("outlier") else ""),
        ("outlier_tier", m.get("tier")),
        ("vs_channel_average", f"{m.get('vsChannelAvg') or 0:.2f}x" if m.get("vsChannelAvg") else ""),
        ("channel_average_views", int(m.get("channelAvgViews") or 0) or ""),
    ]
    if seed.get("outlierText"):
        v.append(("outlier_" + (seed.get("outlierKey") or "card"), seed.get("outlierText")))
    v += [
        ("category", m.get("category")), ("language", m.get("language")),
        ("definition", m.get("definition")), ("captions_on_youtube", "yes" if m.get("hasCaptions") else "no"),
        ("licensed_content", "yes" if m.get("licensed") else "no"),
        ("made_for_kids", "yes" if m.get("madeForKids") else "no"),
        ("privacy", m.get("privacy")), ("license", m.get("license")),
        ("live_content", m.get("liveContent")),
        ("topics", ", ".join(m.get("topics") or [])),
        ("tags_count", len(m.get("tags") or [])),
        ("data_source", "YouTube Data API" if m.get("source") == "api" else "yt-dlp"),
        ("exported_at", time.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    return v


def write_dossier_csv(path, rows):
    """UTF-8 with BOM (Excel opens Persian / Arabic / emoji correctly),
    written atomically."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADERS)
        for r in rows:
            w.writerow([_safe(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", x) if isinstance(x, str) else x)
                        for x in r])
    os.replace(tmp, path)
    return path


def dossier_filename(meta, ext="csv"):
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str((meta or {}).get("title") or "")).strip()
    title = re.sub(r"\s+", " ", title)[:70].rstrip(". ")
    vid = (meta or {}).get("videoId") or "video"
    return f"{title or vid} [{vid}] dossier.{ext}"


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


# ── v2.2: one whole dossier in one call (the Archive's full export) ────────
def collect(video_id, keys=(), dead_keys=None, stop_check=None):
    """Everything the dossier card shows for ONE video, gathered in one
    blocking call (run it on a worker thread):

        {"meta": dict | None, "top": [...], "latest": [...],
         "transcript": {"status", "text", "words", "chars"},
         "commentsOff": bool, "errors": [str]}

    Same source ladder as the card: the API with the keys in order (a key
    that hits "quota exceeded" goes into `dead_keys`, shared across calls so
    a long export stops trying it), yt-dlp when there is no key / quota or the
    API fails. Never raises; whatever failed is listed in "errors"."""
    from app.core.youtube_api import QuotaExceededError, build_youtube_service
    stop = stop_check or (lambda: False)
    dead = dead_keys if dead_keys is not None else set()
    out = {"meta": None, "top": [], "latest": [], "transcript": {},
           "commentsOff": False, "errors": []}
    yt, yt_key = None, None
    for k in keys or ():
        if k in dead:
            continue
        if stop():
            return out
        try:
            svc = build_youtube_service(k, timeout=25)
            out["meta"] = meta_from_api(svc, video_id, stop_check=stop)
            yt, yt_key = svc, k
            break
        except QuotaExceededError:
            dead.add(k)
            continue
        except LookupError as exc:          # deleted / private: yt-dlp won't do better
            out["errors"].append(str(exc))
            return out
        except Exception as exc:            # noqa: BLE001 — fall back to yt-dlp
            out["errors"].append("API: " + str(exc)[:200])
            break
    if out["meta"] is None and not stop():
        try:
            out["meta"] = meta_from_ytdlp(video_id)
        except Exception as exc:            # noqa: BLE001
            out["errors"].append("details: " + str(exc)[:200])
    m = out["meta"]
    if m is None or stop():
        return out

    try:
        got = False
        if yt is not None:
            try:
                out["top"] = comments_from_api(yt, video_id, "relevance", stop_check=stop)
                out["latest"] = comments_from_api(yt, video_id, "time", stop_check=stop)
                got = True
            except CommentsOff:
                raise
            except QuotaExceededError:
                dead.add(yt_key)
            except Exception as exc:        # noqa: BLE001 — yt-dlp below
                out["errors"].append("API comments: " + str(exc)[:200])
        if not got and not stop():
            out["top"] = comments_from_ytdlp(video_id, "top")
            if not stop():
                out["latest"] = comments_from_ytdlp(video_id, "new")
    except CommentsOff:
        out["commentsOff"] = True
    except Exception as exc:                # noqa: BLE001
        out["errors"].append("comments: " + str(exc)[:200])

    if stop():
        return out
    try:
        from app.core.transcripts import fetch_transcript
        status, text, _kind = fetch_transcript(video_id, thorough=True,
                                               expect_secs=int(m.get("durationS") or 0))
        ok = str(status).startswith("Available") and text and text != "N/A"
        text = text if ok else ""
        out["transcript"] = {"status": status if ok else "Not Available", "text": text,
                             "words": word_count(text), "chars": len(text)}
    except Exception as exc:                # noqa: BLE001
        out["transcript"] = {"status": "Not Available", "text": ""}
        out["errors"].append("transcript: " + str(exc)[:200])
    return out


_MULTI_HEADERS = ["video_no", "video_id", "video_title"] + _CSV_HEADERS


def write_multi_dossier_csv(path, videos):
    """Many dossiers in ONE file, the same rows as a single dossier CSV with
    three leading columns so every row says which video it belongs to (filter
    or pivot on video_no / video_id in Excel). `videos` is a list of
    (video_no, video_id, title, rows) with `rows` from dossier_rows().
    UTF-8 with BOM, written atomically."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    clean = lambda x: _safe(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", x) if isinstance(x, str) else x)
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(_MULTI_HEADERS)
        for no, vid, title, rows in videos:
            for r in rows:
                w.writerow([no, vid, clean(title)] + [clean(x) for x in r])
    os.replace(tmp, path)
    return path


# ── v2.3: the Archive's full export as a real table ───────────────────────
# v2.2 wrote every video as ~100 stacked "section / field / value" rows, so a
# 40-video export was 4,000 rows of one video's facts piled on the next one's.
# Now it is the shape people expect from a spreadsheet: ONE ROW PER VIDEO, in
# the order the section shows them, and every fact in its own fixed column
# (stats, rates, outlier, tags, description, each thumbnail size, the whole
# transcript, then comment 1..N with its author / likes / replies / date).
# Every row has every column, so nothing ever slides sideways: a video with
# 12 comments just leaves comment 13..30 empty. Text longer than an Excel
# cell continues in `<column>_part2`, right next to it, never cut.
_THUMB_LABELS = ("maxres", "standard", "high", "medium", "default")
_COMMENT_COLS = (("comment", "text"), ("author", "author"), ("likes", "likes"),
                 ("replies", "replies"), ("date", "publishedAt"))
_LINK_COLS = ("url", "channel_url")
_WIDE_COLS = {"title": 48, "description": 60, "transcript": 60, "tags": 40,
              "topics": 30, "export_notes": 40, "archive_section": 28, "channel": 24}


def _wide_rows(videos):
    """(headers, rows) for write_wide_dossier: fixed columns, every row full."""
    seed_keys = []
    n_top = n_new = 0
    thumbs = set()
    for v in videos:
        seed = v.get("seed") or {}
        if seed.get("outlierText"):
            k = "outlier_" + (seed.get("outlierKey") or "card")
            if k not in seed_keys:
                seed_keys.append(k)
        n_top = max(n_top, len(v.get("top") or []))
        n_new = max(n_new, len(v.get("latest") or []))
        for t in (v.get("meta") or {}).get("thumbnails") or []:
            thumbs.add(t.get("label"))

    base = [k for k, _ in _video_fields({}, {})]
    # the archive's own outlier figure sits next to the dossier's
    at = base.index("channel_average_views") + 1 if "channel_average_views" in base else len(base)
    base = base[:at] + seed_keys + base[at:]
    lead = ["video_no", "title", "url", "video_id"]
    headers = lead + [k for k in base if k not in lead and k != "exported_at"]
    headers += ["tags", "description"]
    headers += ["thumbnail_" + t for t in _THUMB_LABELS if t in thumbs]
    headers += ["transcript_status", "transcript_words", "transcript_characters", "transcript"]
    for label, n in (("top", n_top), ("latest", n_new)):
        for i in range(1, n + 1):
            headers += [f"{label}_{i}_{c}" for c, _ in _COMMENT_COLS]
    headers += ["comments_note", "export_notes", "archive_section", "exported_at"]

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for v in videos:
        m = v.get("meta") or {}
        row = dict.fromkeys(headers, "")
        if m:
            for k, val in _video_fields(m, v.get("seed")):
                if k in row:
                    row[k] = "" if val is None else val
            row["tags"] = ", ".join(m.get("tags") or [])
            row["description"] = m.get("description") or ""
            for t in m.get("thumbnails") or []:
                if "thumbnail_" + str(t.get("label")) in row:
                    row["thumbnail_" + t["label"]] = t.get("url") or ""
            tx = v.get("transcript") or {}
            row["transcript_status"] = tx.get("status") or "Not Available"
            if tx.get("text"):
                row["transcript_words"] = tx.get("words") or ""
                row["transcript_characters"] = tx.get("chars") or ""
                row["transcript"] = tx.get("text")
            for label, lst in (("top", v.get("top")), ("latest", v.get("latest"))):
                for i, c in enumerate(lst or [], start=1):
                    for col, key in _COMMENT_COLS:
                        val = c.get(key)
                        if key in ("likes", "replies"):
                            val = int(val or 0)
                        elif key == "text" and c.get("pinned"):
                            val = "[pinned] " + str(val or "")
                        row[f"{label}_{i}_{col}"] = "" if val is None else val
        row["video_no"] = v.get("no") or ""
        row["title"] = m.get("title") or v.get("title") or ""
        row["video_id"] = m.get("videoId") or v.get("video_id") or ""
        row["url"] = m.get("url") or v.get("url") or (
            f"https://www.youtube.com/watch?v={row['video_id']}" if row["video_id"] else "")
        if v.get("comments_off"):
            row["comments_note"] = "comments turned off for this video"
        row["export_notes"] = " | ".join(str(e) for e in (v.get("errors") or []) if e)
        row["archive_section"] = v.get("section") or ""
        row["exported_at"] = stamp
        rows.append(row)
    return headers, rows


def write_wide_dossier(path, videos, fmt="csv"):
    """Many dossiers as ONE table: a row per video (in the given order), a
    fixed column per fact. `videos` is a list of dicts with keys no,
    video_id, title, url, section, meta, top, latest, transcript, seed,
    comments_off, errors. fmt "csv" (UTF-8 with BOM, Excel opens Persian /
    emoji correctly) or "xlsx" (v2.4: Index + one vertical block per video + the
    wide table as a third sheet). Written atomically."""
    from app.core.exports import _clean_cell, _spill_long_cells
    headers, rows = _wide_rows(videos)
    rows = [{k: _clean_cell(r.get(k, "")) for k in headers} for r in rows]
    full_headers, full_rows = headers, rows          # unspilled: the vertical sheet shows whole text
    if rows:
        headers, rows = _spill_long_cells(rows)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        if fmt == "xlsx":
            _write_vertical_xlsx(tmp, full_headers, full_rows, headers, rows)
        else:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in rows:
                    w.writerow([_safe(r.get(h, "")) for h in headers])
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return path


def _write_wide_xlsx(path, headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    _add_wide_sheet(wb, wb.active, headers, rows)
    wb.save(path)


def _add_wide_sheet(wb, ws, headers, rows):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws.title = "videos"
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F2430")
    link_font = Font(color="2F6FEB", underline="single")
    top = Alignment(vertical="top")
    ws.append(headers)
    for c in ws[1]:
        c.font = head_font
        c.fill = head_fill
        c.alignment = Alignment(vertical="center")
    for r, row in enumerate(rows, start=2):
        for c, h in enumerate(headers, start=1):
            v = row.get(h, "")
            if isinstance(v, str) and len(v) > 32766:
                v = v[:32765] + "…"          # safety net, the spill keeps cells far shorter
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = top
            if isinstance(v, str):
                cell.data_type = "s"          # "=..." in a title stays text, never a formula
                if h in _LINK_COLS or h.startswith("thumbnail_"):
                    if v.startswith("http"):
                        cell.hyperlink = v
                        cell.font = link_font
    for c, h in enumerate(headers, start=1):
        base = re.sub(r"_part\d+$", "", h)
        if base in _WIDE_COLS:
            w = _WIDE_COLS[base]
        elif base.endswith("_comment"):
            w = 50
        elif base.endswith("_author") or base.endswith("_date") or base.startswith("thumbnail_") or base in _LINK_COLS:
            w = 22
        else:
            w = max(10, min(24, len(h) + 2))
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "C2"                    # header + video_no / title stay put
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"


# ── v2.4: the xlsx as vertical blocks ─────────────────────────────────────
# 355 columns meant scrolling sideways for ever. The workbook now opens on
#   Index               one compact row per video, title links to its block
#   Videos (Vertical)   one BLOCK per video, "field | value" rows top to
#                       bottom in sections (Video, Channel, Performance,
#                       Content details, Tags & Description, Thumbnails,
#                       Transcript, Top / Latest comments, Export info);
#                       empty fields are left out
#   videos              the old wide table (one row per video) for filters
#                       and pivots
_V_SECTIONS = (
    ("Video", ("url", "video_id", "published_at", "age_days", "duration",
               "duration_seconds", "is_short")),
    ("Channel", ("channel", "channel_handle", "channel_id", "channel_url",
                 "subscribers", "channel_total_views", "channel_video_count")),
    ("Performance", ("views", "likes", "comments", "engagement_rate", "like_rate",
                     "comment_rate", "views_per_day", "outlier_views_per_subscriber",
                     "outlier_tier", "vs_channel_average", "channel_average_views")),
    ("Content details", ("category", "language", "definition", "captions_on_youtube",
                         "licensed_content", "made_for_kids", "privacy", "license",
                         "live_content", "topics", "tags_count", "data_source")),
    ("Tags & Description", ("tags", "description")),
    ("Thumbnails", tuple("thumbnail_" + t for t in _THUMB_LABELS)),
    ("Transcript", ("transcript_status", "transcript_words", "transcript_characters",
                    "transcript")),
)
_V_TAIL = ("comments_note", "export_notes", "archive_section", "exported_at")
_V_INDEX_COLS = (("#", "video_no", 6), ("Title", "title", 75), ("Channel", "channel", 28),
                 ("Subscribers", "subscribers", 13), ("Views", "views", 12),
                 ("Likes", "likes", 10), ("Comments", "comments", 11),
                 ("Engagement", "engagement_rate", 12), ("Views/day", "views_per_day", 11),
                 ("Outlier tier", "outlier_tier", 18), ("Duration", "duration", 10),
                 ("Published", "published_at", 20))
_V_COMMENT_RE = re.compile(r"^(top|latest)_(\d+)_(comment|author|likes|replies|date)$")
_V_TEXT_WIDTH = 100          # characters per line in the value column, for row heights


def _v_height(text):
    import math
    n = sum(max(1, math.ceil(len(line) / _V_TEXT_WIDTH)) for line in str(text).split("\n"))
    return min(409, max(15, n * 13 + 4))


def _v_split_description(text, limit=1500):
    """Description / transcript over several rows: a single cell taller than Excel's
    409 pt row limit would be cut off on screen."""
    from app.core.exports import _split_text
    lines = []
    for line in str(text).split("\n"):
        lines += _split_text(line, limit)    # a transcript is one huge line: cut at spaces
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    chunks.append(cur)
    return [c.strip("\n") for c in chunks]


def _write_vertical_xlsx(path, full_headers, full_rows, wide_headers, wide_rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as col_letter
    from openpyxl.worksheet.properties import PageSetupProperties

    F, NAVY, ACC, SEC, LINE = "Arial", "1F2430", "2F5D8A", "E8EEF5", "D0D7E0"
    thin = Side(style="thin", color=LINE)
    f_base = Font(name=F, size=10)
    f_lab = Font(name=F, size=10, bold=True, color="3A4556")
    f_link = Font(name=F, size=10, color="0563C1", underline="single")
    left_top = Alignment(vertical="top", wrap_text=True, horizontal="left")

    wb = Workbook()
    wi = wb.active
    wi.title = "Index"
    wv = wb.create_sheet("Videos (Vertical)")
    _add_wide_sheet(wb, wb.create_sheet(), wide_headers, wide_rows)

    placed = set(sum((list(f) for _n, f in _V_SECTIONS), [])) | set(_V_TAIL) | {"video_no", "title"}
    comment_keys = {}
    for h in full_headers:
        m = _V_COMMENT_RE.match(h)
        if m:
            comment_keys.setdefault((m.group(1), int(m.group(2))), {})[m.group(3)] = h
            placed.add(h)
    extra_perf = [h for h in full_headers if h.startswith("outlier_") and h not in placed]
    placed.update(extra_perf)
    leftovers = [h for h in full_headers if h not in placed]

    def val(row, h):
        v = row.get(h, "")
        return None if v in ("", None) else v

    wv.sheet_view.showGridLines = False
    wv.column_dimensions["A"].width = 30
    wv.column_dimensions["B"].width = 105
    wv.cell(1, 1, "All videos: vertical view").font = Font(name=F, size=14, bold=True, color=NAVY)
    wv.cell(2, 1, "Each video is one block, its fields listed top to bottom. Empty fields are left out. "
                  "Use the Index sheet to jump to a video; the 'videos' sheet is the same data as one wide row per video."
            ).font = Font(name=F, size=9, italic=True, color="6B7686")

    def put(r, label, v, link=False):
        a = wv.cell(r, 1, label)
        a.font, a.alignment = f_lab, Alignment(vertical="top", wrap_text=True)
        b = wv.cell(r, 2, v)
        b.font, b.alignment = f_base, left_top
        if isinstance(v, str):
            b.data_type = "s"             # "=..." in a title stays text, never a formula
            wv.row_dimensions[r].height = _v_height(v)
            if link and v.startswith("http"):
                b.hyperlink, b.font = v, f_link
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            b.number_format = "#,##0" if float(v).is_integer() else "#,##0.00"
        for c in (a, b):
            c.border = Border(bottom=thin)

    def section(r, name):
        for c in (1, 2):
            wv.cell(r, c).fill = PatternFill("solid", fgColor=SEC)
        wv.cell(r, 1, name.upper()).font = Font(name=F, size=9, bold=True, color=ACC)
        wv.row_dimensions[r].height = 18

    r = 4
    anchors = []
    for row in full_rows:
        anchors.append(r)
        title = str(val(row, "title") or "")
        for c in (1, 2):
            wv.cell(r, c).fill = PatternFill("solid", fgColor=NAVY)
        a = wv.cell(r, 1, f"VIDEO {val(row, 'video_no') or ''}".strip())
        a.font, a.alignment = Font(name=F, size=11, bold=True, color="FFFFFF"), Alignment(vertical="center")
        b = wv.cell(r, 2, title)
        b.data_type = "s"
        b.font = Font(name=F, size=11, bold=True, color="FFFFFF")
        b.alignment = Alignment(vertical="center", wrap_text=True)
        wv.row_dimensions[r].height = 26 if len(title) < 100 else 40
        r += 1
        for name, fields in _V_SECTIONS:
            if name == "Performance":
                fields = tuple(fields) + tuple(extra_perf)
            items = [(f, val(row, f)) for f in fields if f in row and val(row, f) is not None]
            if not items:
                continue
            section(r, name)
            r += 1
            for f, v in items:
                if f in ("description", "transcript"):
                    for i, chunk in enumerate(_v_split_description(v)):
                        put(r, f if i == 0 else "", chunk)
                        r += 1
                else:
                    put(r, f, v, link=f in _LINK_COLS or f.startswith("thumbnail_"))
                    r += 1
        for kind, label in (("top", "Top comments"), ("latest", "Latest comments")):
            items = []
            for key in sorted(k for k in comment_keys if k[0] == kind):
                d = comment_keys[key]
                text = val(row, d["comment"])
                if text is None:
                    continue
                meta = " · ".join(x for x in (
                    str(val(row, d["author"])) if val(row, d["author"]) is not None else None,
                    f"{val(row, d['likes'])} likes" if val(row, d["likes"]) is not None else None,
                    f"{val(row, d['replies'])} replies" if val(row, d["replies"]) is not None else None,
                    str(val(row, d["date"])) if val(row, d["date"]) is not None else None) if x)
                items.append((f"{kind}_{key[1]}", meta + "\n" + str(text)))
            if items:
                section(r, f"{label} ({len(items)})")
                r += 1
                for lab, txt in items:
                    put(r, lab, txt)
                    r += 1
        items = [(f, val(row, f)) for f in tuple(_V_TAIL) + tuple(leftovers) if val(row, f) is not None]
        if items:
            section(r, "Export info")
            r += 1
            for f, v in items:
                put(r, f, v)
                r += 1
        r += 2
    wv.freeze_panes = "A4"
    wv.page_setup.orientation = "portrait"
    wv.page_setup.fitToWidth, wv.page_setup.fitToHeight = 1, 0
    wv.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    # Index
    wi.sheet_view.showGridLines = False
    for i, (h, _k, w) in enumerate(_V_INDEX_COLS, start=1):
        c = wi.cell(1, i, h)
        c.font = Font(name=F, size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        wi.column_dimensions[col_letter(i)].width = w
    wi.row_dimensions[1].height = 24
    centred = ("video_no", "engagement_rate", "duration", "published_at", "outlier_tier")
    for n, row in enumerate(full_rows, start=2):
        for i, (_h, k, _w) in enumerate(_V_INDEX_COLS, start=1):
            v = val(row, k)
            c = wi.cell(n, i, v)
            c.font = f_base
            c.border = Border(bottom=thin)
            c.alignment = Alignment(vertical="center", wrap_text=(k == "title"),
                                    horizontal="center" if k in centred else None)
            if isinstance(v, str):
                c.data_type = "s"
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and k != "video_no":
                c.number_format = "#,##0"
            if k == "title" and v is not None:
                c.hyperlink = f"#'Videos (Vertical)'!A{anchors[n - 2]}"
                c.font = Font(name=F, size=10, color="0563C1", underline="single")
        wi.row_dimensions[n].height = 28
    wi.freeze_panes = "C2"
    if full_rows:
        wi.auto_filter.ref = f"A1:{col_letter(len(_V_INDEX_COLS))}{len(full_rows) + 1}"
    wi.page_setup.orientation = "landscape"
    wi.page_setup.fitToWidth, wi.page_setup.fitToHeight = 1, 0
    wi.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    wb.active = 0
    wb.save(path)


def write_single_dossier_xlsx(path, meta, top, latest, transcript, seed=None,
                              comments_off=False, errors=None):
    """v2.4: ONE video's dossier as the same vertical workbook the Archive
    export writes (Index, Videos (Vertical), videos). `transcript` is
    {"status", "text", "words", "chars"} like dossier_rows() takes."""
    m = meta or {}
    entry = {
        "no": 1, "video_id": m.get("videoId") or "", "title": m.get("title") or "",
        "url": m.get("url") or "", "section": "Video Dossier", "meta": m,
        "top": top or [], "latest": latest or [], "transcript": transcript or {},
        "seed": seed or {}, "comments_off": bool(comments_off), "errors": errors or [],
    }
    return write_wide_dossier(path, [entry], "xlsx")


__all__ = ["COMMENT_COUNT", "write_single_dossier_xlsx", "write_wide_dossier", "TOP_POOL", "by_likes", "collect", "write_multi_dossier_csv", "CommentsOff", "meta_from_api", "meta_from_ytdlp",
           "comments_from_api", "comments_from_ytdlp", "dossier_rows",
           "write_dossier_csv", "dossier_filename", "word_count", "fmt_count",
           "fmt_duration"]
