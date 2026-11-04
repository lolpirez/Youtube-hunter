"""app.core.exports - Youtube Scrapper."""

import csv
import json
import os
import re
import sqlite3

# Control characters that Excel/openpyxl reject outright (everything except
# \t \n \r). YouTube titles/descriptions/transcripts DO occasionally contain
# them — previously this made the export blow up at the very end of a scrape.
_ILLEGAL_CELL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


_XLSX_CELL_MAX = 32767

# v1.0.5 — the "transcript found but missing from the CSV" bug.
#
# The scraper always wrote the WHOLE transcript into the CSV, and the log was
# right: the text was in the file. But Excel cannot hold more than 32,767
# characters in one cell (and on CSV import it already breaks at ~32,758).
# A 25-40 minute video easily has a longer transcript. Excel does not warn:
# it cuts the cell and pours the rest into the NEXT row / cells, so the
# sheet gets extra half-rows, the following videos' columns slide out of
# place and their transcript cells read empty or hold someone else's text.
# That is the 5-8 "missing" transcripts per 60-video run.
#
# Fix: a long text cell is split, at a word boundary, into continuation
# columns right after it (`transcript`, `transcript_part2`, `transcript_part3`
# ...). Nothing is cut, every row stays one row, and the app's own readers
# glue the parts back together (merge_spill_columns). xlsx does the same
# instead of truncating to 32,767 characters as it used to.
_SPILL_LIMIT = 32000          # safe margin under Excel's ~32,758 CSV limit
_SPILL_SUFFIX = "_part"

# A text cell starting with = + - @ is read by Excel as a FORMULA: a
# transcript that opens with "- Hey guys" (speaker dash, very common in manual
# subtitles) showed up as #NAME? instead of text. Only these free-text
# columns are guarded; numbers like a negative value are left alone.
_TEXT_COLUMNS = ("title", "description", "tags", "transcript")
_FORMULA_LEAD = ("=", "+", "-", "@")


def _clean_cell(value):
    if isinstance(value, str):
        return _ILLEGAL_CELL_RE.sub("", value)
    return value


def _split_text(text, limit=_SPILL_LIMIT):
    """`text` in pieces of at most `limit` characters, cut at whitespace."""
    parts = []
    while len(text) > limit:
        cut = text.rfind(" ", int(limit * 0.8), limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:]
    parts.append(text)
    return parts


def _guard_formula(key, value):
    if (isinstance(value, str) and value[:1] in _FORMULA_LEAD
            and str(key).split(_SPILL_SUFFIX)[0] in _TEXT_COLUMNS):
        return " " + value      # a leading space keeps it plain text
    return value


def _spill_long_cells(data, limit=_SPILL_LIMIT):
    """(headers, rows) with every over-long text cell split into continuation
    columns placed right after its column. Rows that need fewer parts get
    empty continuation cells, so the table stays rectangular."""
    headers = list(data[0].keys())
    for row in data[1:]:              # keys a later row added (rare)
        for k in row.keys():
            if k not in headers:
                headers.append(k)
    parts_needed = {}
    for row in data:
        for k, v in row.items():
            if isinstance(v, str) and len(v) > limit:
                n = len(_split_text(v, limit))
                if n > parts_needed.get(k, 1):
                    parts_needed[k] = n
    if not parts_needed:
        return headers, data
    out_headers = []
    for h in headers:
        out_headers.append(h)
        for i in range(2, parts_needed.get(h, 1) + 1):
            out_headers.append(f"{h}{_SPILL_SUFFIX}{i}")
    out_rows = []
    for row in data:
        new = {}
        for h in headers:
            v = row.get(h, "")
            n = parts_needed.get(h, 1)
            if n > 1:
                pieces = _split_text(v, limit) if isinstance(v, str) else [v]
                pieces += [""] * (n - len(pieces))
                new[h] = pieces[0]
                for i, piece in enumerate(pieces[1:], start=2):
                    new[f"{h}{_SPILL_SUFFIX}{i}"] = piece
            else:
                new[h] = v
        out_rows.append(new)
    return out_headers, out_rows


