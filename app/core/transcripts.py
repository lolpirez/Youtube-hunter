"""app.core.transcripts - Youtube Scrapper."""

import html
import json
import re
import threading
import time as _time
import urllib.parse
import urllib.request

from app.core.net import _urlopen, configured_proxy

# ── lazy library binding ─────────────────────────────────────────────────
# `youtube_transcript_api` costs about 7.5 MB of resident memory, and this module
# is imported at STARTUP (dashboard_vm imports fetch_transcript for the scrape
# form), so every launch used to pay for a library that is only needed when the
# user actually asks for a transcript. Resolved once, on first use, and cached.
_YTA = None


def _yta():
    """(api_class, has_new_api, block_errors, no_transcript_errors)."""
    global _YTA
    if _YTA is None:
        import youtube_transcript_api as mod
        from youtube_transcript_api import YouTubeTranscriptApi as api
        block = tuple(
            e for e in (getattr(mod, n, None)
                        for n in ("RequestBlocked", "IpBlocked", "TooManyRequests"))
            if isinstance(e, type))
        no_tr = tuple(
            e for e in (getattr(mod, n, None)
                        for n in ("TranscriptsDisabled", "NoTranscriptFound",
                                  "VideoUnavailable", "CouldNotRetrieveTranscript"))
            if isinstance(e, type))
        _YTA = (api, hasattr(api, "fetch"), block, no_tr)
    return _YTA
# ═════════════════════════════════════════════════════════════════════════
#  TRANSCRIPTS — single clean code path for old AND new library versions
#  ---------------------------------------------------------------------
#  The previous implementation mixed the 0.6.x static API with the 1.x
#  instance API; on a current install two of its three attempts raised
#  AttributeError on every video. This resolves the right API once and
#  distinguishes "no transcript exists" from "YouTube is blocking us".
# ═════════════════════════════════════════════════════════════════════════
_TRANSCRIPT_LANGS = ["en", "en-US", "en-GB", "fa", "ar", "es", "fr", "de",
                      "it", "pt", "ru", "ja", "ko", "hi", "tr", "nl", "pl"]

# Concurrent yt-dlp caption fetches. See _fetch_via_ytdlp. Resized per scrape
# by set_turbo() — the Worker Threads setting decides how hard we push.
_CAPTION_SLOTS = threading.Semaphore(2)


# ═════════════════════════════════════════════════════════════════════════
#  TURBO — the Worker Threads setting (1-8) → how parallel transcripts run
#  ---------------------------------------------------------------------
#  A transcript is almost pure network wait (two small HTTPS round-trips), so
#  the CPU is idle while it runs and several can be in flight at once. What
#  actually decides the speed is YouTube's caption endpoint: it rate-limits
#  per IP and starts answering 429 ("Sorry") after a fast burst. That is why
#  more threads used to make a 300-video scrape SLOWER, not faster — see the
#  shared caption pacer below. Workers are capped where more of them stop
#  helping, and each level also sets how fast that pacer may creep up after a
#  run of successes (caption_floor: lower = faster ceiling).
# ═════════════════════════════════════════════════════════════════════════
_TURBO = {
    #     transcript workers, yt-dlp fallback slots
    1: (1, 1),
    2: (2, 2),
    3: (3, 2),
    4: (4, 3),
    5: (6, 3),
    6: (8, 4),
    7: (8, 4),
    8: (10, 5),
}
_CAPTION_FLOORS = {
    1: 1.50, 2: 1.30, 3: 1.15, 4: 1.00,
    5: 0.85, 6: 0.75, 7: 0.65, 8: 0.55,
}


# v1.0.6: the two transcript modes behave differently when YouTube pushes back.
#   fast      speed first. An IP block never stops the run: one short retry,
#             then the video is parked (retried at the end / next run). While
#             the caption endpoint is refusing, the circuit breaker skips the
#             doomed requests outright and probes every few seconds.
#   thorough  completeness first. Gentler start, more retries, the full yt-dlp
#             client ladder, and the scraper waits a block out.
_MODE_PROFILES = {
    False: {"timeout": 8, "rate_retries": 1, "cool": 3.0, "ceil": 2.5,
            "probe": 8.0, "ladder": ([],)},
    True: {"timeout": 12, "rate_retries": 3, "cool": 6.0, "ceil": 3.5,
           "probe": 15.0, "ladder": None},
}
# v1.0.7: wall-clock budget for the thorough mode's full yt-dlp client ladder.
# It used to walk up to twelve rungs (25 s socket timeout, 2 extractor retries
# each), so one stubborn video could hold a worker for minutes.
_LADDER_BUDGET = 45.0
# v1.0.7: thorough "verified" answers (a confirmed "no captions", or a short
# transcript that every source agreed on) are trusted from the cache for this
# long, so a re-run does not re-check the same videos from scratch.
_VERIFIED_TTL = 14 * 24 * 3600

# Side work for the thorough re-checks: the confirmations run IN PARALLEL
# instead of one after another (the library's watch page and yt-dlp's player
# answer used to queue behind each other on every caption-less video).
_RECHECK_POOL = None
_RECHECK_LOCK = threading.Lock()


def _recheck_pool():
    global _RECHECK_POOL
    with _RECHECK_LOCK:
        if _RECHECK_POOL is None:
            from concurrent.futures import ThreadPoolExecutor
            _RECHECK_POOL = ThreadPoolExecutor(max_workers=8,
                                               thread_name_prefix="tx-recheck")
        return _RECHECK_POOL
_MODE = {"thorough": False}


def _mp():
    return _MODE_PROFILES[bool(_MODE["thorough"])]


def turbo_profile(thread_count):
    """{"workers", "ytdlp_slots", "caption_floor", "turbo"} for a level."""
    try:
        n = max(1, min(8, int(thread_count or 1)))
    except (TypeError, ValueError):
        n = 1
    workers, slots = _TURBO[n]
    return {"level": n, "workers": workers, "ytdlp_slots": slots,
            "caption_floor": _CAPTION_FLOORS[n], "turbo": n >= 6}


def set_turbo(thread_count, thorough=False):
    """Apply a Worker Threads value (and the transcript mode) before a scrape.
    Returns the profile."""
    global _CAPTION_SLOTS
    _MODE["thorough"] = bool(thorough)
    prof = turbo_profile(thread_count)
    # A fresh semaphore: fetches still holding the old one finish normally.
    _CAPTION_SLOTS = threading.Semaphore(prof["ytdlp_slots"])
    _caption_reset(prof["caption_floor"])
    reset_transcript_stats()
    with _LADDER_LOCK:
        _deep_spent.clear()     # a new scrape gives every video its deep tries back
    return prof


# One YouTubeTranscriptApi (and so one requests.Session with its keep-alive
# connection pool) PER WORKER THREAD. The old code built a new client for every
# video, which meant a fresh TCP + TLS handshake to youtube.com twice per
# transcript — on a slow or proxied link that handshake was most of the time.
# requests.Session is not guaranteed thread-safe, hence thread-local.
_TLS = threading.local()


def _session():
    sess = getattr(_TLS, "session", None)
    if sess is None:
        try:
            import requests
            from requests.adapters import HTTPAdapter
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            sess.headers.update({"Accept-Language": "en-US,en;q=0.9"})
        except Exception:      # noqa: BLE001 — the library makes its own then
            sess = False
        _TLS.session = sess
    return sess or None


# Circuit breaker for the library path. On a network where youtube.com blocks
# the library outright, every video used to pay a doomed library attempt before
# falling back to yt-dlp. After a few blocks in a row the library is skipped
# for a while and transcripts go straight to the fallback; one success closes
# the breaker again.
_BREAKER_TRIP = 4
_BREAKER_COOLDOWN = 90.0
_breaker = {"fails": 0, "open_until": 0.0, "probe_at": 0.0}
_breaker_lock = threading.Lock()


def _library_allowed():
    """Closed breaker: always. Open: one probe every few seconds (half-open),
    so a block that lifts, or a VPN switch, is noticed without a reset."""
    import time as _t
    with _breaker_lock:
        now = _t.monotonic()
        if now >= _breaker["open_until"]:
            return True
        if now >= _breaker["probe_at"]:
            _breaker["probe_at"] = now + _mp()["probe"]
            return True
        return False


def _library_result(kind):
    import time as _t
    with _breaker_lock:
        if kind in ("blocked", "rate"):
            _breaker["fails"] += 1
            if _breaker["fails"] >= _BREAKER_TRIP:
                now = _t.monotonic()
                _breaker["open_until"] = now + _BREAKER_COOLDOWN
                _breaker["probe_at"] = now + _mp()["probe"]
                _breaker["fails"] = 0
        elif kind in ("", "none"):
            _breaker["fails"] = 0
            _breaker["open_until"] = 0.0


def reset_breaker():
    """Called when the user switches IP / the run resumes / a new scrape.
    Also wakes every worker sleeping in the pacer (see _caption_wait)."""
    with _breaker_lock:
        _breaker["fails"] = 0
        _breaker["open_until"] = 0.0
        _breaker["probe_at"] = 0.0
    _caption_reset()          # a new IP starts from the safe pace again


