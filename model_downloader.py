#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定模型清单的下载、进度和完整性校验。"""
import os
import shutil
import threading
import time

import model_catalog


class ModelDownloadMonitor:
    """只统计 WordGrab 的五个模型，重复缓存不会让进度超过 100%。"""

    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.monitoring = False
        self.monitor_thread = None
        self.current_model = ""
        self.current_index = 0
        self.total_models = len(model_catalog.MODEL_SPECS)
        self._state_lock = threading.RLock()

    def set_current_model(self, spec, index):
        with self._state_lock:
            self.current_model = spec.label
            self.current_index = index

    def snapshot(self, speed_bytes_s=0):
        downloaded, total = model_catalog.download_progress()
        progress = min(0.995, downloaded / total) if total else 0
        with self._state_lock:
            current_model = self.current_model
            current_index = self.current_index
        eta_seconds = None
        if speed_bytes_s > 0 and downloaded < total:
            eta_seconds = max(0, int((total - downloaded) / speed_bytes_s))
        return progress, {
            "downloaded_mb": round(downloaded / (1024 * 1024), 1),
            "total_mb": round(total / (1024 * 1024), 1),
            "speed_mb_s": round(speed_bytes_s / (1024 * 1024), 2),
            "eta_seconds": eta_seconds,
            "current_model": current_model,
            "current_index": current_index,
            "total_models": self.total_models,
        }

    def emit(self, stage=None, speed_bytes_s=0):
        if not self.progress_callback:
            return
        progress, details = self.snapshot(speed_bytes_s)
        if stage is None:
            current = details.get("current_model")
            stage = f"正在准备：{current}" if current else "正在准备语音模型…"
        self.progress_callback(stage, progress, details)

    def start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="wordgrab-model-progress",
            daemon=True,
        )
        self.monitor_thread.start()

    def stop_monitoring(self):
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

    def _monitor_loop(self):
        previous_bytes, _ = model_catalog.download_progress()
        previous_time = time.monotonic()
        while self.monitoring:
            time.sleep(1)
            if not self.monitoring:
                break
            current_bytes, _ = model_catalog.download_progress()
            current_time = time.monotonic()
            elapsed = max(0.001, current_time - previous_time)
            speed = max(0, current_bytes - previous_bytes) / elapsed
            self.emit(speed_bytes_s=speed)
            previous_bytes = current_bytes
            previous_time = current_time


def _ensure_disk_space():
    cache_root = model_catalog.model_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    downloaded, total = model_catalog.download_progress()
    remaining = max(0, total - downloaded)
    reserve = 512 * 1024 * 1024
    free = shutil.disk_usage(cache_root).free
    if free < remaining + reserve:
        needed_gb = (remaining + reserve) / (1024 ** 3)
        free_gb = free / (1024 ** 3)
        raise RuntimeError(
            f"磁盘空间不足：还需要约 {needed_gb:.1f} GB，当前可用 {free_gb:.1f} GB。"
        )


def friendly_download_error(exc):
    message = str(exc).strip()
    lowered = message.lower()
    if "安装包缺少语音组件" in message or "nonetype" in lowered:
        return "当前安装包的语音组件不完整，请安装更新版本后重试。"
    if "no space left" in lowered or "磁盘空间不足" in message:
        return message or "磁盘空间不足，请清理空间后重试。"
    if any(token in lowered for token in (
        "timed out", "timeout", "connection", "network",
        "name or service not known", "nodename nor servname",
        "ssl", "proxy", "http",
    )):
        return "网络连接中断。已下载的部分会保留，请检查网络后点击“继续下载”。"
    return f"模型准备失败（{type(exc).__name__}）：{message or '未知错误'}"


def download_models_with_progress(progress_callback=None):
    """按固定版本逐个下载五个必需模型，并在返回前校验完整性。"""
    import engine
    from modelscope.hub.snapshot_download import snapshot_download

    # 先检查安装包自身，避免用户下载近 3 GB 后才发现运行组件缺失。
    engine.verify_runtime_components()
    _ensure_disk_space()

    monitor = ModelDownloadMonitor(progress_callback)
    monitor.start_monitoring()
    monitor_stopped = False
    try:
        for index, spec in enumerate(model_catalog.MODEL_SPECS, start=1):
            monitor.set_current_model(spec, index)
            ready_path = model_catalog.ready_model_directory(spec)
            if ready_path is not None:
                monitor.emit(f"已校验：{spec.label}")
                continue

            monitor.emit(f"正在下载 {index}/{len(model_catalog.MODEL_SPECS)}：{spec.label}")
            downloaded_path = snapshot_download(
                spec.model_id,
                revision=spec.revision,
                cache_dir=str(model_catalog.model_cache_root()),
            )
            ready, problems = model_catalog.validate_model_directory(downloaded_path, spec)
            if not ready:
                raise RuntimeError(
                    f"{spec.label}文件不完整：{'、'.join(problems) or '校验失败'}"
                )

        statuses = model_catalog.model_statuses()
        incomplete = [
            f"{status['label']}（{'; '.join(status['problems']) or status['status']}）"
            for status in statuses
            if status["status"] != "ready"
        ]
        if incomplete:
            raise RuntimeError("模型文件未完整下载：" + "、".join(incomplete))
        # 先停止后台采样，再发出 100%，避免结束瞬间被旧的 99.5% 进度覆盖。
        monitor.stop_monitoring()
        monitor_stopped = True
        if progress_callback:
            _, details = monitor.snapshot()
            details.update(downloaded_mb=round(model_catalog.EXPECTED_TOTAL_BYTES / (1024 * 1024), 1))
            progress_callback("五个语音模型均已校验", 1.0, details)
        return statuses
    finally:
        if not monitor_stopped:
            monitor.stop_monitoring()


if __name__ == "__main__":
    def print_progress(message, progress, info):
        percent = int((progress or 0) * 100)
        current = info.get("current_model") if info else ""
        print(f"\r{percent:3d}% {message} {current or ''}".rstrip(), end="", flush=True)
        if progress == 1.0:
            print()

    print("开始下载 WordGrab 所需模型...")
    download_models_with_progress(print_progress)
    print("✓ 所有模型已准备就绪")
