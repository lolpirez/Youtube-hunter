# v1.0.4: complete transcripts, a clear IP-block card

## Why some runs ended with 60/100 transcripts

YouTube rate-limits caption requests per IP. When that happened the old
gate only paused after **3 refusals in a row**. Everything else was written
off as `Not Available` and never asked again:

1. a throttled IP that lets some captions through and refuses others never
   produces 3 in a row, so every refusal was silently lost;
2. the 1-2 videos refused just before each confirmed block were lost;
3. after *New IP* only the one video that triggered the pause was retried;
4. network errors were counted as "no captions";
5. the yt-dlp fallback trusted android / tv clients that answer a bot-checked
   IP with no caption list, and cached that as "no captions" for 3 days.

## What a run does now

* Every refused / failed video is parked and retried in a **second pass**
  at the end of the run (after a short breather, same gate, so a hard block
  pauses again and shows the card).
* Still-missing rows are labelled in the export: `Blocked by YouTube`,
  `Failed (retry later)` or `Skipped`. `Not Available` means only one thing:
  YouTube says the video has no captions.
* A resumed run (checkpoint) and a re-run both refill those rows; cached
  transcripts cost nothing.

## Choices on the block card (and why)

| Option | Kept? | Why |
|---|---|---|
| Check new IP & resume | primary | instant and verified (the IP must change) |
| Auto-retry countdown (2 / 5 / 10 min, same IP) | new | most blocks are rate limits that lift; a run left alone heals itself instead of waiting forever |
| Retry now | new, quiet link | for people without a VPN who know the limit lifted |
| Finish without transcripts | kept, demoted to a link | sometimes metadata is all you need; the file says exactly which rows are missing |

There is no "strict mode" setting: the default already never drops a
transcript silently, and an incomplete export is always labelled and
fixable with one more run.

## Where the user sees a block

* Scraper: card at the very top (page scrolls up), header pill turns red.
* Rail: pulsing dot on the Scraper row.
* Any other page: floating "Transcripts paused · IP blocked · Fix it" pill.
* Desktop notification (unchanged).
* After the run: coverage strip with *Fill the gaps*.

Files: `app/qml_backend/vm/dashboard_vm.py`, `app/core/transcripts.py`,
`components/TranscriptBlockCard.qml` (new), `components/TranscriptCoverage.qml`
(new), `components/BlockAlertPill.qml` (new), `pages/DashboardPage.qml`,
`components/NavButton.qml`, `components/Sidebar.qml`,
`components/ScrapeResults.qml`, `Main.qml`.
