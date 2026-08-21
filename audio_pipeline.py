"""WordGrab realtime audio input helpers.

The recording file always receives the untouched device samples.  This module
only prepares a copy for live ASR, so changing enhancement settings can never
damage the user's source recording.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np


TARGET_SAMPLE_RATE = 16000
DEFAULT_BLOCK_SECONDS = 0.1
BLUETOOTH_MARKERS = (
    "airpods", "bluetooth", "wireless", "buds", "headset", "wh-", "wf-",
)


def is_bluetooth_device(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(marker in lowered for marker in BLUETOOTH_MARKERS)


def list_input_devices(sd_module=None) -> list[Dict[str, Any]]:
    """Return stable, JSON-safe microphone descriptions."""
    if sd_module is None:
        import sounddevice as sd_module

    try:
        default_input = int(sd_module.default.device[0])
    except (TypeError, ValueError, IndexError):
        default_input = -1
    try:
        hostapis = list(sd_module.query_hostapis())
    except Exception:
        hostapis = []

    devices = []
    for index, raw in enumerate(sd_module.query_devices()):
        channels = int(raw.get("max_input_channels") or 0)
        if channels <= 0:
            continue
        hostapi_index = int(raw.get("hostapi") or 0)
        hostapi = ""
        if 0 <= hostapi_index < len(hostapis):
            hostapi = str(hostapis[hostapi_index].get("name") or "")
        name = str(raw.get("name") or f"麦克风 {index}")
        devices.append({
            "id": index,
            "name": name,
            "sample_rate": int(round(float(raw.get("default_samplerate") or TARGET_SAMPLE_RATE))),
            "channels": channels,
            "hostapi": hostapi,
            "is_default": index == default_input,
            "is_bluetooth": is_bluetooth_device(name),
        })
    return devices


def resolve_input_device(preferred_name: str = "", sd_module=None) -> Tuple[Optional[int], Dict[str, Any], bool]:
    """Resolve a remembered device name, falling back to the system default."""
    devices = list_input_devices(sd_module)
    preferred = str(preferred_name or "").strip()
    if preferred:
        exact = next((device for device in devices if device["name"] == preferred), None)
        if exact:
            return exact["id"], exact, False
    default = next((device for device in devices if device["is_default"]), None)
    if default is None and devices:
        default = devices[0]
    if default is None:
        raise RuntimeError("没有检测到可用的麦克风")
    return default["id"], default, bool(preferred)


class StreamingResampler:
    """Small stateful linear resampler with phase continuity across callbacks.

    ASR consumes 16 kHz mono speech, while Core Audio devices commonly run at
    44.1/48 kHz. Keeping both the final sample and fractional phase prevents a
    discontinuity at every 100 ms callback boundary.
    """

    def __init__(self, input_rate: int, output_rate: int = TARGET_SAMPLE_RATE):
        self.input_rate = max(1, int(input_rate))
        self.output_rate = max(1, int(output_rate))
        self._step = self.input_rate / self.output_rate
        self._buffer = np.empty(0, dtype="float32")
        self._position = 0.0
        self.total_input = 0
        self.total_output = 0

    def process(self, samples: Iterable[float]) -> np.ndarray:
        audio = np.asarray(samples, dtype="float32").reshape(-1)
        if not audio.size:
            return np.empty(0, dtype="float32")
        self.total_input += len(audio)
        if self.input_rate == self.output_rate:
            self.total_output += len(audio)
            return np.ascontiguousarray(audio)

        self._buffer = np.concatenate((self._buffer, audio))
        if len(self._buffer) < 2:
            return np.empty(0, dtype="float32")

        # Interpolation needs a sample on both sides. Keep the last source
        # sample for the next callback instead of ever indexing past it.
        available = (len(self._buffer) - 1 - 1e-12) - self._position
        count = int(math.floor(available / self._step)) + 1 if available >= 0 else 0
        if count <= 0:
            return np.empty(0, dtype="float32")
        positions = self._position + np.arange(count, dtype="float64") * self._step
        left = positions.astype(np.int64)
        fraction = (positions - left).astype("float32")
        output = self._buffer[left] + (self._buffer[left + 1] - self._buffer[left]) * fraction

        next_position = self._position + count * self._step
        discard = min(int(next_position), max(0, len(self._buffer) - 1))
        self._buffer = self._buffer[discard:]
        self._position = next_position - discard
        self.total_output += len(output)
        return np.ascontiguousarray(output.astype("float32", copy=False))

    def flush(self) -> np.ndarray:
        if self.input_rate == self.output_rate or not self._buffer.size:
            self._buffer = np.empty(0, dtype="float32")
            self._position = 0.0
            return np.empty(0, dtype="float32")
        expected = int(round(self.total_input * self.output_rate / self.input_rate))
        remaining = max(0, expected - self.total_output)
        if remaining == 0:
            self._buffer = np.empty(0, dtype="float32")
            return np.empty(0, dtype="float32")
        padded = np.concatenate((self._buffer, np.repeat(self._buffer[-1], 2)))
        positions = self._position + np.arange(remaining, dtype="float64") * self._step
        positions = np.minimum(positions, len(padded) - 1.000001)
        left = positions.astype(np.int64)
        fraction = (positions - left).astype("float32")
        output = padded[left] + (padded[left + 1] - padded[left]) * fraction
        self.total_output += len(output)
        self._buffer = np.empty(0, dtype="float32")
        self._position = 0.0
        return np.ascontiguousarray(output.astype("float32", copy=False))


@dataclass
class EnhancementMetrics:
    input_rms: float
    output_rms: float
    peak: float
    gain: float
    noise_floor: float
    speech_active: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_rms": round(self.input_rms, 7),
            "output_rms": round(self.output_rms, 7),
            "peak": round(self.peak, 7),
            "gain": round(self.gain, 3),
            "noise_floor": round(self.noise_floor, 7),
            "speech_active": self.speech_active,
        }


class RealtimeAudioEnhancer:
    """Stateful speech conditioning designed for small streaming blocks."""

    def __init__(
        self,
        sample_rate: int = TARGET_SAMPLE_RATE,
        target_rms: float = 0.04,
        max_gain: float = 8.0,
        highpass_hz: float = 80.0,
        peak_limit: float = 0.89,
        noise_reduction: bool = False,
    ):
        self.sample_rate = max(1, int(sample_rate))
        self.target_rms = float(target_rms)
        self.max_gain = max(1.0, float(max_gain))
        self.peak_limit = min(0.99, max(0.1, float(peak_limit)))
        rc = 1.0 / (2.0 * math.pi * max(1.0, float(highpass_hz)))
        dt = 1.0 / self.sample_rate
        self._highpass_alpha = rc / (rc + dt)
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._gain = 1.0
        self._noise_floor = 0.0008
        self.noise_reduction = bool(noise_reduction)

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def _highpass(self, audio: np.ndarray) -> np.ndarray:
        output = np.empty_like(audio)
        previous_input = self._previous_input
        previous_output = self._previous_output
        alpha = self._highpass_alpha
        for index, sample in enumerate(audio):
            filtered = alpha * (previous_output + float(sample) - previous_input)
            output[index] = filtered
            previous_input = float(sample)
            previous_output = filtered
        self._previous_input = previous_input
        self._previous_output = previous_output
        return output

    def process(self, samples: Iterable[float]) -> Tuple[np.ndarray, EnhancementMetrics]:
        audio = np.asarray(samples, dtype="float32").reshape(-1)
        if not audio.size:
            metrics = EnhancementMetrics(0.0, 0.0, 0.0, self._gain, self._noise_floor, False)
            return np.empty(0, dtype="float32"), metrics

        filtered = self._highpass(audio)
        input_rms = float(np.sqrt(np.mean(np.square(filtered, dtype="float64"))))

        # Update the floor mainly on quiet blocks. A slow upward rate prevents
        # soft speech from being learned as background noise.
        if input_rms <= self._noise_floor * 2.5:
            rate = 0.12 if input_rms < self._noise_floor else 0.015
            self._noise_floor += (input_rms - self._noise_floor) * rate
            self._noise_floor = max(1e-6, self._noise_floor)
        speech_threshold = min(0.003, max(0.0006, self._noise_floor * 2.2))
        speech_active = input_rms >= speech_threshold

        if speech_active:
            desired = min(self.max_gain, max(1.0, self.target_rms / max(input_rms, 1e-8)))
            smoothing = 0.24 if desired < self._gain else 0.055
            self._gain += (desired - self._gain) * smoothing
        else:
            # Do not amplify silence; let a previously high gain decay slowly.
            self._gain += (1.0 - self._gain) * 0.025

        processed = filtered
        if self.noise_reduction and not speech_active:
            processed = processed * 0.35
        processed = processed * self._gain
        processed = np.tanh(processed / self.peak_limit) * self.peak_limit
        processed = np.ascontiguousarray(processed.astype("float32", copy=False))
        output_rms = float(np.sqrt(np.mean(np.square(processed, dtype="float64"))))
        peak = float(np.max(np.abs(processed)))
        metrics = EnhancementMetrics(
            input_rms=input_rms,
            output_rms=output_rms,
            peak=peak,
            gain=self._gain,
            noise_floor=self._noise_floor,
            speech_active=speech_active,
        )
        return processed, metrics


class InputLevelMonitor:
    """Background microphone meter used by settings before recording starts."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status: Dict[str, Any] = {"active": False, "rms": 0.0, "peak": 0.0}

    def start(self, preferred_name: str = "") -> Dict[str, Any]:
        self.stop()
        self._stop.clear()
        with self._lock:
            self._status = {"active": False, "starting": True, "rms": 0.0, "peak": 0.0, "error": ""}

        def run():
            try:
                import sounddevice as sd

                device_id, info, fallback = resolve_input_device(preferred_name, sd)
                sample_rate = int(info["sample_rate"])

                def callback(indata, _frames, _callback_time, status):
                    mono = np.asarray(indata[:, 0], dtype="float32") if len(indata) else np.empty(0, dtype="float32")
                    rms = float(np.sqrt(np.mean(np.square(mono, dtype="float64")))) if mono.size else 0.0
                    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
                    with self._lock:
                        previous_rms = float(self._status.get("rms") or 0.0)
                        previous_peak = float(self._status.get("peak") or 0.0)
                        self._status.update({
                            "active": True,
                            "starting": False,
                            "rms": round(previous_rms * 0.65 + rms * 0.35, 7),
                            "peak": round(max(peak, previous_peak * 0.82), 7),
                            "stream_status": str(status or ""),
                        })

                with self._lock:
                    self._status.update({"device": info, "fallback": fallback})
                blocksize = max(128, int(sample_rate * 0.05))
                with sd.InputStream(
                    device=device_id,
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=blocksize,
                    callback=callback,
                ):
                    while not self._stop.wait(0.1):
                        pass
            except Exception as exc:
                with self._lock:
                    self._status.update({"active": False, "starting": False, "error": str(exc)})
            finally:
                with self._lock:
                    self._status["active"] = False

        self._thread = threading.Thread(target=run, name="wordgrab-input-meter", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            status = dict(self._status)
            if isinstance(status.get("device"), dict):
                status["device"] = dict(status["device"])
            status["updated_at"] = time.time()
            return status
