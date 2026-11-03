"""app.core.ai.content_lab - Youtube Scrapper."""

import json

from app import i18n
from app.i18n import tr
from app.core.net import RequestCancelled

# ═════════════════════════════════════════════════════════════════════════
#  CONTENT LAB — the AI call behind the blueprint builder.
#
#  v2 of the schema (CONTENT_LAB_SCHEMA_VERSION below). v1 asked for a title,
#  one hook sentence, four execution notes and a pacing outline — enough to
#  judge an idea, not enough to shoot it. v2 asks for the things that
#  actually sit between "good idea" and "camera rolling":
#
#    full_script[]  → the words, verbatim, timecoded
#    shot_list[]    → what to point the camera at, plus B-roll cover
#    competitors{}  → what the reference videos already do, and the gap left
#    thumbnails[]   → three image prompts to A/B, not one
#    hook{}         → two visual and two caption options, not one of each
#    cta_options[]  → what to ask for, and exactly when
#
#  Deliberately NOT asked for — a model cannot know these, and inventing
#  them is worse than omitting them:
#    * estimated view counts ("400K – 900K") — no basis whatsoever
#    * similarity percentages against the reference videos
#    * trend-audio usage stats ("~500k uses this week")
#  The reference videos a blueprint was built from are real rows the user
#  ticked, kept by content_lab_merge_entry_videos() and rendered from
#  storage — never from the model.
#
#  The parser below is deliberately defensive: a partially-broken model
#  reply degrades to empty sections instead of crashing the page. It also
#  accepts v1 payloads (a plain string where v2 wants a list), so blueprints
#  generated before this change still render — see _content_lab_clean_idea.
# ═════════════════════════════════════════════════════════════════════════

CONTENT_LAB_SCHEMA_VERSION = 10

# ── v10 "Masterplan" (v1.0.14) ─────────────────────────────────────────────
# Checked against two full "4-act YouTube masterplan" briefs. v9 already
# covered most of them (promise + payoff time, angle + triggers, 3 hooks,
# evidence + model-free data pulse, 4 titles, 3 A/B thumbnails, timed script,
# re-hooks, capture list, prep, fast version, caption / SEO, CTA, pinned
# comment, risks, next videos, Shorts funnel). What was missing, and helps
# EVERY channel type, is added; the old fields that already have renderers
# are re-used rather than inventing new UI (~+500 tokens per greenlight):
#   core_promise.stakes   what the viewer loses by skipping it
#   core_promise.loop     Shorts: how the last beat flows back into the first
#   target                Broad vs Niche + the traffic lane it is built for
#                         (Browse / Search / Suggested) + how it holds both
#   anti_hook             now a RAW & HONEST opener (no hype, cold open,
#                         skeptic-defuser), asked for long-form too
#   packaging_brief       pairing + stand_out: how title and thumbnail split
#                         the job instead of repeating each other
#   style_guide           music + timed AUDIO cues (dead silence, beat drop…)
#   discovery             searches, suggested-beside, why it is clicked, and
#                         spoken_keywords (said out loud, indexed by YouTube)
#   end_screen            end_target + the last-5-seconds bridge line (no
#                         goodbye), the "binge tunnel"
#   rehooks               now must include open loops (plant -> payoff time)
# Rules tightened: titles <= 60 chars in four fixed formulas, thumbnail text
# never repeats the title, bottom-right corner left clear for the timestamp,
# tags cut to 0-5 (they barely matter), the description's first two lines
# written for search / Home, CTAs never beg, the pinned comment starts a
# debate. seo.keywords is no longer asked: discovery.search_queries is it.

# ── v9 "Universal" (v1.0.6) ────────────────────────────────────────────────
# v8 Essentials was lean, but it stopped at the WORDS: a cook, a walking-tour
# filmer or a faceless editor still had to work out what to capture and what
# to prepare, and nothing said what the video promises or when it pays off.
# v9 adds five compact, channel-agnostic fields (~700 tokens) and re-enables
# their existing renderers:
#   channel_type     the format the greenlight was written for (verifiable)
#   core_promise     the one promise + exactly when it is paid off
#   shot_list        the CAPTURE list: camera, screen, source clips, stock,
#                    route stops, steps — whatever this format films
#   production_plan  prep / shoot / edit time, place, what you need, the
#                    before-you-record checklist (gear only beyond a phone)
#   lite_version     the version that ships in an hour
#   funnel           Shorts to cut from a long video / where a Short points
# The creator profile also grows: channel type, stage, voice and "what
# already works", so the model stops guessing the format at all.

# ── v7: the audit pack (v1.0.3) ────────────────────────────────────────────
# A section-by-section audit of the v6 greenlight found four places where two
# sections said the same thing (and one where the model was asked to invent
# data), and four things a working creator still had to work out alone.
#
# Dropped from the ASK (old records still render them; nothing is migrated):
#   execution.audio    duplicated style_guide.music + style_guide.sfx
#   packaging.timing   duplicated publish_plan, and "best time to post" is not
#                      public data — the model was guessing
#   ab_plan.watch      duplicated checkpoints[1 hour]
#   playbook           duplicated checkpoints[].if_not ("If it stalls")
#   seo.keywords       duplicated discovery.search_queries (the SEO section
#                      now shows those instead)
# Re-framed: publish_plan.day/time must come from the creator's CSV data or
# say plainly that it is a starting slot to confirm in YouTube Studio.
# Added:
#   packaging_brief    thumbnail text, focal point, how title + thumbnail
#                      split the job, how it stands out in the feed
#   fact_check         every checkable claim in the script + how to verify it
#   lite_version       the fast version for a creator short on time
#   funnel             Shorts to cut from a long video / where a Short sends
#                      viewers — YouTube's own format funnel

# ── v6: the "after you hit publish" pack ───────────────────────────────────
# v5 stopped at the upload. v6 fills the gaps a working creator still filled
# by hand, and the blueprint gains a fourth act ("Grow") for what happens next:
#   why_now       shelf life (Evergreen / Seasonal / Trending) + the window
#   discovery     the exact searches, what it should be suggested beside,
#                 why it gets clicked on Home / the Shorts feed
#   delivery      how to SAY the script: tone, pace, energy, words to punch
#   end_screen    timed cards, the end-screen target, the last spoken line
#   checkpoints   1h / 24h / 7d: what to check, what good looks like, what next
#   reply_bank    the comments this video will get, with a reply ready
# Every number in checkpoints is phrased against the channel's OWN usual —
# the prompt forbids invented absolute figures, same rule as ab_plan.
_SHELF_LIFE = ("Evergreen", "Seasonal", "Trending")

# ── v5: the "finish the job" pack ──────────────────────────────────────────
# v4 told a creator what to say and why; v5 closes the gaps that still sent
# them back to guesswork:
#   score_breakdown  the 0-100 score as five audited 0-20 parts (the old single
#                    self-rating clustered at 82-92 whatever the idea was)
#   core_promise     the one promise title + thumbnail + hook all make, and
#                    exactly when the video pays it off
#   style_guide      look, palette, type, captions, music mood, timed SFX
#   on_screen_text   every overlay that is not a running caption, timed
#   production_plan  prep / shoot / edit estimates, gear, props, checklist
#   ab_plan          which thumbnail + title to launch with, when to swap
#   playbook         "if Studio shows X in the first 48h, do Y"
#   rights           copyright / Content ID risk and how to stay transformative
#   community_post   the teaser for the Community tab / Stories
# Script rows also gain "visual" (what is on screen) so "line" can stay the
# words actually spoken — v4 scripts drifted into stage directions
# ("(montage, faster cuts…)") that nobody can read on camera.
# Generation runs in batches of _BATCH so a 4-blueprint request no longer
# squeezes four dossiers into one reply and thins out the last two.
_BATCH = 2
_SCORE_PARTS = ("hook", "retention", "packaging", "novelty", "feasibility")
_HEX_RE = __import__("re").compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _content_lab_clean_str(v, fallback=""):
    if isinstance(v, (dict, list)):
        return fallback
    s = str(v).strip() if v is not None else ""
    return s or fallback


def _content_lab_clean_list(v, max_n=6):
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:max_n]:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _content_lab_clean_str_or_list(v, max_n=2):
    """A field that is a LIST of options in schema v2 but was a single string
    in v1. Returns a list either way, so one render path serves both.

    This is the whole of the v1 compatibility story for hook.visual and
    hook.text: an old stored blueprint yields a one-item list and the UI shows
    one option instead of two, rather than showing nothing.
    """
    if isinstance(v, list):
        return _content_lab_clean_list(v, max_n)
    s = _content_lab_clean_str(v)
    return [s] if s else []


def _content_lab_clean_obj_list(v, fields, max_n=8, bools=()):
    """Coerce a list of AI-produced objects onto a fixed field set, dropping
    any entry where every field came back empty. `bools` names fields that
    should be coerced to True/False rather than to a string."""
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:max_n]:
        if not isinstance(item, dict):
            continue
        cleaned = {}
        for f in fields:
            if f in bools:
                cleaned[f] = bool(item.get(f))
            else:
                cleaned[f] = _content_lab_clean_str(item.get(f))
        if any(cleaned[f] for f in fields if f not in bools):
            out.append(cleaned)
    return out


# Production difficulty is a judgement an experienced editor makes from the
# shot list (how much footage, how much motion work), so the model is asked
# for it — unlike a view estimate, which has nothing to reason from.
_CONTENT_LAB_DIFFICULTIES = ("Easy", "Medium", "Hard")


