"""app.core.db.search_pages - YouTube Hunter.

A short-lived cache of single search.list PAGES (v1.1).

search.list is the expensive call: 100 quota units a page, ~100 pages a day on
a default key. The run-level cache (niche_cache) only helps when the WHOLE
request is identical. Change the depth from 100 to 300, flip Deep scan, add a
second keyword or re-run after the run cache was cleared, and every page was
bought again, including the ones bought five minutes earlier.

This cache sits one level lower: one row per page, keyed by the exact request
YouTube saw (query, window, region, language, duration, category, order and
page token). Any run that would send the same request within the TTL gets the
stored ids and next-page token back and spends nothing.

Stable time windows are what make the keys line up: build_time_windows() now
anchors its windows to the start of the current UTC hour, so two runs a few
minutes apart send byte-identical publishedAfter / publishedBefore values.

Only ids and the next token are stored (a page is ~50 short strings), and rows
older than a day are pruned on write, so the table stays tiny. Every helper is
best-effort: a cache problem must never break a search, it only costs quota.
"""

import hashlib
import json
import time

from app.core.db._sqlite import connect as db_connect
from app.core.db.ideas import DATA_DB_PATH

# How long a stored page is trusted. Search rankings move slowly; the NUMBERS
# on the videos are always re-read through videos.list (1 unit / 50), so a few
# hours of ranking age costs nothing a creator would notice.
PAGE_TTL_S = 6 * 3600
_PRUNE_AFTER_S = 24 * 3600

_ready = False


def _ensure():
    global _ready
    if _ready:
        return
    conn = db_connect(DATA_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_page_cache (
                key         TEXT PRIMARY KEY,
                ids         TEXT,
                next_token  TEXT,
                fetched_at  REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()
    _ready = True


def page_key(kwargs):
    """Stable key for one search.list request (its exact kwargs).

    The query is folded (case / whitespace) the same way the run cache folds
    it: YouTube answers "King" and "king " identically, so they share a page.
    """
    p = dict(kwargs or {})
    q = p.get("q")
    if isinstance(q, str):
        p["q"] = " ".join(q.split()).casefold()
    blob = json.dumps(p, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def page_get(kwargs, ttl_s=PAGE_TTL_S):
    """(ids, next_token) for a fresh stored page, or None."""
    try:
        _ensure()
        conn = db_connect(DATA_DB_PATH)
        try:
            row = conn.execute(
                "SELECT ids, next_token, fetched_at FROM search_page_cache "
                "WHERE key = ?", (page_key(kwargs),)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        if time.time() - float(row[2] or 0) > ttl_s:
            return None
        return json.loads(row[0] or "[]"), (row[1] or None)
    except Exception:          # noqa: BLE001 - cache only, never fatal
        return None


def page_put(kwargs, ids, next_token):
    """Store one page. Prunes day-old rows in the same transaction."""
    try:
        _ensure()
        now = time.time()
        conn = db_connect(DATA_DB_PATH)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO search_page_cache "
                "(key, ids, next_token, fetched_at) VALUES (?, ?, ?, ?)",
                (page_key(kwargs), json.dumps(list(ids or [])),
                 next_token or "", now))
            conn.execute("DELETE FROM search_page_cache WHERE fetched_at < ?",
                         (now - _PRUNE_AFTER_S,))
            conn.commit()
        finally:
            conn.close()
    except Exception:          # noqa: BLE001
        pass


def page_cache_clear():
    try:
        _ensure()
        conn = db_connect(DATA_DB_PATH)
        try:
            conn.execute("DELETE FROM search_page_cache")
            conn.commit()
        finally:
            conn.close()
    except Exception:          # noqa: BLE001
        pass
