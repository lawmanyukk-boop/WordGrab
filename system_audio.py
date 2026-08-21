"""macOS ScreenCaptureKit system-audio bridge and safe speech mixer."""

from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np


SYSTEM_SAMPLE_RATE = 48000


def helper_path() -> Optional[str]:
    candidates = [
        os.environ.get("WORDGRAB_SYSTEM_AUDIO_HELPER", ""),
        str(Path(__file__).resolve().parent / "build" / "system_audio_capture"),
        str(Path(sys.executable).resolve().parent.parent / "Resources" / "system_audio_capture"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def support_status() -> Dict[str, Any]:
    if sys.platform != "darwin":
        return {"supported": False, "message": "系统音频录制目前仅支持 macOS"}
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        major = 0
    if major and major < 13:
        return {"supported": False, "message": "系统音频录制需要 macOS 13 或更高版本"}
    path = helper_path()
    if not path:
        return {"supported": False, "message": "系统音频组件尚未安装"}
    return {"supported": True, "message": "可用", "helper": path, "sample_rate": SYSTEM_SAMPLE_RATE}


def mix_for_speech(microphone, system, peak_limit: float = 0.89):
    """Mix aligned mono arrays with light speech-aware system ducking."""
    mic = np.asarray(microphone, dtype="float32").reshape(-1)
    sys_audio = np.asarray(system, dtype="float32").reshape(-1)
    length = max(len(mic), len(sys_audio))
    if length == 0:
        return np.empty(0, dtype="float32"), {"ducking": False, "system_gain": 1.0}
    if len(mic) < length:
        mic = np.pad(mic, (0, length - len(mic)))
    if len(sys_audio) < length:
        sys_audio = np.pad(sys_audio, (0, length - len(sys_audio)))
    mic_rms = float(np.sqrt(np.mean(np.square(mic, dtype="float64"))))
    system_rms = float(np.sqrt(np.mean(np.square(sys_audio, dtype="float64"))))
    speaking = mic_rms >= 0.006
    system_gain = 0.48 if speaking else 0.78
    # Avoid boosting an already dominant system track.
    if system_rms > 0.16:
        system_gain *= 0.75
    mixed = mic + sys_audio * system_gain
    limit = min(0.99, max(0.2, float(peak_limit)))
    mixed = np.tanh(mixed / limit) * limit
    return np.ascontiguousarray(mixed.astype("float32", copy=False)), {
        "ducking": speaking,
        "system_gain": round(system_gain, 3),
        "mic_rms": round(mic_rms, 7),
        "system_rms": round(system_rms, 7),
    }


class SystemAudioCapture:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._queue: queue.Queue = queue.Queue(maxsize=600)
        self._pending = np.empty(0, dtype="float32")
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._status: Dict[str, Any] = {
            "active": False, "ready": False, "error": "", "dropped_blocks": 0,
            "sample_rate": SYSTEM_SAMPLE_RATE,
        }

    def start(self, timeout: float = 12.0) -> Dict[str, Any]:
        support = support_status()
        if not support["supported"]:
            raise RuntimeError(support["message"])
        self.stop()
        self._ready.clear()
        self._queue = queue.Queue(maxsize=600)
        self._pending = np.empty(0, dtype="float32")
        process = subprocess.Popen(
            [support["helper"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
        )
        self._process = process
        with self._lock:
            self._status.update(active=True, ready=False, error="", dropped_blocks=0)

        threading.Thread(target=self._read_stdout, name="wordgrab-system-audio", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="wordgrab-system-audio-status", daemon=True).start()
        if not self._ready.wait(timeout):
            error = self.status().get("error") or "等待系统录音权限超时"
            self.stop()
            raise RuntimeError(error)
        status = self.status()
        if status.get("error"):
            self.stop()
            raise RuntimeError(status["error"])
        return status

    def _read_stdout(self):
        process = self._process
        if not process or not process.stdout:
            return
        remainder = b""
        while process.poll() is None:
            data = process.stdout.read(19200)
            if not data:
                break
            data = remainder + data
            byte_count = len(data) - (len(data) % 4)
            remainder = data[byte_count:]
            if byte_count:
                samples = np.frombuffer(data[:byte_count], dtype="<f4").copy()
                try:
                    self._queue.put_nowait(samples)
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    self._queue.put_nowait(samples)
                    with self._lock:
                        self._status["dropped_blocks"] = int(self._status.get("dropped_blocks") or 0) + 1
        with self._lock:
            self._status["active"] = False

    def _read_stderr(self):
        process = self._process
        if not process or not process.stderr:
            return
        while True:
            raw = process.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            with self._lock:
                self._status["message"] = line
                if line.startswith("READY"):
                    self._status["ready"] = True
                    self._ready.set()
                elif line.startswith("ERROR"):
                    self._status["error"] = line[5:].strip() or "系统音频录制失败"
                    self._ready.set()
        if not self._ready.is_set():
            with self._lock:
                self._status["error"] = self._status.get("error") or "系统音频组件意外退出"
            self._ready.set()

    def read_samples(self, count: int, timeout: float = 0.03, pad: bool = True) -> np.ndarray:
        target = max(0, int(count))
        deadline = time.monotonic() + max(0.0, float(timeout))
        while len(self._pending) < target:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = self._queue.get(timeout=remaining)
                self._pending = np.concatenate((self._pending, chunk))
            except queue.Empty:
                break
        output = self._pending[:target]
        self._pending = self._pending[len(output):]
        if pad and len(output) < target:
            output = np.pad(output, (0, target - len(output)))
        return np.ascontiguousarray(output.astype("float32", copy=False))

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {**self._status, "queued_blocks": self._queue.qsize()}

    def discard_buffer(self) -> None:
        self._pending = np.empty(0, dtype="float32")
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> Dict[str, Any]:
        process = self._process
        self._process = None
        if process:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
        with self._lock:
            self._status["active"] = False
            self._status["ready"] = False
        return self.status()