def _content_lab_default_idea(format="short"):
    return {
        # Which schema shape this record was written with. Read by the UI to
        # decide whether a stored blueprint predates the v2 sections and
        # should offer "regenerate in full" instead of rendering them empty.
        "schema_version": CONTENT_LAB_SCHEMA_VERSION,
        # "short" | "long" — which batch/tab this idea belongs to. Decided
        # once at generation time by content_lab_classify_format() and kept
        # fixed afterwards (regenerating an idea preserves its own format
        # rather than re-deriving it from the entry's videos, since one
        # entry can hold both short and long ideas from different runs).
        "format": format if format in ("short", "long") else "short",
        "title": tr("Untitled idea"),
        "score": 82,
        "score_label": tr("PROMISING"),
        "hook_preview": "",
        "source": "",
        # ── who and what, so a blueprint is self-describing once it has been
        # sitting in the library for a week ──
        "audience": "",
        "pillar": "",
        "difficulty": "Medium",
        # v9: the format the greenlight was written for ("Cooking", "Vlog" …)
        "channel_type": "",
        # Three thumbnail prompts to A/B rather than one to accept. The
        # singular "thumbnail_prompt" stays as the first of them so v1
        # readers (the PDF export, older stored records) keep working.
        "thumbnails": [],
        "thumbnail_prompt": "",
        "titles": [],
        "viral_angle": {"concept": "", "triggers": [], "reasoning": ""},
        # visual/text are LISTS in v2 (two options each) — see
        # _content_lab_clean_str_or_list for how v1 strings are absorbed.
        "hook": {"focus_tag": "", "spoken": [], "visual": [], "text": [], "reasoning": ""},
        # An ANTI-HOOK is a reverse-psychology / disqualifying opener that works
        # by pushing the wrong viewer away ("don't watch this if…", "this isn't
        # for everyone") so the right viewer leans in — on a Short a swipe then
        # feels like a loss rather than a skip. Short-form ONLY: it's a scroll-
        # stopping device that has no equivalent on a 10-minute video, so this
        # stays empty on long-form (mirrors how `seo` is long-form-only). Two
        # options + a one-line why, same shape as `hook`.
        "anti_hook": {"focus_tag": "", "options": [], "reasoning": ""},
        # The words, verbatim and timecoded. The single biggest gap in v1:
        # you could tell whether an idea was good but not read it out.
        "full_script": [],
        # What to point the camera at, and which shots exist only to cover
        # cuts (broll=True) so an editor can shoot the essentials first.
        "shot_list": [],
        # What the reference videos already do, and the opening they leave.
        # "rows" describes real videos the user ticked where possible; "gap"
        # is the positioning conclusion drawn from them.
        "competitors": {"rows": [], "gap": ""},
        "execution": {"editing": "", "assets": "", "audio": "", "render": "",
                      "reasoning": ""},
        "packaging": {"framework": "", "timing": ""},
        # Two ways to ask, each with its own timing — a Short optimised for
        # loops wants a different ask than a long-form mid-roll.
        "cta_options": [],
        # No "sources" here on purpose — that used to be an AI-fabricated
        # pattern blurb with nothing real to link to, complete with invented
        # similarity percentages. The Retention section lists the entry's
        # ACTUAL reference videos instead (from content_lab_get_entry_videos,
        # with real clickable URLs), so the model isn't asked to invent this
        # part of the schema at all.
        "retention": {"total_seconds": 60, "phases": []},
        # The working-creator pack, same three fields the combined report's
        # blueprint section carries. Deliberately NO "success_metrics" here:
        # numeric targets need a channel's own averages, and this module has a
        # hard rule against inventing numbers — that target belongs to the
        # report, which is generated FROM the channel's real export.
        "risks": [],
        "publish_plan": {"day": "", "time": "", "why": "", "first_hour": []},
        "series_next": [],
        # SEO is a long-form-only concern — YouTube keywords/tags/description/
        # chapter timestamps don't map onto a 30-60s Short, so this stays an
        # empty dict for short-form ideas and the blueprint UI simply won't
        # render the section for them.
        "seo": {"keywords": [], "tags": [], "description": "", "timestamps": []},
        # ── v4: the "anyone can pick this up" pack ──
        # Who exactly is watching, and what they want / fear / object to — so a
        # creator who did not build the brief still knows who they are talking to.
        "audience_profile": {"persona": "", "pain_points": [], "desires": [],
                             "objections": [], "watch_context": ""},
        # Re-hooks / open loops placed through the video: the moments that stop
        # the mid-video drop, with the actual line to say.
        "rehooks": [],
        # Data-backed reasons this should work, each citing a REAL input row
        # (V# = a ticked reference video, C# = a top video from a CSV export).
        # The model only names the ref; the title/link/views are resolved here
        # from the real rows, never written by the model.
        "evidence": [],
        # Advertiser-friendliness + sponsor fit. No money figures (see rule).
        "monetization": {"ad_safety": "", "why": "", "sponsor_fit": [],
                         "product_tie_in": ""},
        # Where else this exact video can live, and what changes per platform.
        "repurpose": [],
        "pinned_comment": "",
        # Shorts get a caption + hashtags (long-form uses `seo`).
        "caption_pack": {"description": "", "hashtags": []},
        # Resolved reference catalogue the run was built from (real rows):
        # [{ref, title, url, channel, views, multiplier, source}].
        "refs": [],
        # Model-free numbers computed from the inputs (see _data_pulse).
        "data_pulse": {},
        # Which CSV exports fed this run (names + paths, for regenerate).
        "data_sources": [],
        # ── v5 pack (see the note at CONTENT_LAB_SCHEMA_VERSION) ──
        "score_breakdown": [],
        "core_promise": {"promise": "", "payoff": "", "payoff_time": "",
                         "differentiator": "", "stakes": "", "loop": ""},
        # v10: who it is built for and which traffic lane it is built to win
        "target": {"spectrum": "", "lane": "", "both": ""},
        "style_guide": {"look": "", "palette": [], "typography": "",
                        "captions": "", "music": "", "sfx": []},
        "on_screen_text": [],
        "production_plan": {"prep": "", "shoot": "", "edit": "", "gear": [],
                            "props": [], "location": "", "checklist": []},
        "ab_plan": {"thumbnail_first": "", "title_first": "", "swap_rule": "",
                    "watch": ""},
        "playbook": [],
        "rights": {"risk": "", "notes": "", "safe_moves": []},
        "community_post": "",
        # ── v6 pack (see the note at CONTENT_LAB_SCHEMA_VERSION) ──
        "why_now": {"shelf_life": "", "window": "", "why": ""},
        "discovery": {"search_queries": [], "suggested_beside": [], "browse_trigger": "",
                      "spoken_keywords": []},
        "delivery": {"tone": "", "pace": "", "energy": "", "emphasis": [], "avoid": []},
        "end_screen": {"cards": [], "end_target": "", "line": ""},
        "checkpoints": [],
        "reply_bank": [],
        # ── v7 pack (see the note at CONTENT_LAB_SCHEMA_VERSION) ──
        "packaging_brief": {"thumb_text": [], "focal": "", "pairing": "", "stand_out": ""},
        "fact_check": [],
        "lite_version": {"time": "", "cut": "", "keep": ""},
        "funnel": {"clips": [], "link_to": "", "why": ""},
    }


def _content_lab_clean_idea(raw, format="short"):
    """Defensively coerces one AI-produced idea object onto the full schema
    the Content Lab UI renders.

    Also the v1→v2 read path: a stored blueprint from before the schema bump
    has no full_script / shot_list / competitors and carries plain strings
    where v2 wants lists. Every one of those degrades to an empty list here
    rather than raising, and `schema_version` is left at whatever the record
    claimed (defaulting to 1) so the UI can offer a full regeneration instead
    of silently showing half a card.
    """
    d = _content_lab_default_idea(format)
    if not isinstance(raw, dict):
        return d
    try:
        d["schema_version"] = int(raw.get("schema_version") or 1)
    except (TypeError, ValueError):
        d["schema_version"] = 1
    d["title"] = _content_lab_clean_str(raw.get("title"), d["title"])
    try:
        score = int(float(raw.get("score", d["score"])))
    except Exception:
        score = d["score"]
    d["score"] = max(0, min(100, score))
    d["score_label"] = _content_lab_clean_str(raw.get("score_label"), d["score_label"]).upper()
    d["hook_preview"] = _content_lab_clean_str(raw.get("hook_preview"))
    d["source"] = _content_lab_clean_str(raw.get("source"))
    d["audience"] = _content_lab_clean_str(raw.get("audience"))
    d["pillar"] = _content_lab_clean_str(raw.get("pillar"))
    diff = _content_lab_clean_str(raw.get("difficulty")).title()
    d["difficulty"] = diff if diff in _CONTENT_LAB_DIFFICULTIES else "Medium"
    d["channel_type"] = _content_lab_clean_str(raw.get("channel_type"))[:40]
    d["titles"] = _content_lab_clean_list(raw.get("titles"), 5)

    # ── thumbnails: 3 labelled prompts in v2, one bare string in v1 ──
    thumbs = _content_lab_clean_obj_list(raw.get("thumbnails"), ("label", "prompt", "text"), 3)
    if not thumbs:
        legacy = _content_lab_clean_str(raw.get("thumbnail_prompt"))
        if legacy:
            thumbs = [{"label": tr("Concept A"), "prompt": legacy}]
    # Any prompt that came back without its own label still needs one, or the
    # A/B/C chips render blank.
    for i, t in enumerate(thumbs):
        if not t.get("label"):
            t["label"] = tr("Concept {x}").format(x="ABC"[i] if i < 3 else str(i + 1))
    d["thumbnails"] = thumbs
    # Kept in sync so the PDF export and any other single-prompt reader still
    # find something without having to know about the list.
    d["thumbnail_prompt"] = thumbs[0]["prompt"] if thumbs else ""

    va = raw.get("viral_angle") if isinstance(raw.get("viral_angle"), dict) else {}
    d["viral_angle"] = {
        "concept": _content_lab_clean_str(va.get("concept")),
        "triggers": _content_lab_clean_list(va.get("triggers"), 5),
        "reasoning": _content_lab_clean_str(va.get("reasoning")),
    }

    hk = raw.get("hook") if isinstance(raw.get("hook"), dict) else {}
    d["hook"] = {
        "focus_tag": _content_lab_clean_str(hk.get("focus_tag")),
        # v8: three spoken / first-line openers, each a different technique
        "spoken": _content_lab_clean_str_or_list(hk.get("spoken"), 3),
        "visual": _content_lab_clean_str_or_list(hk.get("visual"), 2),
        "text": _content_lab_clean_str_or_list(hk.get("text"), 2),
        "reasoning": _content_lab_clean_str(hk.get("reasoning")),
    }

    # ── anti-hook ──
    # v1-v9: short-form only (a reverse-psychology opener). v10: a raw &
    # honest opener on BOTH formats; older long-form records simply have none.
    if d["format"] == "short" or int(raw.get("schema_version") or 0) >= 10 \
            or not raw.get("schema_version"):
        ah = raw.get("anti_hook") if isinstance(raw.get("anti_hook"), dict) else {}
        d["anti_hook"] = {
            "focus_tag": _content_lab_clean_str(ah.get("focus_tag")),
            "options": _content_lab_clean_str_or_list(ah.get("options"), 2),
            "reasoning": _content_lab_clean_str(ah.get("reasoning")),
        }

    # ── the words ──
    d["full_script"] = _content_lab_clean_obj_list(
        raw.get("full_script"), ("time", "speaker", "line", "visual"), 14)
    # ── the camera plan; `broll` marks cover shots, so it must stay a bool ──
    d["shot_list"] = _content_lab_clean_obj_list(
        raw.get("shot_list"), ("shot", "type", "desc", "beat", "broll"), 12, bools=("broll",))
    # Auto-label any shot the model left unnamed (A1, A2, …) so the mono chips
    # in the UI are never blank.
    for i, s in enumerate(d["shot_list"], 1):
        if not s.get("shot"):
            s["shot"] = f"{'B' if s.get('broll') else 'A'}{i}"

    cp = raw.get("competitors") if isinstance(raw.get("competitors"), dict) else {}
    d["competitors"] = {
        "rows": _content_lab_clean_obj_list(cp.get("rows"), ("ref", "name", "angle", "gap", "url"), 5),
        "gap": _content_lab_clean_str(cp.get("gap")),
    }

    ex = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    d["execution"] = {
        "editing": _content_lab_clean_str(ex.get("editing")),
        "assets": _content_lab_clean_str(ex.get("assets")),
        "audio": _content_lab_clean_str(ex.get("audio")),
        "render": _content_lab_clean_str(ex.get("render")),
        "reasoning": _content_lab_clean_str(ex.get("reasoning")),
    }

    pk = raw.get("packaging") if isinstance(raw.get("packaging"), dict) else {}
    d["packaging"] = {
        "framework": _content_lab_clean_str(pk.get("framework")),
        "timing": _content_lab_clean_str(pk.get("timing")),
    }

    d["cta_options"] = _content_lab_clean_obj_list(
        raw.get("cta_options"), ("label", "text", "timing"), 3)

    rt = raw.get("retention") if isinstance(raw.get("retention"), dict) else {}
    try:
        total_seconds = int(float(rt.get("total_seconds", 60)))
    except Exception:
        total_seconds = 60
    phases = []
    for p in (rt.get("phases") or [])[:6]:
        if not isinstance(p, dict):
            continue
        try:
            end_pct = max(1, min(100, int(float(p.get("end_pct", 0)))))
        except Exception:
            continue
        label = _content_lab_clean_str(p.get("label"))
        if not label:
            continue
        phases.append({"label": label, "end_pct": end_pct,
                       "desc": _content_lab_clean_str(p.get("desc"))})
    d["retention"] = {"total_seconds": max(10, total_seconds), "phases": phases}

    # SEO only applies to long-form — leave the short-form default (empty)
    # untouched even if a model reply included the field anyway.
    if d["format"] == "long":
        seo = raw.get("seo") if isinstance(raw.get("seo"), dict) else {}
        timestamps = []
        for t in (seo.get("timestamps") or [])[:12]:
            if not isinstance(t, dict):
                continue
            label = _content_lab_clean_str(t.get("label"))
            time_s = _content_lab_clean_str(t.get("time"))
            if label and time_s:
                timestamps.append({"time": time_s, "label": label})
        d["seo"] = {
            "keywords": _content_lab_clean_list(seo.get("keywords"), 8),
            "tags": _content_lab_clean_list(seo.get("tags"), 15),
            "description": _content_lab_clean_str(seo.get("description")),
            "timestamps": timestamps,
        }

    # ── the working-creator pack ──
    # Cleaned rather than trusted: a model that answers publish_plan with a bare
    # string ("Friday 6pm") still yields a usable "day", and a risks entry with
    # no fix is dropped rather than rendered as half a thought.
    d["risks"] = _content_lab_clean_obj_list(raw.get("risks"), ("risk", "fix"), 5)

    pp = raw.get("publish_plan")
    if isinstance(pp, str):
        # A bare string is a day/time answer folded into "day" — the renderer
        # shows it, and an empty plan would just hide the block.
        pp = {"day": pp.strip()} if pp.strip() else {}
    pp = pp if isinstance(pp, dict) else {}
    first_hour = _content_lab_clean_list(pp.get("first_hour"), 6)
    d["publish_plan"] = {
        "day": _content_lab_clean_str(pp.get("day")),
        "time": _content_lab_clean_str(pp.get("time")),
        "why": _content_lab_clean_str(pp.get("why")),
        "first_hour": first_hour,
    }
    if not any(d["publish_plan"].values()):
        d["publish_plan"] = {"day": "", "time": "", "why": "", "first_hour": []}

    d["series_next"] = _content_lab_clean_obj_list(
        raw.get("series_next"), ("title", "why"), 4)

    # ── v4 pack ──
    ap = raw.get("audience_profile") if isinstance(raw.get("audience_profile"), dict) else {}
    d["audience_profile"] = {
        "persona": _content_lab_clean_str(ap.get("persona")),
        "pain_points": _content_lab_clean_list(ap.get("pain_points"), 4),
        "desires": _content_lab_clean_list(ap.get("desires"), 4),
        "objections": _content_lab_clean_list(ap.get("objections"), 3),
        "watch_context": _content_lab_clean_str(ap.get("watch_context")),
    }
    d["rehooks"] = _content_lab_clean_obj_list(
        raw.get("rehooks"), ("time", "device", "line"), 6)
    d["evidence"] = _content_lab_clean_obj_list(
        raw.get("evidence"), ("claim", "ref", "url", "title", "views"), 5)
    mo = raw.get("monetization") if isinstance(raw.get("monetization"), dict) else {}
    safety = _content_lab_clean_str(mo.get("ad_safety")).title()
    d["monetization"] = {
        "ad_safety": safety if safety in ("Safe", "Limited", "Risky") else "",
        "why": _content_lab_clean_str(mo.get("why")),
        "sponsor_fit": _content_lab_clean_list(mo.get("sponsor_fit"), 4),
        "product_tie_in": _content_lab_clean_str(mo.get("product_tie_in")),
    }
    d["repurpose"] = _content_lab_clean_obj_list(
        raw.get("repurpose"), ("platform", "how"), 4)
    d["pinned_comment"] = _content_lab_clean_str(raw.get("pinned_comment"))
    if d["format"] == "short":
        cpk = raw.get("caption_pack") if isinstance(raw.get("caption_pack"), dict) else {}
        tags = [t if t.startswith("#") else "#" + t.lstrip("#")
                for t in _content_lab_clean_list(cpk.get("hashtags"), 8)]
        d["caption_pack"] = {
            "description": _content_lab_clean_str(cpk.get("description")),
            "hashtags": tags,
        }
    _clean_v5(raw, d)
    _clean_v6(raw, d)
    _clean_v7(raw, d)
    _clean_v10(raw, d)
    # stored, already-resolved data passes straight through
    if isinstance(raw.get("refs"), list):
        d["refs"] = [r for r in raw["refs"][:24] if isinstance(r, dict)]
    if isinstance(raw.get("data_pulse"), dict):
        d["data_pulse"] = raw["data_pulse"]
    if isinstance(raw.get("data_sources"), list):
        d["data_sources"] = [x for x in raw["data_sources"][:8] if isinstance(x, dict)]
    return d


