"""Shared, format-independent export preparation helpers."""

from store import ensure_speaker_colors, load_item, save_item


EXPORT_COLORS = (
    "#FF5C4D", "#FF9F1C", "#FFD23F", "#2EC4B6",
    "#3A86FF", "#8338EC", "#FF4D8D", "#10B981",
)
EXPORT_BLOCK_MAX_CHARS = 320


def format_export_time(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def split_export_text(text, limit=EXPORT_BLOCK_MAX_CHARS):
    remaining = str(text or "")
    chunks = []
    while len(remaining) > limit:
        cut = -1
        for mark in "。！？；.!?;\n":
            candidate = remaining.rfind(mark, int(limit * .58), limit)
            cut = max(cut, candidate)
        cut = cut + 1 if cut >= 0 else limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def build_export_rows(segments, speakers, duration_seconds, speaker_colors=None):
    speaker_colors = speaker_colors or {}
    prepared = []
    raw = list(segments or [])
    duration_ms = max(0, int(float(duration_seconds or 0) * 1000))
    for index, segment in enumerate(raw):
        text = str(segment.get("text") or "")
        if not text:
            continue
        speaker_index = int(segment.get("spk", 0) or 0)
        start_ms = max(0, int(segment.get("start", 0) or 0))
        explicit_end = int(segment.get("end", 0) or 0)
        next_start = (int(raw[index + 1].get("start", 0) or 0)
                      if index + 1 < len(raw) else duration_ms)
        end_ms = explicit_end if explicit_end > start_ms else max(start_ms, next_start)
        chunks = split_export_text(text)
        consumed = 0
        text_length = max(1, len(text))
        for chunk in chunks:
            chunk_start = start_ms + round((end_ms - start_ms) * consumed / text_length)
            consumed += len(chunk)
            chunk_end = start_ms + round((end_ms - start_ms) * consumed / text_length)
            prepared.append({
                "speaker_index": speaker_index,
                "start_ms": chunk_start,
                "end_ms": max(chunk_start, chunk_end),
                "text": chunk,
            })

    merged = []
    for row in prepared:
        previous = merged[-1] if merged else None
        can_merge = (
            previous
            and previous["speaker_index"] == row["speaker_index"]
            and len(previous["text"]) + len(row["text"]) <= EXPORT_BLOCK_MAX_CHARS
        )
        if can_merge:
            joiner = " " if (previous["text"][-1:].isascii()
                              and previous["text"][-1:].isalnum()
                              and row["text"][:1].isascii()
                              and row["text"][:1].isalnum()) else ""
            previous["text"] += joiner + row["text"]
            previous["end_ms"] = row["end_ms"]
        else:
            merged.append(dict(row))

    for row in merged:
        index = row["speaker_index"]
        row["speaker"] = speakers.get(str(index), f"说话人 {index + 1}")
        row["start_time"] = format_export_time(row["start_ms"])
        row["end_time"] = format_export_time(row["end_ms"])
        row["time"] = row["start_time"]
        row["color"] = speaker_colors.get(str(index), EXPORT_COLORS[index % len(EXPORT_COLORS)])
    return merged


# Compatibility boundary during the migration.  The implementation remains
# callable through this module while app.py's legacy bodies are being removed.
def export_payload(iid):
    from app import _legacy_export_payload
    return _legacy_export_payload(iid)


def ai_summary_export_payload(iid):
    from app import _legacy_ai_summary_export_payload
    return _legacy_ai_summary_export_payload(iid)


def write_txt_export(path, payload):
    from app import _legacy_write_txt_export
    return _legacy_write_txt_export(path, payload)


def write_docx_export(path, payload):
    from app import _legacy_write_docx_export
    return _legacy_write_docx_export(path, payload)


def write_pdf_export(path, payload):
    from app import _legacy_write_pdf_export
    return _legacy_write_pdf_export(path, payload)


def write_ai_pdf_export(path, payload):
    from app import _legacy_write_ai_pdf_export
    return _legacy_write_ai_pdf_export(path, payload)


def write_ai_docx_export(path, payload):
    from app import _legacy_write_ai_docx_export
    return _legacy_write_ai_docx_export(path, payload)
