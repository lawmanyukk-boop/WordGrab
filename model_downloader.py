#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型下载进度追踪器。"""
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
        self.initial_size_mb = 0

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

        # ModelScope 不会稳定提供总字节数。以本次下载开始前的缓存大小为
        # 基线，只报告本次新增的数据量，避免伪造一个错误的总大小。
        self.initial_size_mb = self.get_cache_size()
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

        while self.monitoring:
            time.sleep(2)  # 每2秒检查一次

            current_size = self.get_cache_size()
            downloaded_mb = max(0, current_size - self.initial_size_mb)
            speed_mb_s = max(0, (current_size - last_size) / 2)  # 2秒间隔
            if self.progress_callback:
                self.progress_callback("正在下载语音模型…", None, {
                    "downloaded_mb": round(downloaded_mb, 1),
                    "speed_mb_s": round(speed_mb_s, 2),
                })

            last_size = current_size

def download_models_with_progress(progress_callback=None):
    """下载模型并显示进度

    Args:
        progress_callback: 回调函数 callback(message, progress, info)
    """
    # 使用引擎的运行时别名加载，避免下载器和引擎分别下载不同的模型集。
    from engine import get_model
    get_model(progress_callback)


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