def _clean_v5(raw, d):
    """The v5 pack, cleaned the same defensive way as everything above: a
    malformed field degrades to its empty default instead of raising."""
    # ── score breakdown: five 0-20 parts; when all five came back the headline
    # score IS their sum, so the number on the cover can be audited ──
    sb = raw.get("score_breakdown")
    parts = []
    if isinstance(sb, dict):
        items = [(k, sb.get(k)) for k in _SCORE_PARTS]
    elif isinstance(sb, list):
        items = [(str((x or {}).get("key") or "").lower(), x) for x in sb if isinstance(x, dict)]
    else:
        items = []
    for key, v in items:
        if key not in _SCORE_PARTS or any(p["key"] == key for p in parts):
            continue
        if isinstance(v, dict):
            pts, why = v.get("points", v.get("score")), v.get("why")
        else:
            pts, why = v, ""
        try:
            pts = int(round(float(pts)))
        except (TypeError, ValueError):
            continue
        parts.append({"key": key, "points": max(0, min(20, pts)),
                      "why": _content_lab_clean_str(why)})
    parts.sort(key=lambda p: _SCORE_PARTS.index(p["key"]))
    d["score_breakdown"] = parts
    if len(parts) == len(_SCORE_PARTS):
        d["score"] = sum(p["points"] for p in parts)

    cp = raw.get("core_promise") if isinstance(raw.get("core_promise"), dict) else {}
    d["core_promise"] = {k: _content_lab_clean_str(cp.get(k))
                         for k in ("promise", "payoff", "payoff_time", "differentiator",
                                   "stakes", "loop")}

    sg = raw.get("style_guide") if isinstance(raw.get("style_guide"), dict) else {}
    palette = [c for c in _content_lab_clean_list(sg.get("palette"), 5) if _HEX_RE.match(c)]
    d["style_guide"] = {
        "look": _content_lab_clean_str(sg.get("look")),
        "palette": palette,
        "typography": _content_lab_clean_str(sg.get("typography")),
        "captions": _content_lab_clean_str(sg.get("captions")),
        "music": _content_lab_clean_str(sg.get("music")),
        "sfx": _content_lab_clean_obj_list(sg.get("sfx"), ("time", "cue"), 6),
    }
    d["on_screen_text"] = _content_lab_clean_obj_list(
        raw.get("on_screen_text"), ("time", "text", "style"), 10)

    pp = raw.get("production_plan") if isinstance(raw.get("production_plan"), dict) else {}
    d["production_plan"] = {
        "prep": _content_lab_clean_str(pp.get("prep")),
        "shoot": _content_lab_clean_str(pp.get("shoot")),
        "edit": _content_lab_clean_str(pp.get("edit")),
        "gear": _content_lab_clean_list(pp.get("gear"), 6),
        "props": _content_lab_clean_list(pp.get("props"), 5),
        "location": _content_lab_clean_str(pp.get("location")),
        "checklist": _content_lab_clean_list(pp.get("checklist"), 7),
    }
    ab = raw.get("ab_plan") if isinstance(raw.get("ab_plan"), dict) else {}
    d["ab_plan"] = {k: _content_lab_clean_str(ab.get(k))
                    for k in ("thumbnail_first", "title_first", "swap_rule", "watch")}
    d["playbook"] = _content_lab_clean_obj_list(raw.get("playbook"), ("signal", "action"), 5)
    rg = raw.get("rights") if isinstance(raw.get("rights"), dict) else {}
    risk = _content_lab_clean_str(rg.get("risk")).title()
    d["rights"] = {
        "risk": risk if risk in ("Low", "Medium", "High") else "",
        "notes": _content_lab_clean_str(rg.get("notes")),
        "safe_moves": _content_lab_clean_list(rg.get("safe_moves"), 4),
    }
    d["community_post"] = _content_lab_clean_str(raw.get("community_post"))


def _clean_v6(raw, d):
    """The v6 "after you hit publish" pack — same defensive rules as v5."""
    wn = raw.get("why_now")
    if isinstance(wn, str):
        wn = {"why": wn}
    wn = wn if isinstance(wn, dict) else {}
    shelf = _content_lab_clean_str(wn.get("shelf_life"))
    # a Persian reply sometimes translates the fixed token anyway
    shelf = {"همیشگی": "Evergreen", "همیشه‌سبز": "Evergreen", "فصلی": "Seasonal",
             "ترند": "Trending", "داغ": "Trending"}.get(shelf, shelf).title()
    d["why_now"] = {
        "shelf_life": shelf if shelf in _SHELF_LIFE else "",
        "window": _content_lab_clean_str(wn.get("window")),
        "why": _content_lab_clean_str(wn.get("why")),
    }
    dc = raw.get("discovery") if isinstance(raw.get("discovery"), dict) else {}
    d["discovery"] = {
        "search_queries": _content_lab_clean_list(dc.get("search_queries"), 6),
        "suggested_beside": _content_lab_clean_list(dc.get("suggested_beside"), 3),
        "browse_trigger": _content_lab_clean_str(dc.get("browse_trigger")),
        "spoken_keywords": _content_lab_clean_list(dc.get("spoken_keywords"), 6),
    }
    dl = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
    d["delivery"] = {
        "tone": _content_lab_clean_str(dl.get("tone")),
        "pace": _content_lab_clean_str(dl.get("pace")),
        "energy": _content_lab_clean_str(dl.get("energy")),
        "emphasis": _content_lab_clean_list(dl.get("emphasis"), 6),
        "avoid": _content_lab_clean_list(dl.get("avoid"), 4),
    }
    es = raw.get("end_screen") if isinstance(raw.get("end_screen"), dict) else {}
    d["end_screen"] = {
        "cards": _content_lab_clean_obj_list(es.get("cards"), ("time", "target"), 4),
        "end_target": _content_lab_clean_str(es.get("end_target")),
        "line": _content_lab_clean_str(es.get("line")),
    }
    d["checkpoints"] = _content_lab_clean_obj_list(
        raw.get("checkpoints"), ("when", "check", "good_sign", "if_not"), 4)
    d["reply_bank"] = _content_lab_clean_obj_list(
        raw.get("reply_bank"), ("comment", "reply"), 5)


