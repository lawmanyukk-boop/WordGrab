<p align="center">
  <img src="assets/icon_1024.png" alt="WordGrab Logo" width="150"/>
</p>

<h1 align="center">WordGrab</h1>

<p align="center">
  <strong>让你的录音秒变文字，数据永不离开电脑 🔒</strong>
</p>

<p align="center">
  本地离线中文语音转写工具，支持说话人分离；音频始终留在本机，适合重视隐私的录音整理场景
</p>

<p align="center">
  <strong>A private, offline Chinese speech-to-text app with speaker diarization for macOS and Windows.</strong>
</p>

<p align="center">
  <a href="#-立即开始"><strong>快速开始</strong></a> •
  <a href="#-核心功能"><strong>功能介绍</strong></a> •
  <a href="#-适合谁用"><strong>使用场景</strong></a> •
  <a href="https://github.com/lawmanyukk-boop/WordGrab/issues"><strong>反馈问题</strong></a>
</p>

---

## 为什么选择 WordGrab？

<table>
<tr>
<td width="25%">🔒 <strong>隐私第一</strong></td>
<td>商务会议、客户访谈、个人备忘——你的录音只在你的电脑里处理，永不上传</td>
</tr>
<tr>
<td width="25%">⚡ <strong>快速高效</strong></td>
<td>本地转写，无需上传下载，2小时录音10分钟搞定，无需排队等待</td>
</tr>
<tr>
<td width="25%">💰 <strong>完全免费</strong></td>
<td>一次下载，永久免费使用，不按分钟计费，不限使用次数</td>
</tr>
<tr>
<td width="25%">🎯 <strong>说话人分离</strong></td>
<td>自动识别谁说了什么，会议记录从此轻松，告别手动标记</td>
</tr>
</table>

---

## ✨ 核心功能

### 🎙️ 专业级转写引擎
- **说话人自动分离** - 再也不用手动标记"谁说的"，AI 自动识别不同说话人
- **两阶段转写** - 先快速生成逐步出现的初稿，完整版稍后自动完成
- **响度智能优化** - 内置响度归一化预处理，就算录音声音小或距离远，也能准确识别
- **热词定制** - 支持添加常用人名、专有名词，提升识别准确度

### 🖥️ 简洁强大的界面
- **历史记录管理** - 所有录音转写一目了然，快速搜索找到任何一条
- **音频文字联动** - 点击文稿跳转播放，音频文字完美同步，方便校对
- **说话人改名** - 识别出说话人后，可自定义名称（如"张经理"、"客户A"）
- **多格式导出** - 一键导出 Word、PDF、TXT，直接用于工作汇报或存档

### 🤖 AI 智能总结（可选）
- **智能内容识别** - 自动识别会议、访谈、讲座、通话、备忘类型，调整分析重点
- **专业摘要生成** - 提取关键信息、待办事项、决策要点，生成结构化总结
- **隐私保护设计** - 你的 API Key 只存本机，AI 只看文字不碰原始音频
- **灵活接口支持** - 支持任何 OpenAI 兼容接口（OpenAI、Claude、本地模型等）

### 💾 本地数据管理
- **SQLite 索引** - 历史记录使用本地数据库管理，搜索快速
- **JSON 文稿备份** - 文稿内容独立保存为 JSON 文件，自动备份便于恢复
- **命令行模式** - 支持 CLI 批量转写，适合自动化工作流集成

---

## 📊 WordGrab vs 在线转写服务

| 特性 | WordGrab | 在线转写服务 |
|------|:--------:|:-----------:|
| **隐私保护** | ✅ 100% 本地处理 | ❌ 音频上传云端 |
| **使用成本** | ✅ 完全免费 | ❌ 按分钟收费 |
| **网络要求** | ✅ 离线可用* | ❌ 必须联网 |
| **说话人分离** | ✅ 自动识别 | ⚠️ 部分支持/额外收费 |
| **数据所有权** | ✅ 完全掌控 | ❌ 存储在第三方 |
| **使用次数** | ✅ 无限制 | ⚠️ 可能有配额限制 |

<sub>*首次下载模型需联网（一次性 2GB），之后完全离线运行</sub>

---

