#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WordGrab 使用的固定模型清单、缓存位置和完整性校验。

模型版本与文件大小固定后，首次准备、设置页状态和清理操作都以同一份
清单为准，不再通过目录名称的模糊匹配猜测模型是否可用。
"""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    model_id: str
    revision: str
    expected_bytes: int
    required_files: tuple

    @property
    def directory_name(self):
        return self.model_id.split("/", 1)[1]


# 文件大小来自各模型固定 tag 的 ModelScope 文件清单。required_files 只列
# 运行必需的大文件；expected_bytes 则用于显示整个仓库的准确下载进度。
MODEL_SPECS = (
    ModelSpec(
        key="transcription",
        label="高精度中文转写",
        model_id="iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        revision="v2.0.9",
        expected_bytes=998_938_463,
        required_files=(
            ("configuration.json", 478),
            ("config.yaml", 3_474),
            ("model.pt", 989_763_045),
            ("tokens.json", 93_676),
        ),
    ),
    ModelSpec(
        key="streaming",
        label="实时字幕",
        model_id="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
        revision="v2.0.5",
        expected_bytes=889_357_883,
        required_files=(
            ("configuration.json", 472),
            ("config.yaml", 2_940),
            ("model.pt", 880_716_041),
            ("tokens.json", 93_676),
        ),
    ),
    ModelSpec(
        key="vad",
        label="语音片段检测",
        model_id="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        revision="v2.0.4",
        expected_bytes=4_029_275,
        required_files=(
            ("configuration.json", 365),
            ("config.yaml", 1_215),
            ("model.pt", 1_721_366),
        ),
    ),
    ModelSpec(
        key="punctuation",
        label="标点恢复",
        model_id="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
        revision="v2.0.4",
        expected_bytes=1_186_773_520,
        required_files=(
            ("configuration.json", 450),
            ("config.yaml", 812),
            ("model.pt", 1_125_507_622),
            ("tokens.json", 8_280_697),
        ),
    ),
    ModelSpec(
        key="speakers",
        label="说话人区分",
        model_id="iic/speech_campplus_sv_zh-cn_16k-common",
        revision="v2.0.2",
        expected_bytes=28_961_033,
        required_files=(
            ("configuration.json", 581),
            ("config.yaml", 537),
            ("campplus_cn_common.bin", 28_036_335),
        ),
    ),
)

MODEL_BY_KEY = {spec.key: spec for spec in MODEL_SPECS}
EXPECTED_TOTAL_BYTES = sum(spec.expected_bytes for spec in MODEL_SPECS)


def app_data_directory():
    """返回各平台可写的应用数据目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "WordGrab"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "WordGrab"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "WordGrab"


def model_cache_root():
    """WordGrab 独立模型目录；可由测试环境显式覆盖。"""
    configured = os.environ.get("MODELSCOPE_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    return app_data_directory() / "models"


def cache_roots(include_legacy=True):
    roots = [model_cache_root()]
    legacy = Path.home() / ".cache" / "modelscope"
    legacy_disabled = os.environ.get("WORDGRAB_DISABLE_LEGACY_MODEL_CACHE") == "1"
    if include_legacy and not legacy_disabled and legacy not in roots:
        roots.append(legacy)
    return tuple(roots)


def _candidate_directories(spec, include_legacy=True):
    group, name = spec.model_id.split("/", 1)
    found = []
    for root in cache_roots(include_legacy=include_legacy):
        for candidate in (
            root / group / name,
            root / "._____temp" / group / name,
            root / "hub" / group / name,
            root / "models" / group / name,
            root / "hub" / "models" / group / name,
        ):
            if candidate not in found:
                found.append(candidate)
    return tuple(found)


def primary_model_directory(spec):
    group, name = spec.model_id.split("/", 1)
    return model_cache_root() / group / name


def _revision_matches(path, spec):
    try:
        value = (path / ".mv").read_text(encoding="utf-8")
    except OSError:
        return False
    return f"Revision:{spec.revision}" in value


def validate_model_directory(path, spec):
    """返回 (是否完整, 缺失或损坏说明)。"""
    path = Path(path)
    problems = []
    if not path.is_dir():
        return False, ("目录不存在",)
    if not _revision_matches(path, spec):
        problems.append(f"版本不是 {spec.revision}")
    for relative, expected_size in spec.required_files:
        file_path = path / relative
        try:
            actual_size = file_path.stat().st_size
        except OSError:
            problems.append(f"缺少 {relative}")
            continue
        if actual_size != expected_size:
            problems.append(f"{relative} 大小异常")
    return not problems, tuple(problems)


def ready_model_directory(spec_or_key):
    spec = MODEL_BY_KEY[spec_or_key] if isinstance(spec_or_key, str) else spec_or_key
    for candidate in _candidate_directories(spec):
        ready, _ = validate_model_directory(candidate, spec)
        if ready:
            return candidate
    return None


def require_model_directory(spec_or_key):
    spec = MODEL_BY_KEY[spec_or_key] if isinstance(spec_or_key, str) else spec_or_key
    path = ready_model_directory(spec)
    if path is None:
        raise RuntimeError(
            f"{spec.label}模型尚未准备完整。请重新打开首次设置并下载语音模型。"
        )
    return str(path)


def model_statuses():
    statuses = []
    for spec in MODEL_SPECS:
        ready_path = ready_model_directory(spec)
        existing = [path for path in _candidate_directories(spec) if path.is_dir()]
        problems = ()
        if not ready_path and existing:
            _, problems = validate_model_directory(existing[0], spec)
        statuses.append({
            "key": spec.key,
            "label": spec.label,
            "model_id": spec.model_id,
            "revision": spec.revision,
            "expected_bytes": spec.expected_bytes,
            "status": "ready" if ready_path else ("corrupt" if existing else "missing"),
            "path": str(ready_path or (existing[0] if existing else primary_model_directory(spec))),
            "problems": list(problems),
        })
    return statuses


def all_models_ready():
    return all(status["status"] == "ready" for status in model_statuses())


def _directory_size(path):
    total = 0
    if not path.is_dir():
        return 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def model_bytes_present(spec):
    """按单个模型取最大一份缓存，重复目录不会导致进度超过 100%。"""
    largest = max((_directory_size(path) for path in _candidate_directories(spec)), default=0)
    return min(largest, spec.expected_bytes)


def download_progress():
    downloaded = sum(model_bytes_present(spec) for spec in MODEL_SPECS)
    return downloaded, EXPECTED_TOTAL_BYTES


def installed_model_size():
    directories = {
        path
        for spec in MODEL_SPECS
        for path in _candidate_directories(spec)
        if path.is_dir()
    }
    return sum(_directory_size(path) for path in directories)


def remove_wordgrab_models():
    """只删除 WordGrab 清单内的模型，不影响用户的其他 ModelScope 模型。"""
    directories = {
        path
        for spec in MODEL_SPECS
        for path in _candidate_directories(spec)
        if path.is_dir()
    }
    freed = sum(_directory_size(path) for path in directories)
    for path in sorted(directories, key=lambda value: len(str(value)), reverse=True):
        shutil.rmtree(path, ignore_errors=True)
    return freed, tuple(str(path) for path in directories)