_SPECTRUM = ("Broad", "Niche")
_LANES = ("Browse", "Search", "Suggested")


def _clean_v10(raw, d):
    """The v10 "Masterplan" target block — fixed tokens kept fixed, a
    translated / free-text answer degrades to empty instead of a wrong chip."""
    tg = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    spec = _content_lab_clean_str(tg.get("spectrum")).title()
    spec = {"عام": "Broad", "گسترده": "Broad", "تخصصی": "Niche", "خاص": "Niche"}.get(spec, spec)
    lane = _content_lab_clean_str(tg.get("lane")).title()
    lane = {"جستجو": "Search", "سرچ": "Search", "پیشنهادی": "Suggested",
            "صفحه اصلی": "Browse", "براوز": "Browse", "Home": "Browse",
            "Shorts Feed": "Browse", "Suggested Videos": "Suggested"}.get(lane, lane)
    d["target"] = {
        "spectrum": spec if spec in _SPECTRUM else "",
        "lane": lane if lane in _LANES else "",
        "both": _content_lab_clean_str(tg.get("both")),
    }


def _clean_v7(raw, d):
    """The v7 audit pack — same defensive rules as v5 / v6."""
    pb = raw.get("packaging_brief") if isinstance(raw.get("packaging_brief"), dict) else {}
    d["packaging_brief"] = {
        "thumb_text": _content_lab_clean_str_or_list(pb.get("thumb_text"), 3),
        "focal": _content_lab_clean_str(pb.get("focal")),
        "pairing": _content_lab_clean_str(pb.get("pairing")),
        "stand_out": _content_lab_clean_str(pb.get("stand_out")),
    }
    d["fact_check"] = _content_lab_clean_obj_list(raw.get("fact_check"), ("claim", "verify"), 5)
    lv = raw.get("lite_version") if isinstance(raw.get("lite_version"), dict) else {}
    d["lite_version"] = {k: _content_lab_clean_str(lv.get(k)) for k in ("time", "cut", "keep")}
    fn = raw.get("funnel") if isinstance(raw.get("funnel"), dict) else {}
    d["funnel"] = {
        "clips": _content_lab_clean_obj_list(fn.get("clips"), ("from", "hook"), 4),
        "link_to": _content_lab_clean_str(fn.get("link_to")),
        "why": _content_lab_clean_str(fn.get("why")),
    }


# ── reference resolution (v4) ─────────────────────────────────────────────
def _norm_title(t):
    import re as _re
    return _re.sub(r"[^\w]+", " ", str(t or "").lower()).strip()


def _build_refs(videos, csv_sources):
    """The run's reference catalogue: V1..Vn for the ticked videos, then the
    C# rows each CSV export contributed. Every entry is a real row."""
    refs = []
    for i, v in enumerate(videos or [], 1):
        url = str(v.get("video_url") or "").strip()
        vid = str(v.get("video_id") or "").strip()
        if not url and vid:
            url = f"https://www.youtube.com/watch?v={vid}"
        refs.append({
            "ref": f"V{i}",
            "title": str(v.get("title") or "").strip(),
            "url": url,
            "channel": str(v.get("channel_title") or "").strip(),
            "views": int(v.get("view_count", 0) or 0),
            "multiplier": v.get("multiplier") if isinstance(v.get("multiplier"), (int, float)) else None,
            "source": "video",
        })
    for src in csv_sources or []:
        for r in src.get("refs") or []:
            refs.append(dict(r))
    return refs


def _resolve_ref(refs, ref="", title=""):
    """Find the real row a model citation points at — by its id first, then by
    title (the model sometimes quotes the title instead of the id)."""
    ref = str(ref or "").strip().strip("[]").upper()
    if ref:
        for r in refs:
            if r.get("ref", "").upper() == ref:
                return r
    nt = _norm_title(title)
    if nt:
        for r in refs:
            rt = _norm_title(r.get("title"))
            if rt and (rt == nt or (len(nt) > 12 and (nt in rt or rt in nt))):
                return r
    return None


def _data_pulse(videos, csv_sources):
    """Model-free numbers about the inputs, shown in the blueprint's Data pulse
    section. Never sent back through the model, so it cannot be embellished."""
    import statistics as _st
    out = {}
    views = [int(v.get("view_count", 0) or 0) for v in (videos or [])]
    views = [x for x in views if x > 0]
    if views:
        top = max(videos, key=lambda v: int(v.get("view_count", 0) or 0))
        durs = [int(v.get("duration_s", 0) or 0) for v in videos if v.get("duration_s")]
        out["videos"] = {
            "count": len(videos),
            "median_views": int(_st.median(views)),
            "top_views": max(views),
            "top_title": str(top.get("title") or ""),
            "top_url": str(top.get("video_url") or ""),
            "median_duration_s": int(_st.median(durs)) if durs else 0,
            "shorts": sum(1 for v in videos if v.get("is_short")),
        }
    sources = []
    for src in csv_sources or []:
        st = src.get("stats") or {}
        if st:
            sources.append(dict(st, name=src.get("name", "")))
    if sources:
        out["csv"] = sources
    return out


def _attach_refs(ideas, videos, csv_sources):
    refs = _build_refs(videos, csv_sources)
    pulse = _data_pulse(videos, csv_sources)
    srcs = [{"name": s.get("name", ""), "path": s.get("path", ""),
             "rows": s.get("rows", 0)} for s in (csv_sources or [])]
    for d in ideas:
        d["refs"] = refs
        d["data_pulse"] = pulse
        d["data_sources"] = srcs
        for row in d.get("competitors", {}).get("rows", []):
            hit = _resolve_ref(refs, row.get("ref"), row.get("name"))
            if hit:
                row["ref"] = hit.get("ref", "")
                row["url"] = hit.get("url", "")
                row["name"] = hit.get("title") or row.get("name", "")
                row["channel"] = hit.get("channel", "")
                row["views"] = hit.get("views", 0)
            elif refs:
                row["ref"] = ""
                row["url"] = ""
        kept = []
        for ev in d.get("evidence", []):
            hit = _resolve_ref(refs, ev.get("ref"), ev.get("title"))
            if hit:
                ev["ref"] = hit.get("ref", "")
                ev["url"] = hit.get("url", "")
                ev["title"] = hit.get("title", "")
                ev["views"] = str(hit.get("views", "") or "")
            elif refs:
                # a claim citing a row that does not exist is exactly the kind
                # of fabricated evidence this section must not show
                continue
            kept.append(ev)
        d["evidence"] = kept
    return ideas


_THINK_RE = __import__("re").compile(
    r"<(think|thinking|reasoning)>.*?</\1>", __import__("re").S | __import__("re").I)
_IDEAS_KEY_RE = __import__("re").compile(r'\{\s*"ideas"\s*:')
_TRAILING_COMMA_RE = __import__("re").compile(r",\s*([}\]])")


def _strip_reasoning(text):
    """Drop a reasoning model's <think>…</think> preamble (DeepSeek-R1, QwQ,
    many OpenRouter / local models send it INSIDE the content). Its braces
    used to be taken for the JSON and the whole reply read as unstructured."""
    t = _THINK_RE.sub("", text or "")
    low = t.lower()
    for tag in ("</think>", "</thinking>", "</reasoning>"):
        i = low.rfind(tag)
        if i != -1:                       # a close with no open: keep what follows
            t = t[i + len(tag):]
            low = t.lower()
    for tag in ("<think>", "<thinking>", "<reasoning>"):
        if low.lstrip().startswith(tag):  # never closed (cut off): skip to the JSON
            m = _IDEAS_KEY_RE.search(t)
            t = t[m.start():] if m else ""
            break
    return t


def _close_truncated_json(text):
    """Best-effort repair of a reply that was CUT OFF mid-JSON (the provider's
    output cap). Walks the text tracking strings and brackets, then retries
    the parse from the end backwards: cut after the last complete member,
    close every open bracket. Returns the parsed object or None."""
    stack, cuts = [], []
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            cuts.append((i + 1, tuple(stack)))
        elif ch == ",":
            cuts.append((i, tuple(stack)))
    for pos, st in reversed(cuts[-400:]):
        if not st:
            chunk = text[:pos]
        else:
            chunk = text[:pos] + "".join(reversed(st))
        try:
            return json.loads(_TRAILING_COMMA_RE.sub(r"\1", chunk))
        except Exception:
            continue
    return None


def _loads_lenient(candidate):
    """json.loads, then the cheap fixes models need most often: trailing
    junk after the object, trailing commas, and a truncated tail."""
    try:
        return json.loads(candidate)
    except Exception:
        pass
    try:
        return json.JSONDecoder().raw_decode(candidate)[0]
    except Exception:
        pass
    try:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", candidate))
    except Exception:
        pass
    return _close_truncated_json(candidate)


def _content_lab_parse_ideas_response(raw, n_expected=3, format="short"):
    text = _strip_reasoning((raw or "").strip()).strip()
    # code fences anywhere in the reply, not only at the very start
    if "```" in text:
        import re as _re
        m = _re.search(r"```(?:json|JSON)?\s*(.*?)(?:```|$)", text, _re.S)
        if m and "{" in m.group(1):
            text = m.group(1).strip()
    m = _IDEAS_KEY_RE.search(text)
    start = m.start() if m else text.find("{")
    if start == -1:
        start = text.find("[")
    candidate = text[start:] if start != -1 else text
    data = _loads_lenient(candidate) if candidate else None
    try:
        if isinstance(data, dict):
            ideas_raw = data.get("ideas")
            if ideas_raw is None and data.get("title"):
                ideas_raw = [data]          # a model that sent one idea bare
        elif isinstance(data, list):
            ideas_raw = data
        else:
            ideas_raw = None
        ideas_raw = ideas_raw if isinstance(ideas_raw, list) else []
        cleaned = [_content_lab_clean_idea(i, format) for i in ideas_raw[:max(n_expected, 1)]
                   if isinstance(i, dict) and str(i.get("title") or "").strip()]
        if cleaned:
            # Freshly generated, so by definition written to the current
            # schema — the model never sends schema_version itself, and
            # _content_lab_clean_idea has to assume 1 for anything that
            # doesn't (that's how stored v1 records are detected).
            for c in cleaned:
                c["schema_version"] = CONTENT_LAB_SCHEMA_VERSION
            return cleaned
    except Exception:
        pass
    # Fallback: surface *something* rather than a bare error after the user
    # waited on a real AI call.
    d = _content_lab_default_idea(format)
    d["title"] = tr("AI response (unstructured)")
    d["source"] = text[:400]
    d["parse_failed"] = True
    return [d]


_PERSIAN_RE = __import__("re").compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def _is_persian(text):
    """A brief written in Persian gets a Persian blueprint even on an English
    UI — an idea handed over from a Persian chat must not come back English."""
    t = text or ""
    letters = sum(1 for ch in t if ch.isalpha())
    fa = len(_PERSIAN_RE.findall(t))
    return bool(letters) and fa >= max(4, 0.3 * letters)


