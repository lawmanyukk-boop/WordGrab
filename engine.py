#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转写引擎（供 GUI 和 CLI 复用）
FunASR paraformer-zh + fsmn-vad + ct-punc + cam++ 声纹分离
关键：ffmpeg 响度归一化，对轻声/远场录音是精度命门。

两阶段设计（实测 90s 音频：纯 ASR 仅 1.5s，完整管线 33s，时间大头在调度和后处理）：
- transcribe_draft：VAD 切段 → 按块 ASR+标点 → 逐块回调，快出无说话人初稿
- transcribe_full：完整管线（含 cam++ 声纹分离），慢但结果完整
"""
import os, subprocess, tempfile, json, threading, shutil, gc, time

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
_STREAM_MODEL = None
_STREAM_MODEL_WARMED = False
_MODEL_LOCK = threading.Lock()  # 预加载线程与转写线程可能同时进入
_STREAM_MODEL_LOCK = threading.Lock()

# 2026-07-22 在目标 Mac 上用 60 秒真实录音复测：CPU/2 线程、1.2 秒输入块
# 的实时系数为 0.635；MPS 与 4/8 CPU 线程都慢于实时。流式模型以低延迟
# 小矩阵为主，线程过多会增加调度争抢，因此这里不是“线程越多越快”。
STREAMING_DEVICE = "cpu"
STREAMING_NCPU = 2
STREAMING_CHUNK_SECONDS = 1.2
# 在线 Paraformer 在连续讲话时通常要等 is_final 才给出稳定文字。15 秒才
# 强制结句会让用户误以为实时字幕失效；4.8 秒能在可读性和响应速度间平衡。
LIVE_UTTERANCE_MAX_SECONDS = 4.8
LIVE_SILENCE_END_SECONDS = 0.65


def _cached_model_path(model_id):
    """缓存完整时直接使用本地路径，避免每次启动都联网解析模型别名。"""
    candidate = os.path.join(
        os.path.expanduser("~/.cache/modelscope/models"), *model_id.split("/")
    )
    if os.path.isfile(os.path.join(candidate, "configuration.json")):
        return candidate
    return model_id


def get_model(progress=None):
    """懒加载单例模型（冷启动约 30-40 秒，app 启动时可后台预加载）

    首次运行会自动下载约 2GB 模型文件，支持进度提示
    """
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                # 检查模型是否需要下载
                cache_dir = os.path.expanduser("~/.cache/modelscope")
                model_exists = os.path.isdir(cache_dir) and any(
                    os.path.isdir(os.path.join(cache_dir, d))
                    for d in ["hub", "models"]
                    if os.path.isdir(os.path.join(cache_dir, d))
                )

                if not model_exists and progress:
                    # 首次下载，显示下载进度
                    try:
                        from model_downloader import ModelDownloadMonitor
                        monitor = ModelDownloadMonitor(progress)
                        monitor.start_monitoring()
                    except ImportError:
                        monitor = None
                else:
                    monitor = None

                try:
                    if progress:
                        progress("正在加载模型…", None, {"status": "loading"})

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

                    if progress:
                        progress("模型加载完成", 1.0, {"status": "ready"})

                finally:
                    if monitor:
                        monitor.stop_monitoring()

    return _MODEL


def get_streaming_model(progress=None):
    """加载实时模型。与最终模型分开，便于录音结束后释放。"""
    global _STREAM_MODEL
    if _STREAM_MODEL is None:
        with _STREAM_MODEL_LOCK:
            if _STREAM_MODEL is None:
                import torch
                from funasr import AutoModel
                _STREAM_MODEL = AutoModel(
                    model=_cached_model_path(
                        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
                    ),
                    disable_update=True,
                    device=STREAMING_DEVICE,
                    ncpu=STREAMING_NCPU,
                )
    return _STREAM_MODEL


def streaming_model_ready():
    return _STREAM_MODEL is not None and _STREAM_MODEL_WARMED


def prepare_streaming_model():
    """加载并执行一次静音预热，让录音开始后的首句话不承担冷启动开销。"""
    global _STREAM_MODEL_WARMED
    started = time.perf_counter()
    model = get_streaming_model()
    if not _STREAM_MODEL_WARMED:
        import numpy as np
        model.generate(
            input=np.zeros(int(16000 * 0.6), dtype="float32"),
            cache={},
            chunk_size=[0, 10, 5],
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
            is_final=True,
            fs=16000,
            disable_pbar=True,
        )
        _STREAM_MODEL_WARMED = True
    return {
        "device": STREAMING_DEVICE,
        "threads": STREAMING_NCPU,
        "chunk_seconds": STREAMING_CHUNK_SECONDS,
        "prepare_seconds": round(time.perf_counter() - started, 3),
    }


def release_models():
    """释放模型引用；由 GC/底层运行时回收模型内存。"""
    global _MODEL, _STREAM_MODEL, _STREAM_MODEL_WARMED
    _MODEL = None
    _STREAM_MODEL = None
    _STREAM_MODEL_WARMED = False
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def release_streaming_model():
    """在进入最终转写前释放实时模型，避免两个 ASR 模型同时常驻。"""
    global _STREAM_MODEL, _STREAM_MODEL_WARMED
    _STREAM_MODEL = None
    _STREAM_MODEL_WARMED = False
    gc.collect()


def dedupe_live_text(previous, text):
    """去掉实时块与上一块之间的常见重复前缀。"""
    text = str(text or "").strip()
    if not text:
        return ""
    previous_text = str((previous[-1] if previous else {}).get("text") or "")
    if not previous_text:
        return text
    if text == previous_text:
        return ""
    for size in range(min(len(previous_text), len(text)), 0, -1):
        if previous_text[-size:] == text[:size]:
            return text[size:].strip()
    return text


def accumulate_live_text(existing, text):
    """把流式模型的新输出追加到同一条字幕，并处理块边界重复与英文空格。"""
    existing = str(existing or "").strip()
    piece = dedupe_live_text([{"text": existing}], text)
    if not piece:
        return existing
    if not existing:
        return piece
    needs_space = existing[-1].isascii() and existing[-1].isalnum() \
        and piece[0].isascii() and piece[0].isalnum()
    return existing + (" " if needs_space else "") + piece


def live_caption_tail(text, limit=64):
    """返回当前一句话的末尾，保证两行字幕始终露出最新文字。"""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    # 英文尽量从单词边界开始；中文没有空格时直接保留最近字符。
    first_space = tail.find(" ")
    if 0 < first_space < 24:
        tail = tail[first_space + 1:]
    return "…" + tail.lstrip()


def should_finalize_live_utterance(silence_seconds, utterance_seconds):
    """决定实时流何时确认一句，供录音管线和测试共享。"""
    natural_end = (
        utterance_seconds >= 1.0
        and silence_seconds >= LIVE_SILENCE_END_SECONDS
    )
    forced_end = utterance_seconds >= LIVE_UTTERANCE_MAX_SECONDS
    return natural_end or forced_end


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
    """按说话人合并成易读段落；同一人连续讲话也会按长度和时长换段。"""
    out = []
    for s in segments:
        same_speaker = out and out[-1]["spk"] == s["spk"]
        projected_chars = len(out[-1]["text"]) + len(s.get("text", "")) if out else 0
        projected_ms = int(s.get("end", 0)) - int(out[-1].get("start", 0)) if out else 0
        if same_speaker and projected_chars <= 140 and projected_ms <= 25000:
            out[-1]["text"] += s["text"]
            out[-1]["end"] = s.get("end", out[-1].get("end", 0))
        else:
            out.append({"spk": s["spk"], "start": s["start"],
                        "end": s.get("end", s["start"]), "text": s["text"]})
    return out


def normalize_speaker_ids(segments):
    """把 Cam++ 可能产生的非连续说话人编号整理为 0..N-1。"""
    ids = sorted({int(segment.get("spk", 0)) for segment in segments})
    mapping = {speaker_id: index for index, speaker_id in enumerate(ids)}
    return [{**segment, "spk": mapping.get(int(segment.get("spk", 0)), 0)}
            for segment in segments]