def _join_transcript(entries):
    parts = []
    for s in entries:
        txt = getattr(s, "text", None)
        if txt is None and isinstance(s, dict):
            txt = s.get("text", "")
        if txt:
            parts.append(txt)
    return " ".join(parts)


def get_public_ip(timeout=5):
    """Best-effort lookup of this machine's current public IP address.
    Asks a few plain-text endpoints AT THE SAME TIME and takes the first
    answer (v1.0.6: they used to be tried one after another, up to ~18 s
    through a slow proxy before the block card could even compare IPs).
    Returns None (never raises) if all of them fail: callers treat that as
    "couldn't verify" rather than "IP unchanged"."""
    import queue as _queue
    endpoints = (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    )
    proxy = configured_proxy()
    answers = _queue.Queue()

    def _one(url):
        ip = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            # Through the configured proxy: the whole point of this lookup is to
            # report the IP YouTube actually sees, and on a proxied setup a
            # direct request reports the wrong one.
            with _urlopen(req, timeout, proxy) as resp:
                got = resp.read().decode("utf-8", "ignore").strip()
                if got and re.match(r"^[0-9a-fA-F:.]+$", got):
                    ip = got
        except Exception:      # noqa: BLE001
            ip = None
        answers.put(ip)

    for url in endpoints:
        threading.Thread(target=_one, args=(url,), daemon=True).start()
    deadline = _time.monotonic() + timeout + 1.0
    for _ in endpoints:
        left = deadline - _time.monotonic()
        if left <= 0:
            break
        try:
            ip = answers.get(timeout=left)
        except _queue.Empty:
            break
        if ip:
            return ip
    return None


