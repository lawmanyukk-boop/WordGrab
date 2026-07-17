#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转写引擎（供 GUI 和 CLI 复用）
FunASR paraformer-zh + fsmn-vad + ct-punc + cam++ 声纹分离
关键：ffmpeg 响度归一化，对轻声/远场录音是精度命门。

两阶段设计（实测 90s 音频：纯 ASR 仅 1.5s，完整管线 33s，时间大头在调度和后处理）：
- transcribe_draft：VAD 切段 → 按块 ASR+标点 → 逐块回调，快出无说话人初稿
- transcribe_full：完整管线（含 cam++ 声纹分离），慢但结果完整
"""
import os, subprocess, tempfile, json, threading, shutil

HERE = os.path.dirname(os.path.abspath(__file__))


_FFMPEG = None  # 缓存解析结果；惰性解析，避免 import 时缺 ffmpeg 直接崩掉整个 App


def _resolve_ffmpeg():
    """按环境变量、系统 PATH、本地旧版目录的顺序定位 ffmpeg（结果缓存）。
    惰性调用：只有真正要用 ffmpeg 时才解析，缺失时抛出可读错误，
    而不是在 import engine 阶段就让 GUI/CLI 崩溃。"""
    global _FFMPEG
    if _FFMPEG is not None:
        return _FFMPEG

    if os.environ.get("FFMPEG_PATH"):
        _FFMPEG = os.environ["FFMPEG_PATH"]
        return _FFMPEG

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        _FFMPEG = system_ffmpeg
        return _FFMPEG

    bundled_ffmpeg = os.path.join(HERE, "bin", "ffmpeg")
    if os.path.isfile(bundled_ffmpeg) and os.access(bundled_ffmpeg, os.X_OK):
        _FFMPEG = bundled_ffmpeg
        return _FFMPEG

    try:
        import imageio_ffmpeg
        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        return _FFMPEG
    except Exception:
        pass

    raise RuntimeError(
        "未找到 ffmpeg。请先安装：brew install ffmpeg；"
        "或通过环境变量 FFMPEG_PATH 指定 ffmpeg 路径。"
    )


os.environ.setdefault("MODELSCOPE_CACHE", os.path.expanduser("~/.cache/modelscope"))

_MODEL = None
_MODEL_LOCK = threading.Lock()  # 预加载线程与转写线程可能同时进入


def get_model(progress=None):
    """懒加载单例模型（冷启动约 30-40 秒，app 启动时可后台预加载）"""
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                if progress:
                    progress("加载模型…", None)
                import torch
                from funasr import AutoModel
                device = "mps" if torch.backends.mps.is_available() else "cpu"
                _MODEL = AutoModel(
                    model="paraformer-zh",
                    vad_model="fsmn-vad",
                    punc_model="ct-punc",
                    spk_model="cam++",
                    disable_update=True,
                    device=device,
                )
    return _MODEL


def probe_duration(path):
    """秒（float）"""
    try:
        out = subprocess.run(
            [_resolve_ffmpeg(), "-i", path], stderr=subprocess.PIPE, stdout=subprocess.DEVNULL
        ).stderr.decode("utf-8", "ignore")
        import re
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
        if m:
            h, mm, s = m.groups()
            return int(h) * 3600 + int(mm) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def to_wav16k(src):
    """任意音频/视频 → 16k 单声道 wav，含响度归一化 + 高通去低频噪"""
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    af = "highpass=f=100,loudnorm=I=-16:TP=-1.5:LRA=11"
    subprocess.run(
        [_resolve_ffmpeg(), "-y", "-i", src, "-af", af, "-ar", "16000", "-ac", "1", "-vn", wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return wav


DRAFT_CHUNK_MS = 30000  # 初稿分块上限（切点都落在 VAD 静音处，不会切断词）


def transcribe_draft(wav, progress=None, on_chunk=None):
    """快路径初稿：VAD 切段 → 合并成 ~30s 块 → 逐块 ASR+标点，逐块回调。
    复用 get_model() 已加载的子模型，不额外占内存。
    on_chunk(segments_so_far) 每完成一块调用一次。
    返回 [{spk:0,start,end,text}]（无说话人信息）。"""
    def p(stage, pct=None, info=None):
        if progress:
            progress(stage, pct, info)

    m = get_model(progress)
    p("检测语音段…", None)
    vad_res = m.inference(wav, model=m.vad_model, kwargs=m.vad_kwargs)
    vad_segs = (vad_res[0].get("value") or []) if vad_res else []
    if not vad_segs:
        return []

    chunks = []
    cur_start, cur_end = vad_segs[0]
    for s, e in vad_segs[1:]:
        if e - cur_start > DRAFT_CHUNK_MS:
            chunks.append((cur_start, cur_end))
            cur_start = s
        cur_end = e
    chunks.append((cur_start, cur_end))

    import soundfile as sf
    audio, fs = sf.read(wav, dtype="float32")

    segs = []
    for i, (cs, ce) in enumerate(chunks):
        piece = audio[int(cs * fs / 1000): int(ce * fs / 1000)]
        r = m.inference([piece], model=m.model, kwargs=m.kwargs)
        text = ((r[0].get("text") or "") if r else "").strip()
        if text and m.punc_model is not None:
            try:
                pr = m.inference(text, model=m.punc_model, kwargs=m.punc_kwargs)
                text = pr[0]["text"]
            except Exception:
                text = text.replace(" ", "")
        if text:
            segs.append({"spk": 0, "start": int(cs), "end": int(ce), "text": text})
            if on_chunk:
                on_chunk(list(segs))
        p(f"识别中 {i + 1}/{len(chunks)} 段…", (i + 1) / len(chunks))
    return segs


def transcribe_full(wav, progress=None, mode="accuracy"):
    """完整管线（VAD+ASR+标点+cam++ 声纹分离），返回 segments 列表。"""
    def p(stage, pct=None, info=None):
        if progress:
            progress(stage, pct, info)

    model = get_model(progress)
    p("说话人分离中…", None)  # 不确定进度
    # 速度优先使用更大的批处理窗口，减少调度次数；精度优先沿用稳妥的默认窗口。
    batch_size_s = 600 if mode == "speed" else 300
    res = model.generate(input=wav, batch_size_s=batch_size_s, hotword="")

    r = res[0]
    segs = []
    for s in (r.get("sentence_info") or []):
        t = (s.get("text") or "").strip()
        if t:
            segs.append({
                "spk": int(s.get("spk", 0)),
                "start": int(s.get("start", 0)),
                "end": int(s.get("end", 0)),
                "text": t,
            })
    if not segs and r.get("text"):
        segs = [{"spk": 0, "start": 0, "end": 0, "text": r["text"]}]
    return segs


def transcribe(audio_path, progress=None):
    """单次完整转写（CLI 用）。返回 {duration, segments:[{spk,start,end,text}]}
    progress(stage:str, pct:float|None) 回调用于 UI。"""
    def p(stage, pct=None, info=None):
        if progress:
            progress(stage, pct, info)

    dur = probe_duration(audio_path)
    # 预估总耗时：本机约 0.4 倍实时 + 模型加载/解码余量，供 UI 显示进度与剩余时间
    est_total = max(20.0, dur * 0.4 + 8)
    p("解码 + 响度归一化…", 0.05, {"duration": dur, "est_total": est_total})
    wav = to_wav16k(audio_path)
    try:
        segs = transcribe_full(wav, progress)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass
    p("完成", 1.0)
    return {"duration": dur, "segments": segs}


def merge_by_speaker(segments):
    """把连续同说话人的句子合并成段，返回 [{spk,start,text}]"""
    out = []
    for s in segments:
        if out and out[-1]["spk"] == s["spk"]:
            out[-1]["text"] += s["text"]
        else:
            out.append({"spk": s["spk"], "start": s["start"], "text": s["text"]})
    return out