# v1.0.4 — the creator profile ("My channel" on the AI Ideas page). Tokens are
# fixed English keys (stored in config); the model gets a plain sentence each.
# v1.0.6: + channel type, stage, voice and "what already works".
CREATOR_CONSTRAINTS = {
    "faceless": "Faceless channel: the creator never appears on camera. No talking-head shots.",
    "solo": "One person does everything (script, shoot, edit). No crew, no second host.",
    "phone": "Shoots on a phone only. No drone, no studio lighting, no special lenses.",
    "quick_edit": "Edits must be quick: under ~2 hours per video, simple cuts and captions.",
    "no_voice": "No voiceover: tell the story with on-screen text, captions and music.",
    "clips": "Clip / compilation channel: builds on existing footage, adds framing and commentary.",
    "home": "Films at home / in one location. No travel, no outdoor set-ups, no booked venues.",
    "no_budget": "Zero budget: nothing to buy, rent or book; only what the creator already has.",
    "no_script": "Does not read scripts: lines must work as short talking points to say naturally.",
    "family": "Family-friendly: no swearing, no shock or gore, nothing a parent would skip.",
}

# What the videos LOOK like. Picking one is the biggest single lift in fit:
# the model no longer has to guess whether "travel" means a talking-head
# vlog, a silent 4K walk or a faceless explainer.
CREATOR_TYPES = {
    "talking_head": "Talking head: the creator speaks to camera; the face and voice carry the video.",
    "vlog": "Vlog: follows the creator's real day / trip / project, filmed as it happens.",
    "walk": "Walking tour / POV: the camera walks a real place with little or no talking; "
            "the route, the ambient sound and on-screen labels carry it.",
    "tutorial": "Tutorial / how-to: teaches a skill or fixes a problem step by step (tech, DIY, crafts, beauty).",
    "cooking": "Cooking / recipes: the dish, the ingredients and the steps, close-up.",
    "education": "Education / lessons: teaches a subject or a language; clarity, examples and practice moments.",
    "faceless": "Faceless voice-over: narration over B-roll, stock, screen recordings or animation.",
    "compilation": "Compilation / edit: built from existing clips (reactions, rankings, highlights, memes).",
    "gaming": "Gaming: gameplay footage with commentary, challenges or guides.",
    "review": "Reviews / unboxing: products, places or services, judged honestly.",
    "story": "Storytelling / commentary: a narrated story, case, documentary or opinion piece.",
    "music": "Music / ASMR / ambience: sound and visuals first, few or no words.",
    "podcast": "Podcast / interview: conversation between people, often clipped into Shorts.",
}

CREATOR_STAGES = {
    "new": "New channel (under ~1K subscribers): almost no browse / suggested traffic yet. Favour clear, "
           "searchable topics and titles over curiosity-only packaging; one idea per video.",
    "growing": "Growing channel (~1K-100K subscribers): a returning audience exists. Balance search and "
               "browse, and build repeatable series viewers can binge.",
    "established": "Established channel (100K+ subscribers): strong browse and returning viewers. Packaging "
                   "and novelty matter most; keep the channel's recognisable format.",
}

CREATOR_TONES = {
    "energetic": "Energetic: fast, punchy, high-energy delivery.",
    "calm": "Calm: slow, relaxed, soothing delivery; no shouting, no hype.",
    "funny": "Funny: humour first; jokes, timing and playful edits.",
    "serious": "Serious: credible, precise, no clickbait tone.",
    "cinematic": "Cinematic: visual, moody, music-led storytelling.",
    "warm": "Warm: friendly and personal, like talking to a friend.",
}


def creator_fact_lines(creator):
    """The profile as plain fact lines ("- Channel: ..."), shared by the
    greenlight prompt and AI Chat. [] when the profile says nothing."""
    c = creator if isinstance(creator, dict) else {}
    niche = str(c.get("niche") or "").strip()[:400]
    audience = str(c.get("audience") or "").strip()[:300]
    proven = str(c.get("proven") or "").strip()[:400]
    ctype = CREATOR_TYPES.get(str(c.get("type") or ""))
    stage = CREATOR_STAGES.get(str(c.get("stage") or ""))
    tone = CREATOR_TONES.get(str(c.get("tone") or ""))
    rules = [CREATOR_CONSTRAINTS[t] for t in (c.get("constraints") or [])
             if t in CREATOR_CONSTRAINTS]
    # v1.0.7: "Other" format and limits in the creator's own words
    type_custom = str(c.get("type_custom") or "").strip()[:120]
    if str(c.get("type") or "") == "other":
        ctype = (f"{type_custom} (the creator's own description of the format; "
                 "treat it like any listed format)") if type_custom else None
    limits_custom = str(c.get("limits_custom") or "").strip()[:300]
    out = []
    if ctype:
        out.append(f"- Format: {ctype}")
    if niche:
        out.append(f"- Channel: {niche}")
    if audience:
        out.append(f"- Audience: {audience}")
    if stage:
        out.append(f"- Stage: {stage}")
    if tone:
        out.append(f"- Voice: {tone}")
    if proven:
        out.append(f"- Already works on this channel (lean on it): {proven}")
    for r in rules:
        out.append(f"- Hard constraint: {r}")
    if limits_custom:
        out.append(f"- Hard constraint (in the creator's words): {limits_custom}")
    return out


def creator_block(creator):
    """The creator profile as a prompt block, or "" when it says nothing.

    `creator` is {"type", "niche", "audience", "stage", "tone", "proven",
    "constraints": [token, ...]}. It is stated as hard facts about who is
    making the video, because a blueprint that ignores them (a face-cam idea
    for a faceless channel, a drone shot for a phone shooter, a script to read
    for a walking-tour channel) is a blueprint the creator cannot make."""
    lines = creator_fact_lines(creator)
    if not lines:
        return ""
    out = ["\n\nTHE CREATOR — facts about who will make this video. Every blueprint must fit them:"]
    out.extend(lines)
    out.append("If a reference video relies on something these facts rule out, keep its "
               "pattern and find a way to execute it within them. When a Format is given, "
               "\"channel_type\" must name that format.")
    return "\n".join(out)


def generate_content_lab_ideas(videos, client, n=3, avoid_titles=None, format="short",
                               brief="", brief_mode="videos", csv_sources=None,
                               seed_context="", creator=None, out_lang=""):
    """Public entry point — see _generate_content_lab_batch for the prompt.

    v5 asks for roughly 30% more per blueprint than v4, and one reply carrying
    four full dossiers either hit the provider's output cap (→ "unstructured")
    or came back with blueprints 3 and 4 visibly thinner than 1 and 2. So the
    run is split into batches of _BATCH; every later batch is told the titles
    the earlier ones already used, so the set stays distinct. A failure after
    the first batch keeps what already arrived instead of throwing it away."""
    try:
        n = max(1, int(n or 1))
    except (TypeError, ValueError):
        n = 1
    out = []
    avoid = [t for t in (avoid_titles or []) if t]
    remaining = n
    # Gemini has a 64k output window; most OpenAI-compatible / custom models
    # stop at 4-8k. One greenlight per call keeps every reply inside that cap.
    batch_size = _BATCH if getattr(client, "api_mode", "") == "google_genai" else 1
    while remaining > 0:
        k = min(batch_size, remaining)
        try:
            batch = _generate_content_lab_batch(
                videos, client, n=k, avoid_titles=avoid, format=format,
                brief=brief, brief_mode=brief_mode, csv_sources=csv_sources,
                seed_context=seed_context, creator=creator, out_lang=out_lang)
        except RequestCancelled:
            # Stop means stop: never hand back a half run as if it finished.
            raise
        except Exception:
            if not out:
                raise
            break
        if out and batch and batch[0].get("parse_failed"):
            break
        out.extend(batch)
        avoid = avoid + [b.get("title", "") for b in batch if b.get("title")]
        remaining -= k
        if batch and batch[0].get("parse_failed"):
            break
    return out