## 💬 适合谁用？

<table>
<tr>
<td width="25%">📝 <strong>记者 / 研究员</strong></td>
<td>访谈录音快速整理，自动区分受访者和提问者，专注内容分析</td>
</tr>
<tr>
<td width="25%">👔 <strong>商务人士</strong></td>
<td>会议纪要自动生成，客户沟通记录留档，敏感内容不外传</td>
</tr>
<tr>
<td width="25%">🎓 <strong>学生 / 教师</strong></td>
<td>课程讲座笔记整理，小组讨论记录，论文访谈资料管理</td>
</tr>
<tr>
<td width="25%">🎬 <strong>内容创作者</strong></td>
<td>播客、视频字幕制作，采访内容转文字，加速后期制作流程</td>
</tr>
<tr>
<td width="25%">⚖️ <strong>法律 / 咨询</strong></td>
<td>客户咨询记录，案件讨论整理，隐私合规要求高的场景</td>
</tr>
<tr>
<td width="25%">🏥 <strong>医疗 / 心理</strong></td>
<td>患者访谈记录，临床研究整理，保护隐私的专业需求</td>
</tr>
</table>

---

## 🚀 立即开始

### 环境要求

- **macOS** 或 **Windows 10/11**
- Python >= 3.9
- ffmpeg（可选，程序会自动使用内置的 imageio-ffmpeg）

### macOS 快速安装

```bash
# 克隆项目
git clone https://github.com/lawmanyukk-boop/WordGrab.git
cd WordGrab

# 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 启动图形界面
.venv/bin/python app.py

# 或使用命令行模式
.venv/bin/python transcribe.py 你的录音.m4a
```

### Windows 快速安装

```bash
# 克隆项目
git clone https://github.com/lawmanyukk-boop/WordGrab.git
cd WordGrab

# 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 启动图形界面
python app.py

# 或使用命令行模式
python transcribe.py 你的录音.m4a
```

### 首次运行

💡 **首次转写会自动通过 ModelScope 下载所需 AI 模型（约 2GB）到 `~/.cache/modelscope`。**

这是一次性下载，喝杯咖啡等几分钟就好。模型下载完成后，所有转写和音频处理都在本机完成，音频永不上传。

---

## 🔧 高级功能

### AI 智能总结设置（可选）

如果你想使用 AI 总结功能，可以在「设置 → AI 服务」中配置：

1. 填写 **OpenAI 兼容接口地址**（如 `https://api.openai.com/v1`）
2. 填写你的 **API Key**
3. 选择 **模型名称**（如 `gpt-4`、`claude-3-sonnet` 等）

**隐私说明**：
- API Key 保存在仅当前系统用户可读的本机文件中，不写入项目配置或日志
- 生成总结时，WordGrab 只发送文稿文字和时间点，**不发送原始音频**
- 不会发送其他历史文稿或个人信息

### 打包为独立应用（可选）

#### macOS App 打包

```bash
# 使用内置脚本打包
bash scripts/make_app.sh

# 或直接安装到 Applications 目录
bash scripts/make_app.sh /Applications/WordGrab.app
```

脚本会生成 `WordGrab.app`，可以像其他 macOS 应用一样使用。

#### Windows 可执行文件打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包（首次会比较慢，需要分析所有依赖）
pyinstaller WordGrab.spec
```

打包完成后，可执行文件位于 `dist\WordGrab\WordGrab.exe`。

⚠️ **注意**：Windows Defender 可能会误报打包后的 exe 文件，这是 PyInstaller 打包工具的常见现象，允许运行即可。

---

## 💡 精度优化 Tips

- ✅ **响度归一化**：程序已内置 `loudnorm` 预处理，对轻声和远场录音通常有明显帮助
- ✅ **热词优化**：人名和专有名词容易出错，可在 `engine.py` 的 `hotword=""` 中添加常用术语
- ✅ **最佳环境**：近场、单人、安静环境下精度最高；多人抢话、远场和噪声环境会降低准确度
- ⚠️ **模型限制**：严重重叠的对话可能造成个别说话人分离误判，这是当前 AI 模型的正常限制

---

## 📄 输出示例

```text
# 会议录音
# 转写时间 2026-07-15 14:30
# 引擎 FunASR paraformer-zh + 说话人分离(cam++)

