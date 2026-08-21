"""Pluggable transcription provider boundary for WordGrab.

FunASR remains the only enabled provider.  The boundary keeps recording,
queueing and recovery independent from model-specific APIs, without exposing
unfinished model choices to users.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class ProviderCapabilities:
    key: str
    label: str
    languages: tuple[str, ...]
    streaming: bool
    partial_results: bool
    confidence: bool
    speaker_diarization: bool
    sample_rate: int

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["languages"] = list(self.languages)
        return value


class TranscriptionProvider:
    capabilities: ProviderCapabilities

    def prepare_live(self) -> Dict[str, Any]:
        raise NotImplementedError

    def live_ready(self) -> bool:
        raise NotImplementedError

    def transcribe_live(
        self,
        audio,
        cache: Dict[str, Any],
        sample_rate: int,
        is_final: bool = False,
    ) -> str:
        raise NotImplementedError

    def prepare_audio(self, audio_file: str) -> str:
        raise NotImplementedError

    def transcribe_draft(self, wav: str, progress=None, on_chunk=None):
        raise NotImplementedError

    def transcribe_final(self, wav: str, progress=None, mode: str = "accuracy"):
        raise NotImplementedError

    def release_live(self) -> None:
        raise NotImplementedError

    def release_all(self) -> None:
        raise NotImplementedError


class FunASRProvider(TranscriptionProvider):
    capabilities = ProviderCapabilities(
        key="funasr",
        label="FunASR 中文本地转写",
        languages=("zh", "en", "mixed"),
        streaming=True,
        partial_results=True,
        confidence=False,
        speaker_diarization=True,
        sample_rate=16000,
    )

    @staticmethod
    def _engine():
        import engine
        return engine

    def prepare_live(self) -> Dict[str, Any]:
        return self._engine().prepare_streaming_model()

    def live_ready(self) -> bool:
        return self._engine().streaming_model_ready()

    def transcribe_live(self, audio, cache, sample_rate, is_final=False) -> str:
        model = self._engine().get_streaming_model()
        result = model.generate(
            input=audio,
            cache=cache,
            chunk_size=[0, 10, 5],
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
            is_final=bool(is_final),
            fs=int(sample_rate),
            disable_pbar=True,
        )
        return ((result[0].get("text") or "") if result else "").strip()

    def prepare_audio(self, audio_file: str) -> str:
        return self._engine().to_wav16k(audio_file)

    def transcribe_draft(self, wav: str, progress=None, on_chunk=None):
        return self._engine().transcribe_draft(wav, progress=progress, on_chunk=on_chunk)

    def transcribe_final(self, wav: str, progress=None, mode: str = "accuracy"):
        return self._engine().transcribe_full(wav, progress=progress, mode=mode)

    def release_live(self) -> None:
        self._engine().release_streaming_model()

    def release_all(self) -> None:
        self._engine().release_models()


_PROVIDERS: Dict[str, TranscriptionProvider] = {"funasr": FunASRProvider()}


def get_provider(key: str = "funasr") -> TranscriptionProvider:
    selected = str(key or "funasr").strip().lower()
    if selected not in _PROVIDERS:
        raise ValueError(f"不支持的转写引擎：{selected}")
    return _PROVIDERS[selected]


def list_providers() -> list[Dict[str, Any]]:
    return [provider.capabilities.as_dict() for provider in _PROVIDERS.values()]
