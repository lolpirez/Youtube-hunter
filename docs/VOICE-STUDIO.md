# Voice Studio: captions, AI translation, AI dubbing (v1.3)

Open it from any **finished download** (Downloads page, the new *Captions & AI dub*
button on a row) or any **cut clip** (Cutter page, same button on the clip card).
It is a side sheet with two tabs. Jobs keep running when you close it.

## Captions tab
1. **Words from**
   - **YouTube**: the video's own captions via yt-dlp. Uploaded tracks first, then auto
     captions (json3, which carries per-word timing). Exact timing, zero AI cost. For clips
     the captions are re-timed to the clip; a ±0.25 s nudge fixes keyframe drift.
   - **My file**: `.srt / .vtt / .ass / .ssa` (a same-name file next to the video is picked up automatically).
   - **AI listens**: Gemini transcribes the audio in 5-minute slices (needs a Gemini key).

   YouTube's rolling auto-caption fragments are re-segmented into whole sentences first:
   that is what makes the translation read naturally.
2. **Style**: five looks stacked with a live preview in the target script:
   *Clean · Bold Pop (word-by-word highlight) · Boxed · Neon · Cinema*. Size S/M/L,
   position bottom/middle/top. Sizes scale with the frame's short side (Shorts and
   landscape both look right). Persian/Arabic/Hebrew: correct shaping and direction
   (Bold Pop pops whole phrases there, because per-word colouring breaks joined letters).
3. **Translate**: any provider from Settings. A brief (topic, tone, register, glossary)
   is made first and carried into every batch; batches see surrounding lines as context;
   missing/untranslated lines are retried; anything still missing keeps its original
   text and is counted on the job card. Optional **bilingual** captions.
4. **Output**: *Burn in* (re-encode, hardware encoder when the Cutter found one) ·
   *Subtitle track* (switchable, no re-encode) · *.srt only*. `.srt` files are always
   written next to the video. If the ffmpeg build has no libass, burn falls back to a
   subtitle track and the job says so.

## AI Dub tab
Pipeline (`app/core/captions/dubbing.py`):
1. **Performance read**: Gemini listens with the timed lines and returns per line: speaker,
   delivery ("excited, fast, grinning"), and the line with vocal events (`<laugh>`, `<sigh>`,
   `<gasp>`, `<short pause>` …) where they happen; per speaker: gender, age, timbre, cast voice.
2. **Utterances**: consecutive lines of one speaker joined (≤14 s) so the voice acts whole thoughts.
3. **Adaptation**: translated to be *said* in each line's seconds, tags kept.
4. **Voice**: Gemini TTS (`gemini-3.8-flash-tts` "Studio" / `gemini-3.8-flash-lite-tts` "Fast"),
   delivery as `speech_metadata.style`, up to 4 lines in parallel through one app-wide gate, cached by content (re-runs and
   failed jobs resume for free). Falls back to older TTS models / request shapes automatically.
5. **Timing fit**: too long → AI rewrites shorter → ≤1.35× tempo (pitch kept) → trim with fade.
6. **Mix**: original side-chain **ducked** under the dub (music, room tone and laughs in the
   gaps stay), or a **low bed**, or **replace**. Loudness-normalised to −16 LUFS.
7. **Output**: dub = default audio track, original kept as a 2nd track; optional burned
   captions of the dub; plus the `.srt` script and a `.voice.wav`.

**Preview 30 s** runs the whole pipeline on the first 30 seconds: hear the voices before paying for the full video.

## v1.3.1: lean dub + guided sheet

**Why dubs were slow and ate the quota.** Each line's TTS retried a 429 three times, then
tried the two other request shapes, then two fallback models: up to 36 requests for one
refused line, from 3 threads per job, with no app-wide limit. A per-day quota therefore
looked like a job stuck at "Recording voices" while it burned the remaining quota. The
performance read ran with model "thinking" on, returned every line's text again, and was
cached per voice setting, so changing a voice paid for listening again; a failed slice was
cached as empty forever. Long lines were voiced, measured, rewritten (one AI call each) and
voiced again.

**What changed** (`gemini_media.py`, `dubbing.py`, `translate.py`, `sources.py`):
- one app-wide request gate: ≤4 Gemini requests in flight across all jobs; a per-minute 429
  cools the whole app down for Google's `retryDelay` and lowers parallelism; a per-day 429
  stops at once (`QuotaExhausted`) and the model is skipped for the session;
- 429 / 5xx never fan out to other request shapes; only "not served here" (400/404/405/501) does;
- the job stops with a clear message when the quota runs out and offers **Resume**; every
  recorded line, slice and script is cached per file, so Resume only pays for what's missing;
- a dub with more than 20 % unvoiced lines fails (resumable) instead of finishing with gaps;
- performance read: thinking off (2.5 Flash) / minimal (Pro, 3.x), `tagged` only for lines
  that got a tag, per-slice cache at the file level, failed slices not cached;
- script cached at the file level, every dub line gets a character budget (`max`);
- predicted-long lines are tightened in **one batched call before** recording; lines that are
  still long after recording are batched too (no more one-call-per-line);
- utterances up to 14 s (was 10 s): fewer TTS calls;
- "AI listens" caches each 5-minute slice as soon as it's paid for.

**Sheet**: activity first (with Resume), a "what do you want to make?" choice, three numbered
steps, the transcript source folded into one line, and one action in the footer.
**Cutter**: captions and AI dub are switches right in the inspector (under *Clip length*);
switch one on, press Cut, it runs on the clip.

`python tools/check_voice_studio.py` now also checks that a re-run makes zero new TTS calls
and that a per-day 429 costs one request per model, then stops.

## v1.3.3: inline deck, YouTube captions without AI

The download quality box and the Video Cutter inspector now carry the same
inline deck (`components/CaptionDubDeck.qml`): a *Captions* lane and an *AI dub*
lane with the settings that matter, queued to run when the file is ready.

Language defaults to **Original · no AI**. With a language picked, a caption job
tries YouTube first (`jobs.youtube_native`): an uploaded track in that language is
used as-is; with `translate_via = "youtube"` YouTube's free auto-translation is
accepted too. Only when YouTube has neither does the AI translate. The deck's
status line shows which path will run, from `VoiceStudio.tracksFor(videoId)`
(`uploaded`, `translatable`, `spoken`).

## Keys
- Translation: any AI provider in Settings → AI Providers.
- AI listens + dubbing: a **Google Gemini** key (the Gemini profile in Settings).

## Where things live
| | |
|---|---|
| `app/core/captions/` | the engine (no Qt): cues, languages, styles, sources, translate, gemini_media, render, dubbing, jobs |
| `app/qml_backend/vm/voice_studio_vm.py` | the `VoiceStudio` context property |
| `app/qml/components/VoiceStudioSheet.qml` | the sheet |
| `<app data>/voice_studio/<file-hash>/` | per-file cache: transcript, performance, script, voice clips |

![The five styles, English and Persian](voice-studio-styles.png)

## Self-test (no keys, no network)
`python tools/check_voice_studio.py` runs captions (5 styles × 3 outputs) and a full dub on a
synthetic video with the AI calls mocked; real ffmpeg does everything else.
