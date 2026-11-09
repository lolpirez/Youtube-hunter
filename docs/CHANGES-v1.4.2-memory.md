# v1.4.2: memory diet

Goal: cut the ~790-800 MB resident footprint without making anything feel
slower. Every change moves *when* something is paid for; none removes a
feature. Each one has an env switch back to the old behaviour.

| # | What was holding RAM | Change | Old behaviour |
|---|---|---|---|
| 1 | Support window: WebEngineView + Chromium renderer/GPU processes alive from launch (~250 MB in Task Manager) | Built on first click, freed 2 min after closing. One renderer max, no back-forward cache | n/a (code) |
| 2 | All 14 pages pre-built in the background and kept forever | Pre-build only 3 (`YH_PAGE_WARM`), keep landing + 5 most-recent (`YH_PAGE_KEEP`), free pages hidden > 3 min (`YH_PAGE_IDLE`), one per 30 s tick, never while animating. Cutter + AI Chat are never freed | `YH_PAGE_WARM=-1 YH_PAGE_KEEP=0` |
| 3 | bgutil-pot (embedded V8, 60-120 MB) started at launch | Started by the first token request, stops after 10 min idle (`YH_POT_IDLE_S`) | `YH_POT_EAGER=1` |
| 4 | yt-dlp imported 4 s after every launch | Imported when Downloads / Cutter opens or the browser extension connects | `YH_DL_PREWARM=eager` |
| 5 | Thumbnail RAM layer: 600 entries, any size (60-150 MB worst case), duplicate of the disk cache | True LRU, 24 MB byte budget (`YH_THUMB_MEM_MB`) | `YH_THUMB_MEM_MB=150` |
| 6 | Icon caches: 6144 + 2048 images, theme pre-warm of every icon ever seen | 10 MB / 4 MB byte budgets, pre-warm newest 900 | code |
| 7 | Nothing released while in background | `mem_trim.py`: QML GC + component cache after a page is freed; full trim after 45 s in background; working-set trim after 20 s minimised | `YH_NO_WS_TRIM=1` (working set part) |

## Measuring

    set YH_MEM=1
    YouTubeHunter.exe

writes `memory.log` next to `crash.log` (`%APPDATA%\YoutubeScrapperCyber`):
private bytes + working set every 30 s and around every trim. Compare a
session with `YH_PAGE_WARM=-1 YH_PAGE_KEEP=0 YH_POT_EAGER=1
YH_DL_PREWARM=eager` (the old policy) against the defaults.

Task Manager's "Memory" column (Processes tab) is the private working set
and groups the child processes (QtWebEngineProcess, bgutil-pot, deno) under
the app, which is why #1 and #3 matter so much there.
