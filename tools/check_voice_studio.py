"""Offline self-test for Voice Studio: real ffmpeg, mocked AI.

    python tools/check_voice_studio.py

Builds a 12 s test video, then runs captions (burn / soft / srt, translated,
bilingual, every style) and a full dub (performance read, casting, adaptation,
TTS, timing fit, ducked mix, dual audio tracks, burned captions).
"""

import json
import math
import os
import struct
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.captions import cues as C            # noqa: E402
from app.core.captions import dubbing as D          # noqa: E402
from app.core.captions import gemini_media as G     # noqa: E402
from app.core.captions import jobs as J             # noqa: E402
from app.core.captions import render as R           # noqa: E402
from app.core.captions import styles as ST          # noqa: E402
from app.core.captions import translate as T        # noqa: E402


class FakeAI:
    label = "Fake"

    def generate(self, prompt, system=None, timeout=0, max_output_tokens=0):
        if "Write a brief" in prompt:
            return '{"tone": "fun", "source_lang": "en"}'
        if "too long" in prompt:
            if "LINES:" in prompt:
                ids = [json.loads(ln)["id"] for ln in prompt.split("LINES:")[1].split("Return JSON")[0].strip().splitlines()
                       if ln.startswith("{")]
                return json.dumps({"t": [{"id": i, "x": "کوتاه"} for i in ids]})
            return '{"x": "کوتاه"}'
        block = prompt.split("LINES TO TRANSLATE:")[1].split("CONTEXT AFTER")[0]
        items = [json.loads(ln) for ln in block.strip().splitlines() if ln.startswith("{")]
        return json.dumps({"t": [{"id": it["id"], "x": f"خط ترجمه‌شده {it['id']}"} for it in items]})


def fake_listen(creds, audio, prompt, system=None, **kw):
    ids = [json.loads(ln)["id"] for ln in prompt.split("LINES:")[1].strip().splitlines()]
    return {"speakers": [{"id": "S1", "gender": "female", "cast": "Kore"}],
            "lines": [{"id": i, "spk": "S1", "style": "warm", "tagged": f"line {i}"} for i in ids]}


TTS_CALLS = []
REAL_TTS = G.tts


def fake_tts(creds, text, out, voice="Kore", style="", model=None, lang="", should_cancel=None, timeout=0):
    TTS_CALLS.append(text)
    n = int(24000 * min(4.0, 0.07 * len(text)))
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i * 0.115))) for i in range(n)))
    return model


