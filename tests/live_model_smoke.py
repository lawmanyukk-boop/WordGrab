#!/usr/bin/env python3
"""用一段现有 WAV 验证实时模型确实会持续产出文字。

这是手动集成检查，不放入快速单元测试；用法：
python tests/live_model_smoke.py /path/to/audio.wav
"""
import sys

import soundfile as sf

import engine


def main(path):
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio[:, 0]
    model = engine.get_streaming_model()
    cache = {}
    chunk_samples = int(sample_rate * engine.STREAMING_CHUNK_SECONDS)
    utterance_samples = int(sample_rate * engine.LIVE_UTTERANCE_MAX_SECONDS)
    outputs = []
    for start in range(0, min(len(audio), sample_rate * 30), chunk_samples):
        piece = audio[start:start + chunk_samples]
        final = (start + len(piece)) % utterance_samples == 0
        result = model.generate(
            input=piece,
            cache=cache,
            chunk_size=[0, 10, 5],
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
            is_final=final,
            fs=sample_rate,
            disable_pbar=True,
        )
        text = ((result[0].get("text") or "") if result else "").strip()
        if text:
            outputs.append((round((start + len(piece)) / sample_rate, 1), text))
        if final:
            cache = {}
    if not outputs:
        raise SystemExit("FAIL: 前 30 秒没有产生实时文字")
    print("PASS:", outputs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: live_model_smoke.py AUDIO.wav")
    main(sys.argv[1])
