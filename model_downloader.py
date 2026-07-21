#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型下载进度追踪器
监控 ModelScope 模型下载，提供实时进度反馈
"""
import os
import sys
import threading
import time
from pathlib import Path


class ModelDownloadMonitor:
    """监控 ModelScope 模型缓存目录，追踪下载进度"""

    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.cache_dir = Path(os.environ.get(
            "MODELSCOPE_CACHE",
            os.path.expanduser("~/.cache/modelscope")
        ))
        self.monitoring = False
        self.monitor_thread = None
        self.total_expected_mb = 2048  # 预估总大小约 2GB

    def get_cache_size(self):
        """获取当前缓存目录大小（MB）"""
        total = 0
        if not self.cache_dir.exists():
            return 0

        for root, dirs, files in os.walk(self.cache_dir):
            for file in files:
                try:
                    filepath = os.path.join(root, file)
                    total += os.path.getsize(filepath)
                except (OSError, FileNotFoundError):
                    continue

        return total / (1024 * 1024)  # 转换为 MB

    def start_monitoring(self):
        """开始监控下载进度"""
        if self.monitoring:
            return

        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self.monitor_thread.start()

    def stop_monitoring(self):
        """停止监控"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

    def _monitor_loop(self):
        """监控循环"""
        last_size = self.get_cache_size()
        start_time = time.time()
        stall_count = 0

        while self.monitoring:
            time.sleep(2)  # 每2秒检查一次

            current_size = self.get_cache_size()
            progress_pct = min(95, (current_size / self.total_expected_mb) * 100)

            # 计算下载速度
            elapsed = time.time() - start_time
            if elapsed > 0:
                speed_mb_s = (current_size - last_size) / 2  # 2秒间隔

                if speed_mb_s < 0.1:  # 速度很慢或停滞
                    stall_count += 1
                else:
                    stall_count = 0

                # 估算剩余时间
                if speed_mb_s > 0.1:
                    remaining_mb = self.total_expected_mb - current_size
                    eta_seconds = remaining_mb / speed_mb_s
                    eta_text = f"剩余约 {int(eta_seconds // 60)} 分钟" if eta_seconds > 60 else f"剩余约 {int(eta_seconds)} 秒"
                else:
                    eta_text = "正在准备..."

                info = {
                    "downloaded_mb": round(current_size, 1),
                    "total_mb": self.total_expected_mb,
                    "speed_mb_s": round(speed_mb_s, 2),
                    "eta": eta_text
                }

                if self.progress_callback:
                    self.progress_callback(
                        f"正在下载模型 ({round(current_size)}MB / {self.total_expected_mb}MB)",
                        progress_pct / 100,
                        info
                    )

            last_size = current_size

            # 如果长时间停滞，可能下载已完成
            if stall_count > 5 and current_size > 1500:  # 超过1.5GB且停滞
                break


def download_models_with_progress(progress_callback=None):
    """下载模型并显示进度

    Args:
        progress_callback: 回调函数 callback(message, progress, info)
    """
    monitor = ModelDownloadMonitor(progress_callback)

    # 检查模型是否已存在
    cache_size = monitor.get_cache_size()
    if cache_size > 1800:  # 已有 1.8GB+ 缓存，认为已下载
        if progress_callback:
            progress_callback("模型已就绪", 1.0, {"status": "ready"})
        return

    if progress_callback:
        progress_callback("开始下载模型...", 0.0, {"status": "starting"})

    # 启动监控线程
    monitor.start_monitoring()

    try:
        # 触发模型加载（会自动下载）
        from modelscope import snapshot_download

        models = [
            "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
            "iic/speech_campplus_sv_zh-cn_16k-common",
        ]

        for idx, model_id in enumerate(models):
            if progress_callback:
                progress_callback(
                    f"下载模型 {idx + 1}/{len(models)}",
                    None,
                    {"current_model": model_id.split("/")[-1]}
                )

            try:
                snapshot_download(model_id, cache_dir=str(monitor.cache_dir))
            except Exception as e:
                print(f"下载模型时出错: {e}", file=sys.stderr)

        if progress_callback:
            progress_callback("模型下载完成", 1.0, {"status": "completed"})

    finally:
        monitor.stop_monitoring()


if __name__ == "__main__":
    """命令行测试"""
    def print_progress(message, progress, info):
        if progress is not None:
            bar_length = 40
            filled = int(bar_length * progress)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"\r{message} [{bar}] {int(progress * 100)}%", end="", flush=True)
            if info:
                speed = info.get("speed_mb_s", 0)
                if speed > 0:
                    print(f" | {speed:.1f} MB/s | {info.get('eta', '')}", end="", flush=True)
        else:
            print(f"\n{message}", flush=True)

        if progress == 1.0:
            print()  # 完成后换行

    print("开始下载 WordGrab 所需模型...")
    download_models_with_progress(print_progress)
    print("✓ 所有模型已准备就绪")
