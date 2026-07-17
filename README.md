# WordGrab

本地离线中文语音转写工具，支持说话人分离；音频始终留在本机，适合重视隐私的录音整理场景。

**A private, offline Chinese speech-to-text app with speaker diarization for macOS.**

## 功能特性

- 桌面 GUI：历史记录、批量管理、文稿点读联动、音频播放器、说话人改名、搜索，以及导出 TXT、PDF、Word。
- 历史索引使用本地 SQLite 管理，文稿正文仍以独立 JSON 文件保存，并保留 JSON 备份便于恢复。
- 命令行模式：适合快速批量或单个音频转写。
- 两阶段转写：先快速生成逐步出现的初稿，再完成声纹分离并输出完整文稿。
- 内置响度归一化预处理，帮助改善较轻声或远场录音的识别效果。

## 环境要求

- macOS；推荐 Apple Silicon，以使用 MPS 加速。
- Python >= 3.9。
- ffmpeg：`brew install ffmpeg`。

当前第一版仅支持 macOS。

## Quick Start

```bash
git clone https://github.com/lawmanyukk-boop/WordGrab.git
cd WordGrab
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py        # GUI
.venv/bin/python transcribe.py 录音.m4a   # CLI
```

## 首次运行

首次转写会自动通过 ModelScope 下载所需模型（约 2GB）到 `~/.cache/modelscope`。模型下载完成后，推理和音频处理都在本机完成，音频不会上传。

## 生成拖拽 App（可选）

```bash
bash scripts/make_app.sh
```

脚本默认在项目根目录生成 `WordGrab.app`，也可以传入安装位置：

```bash
bash scripts/make_app.sh /Applications/WordGrab.app
```

脚本会把最新版源代码同步到 `~/Library/Application Support/录音转文字`。桌面 App 优先使用该目录中的 `.venv`，没有时回退到系统的 `python3`。开发调试建议直接运行项目目录中的 `.venv/bin/python app.py`。

## 本地数据与隐私

录音、文稿、索引、设置和日志默认保存在本机，不提交到 GitHub。仓库的 `.gitignore` 已排除 `data/`、模型缓存、虚拟环境、运行时副本和构建产物。

## 精度 Tips

- 响度归一化是识别效果的关键。程序已内置 `loudnorm` 预处理，对轻声和远场录音通常有明显帮助。
- 人名和专有名词容易出错；可在 `engine.py` 的 `hotword=""` 中添加常用人名或术语来优化。
- 近场、单人、安静环境下精度最高；多人抢话、远场和噪声环境会明显降低准确度。
- 严重重叠的对话可能造成个别说话人分离误判，这是当前模型的正常限制。

## 输出示例

```text
# 会议录音
# 转写时间 2026-07-15 14:30
# 引擎 FunASR paraformer-zh + 说话人分离(cam++)

[00:00] 说话人1：我们先确认今天的议程。
[00:44] 说话人2：好的，我来补充一下项目进度。
```

## License 与模型授权

本项目代码采用 [MIT License](LICENSE)。`paraformer-zh`、`fsmn-vad`、`ct-punc` 和 `cam++` 模型由 FunASR/ModelScope 在运行时下载，遵循各自的许可证；本仓库不分发这些模型。
