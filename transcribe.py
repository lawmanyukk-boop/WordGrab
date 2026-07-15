#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行转写（拖拽 App 也走这里）
用法: python transcribe.py <音频文件> [输出目录]
"""
import os, sys, datetime
import engine


def fmt_ts(ms):
    s = int(ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def main():
    if len(sys.argv) < 2:
        print("用法: python transcribe.py <音频文件> [输出目录]")
        sys.exit(1)
    audio = os.path.abspath(sys.argv[1])
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(audio)
    base = os.path.splitext(os.path.basename(audio))[0]
    out_txt = os.path.join(out_dir, base + ".txt")

    def prog(stage, pct, info=None):
        print(f"… {stage}", flush=True)

    res = engine.transcribe(audio, progress=prog)
    merged = engine.merge_by_speaker(res["segments"])

    lines = [
        f"# {base}",
        f"# 转写时间 {datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"# 引擎 FunASR paraformer-zh + 说话人分离(cam++)",
        "",
    ]
    for m in merged:
        lines.append(f"[{fmt_ts(m['start'])}] 说话人{m['spk'] + 1}：{m['text']}")

    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"✅ 完成：{out_txt}")


if __name__ == "__main__":
    main()