def _generate_content_lab_batch(videos, client, n=3, avoid_titles=None, format="short",
                                brief="", brief_mode="videos", csv_sources=None,
                                seed_context="", creator=None, out_lang=""):
    """Asks the configured AI provider for `n` full content 'blueprints' and
    returns a list of cleaned idea dicts — see _content_lab_clean_idea() for
    the exact shape, and CONTENT_LAB_SCHEMA_VERSION for what changed in v2.

    A blueprint covers the whole distance from idea to shoot: viral angle, two
    hook options of each kind, three thumbnail prompts, the verbatim timecoded
    script, a shot list with B-roll cover marked, what the reference videos
    already do and the gap they leave, execution and packaging notes, two CTA
    options, and a retention-pacing outline (plus SEO on long-form).

    Metrics are deliberately absent from the ask. The model is told in as many
    words not to return view estimates, projected reach, similarity scores or
    trend-audio counts: it has nothing to derive them from, and a plausible
    fabricated number is more harmful than a missing one. The real numbers a
    blueprint sits next to come from the reference rows the user ticked.

    `brief` is the creator's own idea, in their own words, and `brief_mode`
    decides how it relates to the reference videos:

      "videos" — ignore the brief; reverse-engineer the pattern the reference
                 videos share (the original behaviour, and still the default so
                 existing callers are unaffected).
      "blend"  — the brief is the subject; the reference videos are only a
                 pattern to borrow hook style / pacing / packaging from.
      "brief"  — the brief is all there is; no reference videos are consulted.
      "seeded" — the idea came from AI Chat, where it was derived FROM the
                 reference videos. The idea is fixed (topic, angle, format);
                 the videos are its evidence and its source material, and
                 `seed_context` carries the chat's real data (stats,
                 transcript openings, what the creator said about their
                 channel). Every blueprint executes THAT idea, and nothing
                 about real people may be invented.

    The three are genuinely different asks, not the same prompt with a line
    bolted on: in "blend" the model must not drift off the creator's topic
    toward the references' topic, and in "brief" there is no pattern to study
    at all, so the framing has to change rather than accumulate.
    """
    brief = (brief or "").strip()
    seed_context = (seed_context or "").strip()
    csv_sources = [c for c in (csv_sources or []) if c and c.get("block")]
    if brief_mode not in ("videos", "blend", "brief", "seeded"):
        brief_mode = "videos"
    # A mode that needs a brief but wasn't given one degrades to the reference-
    # video framing rather than sending the model an empty instruction.
    if brief_mode in ("blend", "brief", "seeded") and not brief:
        brief_mode = "videos"
    seeded = brief_mode == "seeded"
    if seeded:
        # From here on a seeded run reads as "blend" (with references) or
        # "brief" (without) everywhere the prompt only cares about that split;
        # the intro below is what makes it different.
        brief_mode = "blend" if (videos or csv_sources) else "brief"
    # ...and "videos"/"blend" both need references; without any, only the
    # brief-only framing can produce anything.
    if not videos and not csv_sources and brief:
        brief_mode = "brief"

    lines = []
    for i, v in enumerate(videos, 1):
        fmt = tr("Shorts") if v.get("is_short") else tr("Long-form")
        subs = v.get("subscriber_count")
        subs_txt = f"{subs:,}" if isinstance(subs, int) else "unknown"
        mult = v.get("multiplier")
        mult_txt = f" — {mult}× its channel's typical views" if isinstance(mult, (int, float)) and mult else ""
        lines.append(
            f'[V{i}] "{v.get("title", "")}" — channel: {v.get("channel_title", "—")} — '
            f'{int(v.get("view_count", 0) or 0):,} views — {fmt} — '
            f'{subs_txt} subscribers{mult_txt}')
    videos_block = "\n".join(lines) or "(no reference videos provided)"

    # ── v4: channel data from CSV exports ──
    # Real scraped rows, reduced to numbers in Python. When the creator picked
    # exports but no videos, the exports ARE the reference set; with videos too,
    # they are the audience/performance evidence the videos are read against.
    csv_block = ""
    if csv_sources:
        parts = [f"--- EXPORT {k}: {c.get('name', '')} ---\n{c['block']}"
                 for k, c in enumerate(csv_sources, 1)]
        csv_block = (
            "\n\nCHANNEL DATA — real exported scrape data the creator selected "
            f"({len(csv_sources)} file(s)). Treat these numbers as facts about what "
            "this audience actually watches: which topics, title patterns, lengths and "
            "publish days over-perform. Ground the angle, hooks, titles, length and "
            "publish plan in them, and cite them in \"evidence\".\n\n"
            + "\n\n".join(parts))
    has_refs = bool(videos) or bool(csv_sources)

    avoid_block = ""
    if avoid_titles:
        avoid_block = ("\n\nThese ideas already exist in this workspace — propose something "
                        "meaningfully different, not a reword of any of them:\n" +
                        "\n".join(f"- {t}" for t in avoid_titles))

    # v1.0.4: the creator can pin the output language ("fa" / "en"); "" keeps
    # the old rule (Persian UI or a Persian brief -> Persian blueprint).
    out_lang = (out_lang or "").strip().lower()
    want_fa = out_lang == "fa" or (out_lang != "en" and (i18n.LANG == "fa" or _is_persian(brief)))
    lang_line = ("\nWrite every text field in Persian (Farsi) — titles, hooks, reasoning, "
                 "everything — except the \"score_label\" and \"focus_tag\" fields, which "
                 "should stay short punchy English tags (e.g. \"EXPLOSIVE\", \"Curiosity Gap\"). "
                 "The fixed-choice \"difficulty\" field keeps its exact English value."
                 if want_fa else
                 ("\nWrite every text field in English, even where the inputs are in another language."
                  if out_lang == "en" else ""))
    creator_txt = creator_block(creator)

    is_long = format == "long"
    # In brief-only mode there are no reference videos to describe, so the
    # format instruction can't open with "These are all ..." — it becomes a
    # plain target-length instruction instead. Same when the only inputs are
    # CSV exports (there are no picked videos to call "these").
    # A seeded idea already fixed its own format, so the references' majority
    # is not described as "these are all ..." (they may not be).
    if brief_mode == "brief" or not videos or seeded:
        format_line = (
            f"Design {n} NEW long-form video blueprints (roughly 6-20 minutes) with chapter-style "
            f"pacing — not Shorts."
            if is_long else
            f"Design {n} NEW short-form/Shorts blueprints (roughly 20-75 seconds) with a fast, "
            f"single-scene beat-by-beat pace — not long-form videos."
        )
    else:
        format_line = (
            f"These are all LONG-FORM videos (multiple minutes each), so design {n} NEW long-form "
            f"video blueprints (roughly 6-20 minutes) with chapter-style pacing — not Shorts."
            if is_long else
            f"These are all SHORT-FORM videos (under ~3 minutes, mostly under a minute), so design "
            f"{n} NEW short-form/Shorts blueprints (roughly 20-75 seconds) with a fast, single-scene "
            f"beat-by-beat pace — not long-form videos."
        )

    if is_long:
        retention_seconds_hint = "6-20 minutes, i.e. 360-1200"
        retention_phase_count_hint = "4-6"
        retention_phase_label_hint = '"Hook"/"Chapter 1"/"Chapter 2"/"Payoff"/"CTA"'
    else:
        retention_seconds_hint = "20-75"
        retention_phase_count_hint = "3-5"
        retention_phase_label_hint = '"Hook"/"Build-up"/"Payoff"/"Loop"'

    seo_field = ""
    seo_shape = ""
    if is_long:
        # v10: the keywords ARE discovery.search_queries (asked once, below);
        # tags barely move anything on today's YouTube, so 0-5 variants only;
        # the description's first two lines are what search and Home show.
        seo_field = (
            '\n- "seo": {"description": a ready-to-paste YouTube description of 2-3 sentences whose '
            'FIRST TWO LINES are written for search results and Home (they are all that shows): open '
            'with the promise and the main search phrase, never "In this video…"; end with a soft '
            'call-to-action, "tags": 0-5 tags, ONLY misspellings / alternate names of the main term '
            '([] if none), "timestamps": array of 4-7 objects {"time": "0:00"-style timestamp, '
            '"label": a chapter name that contains a real search word} matching "retention.phases".}'
        )
        seo_shape = ('\n,"seo":{"description":"...","tags":["..."],'
                      '"timestamps":[{"time":"0:00","label":"..."}]}')

    # ── framing: the three brief modes are genuinely different asks ──
    # Each builds the whole opening of the prompt, rather than appending a
    # "and also consider this" line to one shared intro. In "blend" the
    # creator's topic is the subject and the references are only a style
    # source, so the model has to be told explicitly not to drift toward the
    # references' topic — the single most common failure here.
    if seeded:
        format_line = format_line.replace(" NEW ", " ")
    seed_block = ""
    if seed_context:
        seed_block = "\n\n" + seed_context[:14000]
    if seeded:
        refs_line = (
            f"It was derived from these {len(videos)} reference videos in the creator's own data — "
            f"they are the EVIDENCE for the idea and, where the idea uses their footage or topic, its "
            f"SOURCE MATERIAL:\n\n{videos_block}"
            if videos else
            ("It was derived from the channel data below." if csv_sources else
             "No reference videos were attached in the chat."))
        intro_block = f"""You are an elite YouTube content strategist and video producer. In a coaching
session, the creator and their AI producer already chose THIS video idea from the creator's data:

\"\"\"
{brief}
\"\"\"

{refs_line}{seed_block}

{format_line}

The idea above is FIXED: keep its topic, its angle, its format and its core hook — do not swap it
for a different idea, do not generalise it into a vaguer one, and do not drift onto another topic.
Design exactly {n} production-ready "content blueprints" for it. Blueprint 1 is the most faithful,
strongest execution of the idea exactly as written; any further blueprints keep the same idea and
vary only the hook, structure or packaging.

Quality bar — every blueprint must be something the creator can actually make this week:
- Build it from the real material above. When transcripts are given, the hook, script lines and
  on-screen text must use REAL lines/moments from them (quote them), not invented ones.
- Real people (streamers, athletes, celebrities): never invent quotes, replies, events, feuds or
  outcomes. If the idea is a reaction/clip/compilation of real footage, the script describes which
  real moment to cut in and what the creator adds (framing, captions, commentary, ranking logic) —
  not a fictional story. Never imply something happened that the data does not show.
- Be concrete: name the exact moments/segments to use, the exact on-screen text, the exact title.
  No generic advice ("make it engaging", "use trending audio").
- Respect the creator's constraints stated above (faceless, clip channel, language, gear).{avoid_block}"""
    elif brief_mode == "brief":
        intro_block = f"""You are an elite YouTube content strategist and video producer. A creator
described the video idea they want to make, in their own words:

\"\"\"
{brief}
\"\"\"

{format_line}

Take their idea seriously as the subject — do not replace it with a different
topic — and design exactly {n} distinct, original video "content blueprints" that each execute
it a different way (different angle, hook and structure on the same core idea).{avoid_block}"""
    elif brief_mode == "blend":
        blend_refs_line = (
            f"They also hand-picked {len(videos)} reference videos, not because they want the same "
            f"topic, but because these performed well and they like how they are made:\n\n{videos_block}"
            if videos else
            "They did not pick reference videos; instead the channel data below is their style and "
            "audience source.")
        intro_block = f"""You are an elite YouTube content strategist and video producer. A creator
described the video idea they want to make, in their own words:

\"\"\"
{brief}
\"\"\"

{blend_refs_line}

{format_line}

The creator's idea above is the SUBJECT — every blueprint must be about it. The reference videos
are only a STYLE source: study their hook style, pacing, title pattern, thumbnail style and
audience, then apply that packaging to the creator's own idea. Do not drift onto the reference
videos' topics, and do not rehash any single one of them. Design exactly {n} distinct, original
"content blueprints" that execute the creator's idea in that style.{avoid_block}"""
    elif not videos and csv_sources:
        intro_block = f"""You are an elite YouTube content strategist and video producer. A creator
handed you scraped data from one or more YouTube channels (below) instead of hand-picked videos.
{csv_block}

{format_line.replace("These are all", "The data is mostly")}

Find what genuinely over-performs in this data — topic angle, title pattern, length, hook style,
publish timing — and design exactly {n} NEW, original video "content blueprints" built on those
proven patterns. Do not copy any single video; synthesize a fresh angle the data supports.{avoid_block}"""
        csv_block = ""   # already inlined above
    else:
        intro_block = f"""You are an elite YouTube content strategist and video producer. Below are
{len(videos)} reference videos a creator hand-picked as inspiration because they performed well:

{videos_block}

{format_line}

Study what these videos have in common — topic angle, hook style, pacing, title pattern,
thumbnail style, audience — and design exactly {n} NEW, original video "content blueprints"
inspired by that pattern. Do not copy or rehash any single reference video; synthesize a fresh
angle from the pattern instead.{avoid_block}"""

    # "source" is the one field whose meaning depends on the mode: with no
    # reference videos there is no pattern to have reverse-engineered, so
    # asking for one invites the model to invent a citation.
    if seeded:
        source_line = ('- "source": one short sentence naming which reference video(s)/moment(s) from the '
                       'data above this blueprint is built on (or "the creator\'s idea" if none).')
    elif brief_mode == "brief":
        source_line = ('- "source": one short sentence naming the creative angle this blueprint '
                       'takes on the creator\'s idea.')
    elif brief_mode == "blend":
        source_line = ('- "source": one short sentence naming which reference video\'s style/'
                       'pattern this blueprint borrows its packaging from.')
    else:
        source_line = ('- "source": one short sentence naming the pattern/reference this was '
                       'reverse-engineered from.')

    # The "reasoning" fields ask the model to justify its choices against
    # something. With no reference videos there is no "pattern above" to point
    # at, so the clause is swapped rather than left dangling.
    reasoning_ref = ("" if brief_mode == "brief"
                     else ", referencing the pattern above")
    execution_ref = ("tying the execution choices back to the creator's idea"
                     if brief_mode == "brief"
                     else "tying the execution choices back to the pattern")

    # ── v2 field hints, sized to the format ──
    # A Short's script is 4-6 timecoded beats; a 12-minute video's is 6-8
    # chapter-level narration blocks. Asking for the same count of either
    # produces a script that is useless at one of the two lengths.
    if is_long:
        script_hint = ("8-12 objects, one per chapter/segment, covering the WHOLE video with no "
                       "time gaps, {\"time\": a range like \"4:00–7:30\", \"speaker\": \"VO\" or "
                       "\"On-camera\", \"line\": what is actually SAID in that segment, written out "
                       "as speech (3-5 sentences, ready to read off a teleprompter — never a "
                       "description of what happens), \"visual\": what is on screen meanwhile "
                       "(camera framing, screen recording, B-roll, graphic) in one sentence}")
        shot_hint = ("the CAPTURE list: 5-8 objects covering the whole video, {\"shot\": a short code "
                     "like \"A1\", \"type\": what kind of material, in THIS format's terms (e.g. "
                     "\"Talking head\", \"Close-up of the pan\", \"Route stop\", \"Screen "
                     "recording\", \"Source clip\", \"Stock footage\", \"Gameplay\"), \"desc\": "
                     "what exactly to film, record or gather and how (framing, angle, movement, "
                     "or which clip / search term), \"beat\": the script time range it covers, "
                     "\"broll\": true only for material whose job is covering cuts}")
        cta_hint = ('2 objects: one MID-VIDEO ask placed right after the biggest payoff, and one '
                    'end-screen ask that teases a specific follow-up video rather than a generic '
                    '"subscribe". {"label": short name, "text": exactly what to ask for and how, '
                    '"timing": when in the video}')
    else:
        script_hint = ("6-9 objects, one per beat, covering the WHOLE video with no time gaps, "
                       "{\"time\": a range like \"0:00–0:03\", \"speaker\": \"VO\" or "
                       "\"On-screen\" or \"Music\", \"line\": ONLY the exact words spoken or "
                       "shown as text in that beat — verbatim, ready to read; for a music-only beat "
                       "with no words, leave it \"\", \"visual\": what the viewer SEES in that beat "
                       "(the clip, the framing, the cut) in one sentence — stage directions go "
                       "here, never in \"line\"}")
        shot_hint = ("the CAPTURE list: 3-6 objects, {\"shot\": a short code like \"A1\", \"type\": "
                     "what kind of material in THIS format's terms (e.g. \"Close-up\", \"POV walk\", "
                     "\"Screen recording\", \"Source clip\", \"Stock footage\", \"Face cam\"), "
                     "\"desc\": what exactly to film, record or gather and how (framing, angle, "
                     "movement, or which clip / search term), \"beat\": the script time range it "
                     "covers, \"broll\": true only for material whose job is covering cuts}")
        cta_hint = ('2 objects offering genuinely different asks (e.g. a comment-bait line vs. an '
                    'implicit loop that needs no spoken CTA at all). {"label": short name, "text": '
                    'exactly what to ask for and how, "timing": when in the video}')

    # ── anti-hook (v10: both formats) ──
    # v1-v9 asked for a reverse-psychology opener on Shorts only, and v8
    # stopped asking at all (the "spoken" hooks already carry a reverse-
    # psychology option). v10 brings it back as the opposite of hype: a RAW &
    # HONEST start that builds trust, which works on a 10-minute documentary
    # as well as on a Short.
    anti_hook_field = (
        '\n- "anti_hook": a RAW & HONEST alternative opening, the opposite of a hype hook: no '
        'exaggeration, no fake urgency. Either a plain admission ("I got this wrong for two years"), '
        'a cold open dropped straight into the tension, or a skeptic-defuser for viewers tired of '
        'clickbait ("this isn\'t a hack, it\'s three weeks of boring work, here\'s all of it"). '
        '{"focus_tag": the technique in 1-3 words (e.g. "Cold open", "Honest admission", '
        '"Skeptic defuser"), "options": exactly 2 DIFFERENT opening lines, ready to say or show, '
        'genuinely different from the "hook" options, "reasoning": one sentence on why trust beats '
        'hype for this audience.}')
    anti_hook_shape = (',"anti_hook":{"focus_tag":"...","options":["...","..."],"reasoning":"..."}')

    # The competitor rows must describe REAL videos wherever the user supplied
    # them. Without references there is nothing real to name, so the ask
    # becomes "the kind of video that already exists" rather than a citation
    # the model would otherwise fabricate.
    if not has_refs:
        competitors_hint = (
            '{"rows": array of 3 objects describing the KINDS of video that already cover this '
            'space — {"name": a description of that type of video, NOT an invented specific '
            'video title, "angle": the angle those take, "gap": what they consistently fail to '
            'do}, "gap": one short paragraph naming the specific opening left for this blueprint '
            'and how it should exploit it.}'
        )
    else:
        competitors_hint = (
            '{"rows": array of 3 objects, each about one of the REFERENCE VIDEOS LISTED ABOVE '
            '(the [V#] videos and/or the [C#] export videos) — {"ref": its id exactly as given, '
            'e.g. "V2" or "C1", "name": that video\'s actual title as given above, "angle": the '
            'angle it takes, "gap": what it does badly or leaves out}. Use only the videos listed '
            'above; never invent a video. "gap": one short paragraph naming the specific opening '
            'those videos leave and how this blueprint should exploit it.}'
        )

    # ── v4 fields: the "anyone can pick this up" pack ──
    if has_refs:
        evidence_hint = (
            '2-4 objects {"claim": one concrete, data-backed reason this idea should work, quoting '
            'the real number it rests on (views, multiplier, length, weekday) from the data above, '
            '"ref": the id of the video it rests on exactly as given above, e.g. "V1" or "C3"}. '
            'Only cite videos listed above; if a claim has no row behind it, leave it out')
    else:
        evidence_hint = (
            '1-3 objects {"claim": a concrete reason this idea should work for this audience, '
            '"ref": ""} — no invented statistics')
    # v7: YouTube's own format funnel — a long video feeds Shorts, a Short
    # points at a long video. Asked per format, because the two jobs differ.
    if is_long:
        funnel_hint = ('{"clips": 2-3 objects {"from": the time range in THIS video to cut into a '
                       'Short, "hook": the first line of that Short}, "link_to": the playlist or '
                       '"series_next" video this one should send viewers to, "why": one sentence}')
    else:
        funnel_hint = ('{"clips": [], "link_to": the long-form video (a "series_next" title is '
                       'ideal) this Short should link as its Related video, "why": one sentence on '
                       'why a viewer of this Short wants it}')
    rehook_count = "4-6" if is_long else "2-3"
    if is_long:
        caption_field = ""
        caption_shape = ""
    else:
        caption_field = (
            '\n- "caption_pack": {"description": a ready-to-paste 1-2 line Shorts caption that '
            'adds context without spoiling the payoff, "hashtags": 3-6 hashtags this audience '
            'actually searches, each starting with #}')
        caption_shape = ',"caption_pack":{"description":"...","hashtags":["#...","#..."]}'
    # ── v8 "Essentials" (v1.0.5) ──────────────────────────────────────────
    # v7 asked for ~40 fields per greenlight. Most were niche (gear lists,
    # sponsor fit, reply banks, fact checks, end-screen cards…), many only fit
    # one kind of channel, and together they pushed one reply past 9k tokens —
    # which is exactly why custom / OpenAI-compatible providers with an 8k
    # output cap came back "unstructured". v8 asks only for what helps EVERY
    # channel type (talking head, faceless voice-over, edit/compilation, music,
    # tutorial/cooking, gaming): the angle, the hooks, the title + thumbnail,
    # the words, the caption/SEO, the risks and the next video.
    if is_long:
        caption_field = ""
        caption_shape = ""
    else:
        caption_field = (
            '\n- "caption_pack": {"description": a ready-to-paste 1-2 line Shorts caption that '
            'adds context without spoiling the payoff, "hashtags": 3-6 hashtags this audience '
            'actually searches, each starting with #}')
        caption_shape = ',"caption_pack":{"description":"...","hashtags":["#...","#..."]}'
    rehook_count = "4-6" if is_long else "2-3"
    loop_count = "2" if is_long else "1"
    # v10: a Short must loop; a long video does not
    loop_line = ('', ',"loop":"..."') if is_long else (
        ', "loop": how the LAST beat flows straight back into the first line so a rewatch feels '
        'seamless (the exact last words + why they connect)', ',"loop":"..."')
    spoken_window = "minute" if is_long else "5 seconds"
    loop_shape = "" if is_long else loop_line[1]
    evidence_field = (f'\n- "evidence": {evidence_hint}' if has_refs else "")
    evidence_shape = (',"evidence":[{"claim":"...","ref":"V1"}]' if has_refs else "")

    prompt = f"""{intro_block}{creator_txt}{csv_block}

ADAPT TO THE CHANNEL TYPE. Use the creator's Format when the profile gives one; otherwise decide
silently what kind of channel makes this video — from the idea and the references: talking-head,
vlog, walking tour / POV, tutorial/how-to, cooking, education / language lessons, faceless
voice-over, edit/compilation of existing footage, gaming, review, storytelling / commentary,
music / ASMR, podcast / interview. Then write every field for THAT channel:
- Vlogs: the script is talking points and moments to catch on the day, not a speech; the
  capture list names real moments (arrival, the problem, the reveal), not studio shots.
- Walking tours / POV: the route IS the structure — stops in order, what each one shows, on-screen
  labels (place names, prices, tips) and ambient sound; little or no narration, "line" may be the
  on-screen text.
- Education / language lessons: one clear learning goal, real examples, a practice / quiz moment
  the viewer answers, and the exact words or phrases taught shown on screen.
- Reviews: the verdict early, honest pros and cons, real use, the price/value question.
- Podcasts / interviews: the questions to ask, the moment to clip, and the guest's angle.
- Music / no-voice channels: the "hook" lines and "full_script" lines are on-screen text and visual
  beats, never narration; "line" is "" for music-only beats.
- Tutorials and cooking: show the finished result in the first seconds, titles must be searchable
  (the dish / the problem in plain words), the script walks the real steps in order.
- Edit / compilation / clip channels: name the exact moments to cut in and what the creator adds
  (ranking, captions, commentary); include a copyright / reused-content risk when it applies.
- Faceless: no face on camera anywhere; the hook is carried by the first frame and the first line.

For EACH blueprint, return an object with exactly these fields:
- "title": a short, punchy working title.
- "score_breakdown": {{"hook": {{"points": 0-20, "why": one short sentence}}, "retention": {{...}},
  "packaging": {{...}}, "novelty": {{...}}, "feasibility": {{...}}}} — five honest 0-20 marks:
  hook = how hard the first 3 seconds stop the scroll, retention = whether the structure holds to
  the end, packaging = title + thumbnail click pull, novelty = how different it is from what already
  exists (the references included), feasibility = how reliably this creator can actually make it.
  Be strict: 18-20 only when the data above proves it, 10-13 is ordinary. Most ideas land 55-75.
- "score": exactly the sum of the five score_breakdown points.
- "score_label": one short punchy tag summarizing that score (e.g. "EXPLOSIVE", "STRONG", "SOLID").
- "hook_preview": the single best opening line (said, or shown as text), ready to use.
{source_line}
- "audience": one short phrase naming who this is for — the specific viewer, not "everyone".
- "pillar": the content pillar/category this belongs to, 2-4 words.
- "difficulty": exactly one of "Easy", "Medium", "Hard" — how much footage and editing work it takes.
- "channel_type": 1-3 words naming the format this greenlight is written for (e.g. "Cooking",
  "Walking tour", "Faceless explainer", "Language lesson", "Vlog").
- "core_promise": {{"promise": the ONE thing the title, thumbnail and hook all promise, in one
  sentence, "payoff": how and where the video delivers it, "payoff_time": the timestamp it lands,
  "stakes": what the viewer loses, risks or misses by NOT watching (concrete, one sentence){loop_line[0]}}}.
- "target": {{"spectrum": exactly "Broad" (casual viewers found on Home / Suggested) or "Niche"
  (enthusiasts who already care), "lane": exactly one of "Browse", "Search", "Suggested" — where
  most of its views should come from, which decides how the title is written, "both": one
  sentence on how it keeps the casual viewer AND the enthusiast watching}}.
- "hook": {{"focus_tag": a short label for the main hook style (e.g. "Curiosity Gap"),
  "spoken": an array of exactly 3 DIFFERENT opening lines for the first 3 seconds, each using a
  different technique (e.g. curiosity gap, bold claim / result first, reverse psychology like
  "don't watch this if…") — ready to say, or to show as text on a no-voice channel,
  "visual": an array of exactly 2 DIFFERENT one-sentence options for the first frame on screen,
  "text": an array of exactly 2 DIFFERENT on-screen text options for the hook (readable with the
  sound off, for viewers previewing it muted), "reasoning": one sentence on why the best one works.}}{anti_hook_field}
- "titles": an array of exactly 4 titles, each UNDER 60 characters (mobile cuts the rest), key
  words first, in this order: 1) curiosity gap, 2) concrete result / number / transformation,
  3) identity call-out (names who it is for), 4) counter-intuitive / myth-breaking. No worn-out
  bait ("you won't believe", "insane", "shocking", "gone wrong").
- "thumbnails": an array of exactly 3 objects {{"label": "Concept A"/"Concept B"/"Concept C",
  "text": the 0-3 words written ON the thumbnail — it must ADD to the title, never repeat its
  words ("" for an image-only concept), "prompt": a vivid, detailed AI-image-generation prompt:
  one focal subject on a rule-of-thirds line, the expression / gaze (or an arrow) leading to the
  text, high colour contrast against YouTube's white and dark backgrounds, readable at phone size,
  and the BOTTOM-RIGHT corner kept empty (YouTube's timestamp covers it)}}. Three genuinely
  DIFFERENT approaches to A/B test, not three rewordings of one image.
- "packaging_brief": {{"pairing": one sentence on how the title and the thumbnail split the job
  (what each one says that the other does not), "stand_out": one sentence on what makes it pop
  next to the reference videos in the same feed}}
- "viral_angle": {{"concept": 2-3 sentences on the core creative concept, "triggers": array of
  2-4 short psychological-trigger phrases (e.g. "Curiosity gap", "Social proof"), "reasoning":
  2-3 sentences on WHY this angle performs{reasoning_ref}.}}{evidence_field}
- "full_script": {script_hint}
- "shot_list": {shot_hint}
- "production_plan": {{"prep": time to prepare (e.g. "20 min"), "shoot": time to film / record /
  gather, "edit": time to edit, "location": where it is made (the kitchen, the route from A to B,
  the desk / screen, "stock + screen only"), "props": 2-6 things needed (ingredients, products,
  files, sources, outfits; [] if nothing), "gear": only gear BEYOND a phone and a basic mic ([] if
  none), "checklist": 3-5 things to do before recording (permissions, charge, prep, research)}}
- "lite_version": {{"time": how long the fast version takes, "keep": what must stay or the video
  fails, "cut": what can go when time is short}}
- "funnel": {funnel_hint}
- "rehooks": {rehook_count} objects {{"time": timestamp where attention typically dips, "device":
  the re-hook technique (e.g. "Open loop", "Pattern interrupt"), "line": the exact words or
  on-screen moment}}. At least {loop_count} of them must be OPEN LOOPS: device exactly "Open loop",
  "line" = the question planted, then " → answered at m:ss" (it must be answered LATER, in the
  next phase, so leaving early means never getting the answer).
- "style_guide": {{"music": the music mood and the moment it changes, in one sentence ("none" for a
  talk-only video), "sfx": 2-4 objects {{"time": timestamp, "cue": an AUDIO cue that resets
  attention — a hard cut to dead silence, a beat drop, music in / out, a riser, a hit — and what it
  underlines}}}}
- "retention": {{"total_seconds": your estimated ideal video length in seconds ({retention_seconds_hint}), "phases": an
  array of {retention_phase_count_hint} objects {{"label": phase name e.g. {retention_phase_label_hint}, "end_pct":
  the percentage (1-100) of the video where this phase ends, "desc": one short sentence}} }}.{seo_field}{caption_field}
- "discovery": {{"search_queries": 3-5 phrases people actually type into YouTube search for this
  (plain words, no hashtags), "suggested_beside": 2 kinds of videos (or reference titles from the
  data) it should be suggested next to, "browse_trigger": one sentence on why it gets clicked on
  Home / the Shorts feed, "spoken_keywords": 3-5 key phrases the creator must SAY out loud in the
  first {spoken_window} — YouTube transcribes speech and indexes it}}
- "cta_options": {cta_hint} Never beg for a subscribe or a like: tie each ask to a real need the
  viewer has at that exact moment.
- "pinned_comment": the exact comment to pin: a debate-starting question with two clear sides
  (or an empathetic one people want to answer about themselves) — never "what do you think?".
- "end_screen": {{"end_target": the ONE video / playlist the end screen pushes (a "series_next"
  title is ideal), "line": the exact words for the last ~5 seconds that hand the viewer to it while
  a question is still open — no goodbye, no "thanks for watching", no recap}}
- "risks": an array of 2-3 objects {{"risk": what would make THIS video underperform (or get it
  claimed / demonetized), "fix": the concrete change that removes it}}. Name the thing about THIS
  idea — never generic advice like "post consistently".
- "series_next": an array of 2-3 objects {{"title": the follow-up video, "why": one line on why the
  viewer of this one wants it next}}.

Do NOT include any estimated view count, projected reach, similarity percentage, or trending-audio
usage statistic anywhere. You have no basis for those numbers and a made-up one is worse than none.
The one exception: in "evidence" you may quote a REAL number that appears in the data above.

Quality rules:
- Name exact things: the exact words, the exact overlay text, the exact clip/moment. "Make it
  engaging", "use trending music", "add B-roll" are not answers.
- Every timestamp in full_script, rehooks, shot_list, style_guide and core_promise must fall
  inside retention.total_seconds, and the script must cover the whole length.
- Title words and thumbnail text never repeat each other; the hook pays off the title's promise
  inside the first 3 seconds, the payoff lands at core_promise.payoff_time.
- Time estimates (production_plan, lite_version) are honest for THIS creator's constraints.
- Do not repeat the same sentence across fields; each field adds something new.
- Keep it tight: no field needs more than 3 sentences, except the script lines.

Respond with ONLY minified JSON, no markdown code fences, no thinking, no commentary before or
after, in exactly this shape, with exactly {n} objects in the "ideas" array:
{{"ideas":[{{"title":"...","score_breakdown":{{"hook":{{"points":14,"why":"..."}},"retention":{{"points":13,"why":"..."}},"packaging":{{"points":14,"why":"..."}},"novelty":{{"points":12,"why":"..."}},"feasibility":{{"points":16,"why":"..."}}}},"score":69,"score_label":"...","hook_preview":"...","source":"...",
"audience":"...","pillar":"...","difficulty":"Medium","channel_type":"...",
"core_promise":{{"promise":"...","payoff":"...","payoff_time":"0:40","stakes":"..."{loop_shape}}},
"target":{{"spectrum":"Broad","lane":"Browse","both":"..."}},
"hook":{{"focus_tag":"...","spoken":["...","...","..."],"visual":["...","..."],"text":["...","..."],"reasoning":"..."}}{anti_hook_shape},
"titles":["...","...","...","..."],
"thumbnails":[{{"label":"Concept A","text":"...","prompt":"..."}},{{"label":"Concept B","text":"...","prompt":"..."}},{{"label":"Concept C","text":"...","prompt":"..."}}],
"packaging_brief":{{"pairing":"...","stand_out":"..."}},
"viral_angle":{{"concept":"...","triggers":["...","..."],"reasoning":"..."}}{evidence_shape},
"full_script":[{{"time":"...","speaker":"VO","line":"...","visual":"..."}}],
"shot_list":[{{"shot":"A1","type":"...","desc":"...","beat":"...","broll":false}}],
"production_plan":{{"prep":"...","shoot":"...","edit":"...","location":"...","props":["..."],"gear":[],"checklist":["...","..."]}},
"lite_version":{{"time":"...","keep":"...","cut":"..."}},
"funnel":{{"clips":[{{"from":"...","hook":"..."}}],"link_to":"...","why":"..."}},
"rehooks":[{{"time":"...","device":"Open loop","line":"... → answered at 0:00"}}],
"style_guide":{{"music":"...","sfx":[{{"time":"...","cue":"..."}}]}},
"retention":{{"total_seconds":60,"phases":[{{"label":"...","end_pct":15,"desc":"..."}}]}}{seo_shape}{caption_shape},
"discovery":{{"search_queries":["...","..."],"suggested_beside":["...","..."],"browse_trigger":"...","spoken_keywords":["...","..."]}},
"cta_options":[{{"label":"...","text":"...","timing":"..."}}],"pinned_comment":"...",
"end_screen":{{"end_target":"...","line":"..."}},
"risks":[{{"risk":"...","fix":"..."}}],
"series_next":[{{"title":"...","why":"..."}}]}}]}}{lang_line}"""

    # v2 asks for a full script, a shot list and a competitor table on top of
    # what v1 wanted, so one blueprint is roughly twice the output it used to
    # be. The per-idea budget and the timeout both go up to match — the old
    # 2200/idea ceiling truncated a v2 reply mid-JSON, which the parser could
    # only report as "unstructured".
    # v4 adds the audience / re-hook / evidence / monetization / repurpose pack
    # (~900 tokens per blueprint) and the CSV context makes the prompt longer.
    # v5 adds the style / production / launch pack (~1300 tokens more per
    # blueprint); batches are capped at _BATCH so this stays under 16k for the
    # common two-per-call case.
    timeout = (540 if n > 1 else 360) + (60 if csv_sources else 0)
    # v6 adds the why-now / discovery / delivery / end-screen / grow pack
    # (~800 tokens more per blueprint).
    # v7: four fields dropped, four added — roughly +400 per greenlight.
    # v8 (Essentials): ~3.5-5k tokens per greenlight (Persian costs more).
    # v9 (Universal): +~700 tokens per greenlight (capture list, prep, promise,
    # fast version, funnel).
    # v10 (Masterplan): +~500 tokens per greenlight (target, stakes, raw
    # anti-hook, packaging pairing, audio cues, discovery, end bridge).
    max_tokens = min(16000, 7400 * max(n, 1) + 1500) + (1500 if is_long else 0)
    raw = client.generate(prompt, timeout=timeout, max_output_tokens=max_tokens)
    ideas = _content_lab_parse_ideas_response(raw, n_expected=n, format=format)
    if ideas and ideas[0].get("parse_failed") and getattr(client, "api_mode", "") != "google_genai":
        # One repair pass for providers without a JSON mode: hand the reply
        # back and ask for the same content as valid JSON only. Cheap compared
        # to the run the user already waited for, and it fixes the common
        # failures (prose around the JSON, a stray quote, a missing bracket).
        fix_prompt = ("The text below was supposed to be ONE valid minified JSON object of the form "
                      '{"ideas":[...]} but it is not valid JSON (it may be cut off, wrapped in prose, '
                      "or have a syntax error). Return the SAME content as valid minified JSON only, "
                      "no code fences, no commentary. If an idea is cut off, keep what is there and "
                      "close it properly.\n\n" + (raw or "")[:60000])
        raw2 = client.generate(fix_prompt, timeout=timeout, max_output_tokens=max_tokens)
        again = _content_lab_parse_ideas_response(raw2, n_expected=n, format=format)
        if again and not again[0].get("parse_failed"):
            ideas = again
    return _attach_refs(ideas, videos, csv_sources)
