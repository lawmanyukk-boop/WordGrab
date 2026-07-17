#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WordGrab · 桌面 GUI（pywebview）
飞书妙记风：左侧历史 + 文稿点读联动 + 播放器 + 说话人改名 + 搜索 + 导出
"""
import os, sys, json, time, uuid, shutil, threading, datetime, re, subprocess, hashlib, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")
DEFAULT_DATA = os.path.join(HERE, "data")
STORAGE_CONFIG = os.path.join(HERE, "storage.json")


def configured_data_directory():
    """读取独立于数据目录的启动指针，目录搬迁后重启仍能找到数据。"""
    try:
        with open(STORAGE_CONFIG, encoding="utf-8") as file:
            configured = json.load(file).get("data_directory")
        if configured:
            return os.path.abspath(os.path.expanduser(str(configured)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return DEFAULT_DATA


DATA = configured_data_directory()
try:
    os.makedirs(DATA, exist_ok=True)
except OSError as exc:
    print(f"[storage] 无法使用已设置的数据目录 {DATA!r}: {exc}; 已临时使用默认目录", flush=True)
    DATA = DEFAULT_DATA
    os.makedirs(DATA, exist_ok=True)
INDEX = os.path.join(DATA, "index.json")
SETTINGS = os.path.join(DATA, "settings.json")
TRASH = os.path.join(DATA, ".trash")
INDEX_DB = os.path.join(DATA, "index.db")
APP_VERSION = "1.1.0"

THEME_KEYS = {
    "aurora-sea", "solar-bloom", "lavender-haze", "tide-ember",
    "midnight-prism", "matcha-mist", "rose-quartz", "sandstone-glow",
    "deep-ocean", "graphite-pearl", "rainbow-glow",
}

DEFAULT_SETTINGS = {
    "theme": "aurora-sea",
    "reopen_last": True,
    "auto_open_import": True,
    "default_speed": 1.0,
    "skip_seconds": 15,
    "auto_diarization": True,
    "transcription_mode": "accuracy",
    "export_format": "txt",
    "export_directory": os.path.expanduser("~/Documents"),
    "filename_rule": "source_date",
    "font_size": "standard",
    "list_density": "standard",
    "appearance": "light",
    "follow_system": False,
    "delete_audio_with_transcript": True,
    "last_item_id": "",
}

SETTING_ENUMS = {
    "transcription_mode": {"speed", "accuracy"},
    "export_format": {"docx", "pdf", "txt"},
    "filename_rule": {"source", "source_date"},
    "font_size": {"small", "standard", "large"},
    "list_density": {"compact", "standard"},
    "appearance": {"system", "light", "dark"},
}

MODEL_CACHE_MARKERS = ("paraformer", "fsmn_vad", "punc_ct", "campplus")

AUDIO_EXT = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
             ".aac": "audio/aac", ".mp4": "video/mp4", ".mov": "video/quicktime",
             ".m4v": "video/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg"}

# 供 HTTP 处理器（拖拽上传）回调到 Api 实例
API_REF = None
INDEX_LOCK = threading.RLock()
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB，避免误拖入超大视频占满磁盘

# 转写进度共享给前端轮询（后台线程只写这个 dict，不从子线程调 evaluate_js —— 后者在 macOS 上易崩）
PROGRESS = {}                      # iid -> {stage, pct, info, status, msg, title, partial}
TRANSCRIBE_LOCK = threading.Lock()  # 序列化转写，避免并发共用同一个模型实例出错
DELETED = set()                    # 转写期间被用户删除的 iid，两阶段落盘前都要检查
_DRAG_STRIP_CLASS = None           # macOS 原生透明拖动带（延迟创建，避免非 macOS 导入 AppKit）
_DRAG_STRIPS = {}                  # NSWindow id -> drag view，持有引用避免被回收
_DRAG_OBSERVERS = {}               # NSWindow id -> 通知监听状态，缩放/全屏后重新定位拖动带

# ---------- 历史存储 ----------

def load_index():
    with INDEX_LOCK:
        _ensure_index_db()
        try:
            with sqlite3.connect(INDEX_DB) as connection:
                rows = connection.execute("SELECT payload FROM items ORDER BY position").fetchall()
            return [json.loads(row[0]) for row in rows]
        except (OSError, sqlite3.Error, ValueError):
            pass
        if os.path.exists(INDEX):
            try:
                with open(INDEX, encoding="utf-8") as f:
                    value = json.load(f)
                return value if isinstance(value, list) else []
            except (OSError, ValueError):
                backup = INDEX + ".bak"
                try:
                    with open(backup, encoding="utf-8") as f:
                        value = json.load(f)
                    return value if isinstance(value, list) else []
                except (OSError, ValueError):
                    return []
    return []


def save_index(items):
    with INDEX_LOCK:
        _ensure_index_db()
        payloads = [(index, json.dumps(item, ensure_ascii=False)) for index, item in enumerate(items)]
        with sqlite3.connect(INDEX_DB) as connection:
            connection.execute("BEGIN")
            connection.execute("DELETE FROM items")
            connection.executemany("INSERT INTO items(position, payload) VALUES (?, ?)", payloads)
            connection.commit()
        temporary = INDEX + ".tmp"
        backup = INDEX + ".bak"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(INDEX):
            shutil.copy2(INDEX, backup)
        os.replace(temporary, INDEX)


def _ensure_index_db():
    """首次运行把旧 index.json 迁移到 SQLite；JSON 继续保留作人工恢复备份。"""
    global INDEX_DB
    if os.path.exists(INDEX_DB):
        return
    os.makedirs(os.path.dirname(INDEX_DB), exist_ok=True)
    with sqlite3.connect(INDEX_DB) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS items (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        if os.path.exists(INDEX):
            try:
                with open(INDEX, encoding="utf-8") as file:
                    old_items = json.load(file)
                connection.executemany(
                    "INSERT INTO items(position, payload) VALUES (?, ?)",
                    [(index, json.dumps(item, ensure_ascii=False)) for index, item in enumerate(old_items or [])],
                )
            except (OSError, ValueError, TypeError):
                pass
        connection.commit()


def normalize_settings(data=None):
    raw = data if isinstance(data, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    for key in out:
        if key in raw:
            out[key] = raw[key]
    if out["theme"] not in THEME_KEYS:
        out["theme"] = DEFAULT_SETTINGS["theme"]
    for key, allowed in SETTING_ENUMS.items():
        if out[key] not in allowed:
            out[key] = DEFAULT_SETTINGS[key]
    if "appearance" not in raw:
        out["appearance"] = "system" if raw.get("follow_system") else DEFAULT_SETTINGS["appearance"]
    out["follow_system"] = out["appearance"] == "system"
    if out["skip_seconds"] not in {5, 10, 15, 30}:
        out["skip_seconds"] = 15
    try:
        out["default_speed"] = float(out["default_speed"])
    except (TypeError, ValueError):
        out["default_speed"] = 1.0
    if out["default_speed"] not in {0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0}:
        out["default_speed"] = 1.0
    for key in ("reopen_last", "auto_open_import", "auto_diarization",
                "delete_audio_with_transcript"):
        out[key] = bool(out[key])
    out["last_item_id"] = str(out.get("last_item_id") or "")
    directory = os.path.expanduser(str(out.get("export_directory") or ""))
    out["export_directory"] = directory if os.path.isdir(directory) else os.path.expanduser("~/Documents")
    return out


def load_settings():
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return normalize_settings(json.load(f))
        except (OSError, ValueError, TypeError):
            pass
    return normalize_settings()


def save_settings(data):
    data = normalize_settings(data)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def apply_settings_patch(patch):
    current = load_settings()
    if isinstance(patch, dict):
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                current[key] = value
    return save_settings(current)


def path_size(path):
    total = 0
    if not os.path.exists(path):
        return 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _set_data_globals(directory):
    global DATA, INDEX, SETTINGS, TRASH, INDEX_DB
    DATA = os.path.abspath(os.path.expanduser(directory))
    INDEX = os.path.join(DATA, "index.json")
    SETTINGS = os.path.join(DATA, "settings.json")
    TRASH = os.path.join(DATA, ".trash")
    INDEX_DB = os.path.join(DATA, "index.db")


def _save_data_directory_pointer(directory):
    payload = {"data_directory": os.path.abspath(os.path.expanduser(directory))}
    temporary = STORAGE_CONFIG + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temporary, STORAGE_CONFIG)


def migrate_data_directory(directory):
    """把现有数据复制到空目录，确认成功后再切换并删除旧目录。"""
    source = os.path.abspath(DATA)
    raw_target = str(directory or "").strip()
    if not raw_target:
        return {"ok": False, "message": "没有选择新的保存位置"}
    target = os.path.abspath(os.path.expanduser(raw_target))
    if source == target:
        return {"ok": True, "data_path": source, "moved": False,
                "message": "当前已经使用这个位置"}
    try:
        common = os.path.commonpath([source, target])
    except ValueError:
        common = ""
    if common in {source, target}:
        return {"ok": False, "message": "新位置不能包含当前数据目录，请选择其他空文件夹"}
    if any(value.get("status") in {"running", "queued"} for value in PROGRESS.values()):
        return {"ok": False, "message": "正在转写音频，请等待完成后再更改保存位置"}

    try:
        os.makedirs(target, exist_ok=True)
        meaningful_entries = [
            name for name in os.listdir(target)
            if name not in {".DS_Store", ".localized"}
        ]
        if meaningful_entries:
            return {"ok": False, "message": "为避免覆盖文件，请选择一个空文件夹"}
        probe = os.path.join(target, f".wordgrab-write-test-{uuid.uuid4().hex}")
        with open(probe, "w", encoding="utf-8") as file:
            file.write("ok")
        os.remove(probe)
    except OSError as exc:
        return {"ok": False, "message": f"无法使用这个文件夹：{exc}"}

    copied = []
    try:
        for name in os.listdir(source):
            if name in {".DS_Store", ".localized"}:
                continue
            source_entry = os.path.join(source, name)
            target_entry = os.path.join(target, name)
            copied.append(target_entry)
            if os.path.isdir(source_entry) and not os.path.islink(source_entry):
                shutil.copytree(source_entry, target_entry)
            else:
                shutil.copy2(source_entry, target_entry)
        _save_data_directory_pointer(target)
        _set_data_globals(target)
    except Exception as exc:
        for copied_path in reversed(copied):
            try:
                if os.path.isdir(copied_path) and not os.path.islink(copied_path):
                    shutil.rmtree(copied_path)
                elif os.path.lexists(copied_path):
                    os.remove(copied_path)
            except OSError:
                pass
        return {"ok": False, "message": f"移动数据失败，原位置未改变：{exc}"}

    warning = ""
    try:
        shutil.rmtree(source)
    except OSError:
        warning = "新位置已生效；旧文件夹暂时无法删除，可稍后手动清理。"
    return {
        "ok": True,
        "data_path": DATA,
        "moved": True,
        "message": warning or "文稿和录音已移动到新位置",
    }


def model_cache_dirs():
    roots = [
        os.path.expanduser("~/.cache/modelscope/models/iic"),
        os.path.expanduser("~/.cache/modelscope/hub/models/iic"),
    ]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isdir(full) and any(marker in name.lower() for marker in MODEL_CACHE_MARKERS):
                found.append(full)
    return found


def safe_filename(name):
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", str(name or "转写")).strip(" .")
    return cleaned[:120] or "转写"


def preserve_item_audio(iid, title):
    source = audio_path_of(iid)
    if not source or not os.path.isfile(source):
        return None
    target_dir = os.path.join(DATA, "保留的录音")
    os.makedirs(target_dir, exist_ok=True)
    ext = os.path.splitext(source)[1]
    base = safe_filename(title)
    target = os.path.join(target_dir, base + ext)
    if os.path.exists(target):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = os.path.join(target_dir, f"{base}-{stamp}{ext}")
    shutil.move(source, target)
    return target


EXPORT_COLORS = (
    "#FF5C4D", "#FF9F1C", "#FFD23F", "#2EC4B6",
    "#3A86FF", "#8338EC", "#FF4D8D", "#10B981",
)
SPEAKER_COLOR_POOL = (
    "#FF675B", "#FF9F1C", "#F59E0B", "#FFD23F",
    "#84CC16", "#10B981", "#14B8A6", "#06B6D4",
    "#3A86FF", "#6366F1", "#8338EC", "#A855F7",
    "#D946EF", "#EC4899", "#F43F5E", "#EF4444",
)
EXPORT_BLOCK_MAX_CHARS = 320


def format_export_time(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def split_export_text(text, limit=EXPORT_BLOCK_MAX_CHARS):
    """优先在中文标点处切块，防止一个说话人段落高于整页。"""
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


def export_payload(iid):
    data = load_item(iid)
    meta = next((item for item in load_index() if item["id"] == iid), {})
    speakers = data.get("speakers", {})
    duration = meta.get("duration") or 0
    speaker_colors, colors_changed = ensure_speaker_colors(iid, data)
    if colors_changed:
        save_item(iid, data)
    rows = build_export_rows(data.get("segments", []), speakers, duration, speaker_colors)
    participant_names = []
    for row in rows:
        if row["speaker"] not in participant_names:
            participant_names.append(row["speaker"])
    if not participant_names:
        participant_names = list(speakers.values()) or ["说话人 1"]
    participant_count = meta.get("n_speakers") or len(participant_names) or 1
    joined_participants = " · ".join(participant_names)
    if len(joined_participants) <= 11:
        participants_text = joined_participants
    elif participant_count > 2:
        participants_text = f"{participant_names[0][:8]} 等{participant_count}人"
    elif len(participant_names) > 1:
        participants_text = f"{participant_names[0][:6]} · {participant_names[1][:6]}"
    else:
        participants_text = participant_names[0][:10] + "…"

    created = str(meta.get("created") or "").replace("T", " ").strip()
    created_parts = created.split()
    recorded_text = (f"{created_parts[0]} · {created_parts[1][:5]}"
                     if len(created_parts) >= 2 else created)
    word_count = sum(1 for row in rows for char in row["text"] if not char.isspace())
    return {
        "title": meta.get("title") or "转写",
        "created": created,
        "recorded_text": recorded_text or "—",
        "duration": duration,
        "duration_text": format_export_time(float(duration) * 1000),
        "word_count": word_count,
        "word_count_text": f"{word_count:,} 字",
        "speaker_count": participant_count,
        "participants_text": participants_text,
        "rows": rows,
    }


def write_txt_export(path, payload):
    lines = [payload["title"], payload["created"], ""]
    for row in payload["rows"]:
        lines.append(
            f"[{row['start_time']} - {row['end_time']}] {row['speaker']}：{row['text']}"
        )
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def write_docx_export(path, payload):
    import io
    import struct
    import zlib

    from docx import Document
    from docx.enum.table import (WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE,
                                 WD_TABLE_ALIGNMENT)
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    body_font = "Heiti SC"
    brand_font = "Arial Black"
    page_width_pt, left_margin_pt, right_margin_pt = 595.276, 40.0, 41.0
    content_width_pt = page_width_pt - left_margin_pt - right_margin_pt

    def set_run_font(run, name=body_font, size=12, color="191919", bold=False):
        run.font.name = name
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.rFonts
        if r_fonts is None:
            r_fonts = OxmlElement("w:rFonts")
            r_pr.insert(0, r_fonts)
        r_fonts.set(qn("w:ascii"), name)
        r_fonts.set(qn("w:hAnsi"), name)
        r_fonts.set(qn("w:eastAsia"), name)

    def set_cell_margins(cell, top=0, start=0, bottom=0, end=0):
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_mar = tc_pr.first_child_found_in("w:tcMar")
        if tc_mar is None:
            tc_mar = OxmlElement("w:tcMar")
            tc_pr.append(tc_mar)
        for tag, value in (("top", top), ("start", start),
                           ("bottom", bottom), ("end", end)):
            node = tc_mar.find(qn(f"w:{tag}"))
            if node is None:
                node = OxmlElement(f"w:{tag}")
                tc_mar.append(node)
            node.set(qn("w:w"), str(int(round(value * 20))))
            node.set(qn("w:type"), "dxa")

    def set_cell_borders(cell, **edges):
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = tc_pr.first_child_found_in("w:tcBorders")
        if borders is None:
            borders = OxmlElement("w:tcBorders")
            tc_pr.append(borders)
        for edge, options in edges.items():
            node = borders.find(qn(f"w:{edge}"))
            if node is None:
                node = OxmlElement(f"w:{edge}")
                borders.append(node)
            for key, value in options.items():
                node.set(qn(f"w:{key}"), str(value))

    def set_cell_fill(cell, color):
        tc_pr = cell._tc.get_or_add_tcPr()
        shading = tc_pr.first_child_found_in("w:shd")
        if shading is None:
            shading = OxmlElement("w:shd")
            tc_pr.append(shading)
        shading.set(qn("w:fill"), color.lstrip("#"))

    def set_table_geometry(table, widths_pt, indent_pt=0):
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        tbl_pr = table._tbl.tblPr
        tbl_w = tbl_pr.first_child_found_in("w:tblW")
        if tbl_w is None:
            tbl_w = OxmlElement("w:tblW")
            tbl_pr.append(tbl_w)
        tbl_w.set(qn("w:type"), "dxa")
        tbl_w.set(qn("w:w"), str(int(round(sum(widths_pt) * 20))))
        tbl_ind = tbl_pr.first_child_found_in("w:tblInd")
        if tbl_ind is None:
            tbl_ind = OxmlElement("w:tblInd")
            tbl_pr.append(tbl_ind)
        tbl_ind.set(qn("w:type"), "dxa")
        tbl_ind.set(qn("w:w"), str(int(round(indent_pt * 20))))
        layout = tbl_pr.first_child_found_in("w:tblLayout")
        if layout is None:
            layout = OxmlElement("w:tblLayout")
            tbl_pr.append(layout)
        layout.set(qn("w:type"), "fixed")
        grid = table._tbl.tblGrid
        for child in list(grid):
            grid.remove(child)
        for width in widths_pt:
            col = OxmlElement("w:gridCol")
            col.set(qn("w:w"), str(int(round(width * 20))))
            grid.append(col)
        for row in table.rows:
            for cell, width in zip(row.cells, widths_pt):
                tc_w = cell._tc.get_or_add_tcPr().first_child_found_in("w:tcW")
                if tc_w is None:
                    tc_w = OxmlElement("w:tcW")
                    cell._tc.get_or_add_tcPr().append(tc_w)
                tc_w.set(qn("w:type"), "dxa")
                tc_w.set(qn("w:w"), str(int(round(width * 20))))

    def add_exact_spacer(height_pt):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        paragraph.paragraph_format.line_spacing = Pt(height_pt)
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run("\u200b")
        run.font.size = Pt(1)
        run.font.color.rgb = RGBColor(255, 255, 255)

    def clear_table_borders(cell):
        nil = {"val": "nil"}
        set_cell_borders(cell, top=nil, left=nil, bottom=nil, right=nil,
                         insideH=nil, insideV=nil)

    def color_strip_image(color_values=EXPORT_COLORS, width=1400, height=28, gap=3):
        """用内存 PNG 固定色条宽度，避免不同 Word 引擎压缩空表格。"""
        colors_rgb = [
            tuple(bytes.fromhex(color.lstrip("#"))) for color in color_values
        ]
        usable = width - gap * (len(colors_rgb) - 1)
        boundaries = [round(index * usable / len(colors_rgb))
                      for index in range(len(colors_rgb) + 1)]
        pixels = []
        for index, color in enumerate(colors_rgb):
            pixels.extend([color] * (boundaries[index + 1] - boundaries[index]))
            if index < len(colors_rgb) - 1:
                pixels.extend([(255, 255, 255)] * gap)
        row = b"\x00" + b"".join(bytes(pixel) for pixel in pixels)
        raw = row * height

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        stream = io.BytesIO(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )
        stream.seek(0)
        return stream

    document = Document()
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.left_margin = Pt(left_margin_pt)
    section.right_margin = Pt(right_margin_pt)
    section.top_margin = Pt(14.84)
    section.bottom_margin = Pt(40)
    section.footer_distance = Pt(9)
    document.core_properties.title = payload["title"]
    document.core_properties.subject = "WordGrab 本地语音转录稿"
    document.core_properties.author = "WordGrab"

    styles = document.styles
    styles["Normal"].font.name = body_font
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), body_font)
    styles["Normal"].font.size = Pt(14)
    normal_format = styles["Normal"].paragraph_format
    normal_format.space_before = Pt(0)
    normal_format.space_after = Pt(0)

    header = document.add_table(rows=1, cols=2)
    set_table_geometry(header, [47.2, content_width_pt - 47.2])
    header.rows[0].height = Pt(40)
    header.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    icon_cell, brand_cell = header.rows[0].cells
    for cell in (icon_cell, brand_cell):
        clear_table_borders(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    set_cell_margins(icon_cell)
    set_cell_margins(brand_cell)
    icon_paragraph = icon_cell.paragraphs[0]
    icon_paragraph.paragraph_format.space_after = Pt(0)
    icon_paragraph.add_run().add_picture(
        os.path.join(UI, "icon_1024.png"), width=Pt(40), height=Pt(40))
    brand_paragraph = brand_cell.paragraphs[0]
    brand_paragraph.paragraph_format.space_after = Pt(0)
    set_run_font(brand_paragraph.add_run("WordGrab"), brand_font, 20, "191919")

    add_exact_spacer(9.68)
    rainbow = document.add_table(rows=1, cols=1)
    set_table_geometry(rainbow, [content_width_pt])
    rainbow.rows[0].height = Pt(10)
    rainbow.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    rainbow_cell = rainbow.cell(0, 0)
    set_cell_margins(rainbow_cell)
    clear_table_borders(rainbow_cell)
    rainbow_paragraph = rainbow_cell.paragraphs[0]
    rainbow_paragraph.paragraph_format.space_before = Pt(0)
    rainbow_paragraph.paragraph_format.space_after = Pt(0)
    rainbow_paragraph.add_run().add_picture(
        color_strip_image(), width=Pt(content_width_pt), height=Pt(10))

    add_exact_spacer(16.87)
    metadata = document.add_table(rows=2, cols=3)
    metadata_width = content_width_pt - 1
    metadata_col = metadata_width / 3
    set_table_geometry(metadata, [metadata_col] * 3, indent_pt=1)
    labels = ("录制时间", "时长 · 字数", "参会人")
    values = (payload["recorded_text"],
              f"{payload['duration_text']} · {payload['word_count_text']}",
              payload["participants_text"])
    for row_index, row in enumerate(metadata.rows):
        row.height = Pt(20)
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        for cell_index, cell in enumerate(row.cells):
            set_cell_margins(cell, top=1.5, start=4.2, bottom=0, end=4.2)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            border = {"val": "single", "sz": "2", "color": "000000"}
            set_cell_borders(cell, top=border, left=border, bottom=border, right=border)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            paragraph.paragraph_format.line_spacing = Pt(13)
            text = labels[cell_index] if row_index == 0 else values[cell_index]
            set_run_font(paragraph.add_run(text), body_font, 12, "808080",
                         bold=row_index == 1)

    add_exact_spacer(18.88)
    for row in payload["rows"]:
        block = document.add_table(rows=1, cols=1)
        set_table_geometry(block, [content_width_pt])
        cell = block.cell(0, 0)
        set_cell_margins(cell, top=14, start=18, bottom=19, end=0)
        set_cell_borders(
            cell,
            top={"val": "nil"},
            left={"val": "single", "sz": "48", "color": row["color"].lstrip("#")},
            bottom={"val": "nil"},
            right={"val": "nil"},
        )
        heading = cell.paragraphs[0]
        heading.paragraph_format.space_after = Pt(7)
        heading.paragraph_format.keep_with_next = True
        heading.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        heading.paragraph_format.line_spacing = Pt(20)
        set_run_font(heading.add_run(row["speaker"]), body_font, 16,
                     row["color"].lstrip("#"))
        set_run_font(
            heading.add_run(f"  {row['start_time']} - {row['end_time']}"),
            body_font, 12, "8A8A8A")
        body = cell.add_paragraph()
        body.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        body.paragraph_format.line_spacing = Pt(23)
        set_run_font(body.add_run(row["text"]), body_font, 14, "191919")
        add_exact_spacer(8)

    footer = section.footer
    footer_middle_width = content_width_pt - 112
    footer_table = footer.add_table(rows=1, cols=3, width=Pt(content_width_pt))
    set_table_geometry(footer_table, [100, footer_middle_width, 12])
    footer_table.rows[0].height = Pt(20)
    footer_table.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    footer_brand_cell, footer_spacer_cell, footer_page_cell = footer_table.rows[0].cells
    for cell in footer_table.rows[0].cells:
        set_cell_margins(cell)
        clear_table_borders(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    footer_brand = footer_brand_cell.paragraphs[0]
    footer_brand.paragraph_format.space_before = Pt(0)
    footer_brand.paragraph_format.space_after = Pt(0)
    set_run_font(footer_brand.add_run("●"), "Arial", 11, "191919")
    set_run_font(footer_brand.add_run("  WordGrab"), brand_font, 14, "8A8A8A")
    footer_spacer = footer_spacer_cell.paragraphs[0]
    footer_spacer.paragraph_format.space_before = Pt(0)
    footer_spacer.paragraph_format.space_after = Pt(0)
    footer_spacer.add_run().add_picture(
        color_strip_image(("#FFFFFF",), width=1000, height=2, gap=0),
        width=Pt(footer_middle_width - 2), height=Pt(1))
    footer_page = footer_page_cell.paragraphs[0]
    footer_page.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer_page.paragraph_format.space_before = Pt(0)
    footer_page.paragraph_format.space_after = Pt(0)
    page_run = footer_page.add_run()
    set_run_font(page_run, body_font, 10, "8A8A8A")
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    value = OxmlElement("w:t")
    value.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instruction, separate, value, end):
        page_run._r.append(node)
    for paragraph in list(footer.paragraphs):
        paragraph._element.getparent().remove(paragraph._element)
    document.save(path)


def write_pdf_export(path, payload):
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

    font_name = "WordGrabHeiti"
    light_font = "WordGrabHeitiLight"
    brand_font = "WordGrabArialBlack"

    def register_font(name, candidates):
        if name in pdfmetrics.getRegisteredFontNames():
            return name
        for filename, subfont_index in candidates:
            if os.path.isfile(filename):
                try:
                    pdfmetrics.registerFont(
                        TTFont(name, filename, subfontIndex=subfont_index))
                    return name
                except Exception:
                    continue
        return None

    registered_body = register_font(font_name, (
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ))
    registered_light = register_font(light_font, (
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ))
    registered_brand = register_font(brand_font, (
        ("/System/Library/Fonts/Supplemental/Arial Black.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ))
    if not registered_body:
        font_name = "STSong-Light"
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    if not registered_light:
        light_font = font_name
    if not registered_brand:
        brand_font = "Helvetica-Bold"

    body_style = ParagraphStyle(
        "WordGrabBody", fontName=font_name, fontSize=14, leading=23,
        textColor=colors.HexColor("#191919"), wordWrap="CJK",
    )
    heading_style = ParagraphStyle(
        "WordGrabSpeakerHeading", fontName=font_name, fontSize=16, leading=20,
        textColor=colors.HexColor("#191919"), wordWrap="CJK",
    )

    class BrandHeader(Flowable):
        def __init__(self, width):
            super().__init__()
            self.width = width
            self.height = 40

        def wrap(self, available_width, available_height):
            return self.width, self.height

        def draw(self):
            self.canv.drawImage(
                os.path.join(UI, "icon_1024.png"), 0, 0, 40, 40,
                preserveAspectRatio=True, mask="auto")
            self.canv.setFillColor(colors.HexColor("#191919"))
            self.canv.setFont(brand_font, 20)
            self.canv.drawString(47.2, 5.9, "WordGrab")

    class RainbowStrip(Flowable):
        def __init__(self, width):
            super().__init__()
            self.width = width
            self.height = 10

        def wrap(self, available_width, available_height):
            return self.width, self.height

        def draw(self):
            gap = 1
            segment_width = (self.width - gap * (len(EXPORT_COLORS) - 1)) / len(EXPORT_COLORS)
            x = 0
            for color in EXPORT_COLORS:
                self.canv.setFillColor(colors.HexColor(color))
                self.canv.rect(x, 0, segment_width, self.height, stroke=0, fill=1)
                x += segment_width + gap

    class MetadataSummary(Flowable):
        def __init__(self, width):
            super().__init__()
            self.width = width
            self.height = 39.77

        def wrap(self, available_width, available_height):
            return self.width, self.height

        def draw(self):
            canvas = self.canv
            col_width = self.width / 3
            canvas.setStrokeColor(colors.black)
            canvas.setLineWidth(.2)
            canvas.rect(0, 0, self.width, self.height, stroke=1, fill=0)
            canvas.line(0, 20.58, self.width, 20.58)
            canvas.line(col_width, 0, col_width, self.height)
            canvas.line(col_width * 2, 0, col_width * 2, self.height)
            labels = ("录制时间", "时长 · 字数", "参会人")
            values = (payload["recorded_text"],
                      f"{payload['duration_text']} · {payload['word_count_text']}",
                      payload["participants_text"])
            canvas.setFillColor(colors.HexColor("#808080"))
            canvas.setFont(light_font, 12)
            for index, label in enumerate(labels):
                canvas.drawString(index * col_width + 4.2, 24.5, label)
            canvas.setFont(font_name, 12)
            for index, value in enumerate(values):
                canvas.drawString(index * col_width + 4.2, 5.3, value)

    class SpeakerBlock(Flowable):
        def __init__(self, row, width):
            super().__init__()
            self.row = row
            self.width = width
            self.content_width = width - 18
            heading = (
                f'<font color="{row["color"]}">{escape(row["speaker"])}</font>'
                f'<font size="12" color="#8A8A8A">  '
                f'{escape(row["start_time"])} - {escape(row["end_time"])}</font>'
            )
            self.heading = Paragraph(heading, heading_style)
            self.body = Paragraph(escape(row["text"]), body_style)
            self.heading_height = 0
            self.body_height = 0
            self.height = 0

        def wrap(self, available_width, available_height):
            _, self.heading_height = self.heading.wrap(self.content_width, available_height)
            _, self.body_height = self.body.wrap(self.content_width, available_height)
            self.height = 14 + self.heading_height + 6 + self.body_height + 19
            return self.width, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor(self.row["color"]))
            canvas.setLineWidth(6)
            canvas.setLineCap(1)
            canvas.line(0, 3, 0, self.height - 3)
            canvas.restoreState()
            body_y = 19
            self.body.drawOn(canvas, 18, body_y)
            self.heading.drawOn(canvas, 18, body_y + self.body_height + 6)

    page_width, _ = A4
    left_margin, right_margin = 40, 41
    content_width = page_width - left_margin - right_margin
    story = [
        BrandHeader(content_width),
        Spacer(1, 9.68),
        RainbowStrip(content_width),
        Spacer(1, 16.87),
        MetadataSummary(content_width - 1),
        Spacer(1, 18.88),
    ]
    for row in payload["rows"]:
        story.append(SpeakerBlock(row, content_width))
        story.append(Spacer(1, 8))

    document = SimpleDocTemplate(
        path, pagesize=A4, rightMargin=right_margin, leftMargin=left_margin,
        topMargin=14.84, bottomMargin=40,
        title=payload["title"], author="WordGrab",
        subject="WordGrab 本地语音转录稿",
    )

    def draw_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#191919"))
        canvas.circle(left_margin + 6, 16, 5, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor("#8A8A8A"))
        canvas.setFont(brand_font, 14)
        canvas.drawString(left_margin + 18, 10, "WordGrab")
        canvas.setFont(font_name, 10)
        canvas.drawRightString(A4[0] - right_margin, 18, f"{doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)


def item_dir(iid):
    return os.path.join(DATA, iid)


def load_item(iid):
    with open(os.path.join(item_dir(iid), "transcript.json"), encoding="utf-8") as f:
        return json.load(f)


def save_item(iid, data):
    path = os.path.join(item_dir(iid), "transcript.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def _valid_hex_color(value):
    if not isinstance(value, str):
        return None
    color = value.strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", color):
        return color
    return None


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
    return {
        str(index): palette[position % len(palette)]
        for position, index in enumerate(sorted(int(i) for i in indexes))
    }


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
    d = item_dir(iid)
    for fn in os.listdir(d):
        if fn.startswith("audio"):
            return os.path.join(d, fn)
    return None


# ---------- 本地 HTTP 服务（UI + 支持 Range 的音频） ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_file(self, path, ctype):
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404); return
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(UI, "index.html"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            fn = os.path.basename(path)
            fp = os.path.join(UI, fn)
            if os.path.exists(fp):
                ext = os.path.splitext(fn)[1].lower()
                ctype = {
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".png": "image/png",
                    ".svg": "image/svg+xml",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                }.get(ext, "application/octet-stream")
                return self._send_file(fp, ctype)
        if path.startswith("/audio/"):
            iid = path.split("/audio/")[1]
            ap = audio_path_of(iid)
            if ap:
                ext = os.path.splitext(ap)[1].lower()
                return self._send_file(ap, AUDIO_EXT.get(ext, "application/octet-stream"))
        if path.startswith("/status/"):
            iid = path.split("/status/")[1]
            st = PROGRESS.get(iid) or {"status": "unknown"}
            body = json.dumps(st, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        # 拖拽上传：WebView 里拿不到文件路径，只能把字节流 POST 过来落盘再转写
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(self.path)
        if parsed.path != "/upload" or API_REF is None:
            return self.send_error(404)
        q = parse_qs(parsed.query)
        name = unquote(q.get("name", ["audio"])[0])
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return self.send_error(400, "无效的文件大小")
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            return self.send_error(413, "文件过大，单个文件不能超过 2GB")
        iid = uuid.uuid4().hex[:12]
        os.makedirs(item_dir(iid), exist_ok=True)
        ext = os.path.splitext(name)[1].lower() or ".m4a"
        dst = os.path.join(item_dir(iid), "audio" + ext)
        try:
            remaining = length
            with open(dst, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                raise OSError("上传未完成")
        except Exception as e:
            shutil.rmtree(item_dir(iid), ignore_errors=True)
            self.send_error(500, str(e)); return
        title = os.path.splitext(os.path.basename(name))[0]
        PROGRESS[iid] = {"stage": "准备中…", "pct": None, "info": None, "status": "running",
                         "msg": "", "title": title, "partial": []}
        threading.Thread(target=API_REF._run_transcribe, args=(iid, dst, title),
                         daemon=True).start()
        body = json.dumps({"id": iid, "title": title}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port


# ---------- JS ↔ Python 桥 ----------

class Api:
    def __init__(self):
        self.window = None
        self.port = None

    # 历史列表
    def list_items(self):
        items = load_index()
        for item in items:
            progress = PROGRESS.get(item.get("id"))
            if progress and progress.get("status") in {"running", "draft", "error"}:
                item["status"] = progress.get("status")
                if progress.get("status") == "error":
                    item["error"] = progress.get("msg", "")
        return items

    # 前端埋点日志
    def log(self, msg):
        print(f"[JS] {msg}", flush=True)
        return True

    # 设置（写入本地 data/settings.json，重启后继续生效）
    def get_settings(self):
        return load_settings()

    def update_settings(self, patch):
        return apply_settings_patch(patch)

    def set_theme(self, theme):
        if theme not in THEME_KEYS:
            return False
        apply_settings_patch({"theme": theme})
        return True

    def pick_export_directory(self):
        import webview
        current = load_settings()["export_directory"]
        try:
            res = self.window.create_file_dialog(webview.FOLDER_DIALOG, directory=current)
        except Exception as e:
            print(f"[bridge] 选择导出目录失败: {e!r}", flush=True)
            return None
        if not res:
            return None
        directory = res[0] if isinstance(res, (list, tuple)) else res
        if directory and os.path.isdir(directory):
            apply_settings_patch({"export_directory": directory})
            return directory
        return None

    def pick_data_directory(self):
        """只选择目录；实际搬迁要等前端二次确认后再执行。"""
        import webview
        current_parent = os.path.dirname(DATA)
        start_directory = current_parent if os.path.isdir(current_parent) else DATA
        try:
            res = self.window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=start_directory,
            )
        except Exception as e:
            print(f"[bridge] 选择数据目录失败: {e!r}", flush=True)
            return None
        if not res:
            return None
        directory = res[0] if isinstance(res, (list, tuple)) else res
        if directory and os.path.isdir(directory):
            return os.path.abspath(os.path.expanduser(directory))
        return None

    def set_data_directory(self, directory):
        return migrate_data_directory(directory)

    def get_system_info(self):
        import engine
        try:
            ffmpeg_path = engine._resolve_ffmpeg()
            ffmpeg_ok = True
        except Exception:
            ffmpeg_path = "未找到"
            ffmpeg_ok = False
        caches = model_cache_dirs()
        return {
            "version": APP_VERSION,
            "data_path": DATA,
            "data_size": path_size(DATA),
            "model_path": os.path.expanduser("~/.cache/modelscope"),
            "model_size": sum(path_size(path) for path in caches),
            "model_ready": bool(caches),
            "ffmpeg_ok": ffmpeg_ok,
            "ffmpeg_path": ffmpeg_path,
        }

    def open_local_resource(self, resource):
        allowed = {
            "data": DATA,
            "models": os.path.expanduser("~/.cache/modelscope"),
            "readme": os.path.join(HERE, "README.md"),
            "license": os.path.join(HERE, "LICENSE"),
        }
        path = allowed.get(resource)
        if not path or not os.path.exists(path):
            return False
        try:
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    # 打开某条：返回合并后的分段 + 说话人名 + 音频地址
    def open_item(self, iid):
        import engine
        data = load_item(iid)
        speaker_colors, colors_changed = ensure_speaker_colors(iid, data)
        if colors_changed:
            save_item(iid, data)
        spk_pending = bool(data.get("spk_pending"))
        # 初稿全部 spk=0，按说话人合并会揉成一大段，保留分块粒度
        merged = data["segments"] if spk_pending else engine.merge_by_speaker(data["segments"])
        meta = next((x for x in load_index() if x["id"] == iid), {})
        audio_path = audio_path_of(iid)
        audio_format = (os.path.splitext(audio_path)[1].lstrip(".").upper()
                        if audio_path else "音频")
        return {
            "id": iid,
            "title": meta.get("title", ""),
            "duration": meta.get("duration", 0),
            "created": meta.get("created", ""),
            "audio_format": audio_format,
            "speakers": data.get("speakers", {}),
            "speaker_colors": speaker_colors,
            "segments": merged,
            "spk_pending": spk_pending,
            "audio_url": f"http://127.0.0.1:{self.port}/audio/{iid}",
        }

    # 选文件（原生对话框）
    def pick_file(self):
        import webview
        print("[bridge] pick_file 进入", flush=True)
        try:
            res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            print(f"[bridge] pick_file 返回: {res}", flush=True)
        except Exception as e:
            print(f"[bridge] pick_file 异常: {e}", flush=True)
            return None
        if res:
            return res[0] if isinstance(res, (list, tuple)) else res
        return None

    # 开始转写（后台线程；进度写入 PROGRESS，前端轮询 /status/<iid>）
    def start_transcribe(self, src_path):
        iid = uuid.uuid4().hex[:12]
        title = os.path.splitext(os.path.basename(src_path))[0]
        with INDEX_LOCK:
            items = load_index()
            items.insert(0, {"id": iid, "title": title, "duration": 0,
                             "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "n_speakers": 0, "status": "queued"})
            save_index(items)
        PROGRESS[iid] = {"stage": "准备中…", "pct": None, "info": None, "status": "running",
                        "msg": "", "title": title, "partial": []}
        threading.Thread(target=self._do_transcribe, args=(iid, src_path), daemon=True).start()
        return iid

    @staticmethod
    def _set(iid, stage, pct=None, info=None, status=None, msg=None, **extra):
        # 合并更新（partial/title 等字段跨多次 _set 保留）
        st = PROGRESS.setdefault(iid, {})
        st.update({"stage": stage, "pct": pct})
        if info is not None:
            st["info"] = {**(st.get("info") or {}), **info}
        if status is not None:
            st["status"] = status
        if msg is not None:
            st["msg"] = msg
        st.update(extra)

    def _do_transcribe(self, iid, src_path):
        # 走「导入按钮」：先把源文件复制进条目目录，再复用转写流程
        try:
            os.makedirs(item_dir(iid), exist_ok=True)
            ext = os.path.splitext(src_path)[1].lower() or ".m4a"
            dst = os.path.join(item_dir(iid), "audio" + ext)
            shutil.copy2(src_path, dst)
            title = os.path.splitext(os.path.basename(src_path))[0]
        except Exception as e:
            self._set(iid, "出错", status="error", msg=str(e))
            return
        self._run_transcribe(iid, dst, title)

    def _run_transcribe(self, iid, audio_file, title):
        # 对已落盘的 audio_file 执行转写（导入按钮 / 拖拽上传共用）
        # 两阶段：①快路径初稿（渐进出字，无说话人）→ 存盘可读；②声纹分离 → 更新说话人
        import engine
        settings = load_settings()
        auto_diarization = settings["auto_diarization"]
        transcription_mode = settings["transcription_mode"]
        print(f"[transcribe] start iid={iid} file={audio_file}", flush=True)
        if TRANSCRIBE_LOCK.locked():
            self._set(iid, "排队中，等待上一个转写完成…")
        with TRANSCRIBE_LOCK:
            wav = None
            try:
                def prog(stage, pct, info=None):
                    print(f"[transcribe] {iid} · {stage} pct={pct}", flush=True)
                    self._set(iid, stage, pct, info)

                dur = engine.probe_duration(audio_file)
                self._set(iid, "解码 + 响度归一化…", None, {"duration": dur}, title=title)
                wav = engine.to_wav16k(audio_file)

                # ---- 阶段一：初稿，逐块推给前端 ----
                def on_chunk(segs):
                    st = PROGRESS.get(iid) or {}
                    st["partial"] = segs
                    PROGRESS[iid] = st

                draft = engine.transcribe_draft(wav, progress=prog, on_chunk=on_chunk)
                if iid in DELETED:
                    print(f"[transcribe] item deleted during draft phase iid={iid}", flush=True)
                    self._set(iid, "已删除", status="error", msg="记录已被删除")
                    return
                if draft:
                    needs_second_pass = auto_diarization or transcription_mode == "accuracy"
                    save_item(iid, {"speakers": {"0": "说话人1"},
                                    "speaker_colors": make_speaker_colors(iid, [0]),
                                    "segments": draft,
                                    "spk_pending": needs_second_pass})
                    with INDEX_LOCK:
                        items = load_index()
                        existing = next((item for item in items if item.get("id") == iid), None)
                        if existing:
                            existing.update({"duration": dur, "n_speakers": 1, "status": "draft"})
                        else:
                            items.insert(0, {"id": iid, "title": title, "duration": dur,
                                             "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                             "n_speakers": 1, "status": "draft"})
                        save_index(items)
                    if not needs_second_pass:
                        with INDEX_LOCK:
                            items = load_index()
                            for item in items:
                                if item.get("id") == iid: item["status"] = "done"
                            save_index(items)
                        self._set(iid, "完成", 1.0, status="done")
                        print(f"[transcribe] speed draft done iid={iid} segs={len(draft)}", flush=True)
                        return
                    pending_stage = ("文稿就绪，说话人分离中…" if auto_diarization
                                     else "文稿就绪，精细校对中…")
                    self._set(iid, pending_stage, None, status="draft")
                    print(f"[transcribe] draft ready iid={iid} segs={len(draft)}", flush=True)

                # ---- 阶段二：完整管线（含声纹分离） ----
                segments = engine.transcribe_full(wav, progress=prog, mode=transcription_mode)
                if not auto_diarization:
                    segments = [{**segment, "spk": 0} for segment in segments]
                n_spk = len({s["spk"] for s in segments}) or 1
                speakers = {str(i): f"说话人{i + 1}" for i in range(n_spk)}

                if iid in DELETED or not os.path.isdir(item_dir(iid)):
                    # 用户在阶段二期间删除了这条记录，直接放弃
                    print(f"[transcribe] item deleted during spk phase iid={iid}", flush=True)
                    self._set(iid, "已删除", status="error", msg="记录已被删除")
                    return
                save_item(iid, {"speakers": speakers,
                                "speaker_colors": make_speaker_colors(iid, range(n_spk)),
                                "segments": segments})

                with INDEX_LOCK:
                    items = load_index()
                    if not any(x["id"] == iid for x in items):
                        items.insert(0, {
                            "id": iid, "title": title, "duration": dur,
                            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "n_speakers": n_spk,
                            "status": "done",
                        })
                    else:
                        for x in items:
                            if x["id"] == iid:
                                x["n_speakers"] = n_spk
                                x["status"] = "done"
                                x.pop("error", None)
                    save_index(items)
                print(f"[transcribe] done iid={iid} segs={len(segments)}", flush=True)
                self._set(iid, "完成", 1.0, status="done")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[transcribe-error] iid={iid} {e!r}", flush=True)
                self._set(iid, "出错", status="error", msg=str(e))
                with INDEX_LOCK:
                    items = load_index()
                    existing = next((item for item in items if item.get("id") == iid), None)
                    if existing:
                        existing["status"] = "error"
                        existing["error"] = str(e)
                    else:
                        items.insert(0, {"id": iid, "title": title, "duration": 0,
                                         "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                         "n_speakers": 0, "status": "error", "error": str(e)})
                    save_index(items)
            finally:
                if wav:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass

    # 说话人改名
    def rename_speaker(self, iid, spk_index, new_name):
        data = load_item(iid)
        data.setdefault("speakers", {})[str(spk_index)] = new_name
        save_item(iid, data)
        return True

    # 记录改名
    def rename_item(self, iid, title):
        items = load_index()
        for x in items:
            if x["id"] == iid:
                x["title"] = title
        save_index(items)
        return True

    def bulk_delete_items(self, ids):
        results = []
        for iid in ids or []:
            if any(item.get("id") == iid for item in load_index()):
                result = self.delete_item(iid)
                if result.get("ok"):
                    results.append(iid)
        return {"ok": True, "count": len(results)}

    def bulk_export_items(self, ids, export_format):
        settings = load_settings()
        fmt = str(export_format or "").lower()
        if fmt not in {"txt", "pdf", "docx"}:
            return {"ok": False, "message": "不支持的导出格式"}
        directory = settings["export_directory"]
        try:
            os.makedirs(directory, exist_ok=True)
            exported = 0
            for iid in ids or []:
                payload = export_payload(iid)
                title = safe_filename(payload["title"] or iid)
                output = os.path.join(directory, f"{title}-{iid}.{fmt}")
                if fmt == "txt": write_txt_export(output, payload)
                elif fmt == "pdf": write_pdf_export(output, payload)
                else: write_docx_export(output, payload)
                exported += 1
            return {"ok": True, "count": exported, "directory": directory}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def update_segment(self, iid, segment_index, text):
        data = load_item(iid)
        segments = data.get("segments", [])
        index = int(segment_index)
        if index < 0 or index >= len(segments):
            return False
        text = str(text or "").strip()
        if not text:
            return False
        segments[index]["text"] = text
        save_item(iid, data)
        return True

    def retry_item(self, iid):
        audio_file = audio_path_of(iid)
        if not audio_file or not os.path.isfile(audio_file):
            return {"ok": False, "message": "找不到原始录音"}
        meta = next((x for x in load_index() if x["id"] == iid), {})
        PROGRESS[iid] = {"stage": "准备重试…", "pct": None, "info": None,
                         "status": "running", "msg": "", "title": meta.get("title", iid), "partial": []}
        DELETED.discard(iid)
        threading.Thread(target=self._run_transcribe,
                         args=(iid, audio_file, meta.get("title", iid)), daemon=True).start()
        return {"ok": True}

    def retry_diarization(self, iid):
        audio_file = audio_path_of(iid)
        if not audio_file or not os.path.isfile(audio_file):
            return {"ok": False, "message": "找不到原始录音"}
        meta = next((x for x in load_index() if x.get("id") == iid), {})
        PROGRESS[iid] = {"stage": "准备重新进行说话人分离…", "pct": None,
                         "info": None, "status": "running", "msg": "", "title": meta.get("title", iid), "partial": []}
        threading.Thread(target=self._run_diarization,
                         args=(iid, audio_file, meta.get("title", iid)), daemon=True).start()
        return {"ok": True}

    def _run_diarization(self, iid, audio_file, title):
        import engine
        wav = None
        with TRANSCRIBE_LOCK:
            try:
                wav = engine.to_wav16k(audio_file)
                settings = load_settings()
                segments = engine.transcribe_full(wav, progress=lambda stage, pct, info=None:
                                                   self._set(iid, stage, pct, info),
                                                  mode=settings["transcription_mode"])
                if not settings["auto_diarization"]:
                    segments = [{**segment, "spk": 0} for segment in segments]
                speakers = {str(i): f"说话人{i + 1}" for i in sorted({s["spk"] for s in segments})} or {"0": "说话人1"}
                data = load_item(iid)
                data.update({"speakers": speakers, "segments": segments, "spk_pending": False})
                save_item(iid, data)
                with INDEX_LOCK:
                    items = load_index()
                    for item in items:
                        if item.get("id") == iid:
                            item["n_speakers"] = len(speakers)
                            item["status"] = "done"
                            item.pop("error", None)
                    save_index(items)
                self._set(iid, "完成", 1.0, status="done")
            except Exception as exc:
                self._set(iid, "说话人分离失败", None, status="error", msg=str(exc))
            finally:
                if wav:
                    try: os.unlink(wav)
                    except OSError: pass

    # 删除
    def delete_item(self, iid):
        DELETED.add(iid)  # 若该条还在转写，阻止后续阶段把它重新落盘
        items = load_index()
        meta = next((item for item in items if item["id"] == iid), {})
        if not load_settings()["delete_audio_with_transcript"]:
            try:
                preserve_item_audio(iid, meta.get("title") or iid)
            except OSError:
                pass
        items = [x for x in items if x["id"] != iid]
        save_index(items)
        source_dir = item_dir(iid)
        if os.path.isdir(source_dir):
            os.makedirs(TRASH, exist_ok=True)
            trash_dir = os.path.join(TRASH, iid)
            shutil.rmtree(trash_dir, ignore_errors=True)
            shutil.move(source_dir, trash_dir)
            with open(os.path.join(trash_dir, "meta.json"), "w", encoding="utf-8") as file:
                json.dump(meta, file, ensure_ascii=False)
        settings = load_settings()
        if settings.get("last_item_id") == iid:
            apply_settings_patch({"last_item_id": ""})
        return {"ok": True, "id": iid, "title": meta.get("title") or iid,
                "audio_preserved": not settings["delete_audio_with_transcript"], "undo": True}

    def restore_deleted_item(self, iid):
        source = os.path.join(TRASH, str(iid))
        target = item_dir(iid)
        if not os.path.isdir(source) or os.path.exists(target):
            return {"ok": False, "message": "这份文稿已无法恢复"}
        shutil.move(source, target)
        transcript = os.path.join(target, "transcript.json")
        if not os.path.isfile(transcript):
            shutil.rmtree(target, ignore_errors=True)
            return {"ok": False, "message": "恢复失败：文稿文件不完整"}
        data = load_item(iid)
        meta = {}
        try:
            with open(os.path.join(target, "meta.json"), encoding="utf-8") as file:
                meta = json.load(file)
            os.remove(os.path.join(target, "meta.json"))
        except (OSError, ValueError):
            pass
        items = load_index()
        if not any(x.get("id") == iid for x in items):
            items.insert(0, {"id": iid, "title": meta.get("title", iid),
                             "duration": meta.get("duration", 0),
                             "created": meta.get("created") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "n_speakers": meta.get("n_speakers") or len(data.get("speakers", {})) or 1})
            save_index(items)
        return {"ok": True}

    def clear_history(self):
        if TRANSCRIBE_LOCK.locked():
            return {"ok": False, "message": "仍有音频正在转写，请完成后再清理。"}
        keep_audio = not load_settings()["delete_audio_with_transcript"]
        items = load_index()
        for item in items:
            iid = item["id"]
            DELETED.add(iid)
            if keep_audio:
                try:
                    preserve_item_audio(iid, item.get("title") or iid)
                except OSError:
                    pass
            shutil.rmtree(item_dir(iid), ignore_errors=True)
        save_index([])
        apply_settings_patch({"last_item_id": ""})
        return {"ok": True, "count": len(items)}

    def clear_model_cache(self):
        if TRANSCRIBE_LOCK.locked():
            return {"ok": False, "message": "仍有音频正在转写，请完成后再清理模型。"}
        caches = model_cache_dirs()
        freed = sum(path_size(path) for path in caches)
        for path in caches:
            shutil.rmtree(path, ignore_errors=True)
        return {"ok": True, "freed": freed}

    # 导出 Word / PDF / TXT（弹原生保存对话框选位置）
    def export_document(self, iid, export_format=None):
        import webview
        settings = load_settings()
        fmt = export_format if export_format in {"docx", "pdf", "txt"} else settings["export_format"]
        payload = export_payload(iid)
        title = safe_filename(payload["title"])
        if settings["filename_rule"] == "source_date":
            date = (payload["created"] or datetime.datetime.now().strftime("%Y-%m-%d"))[:10].replace("-", "")
            title = f"{title}-{date}"
        default = f"{title}.{fmt}"
        directory = settings["export_directory"]
        res = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=default,
            directory=directory)
        if not res:
            return None
        out = res if isinstance(res, str) else res[0]
        if os.path.splitext(out)[1].lower() != f".{fmt}":
            out += f".{fmt}"
        if fmt == "docx":
            write_docx_export(out, payload)
        elif fmt == "pdf":
            write_pdf_export(out, payload)
        else:
            write_txt_export(out, payload)
        return out

    def export_txt(self, iid):
        return self.export_document(iid, "txt")


def _preload_model():
    # 启动即后台加载模型（约 30-40 秒），首次转写不用再等
    try:
        import engine
        engine.get_model()
        print("[preload] 模型加载完成", flush=True)
    except Exception as e:
        print(f"[preload] 模型加载失败（转写时会重试）: {e!r}", flush=True)


def _set_dock_icon():
    # 直接用 python 跑时进程会显示 Python 的火箭图标，这里换成本应用的。
    # 必须等主运行循环启动后再设，否则 app 完成启动时会被重置回火箭。
    try:
        import AppKit
        from Foundation import NSOperationQueue
        path = next((p for p in (os.path.join(HERE, "assets", "icon.icns"),
                                 os.path.join(HERE, "icon.icns"))
                     if os.path.exists(p)), None)
        if not path:
            print("[dock] 未找到 icon.icns，跳过", flush=True)
            return

        def apply():
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(path)
            if img:
                AppKit.NSApplication.sharedApplication().setApplicationIconImage_(img)
                print("[dock] Dock 图标已设置", flush=True)
            else:
                print("[dock] icns 加载失败", flush=True)

        NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception as e:
        print(f"[dock] 设置图标失败: {e!r}", flush=True)


def _integrate_native_titlebar(window):
    """让网页内容延伸到标题栏，同时保留 macOS 原生红黄绿按钮。

    这不是彻底移除 NSWindow 标题栏，而是把它变成透明的一体式区域。
    因此关闭、最小化、全屏、顶部拖动和双击缩放仍由 macOS 处理。
    """
    try:
        import AppKit

        native = window.native
        if native is None:
            print("[window] 原生窗口尚未就绪，跳过标题栏融合", flush=True)
            return

        full_size = getattr(
            AppKit,
            "NSWindowStyleMaskFullSizeContentView",
            getattr(AppKit, "NSFullSizeContentViewWindowMask", 1 << 15),
        )
        native.setStyleMask_(native.styleMask() | full_size)
        native.setTitlebarAppearsTransparent_(True)
        native.setTitleVisibility_(getattr(AppKit, "NSWindowTitleHidden", 1))
        native.setMovableByWindowBackground_(True)

        # macOS 11+ 可去掉标题栏与内容之间的系统分隔线。
        if hasattr(native, "setTitlebarSeparatorStyle_"):
            native.setTitlebarSeparatorStyle_(
                getattr(AppKit, "NSTitlebarSeparatorStyleNone", 1)
            )

        # 明确保留系统原生按钮；不在网页里重新绘制一套假的按钮。
        for kind in (
            AppKit.NSWindowCloseButton,
            AppKit.NSWindowMiniaturizeButton,
            AppKit.NSWindowZoomButton,
        ):
            button = native.standardWindowButton_(kind)
            if button is not None:
                button.setHidden_(False)

        print("[window] 已启用一体化原生标题栏", flush=True)
    except Exception as e:
        # 非 macOS 或 PyObjC 不可用时仍可退回普通标题栏启动。
        print(f"[window] 标题栏融合失败: {e!r}", flush=True)


def _install_native_drag_strip(window):
    """在 WebView 顶部覆盖一条透明 NSView，专门接管拖动和双击缩放。

    full-size content view 会让网页覆盖原生标题栏，WKWebView 因而会吞掉鼠标拖动。
    透明拖动带位于最上方 32pt，并从 x=88pt 开始避开红黄绿按钮。
    """
    try:
        import AppKit
        from Foundation import NSNotificationCenter, NSOperationQueue

        global _DRAG_STRIP_CLASS
        if _DRAG_STRIP_CLASS is None:
            class WordGrabDragStrip(AppKit.NSView):
                def acceptsFirstMouse_(self, event):
                    return True

                def mouseDown_(self, event):
                    native = self.window()
                    if native is None:
                        return
                    if event.clickCount() >= 2:
                        native.zoom_(None)
                    else:
                        native.performWindowDragWithEvent_(event)

            _DRAG_STRIP_CLASS = WordGrabDragStrip

        def apply():
            native = window.native
            if native is None or native.contentView() is None:
                print("[window] WebView 尚未就绪，跳过拖动带", flush=True)
                return

            key = id(native)
            center = NSNotificationCenter.defaultCenter()

            # loaded 理论上只触发一次；仍先清理旧监听，避免页面重新载入后重复回调。
            previous_state = _DRAG_OBSERVERS.pop(key, None)
            if previous_state:
                for token in previous_state.get("tokens", ()):
                    center.removeObserver_(token)
                for timer in previous_state.get("timers", ()):
                    try:
                        timer.cancel()
                    except Exception:
                        pass

            def sync_strip():
                """按当前 contentView 的真实坐标重新安装并置顶拖动带。"""
                content = native.contentView()
                if content is None:
                    return

                strip = _DRAG_STRIPS.get(key)
                if strip is None:
                    strip = _DRAG_STRIP_CLASS.alloc().initWithFrame_(
                        AppKit.NSMakeRect(0, 0, 0, 0)
                    )
                    _DRAG_STRIPS[key] = strip
                elif strip.superview() is not None and strip.superview() != content:
                    # 全屏切换等情况下 pywebview 可能替换 contentView。
                    strip.removeFromSuperview()

                bounds = content.bounds()
                drag_height = min(32.0, max(0.0, bounds.size.height))
                drag_left = min(88.0, max(0.0, bounds.size.width))
                is_flipped = bool(content.isFlipped())
                top_y = (bounds.origin.y if is_flipped else
                         bounds.origin.y + max(0.0, bounds.size.height - drag_height))
                frame = AppKit.NSMakeRect(
                    bounds.origin.x + drag_left,
                    top_y,
                    max(0.0, bounds.size.width - drag_left),
                    drag_height,
                )
                strip.setFrame_(frame)
                vertical_mask = (AppKit.NSViewMaxYMargin if is_flipped
                                 else AppKit.NSViewMinYMargin)
                strip.setAutoresizingMask_(AppKit.NSViewWidthSizable | vertical_mask)
                strip.setHidden_(False)

                # WKWebView 启动完成后可能会重新调整自己的子视图层级。
                # 每次同步都重新放到最上层，避免第一次打开时拖动带被网页盖住；
                # 这也是之前“调整高度后才恢复”的根因。
                if strip.superview() == content:
                    strip.removeFromSuperview()
                content.addSubview_positioned_relativeTo_(
                    strip, AppKit.NSWindowAbove, None
                )

            state = {"tokens": [], "callback": None, "pending": False, "timers": []}

            def request_sync(_notification=None):
                # 同一轮缩放可能连续发出多个通知；合并到主线程下一轮更新。
                if state["pending"]:
                    return
                state["pending"] = True

                def update():
                    state["pending"] = False
                    sync_strip()

                NSOperationQueue.mainQueue().addOperationWithBlock_(update)

            state["callback"] = request_sync  # 持有 Python block，避免被回收。
            notification_names = (
                AppKit.NSWindowDidResizeNotification,
                AppKit.NSWindowDidEndLiveResizeNotification,
                AppKit.NSWindowDidEnterFullScreenNotification,
                AppKit.NSWindowDidExitFullScreenNotification,
                AppKit.NSWindowDidChangeScreenNotification,
                AppKit.NSWindowDidChangeBackingPropertiesNotification,
                AppKit.NSWindowDidBecomeKeyNotification,
                AppKit.NSWindowDidBecomeMainNotification,
            )
            for name in notification_names:
                token = center.addObserverForName_object_queue_usingBlock_(
                    name, native, NSOperationQueue.mainQueue(), request_sync
                )
                state["tokens"].append(token)
            _DRAG_OBSERVERS[key] = state

            sync_strip()

            # 首次显示时 WebView 仍可能进行一到两轮布局。仅在 loaded 阶段
            # 安装会导致拖动带被后续布局盖住，直到 resize 通知才重新出现。
            # 在主线程延迟重试几次，确保刚打开就可以拖动。
            for delay in (0.05, 0.18, 0.45, 0.9):
                def retry_sync(_delay=delay):
                    NSOperationQueue.mainQueue().addOperationWithBlock_(sync_strip)

                timer = threading.Timer(delay, retry_sync)
                timer.daemon = True
                timer.start()
                state["timers"].append(timer)

            print("[window] 顶部透明拖动带已启用，并监听窗口尺寸/焦点变化（启动后自动复位）", flush=True)

        NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception as e:
        print(f"[window] 拖动带安装失败: {e!r}", flush=True)


def main():
    global API_REF
    import webview

    IS_MACOS = sys.platform == "darwin"

    api = Api()
    API_REF = api

    # Dock 图标设置只在 macOS 执行
    if IS_MACOS:
        _set_dock_icon()

    port = start_server()
    api.port = port
    threading.Thread(target=_preload_model, daemon=True).start()

    # macOS：无边框窗口 + 原生标题栏美化
    # Windows：普通窗口（自带标题栏、关闭按钮、拖动功能）
    window = webview.create_window(
        "WordGrab", url=f"http://127.0.0.1:{port}/",
        js_api=api,
        width=1120, height=740, min_size=(800, 600),
        background_color="#FFFFFF",
        frameless=IS_MACOS,  # 只在 macOS 无边框
        easy_drag=False,
    )
    api.window = window

    # macOS 专属窗口美化（一体式标题栏、自定义拖动条）
    if IS_MACOS:
        window.events.before_show += _integrate_native_titlebar
        window.events.loaded += _install_native_drag_strip
        # loaded 之后 WebView 还会继续完成原生布局；shown 阶段再安装一次，
        # 避免首次打开必须先调整窗口高度才能触发拖动带。
        window.events.shown += _install_native_drag_strip
        window.events.shown += _set_dock_icon

    webview.start(debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
