# v1.0.5: "Transcript found" but empty in the CSV, plus a Thorough mode

## What was actually wrong

The log was telling the truth: those transcripts WERE fetched and written to
the file. They got lost when the file was opened in Excel:

1. **Excel's 32,767-character cell limit.** A 25-40 min video has a longer
   transcript than that. On CSV import Excel silently cuts the cell at
   ~32,758 characters and pours the rest into the next row(s). The sheet gets
   extra half-rows, the following videos' columns slide out of place, and
   their transcript cells read empty or hold another video's text. One long
   video can wreck several rows: the "5-8 missing out of 60".
2. **Formula cells.** A transcript starting with `-`, `=`, `+` or `@`
   ("- Hey guys", the speaker dash of manual subtitles) is read by Excel as a
   formula and shows `#NAME?` instead of text.
3. **In-app readers.** Python's csv reader refuses fields over 128 KB; one
   2-hour transcript made "Analyze with AI" / chat see the whole export as
   empty.
4. **Cached fragments.** An old 3k preview or a chat head+tail cut in the
   transcript cache could be served as the full transcript.
5. xlsx exports truncated long transcripts at 32,767 characters.

## Fixes (both modes)

- CSV + xlsx: long text cells continue in `transcript_part2`,
  `transcript_part3` ... columns placed right after `transcript`, split at a
  word boundary. Nothing is cut, one video = one row. The app's own readers
  glue the parts back together automatically.
- Text columns (title, description, tags, transcript) that start with
  `= + - @` get a leading space so Excel keeps them as text.
- csv field-size cap lifted for the app's readers.
- Trimmed fragments in the cache are treated as a miss and fetched again.
- A row can never be marked "found" without text; if it happens it is
  re-queued.

## Transcript accuracy: Fast / Thorough (Scraper page, Output step)

| | Fast (default, old behaviour) | Thorough |
|---|---|---|
| Parallel transcripts | Worker Threads setting | max 3 (fewer refusals) |
| "No captions" answer | trusted | confirmed by the library AND yt-dlp |
| Cached "no captions" | trusted for 3 days | checked again |
| Transcript too short for the video (< 0.8 chars/s, 2+ min) | kept | re-fetched (auto track first), longest kept |
| Refused / failed | 1 retry pass | 3 rounds (6 s, 20 s, 45 s breathers) |
| Resumed "Not Available" rows | kept | re-checked |
| Output | file | file + one `.txt` per transcript in `<export>_transcripts/` |

Stored as `"transcript_mode": "fast" | "thorough"` in config.json.

Files: `app/core/exports.py`, `app/core/transcripts.py`,
`app/core/analysis/csv_report.py`, `app/core/ai/chat_sources.py`,
`app/qml_backend/vm/dashboard_vm.py`, `app/qml_backend/vm/settings_vm.py`,
`app/qml/pages/DashboardPage.qml`.