def main():
    tmp = tempfile.mkdtemp(prefix="vs_check_")
    src = os.path.join(tmp, "src.mp4")
    ok, err = R.run(["-f", "lavfi", "-i", "color=c=0x335577:s=1280x720:d=12", "-f", "lavfi",
                     "-i", "sine=f=330:d=12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                     "-shortest", src])
    assert ok, err
    sub = os.path.join(tmp, "in.srt")
    with open(sub, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,500 --> 00:00:03,000\nHello friends, line one.\n\n"
                "2\n00:00:03,200 --> 00:00:06,000\nAnd the second one!\n")
    T._client = lambda *a, **k: FakeAI()
    G.credentials = lambda cfg=None, feature="dub": {
        "api_key": "x", "base_url": G.DEFAULT_BASE, "proxy": "", "model": "m"}
    D.G.credentials = G.credentials
    G.has_gemini = lambda cfg=None, feature="dub": True
    G.listen_json = fake_listen
    G.tts = fake_tts
    print("subtitles filter:", R.has_filter("subtitles"))
    for style in ST.STYLE_KEYS:
        for mode in ("burn", "soft", "srt"):
            r = J.run_captions({"media": src, "source": "file", "sub_path": sub, "target": "fa",
                                "style": style, "bilingual": True, "output": mode},
                               lambda s, p: None, lambda: False)
            print(f"captions {style:7s} {mode:5s} ->", os.path.basename(r["video"] or r["files"][0]))
    r = J.run_dub({"media": src, "source": "file", "sub_path": sub, "target": "fa",
                   "burn_style": "pop"}, lambda s, p: None, lambda: False)
    info = R.probe(r["video"])
    assert info["audio_streams"] == 2, info
    print("dub ->", os.path.basename(r["video"]), r["voices"], "audio tracks:", info["audio_streams"])
    calls = len(TTS_CALLS)
    r2 = J.run_dub({"media": src, "source": "file", "sub_path": sub, "target": "fa",
                    "burn_style": ""}, lambda s, p: None, lambda: False)
    assert len(TTS_CALLS) == calls, "re-run must reuse every recorded line"
    print("dub re-run -> 0 new TTS calls (cache hit)")
    quota_drill(tmp)
    sync_drill(tmp)
    print("OK, output in", tmp)


def quota_drill(tmp):
    """A per-day 429 must stop at once: no shape hopping, no retry storm."""
    import io
    import urllib.error
    from app.core.captions import gemini_media as GM
    sent = []

    def fake_post(creds, url, body, timeout=180):
        sent.append(url)
        err = {"error": {"code": 429, "message": "You exceeded your current quota",
                         "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                      "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                                     {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}}
        raise urllib.error.HTTPError(url, 429, "quota", {}, io.BytesIO(json.dumps(err).encode()))
    GM._post = fake_post
    GM._EXHAUSTED.clear()
    try:
        REAL_TTS({"api_key": "x", "base_url": GM.DEFAULT_BASE}, "hello", os.path.join(tmp, "q.wav"))
        raise AssertionError("expected QuotaExhausted")
    except GM.QuotaExhausted:
        pass
    n_models = 1 + len(GM.TTS_FALLBACKS)
    assert len(sent) == n_models, f"one request per model, got {len(sent)}"
    sent.clear()
    try:
        REAL_TTS({"api_key": "x", "base_url": GM.DEFAULT_BASE}, "again", os.path.join(tmp, "q2.wav"))
    except GM.QuotaExhausted:
        pass
    assert not sent, "exhausted models must not be asked again"
    print(f"quota drill -> {n_models} requests total, then stop (was up to 36 per line)")


def sync_drill(tmp):
    """v1.4.4: tight sync. TTS silence is trimmed, lines start where the
    original says them, a long line leans into the next one before it is
    sped up, nothing is trimmed when a speed-up fits, the last line never
    runs past the video, sound labels are never spoken."""
    # timeline planner
    plan = D.plan_timing([1.0, 4.0, 8.0], [2.0, 4.2, 3.0], 11.0)
    assert plan[0] == (1.0, 1.0, 0.0), plan
    assert plan[1][0] == 4.0 and plan[1][1] == 1.0 and not plan[1][2], plan   # leans, no speed-up
    assert 8.0 <= plan[2][0] <= 8.0 + D._MAX_DRIFT + 1e-6, plan              # never more than the drift late
    end = plan[2][0] + 3.0 / plan[2][1]
    assert end <= 11.0, (plan, end)                                           # fits before the video ends
    hard = D.plan_timing([0.0], [9.0], 4.0)[0]
    assert hard[1] == D._HARD_TEMPO and 0 < hard[2] <= 4.0, hard               # last resort: trim
    # silence trim on a real take
    take = os.path.join(tmp, "take.wav")
    with wave.open(take, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\0\0" * 14400 + b"".join(struct.pack("<h", int(7000 * math.sin(i * 0.1)))
                                                for i in range(48000)) + b"\0\0" * 12000)
    _p, d = D._prep_clip(take)
    assert 1.9 < d < 2.1, d
    # sound labels
    cs = [C.Cue(0, 1, "[Music]"), C.Cue(1, 2, "♪ ♪"), C.Cue(2, 3, "(applause)"), C.Cue(3, 4, "[Music] hello there")]
    kept = D.speakable(cs)
    assert [c.text for c in kept] == ["hello there"], [c.text for c in kept]
    # snapping onto the voice
    snapped = C.snap_to_speech([C.Cue(1.3, 3.5, "a")], [0.0, 1.0, 5.0], [3.2, 7.0])
    assert abs(snapped[0].start - 1.0) < 1e-6 and abs(snapped[0].end - 3.2) < 1e-6, snapped
    print("sync drill -> silence trimmed, 0 s start offset, lean before speed-up, no overrun")


if __name__ == "__main__":
    main()
