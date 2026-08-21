"""Durable recording/transcription job journal."""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union


SCHEMA_VERSION = 1
FILENAME = "job-state.json"
ACTIVE_STATES = {"starting", "recording", "paused", "stopping", "draining", "finalizing", "queued", "running"}
RECOVERABLE_FINAL_STATES = {"stopping", "draining", "finalizing", "queued", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}


PathLike = Union[str, os.PathLike]


def journal_path(folder: PathLike) -> Path:
    return Path(folder) / FILENAME


def read(folder: PathLike) -> Optional[Dict[str, Any]]:
    path = journal_path(folder)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def write(folder: PathLike, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = journal_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = read(folder) or {}
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()
    merged = {
        **previous,
        **dict(payload or {}),
        "schema_version": SCHEMA_VERSION,
        "updated_at": now,
    }
    merged.setdefault("created_at", now)
    merged.setdefault("retry_count", 0)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(merged, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    return merged


def transition(
    folder: PathLike,
    status: str,
    stage: str = "",
    **details: Any,
) -> Dict[str, Any]:
    payload = {"status": str(status), **details}
    if stage:
        payload["stage"] = str(stage)
    return write(folder, payload)


def mark_retry(folder: PathLike, reason: str = "startup_recovery") -> Dict[str, Any]:
    current = read(folder) or {}
    return transition(
        folder,
        "queued",
        "异常退出后重新排队",
        retry_count=int(current.get("retry_count") or 0) + 1,
        retry_reason=reason,
        last_retry_at=time.time(),
    )


def public_summary(folder: PathLike) -> Optional[Dict[str, Any]]:
    value = read(folder)
    if not value:
        return None
    keys = (
        "status", "stage", "updated_at", "retry_count", "error",
        "input_device", "recording_source", "duration", "pipeline",
    )
    return {key: value.get(key) for key in keys if key in value}