_SPILL_KEY_RE = re.compile(r"^(.+)" + re.escape(_SPILL_SUFFIX) + r"(\d+)$")


def merge_spill_columns(row):
    """Undo _spill_long_cells for one row read back from a CSV / xlsx export:
    `transcript` + `transcript_part2` + ... becomes one `transcript` again."""
    if not isinstance(row, dict):
        return row
    extra = {}
    for k in list(row.keys()):
        m = _SPILL_KEY_RE.match(str(k or ""))
        if m and m.group(1) in row:
            extra.setdefault(m.group(1), []).append((int(m.group(2)), k))
    if not extra:
        return row
    for base, items in extra.items():
        text = str(row.get(base) or "")
        for _n, k in sorted(items):
            piece = row.pop(k, "")
            if piece:
                text += str(piece)
        row[base] = text
    return row


def write_transcript_files(data, folder):
    """Thorough mode extra: every transcript as its own UTF-8 .txt file, so the
    full text is always one double-click away, whatever the spreadsheet app
    does with long cells. Returns how many files were written."""
    import unicodedata
    os.makedirs(folder, exist_ok=True)
    n = 0
    for i, row in enumerate(data, start=1):
        text = row.get("transcript")
        if not (isinstance(text, str) and text and text != "N/A"
                and str(row.get("transcript_status") or "").startswith("Available")):
            continue
        title = unicodedata.normalize("NFC", str(row.get("title") or ""))
        title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip()[:80].rstrip(". ")
        vid = str(row.get("video_id") or i)
        name = f"{i:03d} - {title or vid} [{vid}].txt"
        try:
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(str(row.get("title") or "") + "\n")
                f.write(str(row.get("url") or "") + "\n\n")
                f.write(text.strip() + "\n")
            n += 1
        except OSError:
            continue
    return n


def write_export(data, path, fmt):
    """Write scraped rows to disk in the chosen format.

    Hardened: values are sanitized (illegal control chars stripped) and the
    file is written atomically (temp file + os.replace) so a crash or 'disk
    full' mid-write can never leave a corrupt half-file behind."""
    if not data:
        return
    data = [{k: _clean_cell(v) for k, v in row.items()} for row in data]
    tmp = path + ".tmp"
    try:
        if fmt == "csv":
            headers, rows = _spill_long_cells(data)
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: _guard_formula(k, v) for k, v in row.items()})
        elif fmt in ("json", "jsonl"):   # Niche / Archive pass "jsonl"
            with open(tmp, "w", encoding="utf-8") as f:
                for row in data:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        elif fmt == "xlsx":
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "videos"
            # long cells continue in `<col>_part2`... instead of being cut
            # at Excel's 32,767-character cell limit (v1.0.5)
            headers, rows = _spill_long_cells(data)
            ws.append(headers)
            for r, row in enumerate(rows, start=2):
                for c, h in enumerate(headers, start=1):
                    v = row.get(h, "")
                    if isinstance(v, str) and len(v) > _XLSX_CELL_MAX:
                        # safety net only: _spill_long_cells keeps cells
                        # far below the limit
                        v = v[:_XLSX_CELL_MAX - 1] + "…"
                    cell = ws.cell(row=r, column=c, value=v)
                    if isinstance(v, str) and v.startswith("="):
                        # a title like "=HYPERLINK(...)" must stay text,
                        # never become a live formula
                        cell.data_type = "s"
            wb.save(tmp)
        elif fmt == "sqlite":
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            headers = list(data[0].keys())
            cols = ", ".join([f'"{h}" TEXT' for h in headers])
            cur.execute("DROP TABLE IF EXISTS videos")
            cur.execute(f"CREATE TABLE videos ({cols})")
            placeholders = ", ".join(["?"] * len(headers))
            for row in data:
                cur.execute(f"INSERT INTO videos VALUES ({placeholders})",
                            [str(row.get(h, "")) for h in headers])
            conn.commit()
            conn.close()
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
def read_checkpoint_rows(path):
    """Read rows back from a JSONL checkpoint file. Returns a list of dicts
    (empty on any problem — resume is always best-effort)."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get("video_id"):
                        rows.append(row)
                except Exception:
                    continue
    except Exception:
        return []
    return rows