def _looks_short(text, expect_secs):
    """Thorough mode: a transcript far too short for the video's length is
    probably a fragment (a tiny manual track next to a full auto one, or a
    truncated payload). Speech runs ~12-15 characters a second; under ~0.8
    a second on a video of 2+ minutes is worth a second source."""
    try:
        secs = float(expect_secs or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs < 120 or not text or text == "N/A":
        return False
    return len(text) < secs * 0.8


def _ytdlp_slot(video_id, proxy):
    """_ytdlp_quick under the yt-dlp slots (text | "" | None, never raises)."""
    try:
        with _CAPTION_SLOTS:
            return _ytdlp_quick(video_id, proxy)
    except Exception:          # noqa: BLE001
        return None


def _thorough_recheck(video_id, proxy, status, text, err_kind, expect_secs):
    """Thorough mode: do not take one endpoint's word for it.

    Returns (status, text, err_kind, verified). `verified` is True when a
    second source really answered and agreed, so the answer may be trusted
    from the cache on the next run (see _VERIFIED_TTL).

      * "no captions" from the fast InnerTube client is double-checked with the
        library's watch-page list AND yt-dlp's default client (the android
        client behind the fast path sometimes answers without the caption
        list: age gate, PO-token tracks, a half-bot-checked IP).
        v1.0.7: both confirmations run at the same time and the first one
        that finds text wins (they used to run one after the other).
      * a suspiciously short transcript is fetched again from the other
        sources and the LONGEST text wins. v1.0.7: one watch-page list
        serves both alternative tracks (it used to download the page twice),
        and yt-dlp runs alongside it instead of after it.
    """
    from concurrent.futures import as_completed
    has_text = bool(text and text != "N/A")
    pool = _recheck_pool()
    if err_kind == "none" and not has_text:
        futs = {pool.submit(_library_list, video_id, proxy, False): "lib",
                pool.submit(_ytdlp_slot, video_id, proxy): "ytdlp"}
        answered = False
        try:
            for fut in as_completed(futs, timeout=60):
                try:
                    got = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if futs[fut] == "lib":
                    if got[1] and got[1] != "N/A":
                        return (got[0], got[1], "", False)
                    if got[2] == "none":
                        answered = True
                else:
                    if got:
                        return ("Available (Auto)", got, "", False)
                    if got == "":
                        answered = True
        except Exception:      # noqa: BLE001 — timeout: keep the fast answer
            pass
        # Both confirmations failed or agreed: the fast client's answer stands
        # (reporting "blocked" here would raise the IP-block card for videos
        # that simply have no captions).
        return ("Not Available", "N/A", "none", answered)
    if has_text and _looks_short(text, expect_secs):
        best = (status, text, err_kind)
        futs = {pool.submit(_library_longest, video_id, proxy): "lib",
                pool.submit(_ytdlp_slot, video_id, proxy): "ytdlp"}
        answered = False
        try:
            for fut in as_completed(futs, timeout=60):
                try:
                    got = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if futs[fut] == "lib":
                    alt_status, alt_text = got[0], got[1]
                    if got[2] in ("", "none"):
                        answered = True
                else:
                    alt_status, alt_text = "Available (Auto)", got
                    if got is not None:
                        answered = True
                if alt_text and alt_text != "N/A" and len(alt_text) > len(best[1]):
                    best = (alt_status, alt_text, "")
        except Exception:      # noqa: BLE001
            pass
        return best + (answered,)
    return (status, text, err_kind, False)


def fetch_transcript(video_id, thorough=False, expect_secs=0, hedge=False):
    """Fetch a transcript. Returns (status, text, err_kind) and NEVER raises.

    `thorough` (v1.0.5, the Scraper's "Thorough" transcript mode): a cached
    "no captions" is checked again, a fast-path "no captions" is confirmed by
    two other sources, and a transcript that looks too short for the video
    (`expect_secs`, the video's length) is re-fetched and the longest kept.
    Slower, but it is the mode that does not miss captions that exist.
    `hedge` (v1.0.8, thorough retry rounds): every source races at once
    instead of library -> light ladder -> deep ladder one after another, see
    _hedged_fetch.
    err_kind: "" ok/none · "blocked" IP/rate blocked · "error" other.

    The fast path, in order:

      0. The on-disk cache (shared with the AI Chat): a video read before —
         by a previous scrape, a resume, or the chat — costs nothing.
      1. `youtube_transcript_api`, ONE list() + ONE fetch() on a kept-alive
         per-thread session. If YouTube says the video has no captions, that is
         final: we stop here. (The old code then ran the whole yt-dlp client
         ladder — 5-25 s per rung — just to hear "no captions" again. On a
         Shorts-heavy channel that was most of the scrape time.)
      2. Only when the library was blocked or failed: yt-dlp, first a light
         three-rung lookup (no DASH/HLS manifests), and the full download
         client ladder only if nothing answered at all. yt-dlp inherits the
         configured proxy, the JS runtime and the cookies, so this is still the
         path that survives a filtered network.
    """
    video_id = str(video_id or "").strip()
    if not video_id:
        return ("Not Available", "N/A", "none")
    hit = cached_transcript(video_id)
    if hit is not None and thorough:
        # thorough: only a real cached TEXT that does not look cut is trusted,
        # or an answer an earlier thorough run already double-checked
        if (hit[2] == "none" or _looks_short(hit[1], expect_secs)) \
                and not _is_verified(video_id):
            hit = None
    if hit is not None:
        _bump_stat("cache")
        return hit
    _bump_stat("live")

    proxy = configured_proxy()
    if hedge and thorough:
        return _fetch_hedged_tail(video_id, proxy, expect_secs)
    if _library_allowed():
        status, text, err_kind = _library_quick(
            video_id, proxy, trim=False, expect_secs=expect_secs if thorough else 0)
        _library_result(err_kind)
    else:
        if not thorough:
            # v1.0.6 fast mode: the caption endpoint is refusing this IP right
            # now (breaker open). Every request would be refused too, and the
            # yt-dlp ladder used to spend 30-90 s per video proving it. Park
            # the video at once; the breaker probes every few seconds.
            return ("Not Available", "N/A", "blocked")
        status, text, err_kind = ("Not Available", "N/A", "blocked")

    if not (text and text != "N/A") and err_kind in ("blocked", "error"):
        fb = None
        try:
            with _CAPTION_SLOTS:
                # fast: one light rung (default client), never the full ladder
                fb = _ytdlp_quick(video_id, proxy, ladder=None if thorough else ([],))
            if fb is None and thorough and err_kind == "error":
                # Nothing answered the light lookup: the remaining clients,
                # raced on a budget (v1.0.7b; it used to re-walk the whole
                # download ladder one rung at a time, the light rungs
                # included, for up to 45 s). It takes the slot again on its
                # own, so a light lookup elsewhere is not stuck behind it.
                # Not for a "blocked" answer: an IP-level refusal refuses
                # every rung too.
                with _CAPTION_SLOTS:
                    fb = _ytdlp_deep(video_id, proxy)
        except Exception:      # noqa: BLE001 — never raise out of this API
            fb = None
        if fb:
            status, text, err_kind = "Available (Auto)", fb, ""
        elif fb == "":
            # The light lookup got a real player answer with no caption
            # tracks: final, even if the library was blocked.
            status, text, err_kind = "Not Available", "N/A", "none"
        # else (None): nothing answered. Keep "blocked" / "error" — "no
        # captions" was never actually checked, and the user needs to know to
        # switch the VPN node rather than read it as final.

    if err_kind == "rate":
        # Every attempt hit YouTube's caption rate limit. The yt-dlp ladder
        # would hit the same endpoint and only add minutes per video; report
        # it as a block so the scrape pauses for a new IP / a cool-down.
        return ("Not Available", "N/A", "blocked")

    verified = False
    if thorough:
        try:
            status, text, err_kind, verified = _thorough_recheck(
                video_id, proxy, status, text, err_kind, expect_secs)
        except Exception:      # noqa: BLE001 — never raise out of this API
            verified = False

    if text and text != "N/A":
        _cache_put(video_id, status, text, "",
                   verified=verified and _looks_short(text, expect_secs))
        return (status, text, "")
    _cache_put(video_id, "Not Available", "N/A", err_kind,
               verified=verified and err_kind == "none")
    return ("Not Available", "N/A", err_kind or "none")


# ═════════════════════════════════════════════════════════════════════════
#  v1.0.8 HEDGED RETRIES (thorough retry rounds)
#  ---------------------------------------------------------------------
#  A video that failed the main pass used to walk its sources in a row:
#  InnerTube (+ up to 3 rate retries) -> watch-page list -> light yt-dlp race
#  (20 s budget) -> deep yt-dlp race (25 s budget). That is the ~45-60 s per
#  leftover video the retry rounds showed ("Second pass: 0/1 recovered" a
#  minute after the round started). Now the library path starts first and,
#  if it has not produced text within _HEDGE_DELAY, the light AND deep yt-dlp
#  rungs start racing next to it. First real text wins; the round costs the
#  slowest single source, not the sum. The short delay keeps the common case
#  (InnerTube answers in ~1 s) from spawning yt-dlp at all.
#  Own pool + own slots: the retry round runs several videos at once and must
#  not queue behind the main pass's small yt-dlp semaphore.
# ═════════════════════════════════════════════════════════════════════════
_HEDGE_DELAY = 1.8
_HEDGE_BUDGET = 28.0
_HEDGE_SLOTS = threading.Semaphore(6)
_HEDGE_POOL = None
_HEDGE_LOCK = threading.Lock()


def _hedge_pool():
    global _HEDGE_POOL
    with _HEDGE_LOCK:
        if _HEDGE_POOL is None:
            from concurrent.futures import ThreadPoolExecutor
            _HEDGE_POOL = ThreadPoolExecutor(max_workers=16,
                                             thread_name_prefix="tx-hedge")
        return _HEDGE_POOL


def _ytdlp_hedge(video_id, proxy, deep):
    """Light + (optionally) deep rungs in ONE race. text | "" | None."""
    ladder = list(_QUICK_LADDER) + (list(_DEEP_LADDER) if deep else [])
    try:
        with _HEDGE_SLOTS:
            got = _ytdlp_quick(video_id, proxy, ladder=ladder,
                               budget=max(5.0, _HEDGE_BUDGET - _HEDGE_DELAY - 1.0))
    except Exception:          # noqa: BLE001
        got = None
    if deep and got is None:
        with _LADDER_LOCK:
            _deep_spent[video_id] = _deep_spent.get(video_id, 0) + 1
    return got


def _hedged_fetch(video_id, proxy, expect_secs):
    """Race the library path against the yt-dlp rungs.
    Returns (status, text, err_kind, verified). Never raises."""
    from concurrent.futures import FIRST_COMPLETED, wait
    pool = _hedge_pool()
    with _LADDER_LOCK:
        deep = _deep_spent.get(video_id, 0) < _DEEP_MAX_PER_VIDEO
    futs = {}
    lib = None
    if _library_allowed():
        futs[pool.submit(_library_quick, video_id, proxy, False, expect_secs)] = "lib"
    else:
        lib = ("Not Available", "N/A", "blocked")
    yt = None
    yt_started = False
    start = _time.monotonic()
    deadline = start + _HEDGE_BUDGET
    pending = set(futs)
    while True:
        now = _time.monotonic()
        if not yt_started and (lib is not None or now - start >= _HEDGE_DELAY):
            f = pool.submit(_ytdlp_hedge, video_id, proxy, deep)
            futs[f] = "ytdlp"
            pending.add(f)
            yt_started = True
        if not pending or now >= deadline or _aborted():
            break
        wait_for = deadline - now
        if not yt_started:
            wait_for = min(wait_for, max(0.05, start + _HEDGE_DELAY - now))
        done, pending = wait(pending, timeout=min(wait_for, 0.5),
                             return_when=FIRST_COMPLETED)
        for f in done:
            if futs[f] == "lib":
                try:
                    lib = f.result()
                except Exception:  # noqa: BLE001
                    lib = ("Not Available", "N/A", "error")
                _library_result(lib[2])
                if lib[1] and lib[1] != "N/A":
                    return (lib[0], lib[1], "", False)
            else:
                try:
                    yt = f.result()
                except Exception:  # noqa: BLE001
                    yt = None
                if yt == "sorry":
                    yt = None
                if yt:
                    return ("Available (Auto)", yt, "", False)
        # both sources answered with no text: decide now
        if lib is not None and yt_started and not pending:
            break
    if lib is None:
        lib = ("Not Available", "N/A", "error")
    l_kind = lib[2]
    if yt == "":
        # the default client got a real player answer with no caption tracks
        return ("Not Available", "N/A", "none", l_kind == "none")
    if l_kind == "none":
        return ("Not Available", "N/A", "none", False)
    return ("Not Available", "N/A", l_kind or "error", False)


def _fetch_transcript_tail_store(video_id, status, text, err_kind, verified,
                                 expect_secs):
    if text and text != "N/A":
        _cache_put(video_id, status, text, "",
                   verified=verified and _looks_short(text, expect_secs))
        return (status, text, "")
    _cache_put(video_id, "Not Available", "N/A", err_kind,
               verified=verified and err_kind == "none")
    return ("Not Available", "N/A", err_kind or "none")


def _fetch_hedged_tail(video_id, proxy, expect_secs):
    try:
        status, text, err_kind, verified = _hedged_fetch(video_id, proxy, expect_secs)
    except Exception:          # noqa: BLE001 — never raise out of this API
        status, text, err_kind, verified = ("Not Available", "N/A", "error", False)
    if err_kind == "rate":
        return ("Not Available", "N/A", "blocked")
    if text and text != "N/A" and _looks_short(text, expect_secs):
        # a fragment: still compare with the other sources (longest wins)
        try:
            status, text, err_kind, verified = _thorough_recheck(
                video_id, proxy, status, text, err_kind, expect_secs)
        except Exception:      # noqa: BLE001
            verified = False
    return _fetch_transcript_tail_store(video_id, status, text, err_kind,
                                        verified, expect_secs)


def _fetch_transcript_legacy(video_id):
    """The pre-turbo path (library fetch → full yt-dlp ladder on anything but
    success). Kept for reference / A-B testing; nothing calls it."""
    proxy = configured_proxy()
    status, text, err_kind = _fetch_via_library(video_id, proxy)
    if text and text != "N/A":
        return (status, text, err_kind)
    try:
        fallback = _fetch_via_ytdlp(video_id, proxy)
    except Exception:      # noqa: BLE001
        fallback = ""
    if fallback:
        return ("Available (Auto)", _trim(fallback), "")
    if err_kind == "blocked":
        return ("Not Available", "N/A", "blocked")
    if err_kind == "error":
        return ("Not Available", "N/A", "error")
    return ("Not Available", "N/A", "none")


def _trim(text):
    """Legacy 3k preview, kept only for the unused pre-turbo path below. The
    live scraper returns the WHOLE track: a long upload used to stop
    mid-sentence in the export at 3000 characters."""
    text = text.strip()
    return text[:3000] + ("…" if len(text) > 3000 else "")


# The chat keeps the opening AND the ending of a transcript (hook + payoff),
# so its cut is head + " [...] " + tail — see app.core.ai.chat_sources.
_ENDS_HEAD, _ENDS_TAIL = 2300, 700


def _trim_ends(text):
    t = " ".join(str(text or "").split())
    if len(t) <= _ENDS_HEAD + _ENDS_TAIL:
        return t
    head = t[:_ENDS_HEAD].rsplit(" ", 1)[0]
    tail = t[-_ENDS_TAIL:].split(" ", 1)[-1]
    return head + " [...] " + tail


# The cache keeps the WHOLE transcript — the scraper's export column is the
# subtitle itself, so cutting it is not an option. This cap is only a safety
# net against a pathological caption payload; 4M characters covers a 24-hour
# upload with room to spare. Older builds cut entries at 120k, which left the
# export ending mid-sentence with no marker; cached_transcript() treats those
# as a miss so one re-scrape heals them.
_CACHE_TEXT_CAP = 4_000_000
_LEGACY_TEXT_CAP = 120_000


def _library_api(api_cls, proxy):
    """A YouTubeTranscriptApi routed through the app's proxy when one is set.

    Without this the library talks to YouTube directly even on a machine whose
    YouTube access depends on the proxy in Settings — the transcript endpoint
    then answers blocked and the row reads "Not Available".
    """
    cfg = None
    if proxy:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig
            cfg = GenericProxyConfig(http_url=proxy, https_url=proxy)
        except Exception:      # noqa: BLE001 — older library, or no proxy extra
            cfg = None
    key = (id(api_cls), proxy or "")
    cached = getattr(_TLS, "api", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    sess = _session()
    api = None
    kwargs = {}
    if cfg is not None:
        kwargs["proxy_config"] = cfg
    if sess is not None:
        try:
            api = api_cls(http_client=sess, **kwargs)
        except TypeError:
            api = None
    if api is None:
        try:
            api = api_cls(**kwargs)
        except TypeError:
            api = api_cls()    # 0.6.x-style class: no proxy_config parameter
    _TLS.api = (key, api)
    return api


def _fetch_via_library(video_id, proxy):
    """The library path. Returns (status, text, err_kind); text is "N/A" when it
    could not answer, so the caller can fall through to yt-dlp."""
    try:
        _api, _has_new, _block_errs, _no_tr_errs = _yta()
    except Exception:
        return ("Not Available", "N/A", "error")
    try:
        text = ""
        status = "Not Available"
        if _has_new:                                    # youtube-transcript-api >= 1.0
            api = _library_api(_api, proxy)
            try:
                fetched = api.fetch(video_id, languages=_TRANSCRIPT_LANGS)
                text = _join_transcript(fetched)
                status = "Available"
            except _block_errs:
                raise
            except Exception:
                # none of the preferred languages → take ANY available track
                tlist = api.list(video_id)
                first = next(iter(tlist))
                text = _join_transcript(first.fetch())
                status = ("Available (Auto)" if getattr(first, "is_generated", False)
                          else "Available (Manual)")
        else:                                            # youtube-transcript-api 0.6.x
            try:
                entries = _api.get_transcript(
                    video_id, languages=_TRANSCRIPT_LANGS)
                text = _join_transcript(entries)
                status = "Available"
            except _block_errs:
                raise
            except Exception:
                tlist = _api.list_transcripts(video_id)
                first = next(iter(tlist))
                text = _join_transcript(first.fetch())
                status = ("Available (Auto)" if getattr(first, "is_generated", False)
                          else "Available (Manual)")
        text = text.strip()
        if not text:
            return ("Not Available", "N/A", "none")
        return (status, _trim(text), "")
    except _block_errs:
        return ("Not Available", "N/A", "blocked")
    except _no_tr_errs:
        return ("Not Available", "N/A", "none")
    except Exception:
        return ("Not Available", "N/A", "error")


# ═════════════════════════════════════════════════════════════════════════
#  yt-dlp caption fallback
# ═════════════════════════════════════════════════════════════════════════
# Preference order for the caption payload: json3 carries the text as clean
# JSON segments, srv3/vtt need tag stripping. All three are offered by YouTube
# for the same track, so asking for the tidiest one costs nothing.
_CAPTION_FORMATS = ("json3", "srv3", "vtt", "ttml")


def _pick_caption_track(subtitles, automatic):
    """(track, is_auto) for the first preferred language that has one."""
    for source, is_auto in ((subtitles, False), (automatic, True)):
        for lang in _TRANSCRIPT_LANGS:
            for key in (lang, lang.lower(), lang.split("-")[0]):
                track = source.get(key)
                if track:
                    return track, is_auto
    # Nothing in a preferred language — take whatever the video actually has.
    for source, is_auto in ((subtitles, False), (automatic, True)):
        for _lang, track in sorted(source.items()):
            if track:
                return track, is_auto
    return None, False


def _caption_payload_text(raw, ext):
    """Plain text out of a YouTube caption payload (json3 / srv3 / vtt / ttml)."""
    if ext == "json3":
        try:
            data = json.loads(raw)
        except ValueError:
            return ""
        parts = []
        for event in data.get("events") or []:
            for seg in event.get("segs") or []:
                parts.append(seg.get("utf8") or "")
        return "".join(parts)
    if ext in ("srv3", "ttml", "xml"):
        # XML-ish: take the TEXT NODES (whatever sits between a `>` and the next
        # `<`). Nested elements are the norm here (`<p><s>one</s><s> two</s></p>`),
        # so matching whole elements with a lazy regex captures the opening tag
        # of the child instead of its text. The document also arrives as ONE
        # line starting with "<?xml", which a line-based strip would throw away
        # entirely — every word of it.
        chunks = re.findall(r">([^<>]+)<", raw)
        return " ".join(html.unescape(c).strip() for c in chunks if c.strip())
    if ext in ("vtt", "srt"):
        out = []
        for line in raw.splitlines():
            s = line.strip()
            if (not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                    or "-->" in s or s.isdigit()):
                continue
            s = re.sub(r"<[^>]+>", "", s)
            if s:
                out.append(html.unescape(s))
        return " ".join(out)
    return raw


def _fetch_via_ytdlp(video_id, proxy=""):
    """Caption text via yt-dlp, or "" when there is none.

    Uses the SAME client ladder the downloads use (app.core.download.
    CLIENT_LADDER), so a machine where only, say, `android` answers YouTube still
    gets its captions. Bounded: it stops as soon as a rung returns captions, and
    gives up after a few caption-less rungs.
    """
    # A scrape fetches transcripts four at a time; when the library is blocked
    # every one of those would run a full extraction here, and four simultaneous
    # extractions is exactly how an IP gets throttled harder. Two at a time.
    with _CAPTION_SLOTS:
        return _fetch_via_ytdlp_inner(video_id, proxy)


def _fetch_via_ytdlp_inner(video_id, proxy="", budget=None):
    """`budget` (seconds, v1.0.7): stop walking the ladder once it is spent,
    with tighter per-rung timeouts. None keeps the old unbounded walk."""
    from app.core.runtime_deps import js_runtime_opts
    from app.core.utils import ensure_yt_dlp_ready

    try:
        from app.core.download import CLIENT_LADDER, _QuietLogger
    except Exception:          # noqa: BLE001
        CLIENT_LADDER = ([],)

        class _QuietLogger:                      # minimal stand-in
            def debug(self, msg): pass
            def info(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): pass

    yt_dlp, _ffmpeg = ensure_yt_dlp_ready()
    base = {"quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "extractor_retries": 2, "socket_timeout": 25,
            "logger": _QuietLogger()}
    if budget:
        base.update(extractor_retries=1, socket_timeout=15)
    base.update(js_runtime_opts())
    if proxy:
        base["proxy"] = proxy

    url = f"https://www.youtube.com/watch?v={video_id}"
    info, barren = None, 0
    deadline = (_time.monotonic() + float(budget)) if budget else None
    for clients in CLIENT_LADDER:
        if deadline is not None and (_time.monotonic() >= deadline or _aborted()):
            break
        opts = dict(base)
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                got = ydl.extract_info(url, download=False)
        except Exception:      # noqa: BLE001 — next client
            continue
        if not got:
            continue
        if got.get("subtitles") or got.get("automatic_captions"):
            info = got
            break
        # An answer without captions: keep it (so a genuine "no captions on this
        # video" is reported rather than "blocked") but do not walk all twelve
        # rungs hunting for one that happens to carry them.
        if info is None:
            info = got
        barren += 1
        if barren >= 3:
            break
    if not info:
        return ""
    track, _is_auto = _pick_caption_track(info.get("subtitles") or {},
                                          info.get("automatic_captions") or {})
    if not track:
        return ""
    by_ext = {}
    for fmt in track:
        ext = (fmt.get("ext") or "").lower()
        if ext and ext not in by_ext and fmt.get("url"):
            by_ext[ext] = fmt
    chosen = next((by_ext[e] for e in _CAPTION_FORMATS if e in by_ext), None)
    if chosen is None:
        chosen = next(iter(by_ext.values()), None)
    if chosen is None:
        return ""
    req = urllib.request.Request(
        chosen["url"], headers={"User-Agent": "Mozilla/5.0"})
    with _urlopen(req, 25, proxy) as resp:
        raw = resp.read().decode("utf-8", "replace")
    text = _caption_payload_text(raw, (chosen.get("ext") or "").lower())
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ═════════════════════════════════════════════════════════════════════════
#  QUICK PATH — for the AI Chat (archive / niche / link sources)
#  ---------------------------------------------------------------------
#  The chat only needs the opening ~2.6k characters of a transcript, yet it
#  used to go through the scraper's full `fetch_transcript`, which is slow in
#  three ways that add up to 30–90 s per video:
#
#    1. The library path called `fetch()` (list + pick + download) and, when
#       none of the 17 preferred languages matched, called `list()` AGAIN —
#       two round-trips to the watch page for every non-English video.
#    2. When the library said "this video has NO captions" (very common for
#       Shorts), it still fell through to yt-dlp, which walks a client ladder
#       of full extractions (each one 5–25 s) only to confirm the same thing.
#    3. Nothing was remembered: attaching the same archive video to another
#       thread, or re-opening the app mid-read, paid the whole price again.
#
#  `fetch_transcript_quick` fixes all three: one list + one fetch, an
#  authoritative "no captions" stops immediately, the yt-dlp fallback is a
#  short, light ladder (no DASH/HLS manifests, tight timeouts), and every
#  answer — text OR "none" — is cached on disk.
# ═════════════════════════════════════════════════════════════════════════
import os as _os
import sqlite3 as _sqlite3

_QUICK_LADDER = ([], ["android"], ["tv"])          # the rungs that answer most often
_NONE_TTL = 3 * 24 * 3600                          # re-check "no captions" after 3 days
_cache_lock = threading.Lock()


def _cache_path():
    try:
        from app.config import CONFIG_PATH
        return _os.path.join(_os.path.dirname(CONFIG_PATH), "transcripts_cache.db")
    except Exception:          # noqa: BLE001
        return ""


_CONN = None


def _cache_conn():
    """One shared connection (opened lazily, used only under _cache_lock).
    Opening SQLite per lookup was fine one video at a time; at 16 parallel
    transcripts it became a queue of open/close calls on the lock."""
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _cache_path()
    if not path:
        return None
    conn = _sqlite3.connect(path, timeout=5, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:          # noqa: BLE001
        pass
    conn.execute("CREATE TABLE IF NOT EXISTS tx (vid TEXT PRIMARY KEY, status TEXT,"
                 " text TEXT, kind TEXT, at REAL)")
    # v1.0.7: which cached answers a thorough run already double-checked
    conn.execute("CREATE TABLE IF NOT EXISTS tx_verified (vid TEXT PRIMARY KEY, at REAL)")
    _CONN = conn
    return conn


def cached_transcript(video_id):
    """(status, text, err_kind) from the disk cache, or None. The text is the
    full cached transcript; callers trim it to their own view."""
    try:
        with _cache_lock:
            conn = _cache_conn()
            if conn is None:
                return None
            row = conn.execute("SELECT status, text, kind, at FROM tx WHERE vid=?",
                               (video_id,)).fetchone()
    except Exception:          # noqa: BLE001
        return None
    if not row:
        return None
    status, text, kind, at = row
    if text and text != "N/A":
        if len(text) == _LEGACY_TEXT_CAP:
            return None         # older build cut it mid-sentence: fetch again
        if _is_trimmed_fragment(text):
            return None         # a 3k preview / chat head+tail, not the track
        return (status, text, "")
    if kind == "none" and (_time.time() - float(at or 0)) < _NONE_TTL:
        return ("Not Available", "N/A", "none")
    return None                 # a stale "none" or a past error: try again


def _is_trimmed_fragment(text):
    """True for the shortened copies older builds could leave in the cache:
    the legacy 3,000-character preview (ends with "…") and the chat's
    head + " [...] " + tail cut. Serving those as the export's transcript
    made a long video's text stop after a few paragraphs."""
    n = len(text)
    if n == 3001 and text.endswith("…"):
        return True
    if n <= _ENDS_HEAD + _ENDS_TAIL + 16 and " [...] " in text:
        return True
    return False


def has_cached_transcript(video_id):
    """True when cached_transcript() would hit. Reads only the columns it
    needs, so a 300-video pre-scan does not copy hundreds of transcripts."""
    try:
        with _cache_lock:
            conn = _cache_conn()
            if conn is None:
                return False
            row = conn.execute(
                "SELECT (text IS NOT NULL AND text != '' AND text != 'N/A'),"
                " kind, at FROM tx WHERE vid=?", (str(video_id or ""),)).fetchone()
    except Exception:          # noqa: BLE001
        return False
    if not row:
        return False
    has_text, kind, at = row
    if has_text:
        return True
    return kind == "none" and (_time.time() - float(at or 0)) < _NONE_TTL


_stats_lock = threading.Lock()
_stats = {"cache": 0, "live": 0, "throttled": 0}


def reset_transcript_stats():
    with _stats_lock:
        _stats["cache"] = 0
        _stats["live"] = 0
        _stats["throttled"] = 0


def transcript_stats():
    with _stats_lock:
        return dict(_stats)


def _bump_stat(which):
    with _stats_lock:
        _stats[which] += 1


def _cache_put(video_id, status, text, kind, verified=False):
    if kind in ("blocked", "error", "rate"):
        return                  # transient — never remember a network failure
    if text and text != "N/A":
        text = str(text)[:_CACHE_TEXT_CAP]
    try:
        with _cache_lock:
            conn = _cache_conn()
            if conn is None:
                return
            now = _time.time()
            conn.execute("INSERT OR REPLACE INTO tx VALUES (?,?,?,?,?)",
                         (video_id, status, text, kind, now))
            # a new answer replaces the old one: only a thorough double-check
            # may vouch for it again
            if verified:
                conn.execute("INSERT OR REPLACE INTO tx_verified VALUES (?,?)",
                             (video_id, now))
            else:
                conn.execute("DELETE FROM tx_verified WHERE vid=?", (video_id,))
            conn.commit()
    except Exception:          # noqa: BLE001
        pass


def _is_verified(video_id):
    """True when a thorough run double-checked the cached answer recently."""
    try:
        with _cache_lock:
            conn = _cache_conn()
            if conn is None:
                return False
            row = conn.execute("SELECT at FROM tx_verified WHERE vid=?",
                               (str(video_id or ""),)).fetchone()
    except Exception:          # noqa: BLE001
        return False
    return bool(row) and (_time.time() - float(row[0] or 0)) < _VERIFIED_TTL


# ── InnerTube fast path ─────────────────────────────────────────────────
# youtube-transcript-api's list() downloads the FULL watch page (1–1.5 MB) on
# every single video just to read the InnerTube API key, then calls the player
# endpoint anyway. On a normal connection that page costs 3–6 s per video —
# several times the transcript itself. That is also why raising Worker Threads
# felt random: the link was saturated by watch pages long before the extra
# threads could help. The key is the same for every video, so fetch it once
# (the homepage is one request per process) and call the player endpoint
# directly. The player response is ~120–250 KB instead of a 1–1.5 MB page.
# The iOS client answers with a smaller payload, but its caption URLs hand
# back YouTube's "Sorry" HTML page when fetched without the iOS app's headers,
# so we use the same Android client the library itself uses.
_INNERTUBE_PLAYER = "https://www.youtube.com/youtubei/v1/player?key={key}"
_INNERTUBE_TIMEOUT = 15
_INNERTUBE_KEY_TTL = 6 * 3600
_INNERTUBE_KEY_RETRY = 45.0

try:      # the library already carries a working client context; reuse it
    from youtube_transcript_api._settings import INNERTUBE_CONTEXT as _YT_CONTEXT
except Exception:          # noqa: BLE001
    _YT_CONTEXT = {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38"}}

_INNERTUBE_CONTEXTS = (_YT_CONTEXT,)

_innertube_lock = threading.Lock()
_innertube_fetch_lock = threading.Lock()
_innertube = {"key": "", "at": 0.0, "failed_at": 0.0, "version": 0}


def _http_session(proxy):
    """The thread's keep-alive session, with the run's proxy applied."""
    sess = _session()
    if sess is None:
        import requests
        if not proxy:
            return requests
        sess = requests.Session()
        sess.headers.update({"Accept-Language": "en-US,en;q=0.9"})
    sess.proxies = {"http": proxy, "https": proxy} if proxy else {}
    return sess


def _extract_innertube_key(proxy):
    """The public InnerTube key from YouTube's own homepage; "" on failure."""
    try:
        sess = _http_session(proxy)
        r = sess.get("https://www.youtube.com/", timeout=_INNERTUBE_TIMEOUT)
        html_text = r.text if r.status_code == 200 else ""
        if 'action="https://consent.youtube.com/s"' in html_text:
            m = re.search('name="v" value="(.*?)"', html_text)
            if not m:
                return ""
            sess.cookies.set("CONSENT", "YES+" + m.group(1), domain=".youtube.com")
            r = sess.get("https://www.youtube.com/", timeout=_INNERTUBE_TIMEOUT)
            html_text = r.text if r.status_code == 200 else ""
        m = re.search(r'"INNERTUBE_API_KEY":\s*"([a-zA-Z0-9_-]+)"', html_text)
        return m.group(1) if m else ""
    except Exception:          # noqa: BLE001
        return ""


def _innertube_key(proxy, if_version=None):
    """Cached player API key. `if_version` is used after an auth failure: it
    only fetches again when no other worker has refreshed the key since."""
    with _innertube_lock:
        key, ver = _innertube["key"], _innertube["version"]
        if if_version is None:
            if key and _time.time() - _innertube["at"] < _INNERTUBE_KEY_TTL:
                return key
            if _time.time() - _innertube["failed_at"] < _INNERTUBE_KEY_RETRY:
                return ""
        elif ver > if_version and key:
            return key
    with _innertube_fetch_lock:
        with _innertube_lock:
            key, ver = _innertube["key"], _innertube["version"]
            if if_version is None:
                if key and _time.time() - _innertube["at"] < _INNERTUBE_KEY_TTL:
                    return key
                if _time.time() - _innertube["failed_at"] < _INNERTUBE_KEY_RETRY:
                    return ""
            elif ver > if_version and key:
                return key
        key = _extract_innertube_key(proxy)
        with _innertube_lock:
            if key:
                _innertube.update(key=key, at=_time.time(), failed_at=0.0,
                                  version=_innertube["version"] + 1)
                return key
            _innertube["failed_at"] = _time.time()
            return ""


def _pick_innertube_track(tracks):
    """Same language order as the library: for each preferred language, a
    manual track then an ASR one; otherwise the first manual, then the first
    ASR track."""
    manual, generated = {}, {}
    for t in tracks:
        code = t.get("languageCode") or ""
        if not code or not t.get("baseUrl"):
            continue
        bucket = generated if t.get("kind") == "asr" else manual
        bucket[code] = t         # the LAST duplicate wins, matching the library
    for lang in _TRANSCRIPT_LANGS:
        if lang in manual:
            return manual[lang], False
        if lang in generated:
            return generated[lang], True
    for t in tracks:            # no preferred language: the video's own track
        if t.get("baseUrl") and t.get("kind") != "asr":
            return t, False
    for t in tracks:
        if t.get("baseUrl"):
            return t, True
    return None, False


# ── shared caption pacer ────────────────────────────────────────────────
# YouTube rate-limits the caption endpoint per IP: after a fast burst it
# answers 429 with its "Sorry" page for a minute or more. This is the real
# reason a 300-video scrape took minutes no matter the thread count, and why
# Turbo sometimes felt slower than 5 — bigger bursts meant longer blocks.
# Every transcript now takes a slot from ONE process-wide gate. It starts
# safely (≈1.1/s), creeps up after a run of successes (toward the level's
# floor) and backs off hard on a 429. A 429 (or a rate-limited timedtext 403)
# is treated as temporary, not as an IP ban: it is retried after the shared
# cool-down, and only a persistent refusal pauses the scrape.
_CAPTION_START = 0.9          # seconds between transcript starts (thorough floor)
_CAPTION_CEIL = 8.0           # kept for reference; the mode profile sets the ceiling
_CAPTION_RATE_RETRIES = 2     # legacy default; the mode profile decides
_caption_lock = threading.Lock()
# v1.0.6: the pacer sleeps on a Condition, not time.sleep(). The old sleep
# could not be interrupted: after a 429 the gap jumped to 8 s and every worker
# had already booked a slot N x 8 s ahead, so after a new IP / Retry the
# workers went on sleeping 30-60 s before the first request. A reset now bumps
# the epoch and wakes them, and they re-book from the fresh pace at once.
_caption_cv = threading.Condition(_caption_lock)
_caption_epoch = 0
_caption_next = 0.0
_caption_gap = _CAPTION_START
_caption_floor = _CAPTION_START
_caption_streak = 0
_cool_until = 0.0             # one short shared cool-down per throttle episode
_throttle_listener = None
_throttle_announced = False
_abort_check = None


def set_abort_check(fn):
    """fn() -> True when the scrape is stopping: paced waits return early."""
    global _abort_check
    _abort_check = fn


def _aborted():
    fn = _abort_check
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:          # noqa: BLE001
        return False


def tx_epoch():
    """Changes on every pacer reset (new IP, resume, new scrape). The scraper
    compares it around a fetch: a refusal that started before the IP changed
    belongs to the old IP and is retried instead of counted."""
    with _caption_lock:
        return _caption_epoch


def set_throttle_listener(fn):
    """Callback(gap) fired once per throttling episode, for the live log."""
    global _throttle_listener, _throttle_announced
    with _caption_lock:
        _throttle_listener = fn
        _throttle_announced = False


def _announce_throttle(gap):
    global _throttle_announced
    with _caption_lock:
        if _throttle_announced or _throttle_listener is None:
            return
        _throttle_announced = True
        fn = _throttle_listener
    try:
        fn(gap)
    except Exception:          # noqa: BLE001
        pass


def _start_gap():
    """Fast starts AT the level's floor (it does not care about refusals, the
    AIMD below backs off if YouTube complains). Thorough starts gentler."""
    if _MODE["thorough"]:
        # v1.0.7: follows the Worker Threads level too (it was a flat 1.2 s
        # whatever the level, so Turbo did nothing for a thorough run)
        return max(_caption_floor, min(1.2, _caption_floor * 1.3))
    return max(0.45, _caption_floor)


def _caption_reset(floor=None):
    """New scrape / new IP / resume: back to the starting pace, and wake every
    worker sleeping on an old reservation. With no `floor`, keep the level's
    ceiling (reset_breaker on an IP change)."""
    global _caption_next, _caption_gap, _caption_floor, _caption_streak
    global _throttle_announced, _cool_until, _caption_epoch
    with _caption_cv:
        if floor is not None:
            _caption_floor = float(floor)
        _caption_gap = _start_gap()
        _caption_next = 0.0
        _cool_until = 0.0
        _caption_streak = 0
        _throttle_announced = False
        _caption_epoch += 1
        _caption_cv.notify_all()


def _caption_wait(paced=True):
    """Reserve the next transcript start and wait until its slot is due.
    Returns False when the scrape is stopping. Wakes up (and re-books) the
    moment the pacer is reset."""
    global _caption_next
    if not paced:
        return True
    with _caption_cv:
        epoch = _caption_epoch
        slot = max(_time.monotonic(), _caption_next, _cool_until)
        _caption_next = slot + _caption_gap
        while True:
            now = _time.monotonic()
            if slot <= now:
                return True
            _caption_cv.wait(min(slot - now, 0.25))
            if _caption_epoch != epoch:
                epoch = _caption_epoch
                slot = max(_time.monotonic(), _caption_next, _cool_until)
                _caption_next = slot + _caption_gap
            if _aborted():
                return False


def _caption_success():
    """AIMD, recovery side: every 3 successes the gap shrinks 15 % toward the
    level floor (the old 7 % per 5 successes needed ~150 videos to recover
    from one throttle episode, so a whole run crawled after a single 429)."""
    global _caption_gap, _caption_streak, _throttle_announced
    with _caption_lock:
        _caption_streak += 1
        if _caption_streak >= 3 and _caption_gap > _caption_floor:
            _caption_gap = max(_caption_floor, _caption_gap * 0.85)
            _caption_streak = 0
            _throttle_announced = False    # a later episode may be logged again


def _caption_throttled():
    """429/403/"Sorry" page: one short shared cool-down for this episode and a
    moderately slower pace (x1.5, capped by the mode: 2.5 s fast, 4 s
    thorough; the old x1.8 up to 8 s queued workers for a minute)."""
    global _caption_gap, _caption_next, _caption_streak, _cool_until
    with _caption_lock:
        prof = _mp()
        _caption_gap = min(prof["ceil"], max(1.0, _caption_gap * 1.5))
        _caption_streak = 0
        now = _time.monotonic()
        if _cool_until <= now:             # new episode: one breather, not one per worker
            _cool_until = now + prof["cool"]
        _caption_next = max(_caption_next, _cool_until)
        gap = _caption_gap
    _bump_stat("throttled")
    _announce_throttle(gap)
    return gap


def _caption_rate_limited(response):
    """True for the two shapes YouTube's rate-limit answer takes."""
    if response.status_code in (403, 429):
        return True
    ctype = (response.headers.get("content-type") or "").lower()
    head = (response.content or b"")[:200].lstrip().lower()
    return "html" in ctype or head[:2] in (b"<!", b"<h")


def _innertube_alt_auto(tracks, chosen, proxy, timeout):
    """Thorough helper: the ASR track from the SAME player answer (same
    language first). Its text, or "" when there is none / it fails. Costs one
    small caption request instead of a 1-1.5 MB watch page."""
    code = (chosen or {}).get("languageCode") or ""
    asr = [t for t in tracks if t.get("kind") == "asr" and t.get("baseUrl")]
    if not asr:
        return ""
    asr.sort(key=lambda t: 0 if (t.get("languageCode") or "") == code else 1)
    url = asr[0].get("baseUrl") or ""
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("&fmt=srv3", "")
    if "exp=xpe" in url:
        return ""
    try:
        cr = _http_session(proxy).get(url, timeout=timeout)
    except Exception:          # noqa: BLE001
        return ""
    if cr.status_code != 200 or _caption_rate_limited(cr):
        return ""
    raw = cr.content.decode("utf-8", "replace")
    head = raw[:200].lstrip()
    payload = "json3" if head.startswith("{") else ("vtt" if head.startswith("WEBVTT") else "srv3")
    return re.sub(r"\s+", " ", html.unescape(_caption_payload_text(raw, payload))).strip()


def _innertube_quick(video_id, proxy, trim=True, paced=True, rate_retries=None,
                     expect_secs=0):
    """Player endpoint only. Returns (status, text, err_kind) like the library
    path; "none" means YouTube said there is no caption track. `paced=False`
    (the pause-time probe) skips the shared pacer. `expect_secs` (thorough):
    a manual track that looks too short for the video is compared with the
    auto track of the same answer, and the longer one is kept."""
    prof = _mp()
    timeout = prof["timeout"]
    limit = prof["rate_retries"] if rate_retries is None else int(rate_retries)
    key = _innertube_key(proxy)
    if not key:
        return ("Not Available", "N/A", "error")
    with _innertube_lock:
        key_version = _innertube["version"]
    idx, refreshed, rate_tries, last_kind = 0, False, 0, "error"
    while idx < len(_INNERTUBE_CONTEXTS):
        ctx = _INNERTUBE_CONTEXTS[idx]
        if not _caption_wait(paced):
            return ("Not Available", "N/A", "error")
        try:
            r = _http_session(proxy).post(
                _INNERTUBE_PLAYER.format(key=key),
                json={"context": ctx, "videoId": video_id},
                timeout=timeout)
        except Exception:      # noqa: BLE001 — let the library path try
            return ("Not Available", "N/A", "error")
        if r.status_code in (400, 401) and not refreshed:
            body = (r.text or "")[:400].lower()
            if r.status_code == 401 or "api key not valid" in body \
                    or "keyinvalid" in body:
                new_key = _innertube_key(proxy, if_version=key_version)
                refreshed = True
                if new_key and new_key != key:
                    key = new_key
                    with _innertube_lock:
                        key_version = _innertube["version"]
                    continue        # retry this client with the fresh key
        if r.status_code in (403, 429):
            if rate_tries < limit:
                rate_tries += 1
                _caption_throttled()
                continue
            # 429 is the endpoint's rate limit; a 403 here may also be a bot
            # check that the yt-dlp ladder can still get past.
            return ("Not Available", "N/A",
                    "rate" if r.status_code == 429 else "blocked")
        if r.status_code != 200:
            last_kind = "error"
            idx += 1
            continue
        try:
            data = r.json()
        except ValueError:
            last_kind = "error"
            idx += 1
            continue
        play = data.get("playabilityStatus") or {}
        status = play.get("status")
        reason = play.get("reason") or ""
        if status and status != "OK":
            # A bot check is IP-level; a bad client/age gate is worth trying
            # with the next client before giving up.
            last_kind = "blocked" if "bot" in reason.lower() else "error"
            idx += 1
            continue
        tracks = (((data.get("captions") or {})
                   .get("playerCaptionsTracklistRenderer") or {})
                  .get("captionTracks") or [])
        if not tracks:
            return ("Not Available", "N/A", "none")
        track, is_auto = _pick_innertube_track(tracks)
        if not track:
            return ("Not Available", "N/A", "none")
        url = track.get("baseUrl") or ""
        if url.startswith("//"):
            url = "https:" + url
        # The library strips srv3; with it YouTube mixes in the rolling
        # caption segments, so the export would grow and differ from the
        # library's text for tracks that have both forms.
        url = url.replace("&fmt=srv3", "")
        if "exp=xpe" in url:       # this track needs a PO token
            last_kind = "error"
            idx += 1
            continue
        try:
            # No fmt=json3 here: YouTube's default XML carries the same cues
            # the library returns, while json3 drops some formatting-only
            # segments (and the export would differ from older scrapes).
            cr = _http_session(proxy).get(url, timeout=timeout)
        except Exception:      # noqa: BLE001
            last_kind = "error"
            idx += 1
            continue
        if _caption_rate_limited(cr):
            # 403/429, or YouTube's "Sorry" page arriving with HTTP 200 (never
            # cache that as a transcript). It is a rate limit first and only a
            # real block if it survives the shared cool-down and retries.
            if rate_tries < limit:
                rate_tries += 1
                _caption_throttled()
                continue
            return ("Not Available", "N/A", "rate")
        if cr.status_code != 200:
            last_kind = "error"
            idx += 1
            continue
        raw = cr.content.decode("utf-8", "replace")
        head = raw[:200].lstrip()
        if head.startswith("{"):
            payload = "json3"
        elif head.startswith("WEBVTT"):
            payload = "vtt"
        else:
            payload = "srv3"
        text = re.sub(r"\s+", " ", html.unescape(
            _caption_payload_text(raw, payload))).strip()
        if not text:
            last_kind = "none"
            idx += 1
            continue
        _caption_success()
        if expect_secs and not is_auto and _looks_short(text, expect_secs):
            alt = _innertube_alt_auto(tracks, track, proxy, timeout)
            if alt and len(alt) > len(text):
                text, is_auto = alt, True
        status = "Available (Auto)" if is_auto else "Available (Manual)"
        return (status, _trim_ends(text) if trim else text, "")
    return ("Not Available", "N/A", last_kind)


def _library_quick(video_id, proxy, trim=True, expect_secs=0):
    """The light InnerTube path first; the library's watch-page path only when
    that fails. Returns (status, text, err_kind) where err_kind "none" means
    YouTube itself said there are no captions."""
    try:
        fast = _innertube_quick(video_id, proxy, trim=trim, expect_secs=expect_secs)
    except Exception:          # noqa: BLE001 — never break a scrape over this
        fast = ("Not Available", "N/A", "error")
    f_status, f_text, f_kind = fast
    if f_text and f_text != "N/A":
        return (f_status, f_text, f_kind)
    if f_kind in ("none", "blocked", "rate"):
        return (f_status, f_text, f_kind)
    # "error": the library may still get through with its own key/cookies
    # (age gate, a client that answered badly, a transient player failure).
    return _library_list(video_id, proxy, trim=trim)


def _library_list(video_id, proxy, trim=True, prefer_generated=False):
    """youtube-transcript-api's own list() + fetch(). Returns (status, text,
    err_kind). `prefer_generated` asks for the auto (ASR) track first: thorough
    mode uses it when the manual track looks like a fragment."""
    try:
        api_cls, has_new, block_errs, _no_tr = _yta()
        import youtube_transcript_api as mod
    except Exception:          # noqa: BLE001
        return ("Not Available", "N/A", "error")
    # Only these two are an authoritative "this video has no captions"; every
    # other CouldNotRetrieveTranscript subclass (PoToken, age gate, request
    # failed…) is a retrieval problem that yt-dlp may still get past.
    final = tuple(e for e in (getattr(mod, n, None) for n in
                              ("TranscriptsDisabled", "NoTranscriptFound"))
                  if isinstance(e, type))
    try:
        if not _caption_wait():    # the fallback is two more YouTube requests
            return ("Not Available", "N/A", "error")
        if has_new:
            tlist = _library_api(api_cls, proxy).list(video_id)
        else:
            tlist = api_cls.list_transcripts(video_id)
        tracks = list(tlist)
        if not tracks:
            return ("Not Available", "N/A", "none")
        track = None
        if prefer_generated:
            try:
                track = tlist.find_generated_transcript(_TRANSCRIPT_LANGS)
            except Exception:  # noqa: BLE001
                track = next((t for t in tracks if getattr(t, "is_generated", False)), None)
        try:
            if track is None:
                track = tlist.find_transcript(_TRANSCRIPT_LANGS)
        except Exception:      # noqa: BLE001 — none of ours: take the video's own
            # the original-language track first (manual beats generated)
            track = next((t for t in tracks if not getattr(t, "is_generated", False)),
                         tracks[0])
        text = _join_transcript(track.fetch()).strip()
        if not text:
            return ("Not Available", "N/A", "none")
        status = ("Available (Auto)" if getattr(track, "is_generated", False)
                  else "Available (Manual)")
        return (status, _trim_ends(text) if trim else text, "")
    except block_errs:
        return ("Not Available", "N/A", "blocked")
    except final:
        return ("Not Available", "N/A", "none")
    except Exception:          # noqa: BLE001
        return ("Not Available", "N/A", "error")


def _library_longest(video_id, proxy):
    """Thorough helper (v1.0.7): ONE watch-page list, then the auto track and
    the preferred/manual track from it; the longest text wins. Replaces two
    separate _library_list calls that each downloaded the watch page."""
    try:
        api_cls, has_new, block_errs, _no_tr = _yta()
        import youtube_transcript_api as mod
    except Exception:          # noqa: BLE001
        return ("Not Available", "N/A", "error")
    final = tuple(e for e in (getattr(mod, n, None) for n in
                              ("TranscriptsDisabled", "NoTranscriptFound"))
                  if isinstance(e, type))
    try:
        if not _caption_wait():
            return ("Not Available", "N/A", "error")
        if has_new:
            tlist = _library_api(api_cls, proxy).list(video_id)
        else:
            tlist = api_cls.list_transcripts(video_id)
        tracks = list(tlist)
        if not tracks:
            return ("Not Available", "N/A", "none")
        cands = []
        try:
            cands.append(tlist.find_generated_transcript(_TRANSCRIPT_LANGS))
        except Exception:      # noqa: BLE001
            cands.append(next((t for t in tracks if getattr(t, "is_generated", False)), None))
        try:
            cands.append(tlist.find_transcript(_TRANSCRIPT_LANGS))
        except Exception:      # noqa: BLE001
            cands.append(next((t for t in tracks if not getattr(t, "is_generated", False)),
                              tracks[0]))
        best, seen = None, set()
        for t in cands:
            if t is None:
                continue
            key = (getattr(t, "language_code", ""), bool(getattr(t, "is_generated", False)))
            if key in seen:
                continue
            seen.add(key)
            try:
                got = _join_transcript(t.fetch()).strip()
            except block_errs:
                raise
            except Exception:  # noqa: BLE001
                continue
            if got and (best is None or len(got) > len(best[1])):
                st = ("Available (Auto)" if getattr(t, "is_generated", False)
                      else "Available (Manual)")
                best = (st, got, "")
        return best or ("Not Available", "N/A", "none")
    except block_errs:
        return ("Not Available", "N/A", "blocked")
    except final:
        return ("Not Available", "N/A", "none")
    except Exception:          # noqa: BLE001
        return ("Not Available", "N/A", "error")


def _ytdlp_rung(yt_dlp, base, url, clients, proxy):
    """ONE light yt-dlp lookup with one player client. Returns text, "" (this
    client answered: no captions / no usable track) or None (no answer, or an
    answer that proves nothing). Never raises. `"sorry"` is returned for
    YouTube's HTML refusal page so the caller can back the pacer off."""
    yt_args = {"skip": ["dash", "hls", "translated_subs"]}
    if clients:
        yt_args["player_client"] = clients
    opts = dict(base, extractor_args={"youtube": yt_args})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except Exception:          # noqa: BLE001 — this rung did not answer
        return None
    if not info:
        return None
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    if not (subs or auto):
        # v1.0.4: only the DEFAULT client is trusted to say "no captions"
        return "" if not clients else None
    track, _is_auto = _pick_caption_track(subs, auto)
    if not track:
        return "" if not clients else None
    by_ext = {}
    for fmt in track:
        ext = (fmt.get("ext") or "").lower()
        if ext and ext not in by_ext and fmt.get("url"):
            by_ext[ext] = fmt
    chosen = next((by_ext[e] for e in _CAPTION_FORMATS if e in by_ext), None) \
        or next(iter(by_ext.values()), None)
    if chosen is None:
        return ""
    try:
        req = urllib.request.Request(chosen["url"], headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen(req, 15, proxy) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception:          # noqa: BLE001
        return None
    # YouTube's "Sorry" / consent page can arrive with HTTP 200. Never let
    # that HTML become (and be cached as) a transcript.
    head = raw[:200].lstrip().lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return "sorry"
    text = _caption_payload_text(raw, (chosen.get("ext") or "").lower())
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text or None        # empty payload: let another rung decide


# v1.0.7b: the yt-dlp rungs RACE instead of queueing. Each rung is a full
# player request (+ the JS challenge), 3-12 s on a good day; walked one after
# another, the light ladder alone cost up to ~35 s and the deep one up to its
# 45 s budget, which is exactly the ~50 s per stubborn video the thorough
# retry rounds showed. Racing them makes a rung's cost the SLOWEST rung, not
# the sum, and the first real text wins. Own pool: the thorough re-checks
# already run on _RECHECK_POOL and call into this, so sharing it could starve.
_LADDER_POOL = None
_LADDER_LOCK = threading.Lock()
# rungs the light ladder does not try; only for thorough "error" videos
_DEEP_LADDER = (["web_safari"], ["android_vr"], ["mweb"])
_DEEP_BUDGET = 25.0
_QUICK_BUDGET = 20.0
# a video whose deep ladder came back empty this many times in a session is
# not sent down it again (PO-token-only tracks, broken players): each extra
# walk only burnt the retry round's time
_DEEP_MAX_PER_VIDEO = 2
_deep_spent = {}


def _ladder_pool():
    global _LADDER_POOL
    with _LADDER_LOCK:
        if _LADDER_POOL is None:
            from concurrent.futures import ThreadPoolExecutor
            _LADDER_POOL = ThreadPoolExecutor(max_workers=24,
                                              thread_name_prefix="tx-ladder")
        return _LADDER_POOL


def _ytdlp_quick(video_id, proxy="", ladder=None, budget=None):
    """A light yt-dlp caption lookup: three rungs (fast mode: only the default
    client), no manifests, tight timeouts. Returns text, "" (the player
    answered: no captions) or None (nothing answered).

    v1.0.7b: with more than one rung they run AT THE SAME TIME; the first
    text wins, the default client's "no captions" is still the only final
    "" (and it ends the race at once, like it ended the old walk)."""
    from app.core.runtime_deps import js_runtime_opts
    from app.core.utils import ensure_yt_dlp_ready
    try:
        from app.core.download import _QuietLogger
        logger = _QuietLogger()
    except Exception:          # noqa: BLE001
        logger = None
    yt_dlp, _ffmpeg = ensure_yt_dlp_ready()
    base = {"quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "extractor_retries": 0, "socket_timeout": 12,
            "check_formats": False}
    if logger is not None:
        base["logger"] = logger
    base.update(js_runtime_opts())
    if proxy:
        base["proxy"] = proxy
    url = f"https://www.youtube.com/watch?v={video_id}"
    rungs = list(ladder or _QUICK_LADDER)

    if len(rungs) == 1:
        got = _ytdlp_rung(yt_dlp, base, url, rungs[0], proxy)
        if got == "sorry":
            _caption_throttled()
            return None
        return got

    from concurrent.futures import as_completed
    pool = _ladder_pool()
    futs = {pool.submit(_ytdlp_rung, yt_dlp, base, url, c, proxy): bool(c)
            for c in rungs}
    final_none, sorry = False, False
    try:
        for fut in as_completed(futs, timeout=budget or _QUICK_BUDGET):
            try:
                got = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if got == "sorry":
                sorry = True
                continue
            if got:
                return got
            if got == "" and not futs[fut]:
                final_none = True
                break          # the default client said "no captions": final
            if _aborted():
                break
    except Exception:          # noqa: BLE001 — budget spent: what we have
        pass
    if sorry:
        _caption_throttled()
    return "" if final_none else None


def _ytdlp_deep(video_id, proxy=""):
    """Thorough, "error" videos only: the clients the light ladder did not try,
    raced, on a budget. Remembers videos it already failed for."""
    with _LADDER_LOCK:
        if _deep_spent.get(video_id, 0) >= _DEEP_MAX_PER_VIDEO:
            return None
    got = _ytdlp_quick(video_id, proxy, ladder=_DEEP_LADDER, budget=_DEEP_BUDGET)
    if not got:
        with _LADDER_LOCK:
            _deep_spent[video_id] = _deep_spent.get(video_id, 0) + 1
    return got or None


def fetch_transcript_quick(video_id):
    """Like fetch_transcript (same return shape, never raises) but tuned for
    the AI Chat: cached, one library round-trip, no yt-dlp when YouTube has
    already said "no captions", and a short yt-dlp ladder otherwise."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return ("Not Available", "N/A", "none")
    hit = cached_transcript(video_id)
    if hit is not None:
        status, text, kind = hit
        return (status, _trim_ends(text) if text and text != "N/A" else text, kind)
    proxy = configured_proxy()
    status, text, err_kind = _library_quick(video_id, proxy, trim=False)
    if not (text and text != "N/A") and err_kind in ("blocked", "error"):
        try:
            with _CAPTION_SLOTS:
                fb = _ytdlp_quick(video_id, proxy)
        except Exception:      # noqa: BLE001
            fb = None
        if fb:
            status, text, err_kind = "Available (Auto)", fb, ""
        elif fb == "":
            status, text, err_kind = "Not Available", "N/A", "none"
    if err_kind == "rate":
        err_kind = "blocked"
    _cache_put(video_id, status, text, err_kind)
    if text and text != "N/A":
        return (status, _trim_ends(text), err_kind)
    return (status, text, err_kind)


def probe_transcript(video_id):
    """v1.0.6: one unpaced, retry-free InnerTube check, used while a Thorough
    run is paused on a block to notice by itself that YouTube answers again.
    Returns "ok" (text came back, and is cached so the worker's retry is
    instant), "none" (the endpoint answered: no captions, also alive),
    "blocked" / "rate" (still refused) or "error" (unknown)."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return "error"
    try:
        status, text, kind = _innertube_quick(video_id, configured_proxy(), trim=False,
                                              paced=False, rate_retries=0)
    except Exception:          # noqa: BLE001
        return "error"
    if text and text != "N/A":
        _cache_put(video_id, status, text, "")
        return "ok"
    return kind or "none"
