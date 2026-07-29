"""Transcript, export metadata, and speaker-color persistence."""

import datetime
import hashlib
import json
import os
import re
import uuid

import paths


SPEAKER_COLOR_POOL = (
    "#FF675B", "#FF9F1C", "#F59E0B", "#FFD23F",
    "#84CC16", "#10B981", "#14B8A6", "#06B6D4",
    "#3A86FF", "#6366F1", "#8338EC", "#A855F7",
    "#D946EF", "#EC4899", "#F43F5E", "#EF4444",
)


def configure_data_directory(directory):
    paths.configure_data_directory(directory)


def item_dir(iid):
    return paths.item_directory(iid)


def atomic_write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def record_export_path(iid, resource, path):
    if resource not in {"document", "summary"} or not path:
        return
    record_path = os.path.join(item_dir(iid), "exports.json")
    try:
        with open(record_path, encoding="utf-8") as file:
            records = json.load(file)
        if not isinstance(records, dict):
            records = {}
    except (OSError, ValueError, TypeError):
        records = {}
    records[resource] = {
        "path": os.path.abspath(os.path.expanduser(str(path))),
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    atomic_write_json(record_path, records)


def latest_export_path(iid, resource):
    if resource not in {"document", "summary"}:
        return None
    try:
        with open(os.path.join(item_dir(iid), "exports.json"), encoding="utf-8") as file:
            value = json.load(file).get(resource)
        return value.get("path") if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def load_item(iid):
    with open(os.path.join(item_dir(iid), "transcript.json"), encoding="utf-8") as file:
        return json.load(file)


def save_item(iid, data):
    path = os.path.join(item_dir(iid), "transcript.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _valid_hex_color(value):
    if not isinstance(value, str):
        return None
    color = value.strip().upper()
    return color if re.fullmatch(r"#[0-9A-F]{6}", color) else None


def _speaker_indexes(data):
    indexes = set()
    for segment in data.get("segments", []) or []:
        try:
            indexes.add(int(segment.get("spk", 0) or 0))
        except (TypeError, ValueError):
            indexes.add(0)
    for key in (data.get("speakers") or {}).keys():
        try:
            indexes.add(int(key))
        except (TypeError, ValueError):
            pass
    return sorted(indexes) or [0]


def make_speaker_colors(seed, indexes):
    seed = str(seed or uuid.uuid4().hex)
    ranked = sorted(
        (hashlib.sha256(f"{seed}:{color}".encode("utf-8")).hexdigest(), color)
        for color in SPEAKER_COLOR_POOL
    )
    palette = [color for _, color in ranked]
    return {str(index): palette[position % len(palette)]
            for position, index in enumerate(sorted(int(i) for i in indexes))}


def ensure_speaker_colors(iid, data):
    raw = data.get("speaker_colors")
    existing = raw if isinstance(raw, dict) else {}
    changed = not isinstance(raw, dict)
    colors = {}
    generated = make_speaker_colors(iid, _speaker_indexes(data))
    for key, generated_color in generated.items():
        color = _valid_hex_color(existing.get(key))
        colors[key] = color or generated_color
        changed = changed or color is None
    for key, value in existing.items():
        if key not in colors:
            color = _valid_hex_color(value)
            if color:
                colors[key] = color
    if data.get("speaker_colors") != colors:
        data["speaker_colors"] = colors
        changed = True
    return colors, changed


def audio_path_of(iid):
    directory = item_dir(iid)
    for filename in os.listdir(directory):
        if filename.startswith("audio"):
            return os.path.join(directory, filename)
    return None