[00:00] 张经理：我们先确认今天的议程，主要讨论Q3的产品规划。
[00:12] 李工程师：好的，我准备了技术方案，稍后详细说明。
[00:44] 王总监：在此之前，我想先补充一下市场反馈的数据。
[01:15] 张经理：没问题，那我们先听王总监的市场分析。
```

---

## 🔒 隐私与数据安全

- ✅ **本地处理**：所有音频转写在本机完成，不上传任何服务器
- ✅ **数据留存**：录音、文稿、索引、设置和日志默认保存在本机
- ✅ **API Key 保护**：AI 接口密钥加密存储在本地，仅当前用户可读
- ✅ **开源透明**：代码完全开源，你可以审查任何功能的实现
- ✅ **无追踪**：不收集任何使用数据或分析信息

仓库的 `.gitignore` 已排除 `data/`、模型缓存、虚拟环境、运行时副本和构建产物，确保你的个人数据不会意外提交。

---

## 🛠️ 技术架构

- **转写引擎**：FunASR `paraformer-zh`（中文语音识别）
- **说话人分离**：`cam++`（声纹识别与分离）
- **VAD 检测**：`fsmn-vad`（语音活动检测）
- **标点恢复**：`ct-punc`（智能标点符号）
- **音频预处理**：ffmpeg `loudnorm` 滤镜
- **界面框架**：Python Tkinter
- **数据存储**：SQLite + JSON 文件

---

## 📜 License 与模型授权

本项目代码采用 [MIT License](LICENSE)。

`paraformer-zh`、`fsmn-vad`、`ct-punc` 和 `cam++` 模型由 FunASR/ModelScope 在运行时自动下载，遵循各自的许可证；本仓库不分发这些模型文件。

---

## 🤝 参与贡献

欢迎提交 Issue 和 Pull Request！

- 🐛 **发现 Bug？** [提交 Issue](https://github.com/lawmanyukk-boop/WordGrab/issues/new)
- 💡 **有新想法？** [发起讨论](https://github.com/lawmanyukk-boop/WordGrab/discussions)
- 🔧 **想贡献代码？** Fork 项目并提交 PR

---

## ❓ 常见问题

<details>
<summary><strong>Q: 首次运行很慢，是什么原因？</strong></summary>

A: 首次运行需要下载约 2GB 的 AI 模型，这是一次性操作。下载完成后，后续转写完全离线，速度会很快。
</details>

<details>
<summary><strong>Q: 支持哪些音频格式？</strong></summary>

A: 支持常见的音频格式，包括 MP3、M4A、WAV、FLAC、AAC 等。程序会自动使用 ffmpeg 进行格式转换。
</details>

<details>
<summary><strong>Q: 说话人分离准确度如何？</strong></summary>

A: 在安静环境、清晰录音的情况下，说话人分离准确度通常在 85-95%。但在多人抢话、严重重叠对话的场景下，可能会有误判。
</details>

<details>
<summary><strong>Q: 可以转写英文或其他语言吗？</strong></summary>

A: 当前版本主要针对中文优化。英文和其他语言的支持计划在未来版本中加入。
</details>

<details>
<summary><strong>Q: AI 总结功能是必需的吗？</strong></summary>

A: 不是。AI 总结是可选功能，不配置也可以正常使用转写功能。如果你需要智能摘要，可以自行配置 OpenAI 兼容接口。
</details>

<details>
<summary><strong>Q: 为什么 Windows Defender 报毒？</strong></summary>

A: 这是 PyInstaller 打包工具的常见误报，因为打包后的 exe 使用了加壳技术。你可以查看源代码确认安全性，或直接使用 Python 脚本运行。
</details>

---

<p align="center">
  <strong>⭐ 觉得有用？给个 Star 支持一下！</strong>
</p>

<p align="center">
  <a href="https://github.com/lawmanyukk-boop/WordGrab">
    <img src="https://img.shields.io/github/stars/lawmanyukk-boop/WordGrab?style=social" alt="GitHub stars">
  </a>
</p>

<p align="center">
  Made with ❤️ for privacy-conscious users
</p>
