# v1.0.6: transcripts that never stall after an IP block, and two modes that mean it

## The bottleneck (measured, not guessed)

**The shared caption pacer.** Every transcript books a start slot from one
process-wide gate. On a 429 the gap jumped x1.8 up to 8 s, and every worker
had already booked a slot N x gap ahead, sleeping in an uninterruptible
`time.sleep()`. "Check new IP" / cool-down reset the pacer but could not wake
those sleepers. Simulated with 8 workers after a throttle storm:

| | old | v1.0.6 |
|---|---|---|
| wait before each worker starts, even after a new IP | 8, 16, 24 ... **64 s** | 0, 0.55, 1.1 ... 3.9 s (starts at once) |
| successes needed to get back to full speed after one episode | **185** | 30 |

That is the "30 s to 1 min before extraction starts again" users saw. Smaller
contributors on top of it:

* Fast mode still walked the yt-dlp quick ladder AND the full 5-client ladder
  on every blocked video (30-90 s per video, two-five at a time).
* Workers kept firing requests while the run was paused, then finished those
  doomed old-IP attempts after the resume before doing real work.
* The block card's IP lookup asked 3 endpoints one after another (up to ~18 s),
  and resuming needed the user to press "Check new IP".

## What changed

**Pacer (both modes)** `app/core/transcripts.py`
- Waits on a Condition: a reset (new IP, resume, new scrape) wakes every
  worker and they re-book from the fresh pace. Stop also interrupts a wait.
- AIMD: on a 429 one shared cool-down per episode (3 s fast / 8 s thorough)
  and x1.5 slower, capped at 2.5 s / 4 s. Recovery x0.85 every 3 successes.
- Breaker is half-open: while open it lets one probe through every 8 s (fast)
  / 20 s (thorough), so a lifted block or a VPN switch is noticed without a
  reset. 429s ("rate") now count toward it.
- `tx_epoch()`: a refusal whose fetch started before the IP changed is retried
  instead of counted.
- Public IP lookup asks all endpoints in parallel, first answer wins.

**Fast = speed. An IP block does not matter.**
- Starts at the Worker Threads level's top speed instead of the safe start.
- 8 s timeouts, 1 rate retry, then the video is parked.
- Breaker open: blocked videos are parked instantly (no network at all);
  metadata keeps streaming at full speed.
- yt-dlp fallback = default client only, never the full ladder.
- Never pauses, never shows the block card: one log line per block wave.
- One second pass after a 3 s breather; the coverage strip and the next run
  (cache) fill the rest.

**Thorough = every transcript of every video.**
- Gentler start (1.2 s), 15 s timeouts, 3 rate retries, full ladder,
  "no captions" double-checked, short transcripts re-fetched (as v1.0.5).
- On a confirmed block it pauses and **resumes by itself**: a watcher checks
  the public IP every 4 s (new VPN node = resume within seconds) and sends one
  gentle probe every 20 s (YouTube answers again = resume). Buttons still work.
- Cool-down steps 60 / 120 / 240 / 420 s (fallback; the watcher is faster).
- Retry rounds keep going until nothing is missing (up to 10 rounds, breathers
  4, 15, 30, 60, 90, 120 s...). Blocks inside a round pause and wait instead of
  burning rounds; leftovers confirm a block on fewer than 3 in a row.
- A video failing 4 times for a non-IP reason (e.g. PO-token-only track) stops
  and keeps its honest label "Failed (retry later)".
- Workers wait at the gate BEFORE fetching, so a paused run sends nothing.

Files: `app/core/transcripts.py`, `app/qml_backend/vm/dashboard_vm.py`,
`app/qml/pages/DashboardPage.qml` (mode descriptions),
`app/qml/components/TranscriptBlockCard.qml` (copy: auto-resume).
