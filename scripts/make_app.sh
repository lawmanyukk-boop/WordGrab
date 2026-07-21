#!/usr/bin/env bash
# 在当前项目根目录生成可双击启动的 macOS App 包。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-$PROJECT_ROOT/WordGrab.app}"
CONTENTS_DIR="$APP_DIR/Contents"
RUNTIME_ROOT="$HOME/Library/Application Support/录音转文字"

if [[ ! -f "$PROJECT_ROOT/app.py" ]]; then
  echo "找不到 app.py，请从项目目录运行此脚本。" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/assets/icon.icns" ]]; then
  echo "找不到 assets/icon.icns，无法生成 App 图标。" >&2
  exit 1
fi

mkdir -p "$CONTENTS_DIR/MacOS" "$CONTENTS_DIR/Resources"
rm -f "$CONTENTS_DIR/MacOS/WordGrab-bin"
cp "$PROJECT_ROOT/assets/icon.icns" "$CONTENTS_DIR/Resources/icon.icns"

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>WordGrab</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.wordgrab</string>
    <key>CFBundleName</key>
    <string>WordGrab</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2.0</string>
    <key>CFBundleVersion</key>
    <string>1.2.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# 尝试把 Python 解释器复制进 bundle：进程可执行文件位于 .app 内，
# macOS 才会把运行中的进程认作本应用（否则 Dock 显示 Python 的火箭图标和名字）。
BUNDLED_BIN=""
PY_SOURCE=""
for CANDIDATE in "$PROJECT_ROOT/.venv/bin/python" "$RUNTIME_ROOT/.venv/bin/python" "$(command -v python3 || true)"; do
  if [[ -n "$CANDIDATE" && -x "$CANDIDATE" ]]; then PY_SOURCE="$CANDIDATE"; break; fi
done
if [[ -n "$PY_SOURCE" ]]; then
  BASE="$("$PY_SOURCE" -c 'import sys; print(sys.base_exec_prefix)' 2>/dev/null || true)"
  REAL="$BASE/Resources/Python.app/Contents/MacOS/Python"   # framework 构建的真实解释器
  if [[ -f "$REAL" && -f "$BASE/Python3" ]]; then
    cp "$REAL" "$CONTENTS_DIR/MacOS/WordGrab-bin"
    # 二进制里的 Python3 动态库是相对引用，拷出后改成绝对路径并临时签名
    OLD_REF="$(otool -L "$CONTENTS_DIR/MacOS/WordGrab-bin" | awk '/@executable_path.*Python3/{print $1; exit}')"
    if [[ -n "$OLD_REF" ]]; then
      install_name_tool -change "$OLD_REF" "$BASE/Python3" "$CONTENTS_DIR/MacOS/WordGrab-bin" 2>/dev/null
    fi
    codesign -f -s - "$CONTENTS_DIR/MacOS/WordGrab-bin" 2>/dev/null
    chmod +x "$CONTENTS_DIR/MacOS/WordGrab-bin"
    BUNDLED_BIN=1
    echo "已内嵌解释器（Dock 图标/名字将显示为本应用）"
  fi
fi
# App 源码放在 Application Support，避免 macOS 对桌面目录的应用访问限制。
mkdir -p "$RUNTIME_ROOT/ui" "$RUNTIME_ROOT/assets"
cp "$PROJECT_ROOT/app.py" "$PROJECT_ROOT/ai_service.py" "$PROJECT_ROOT/engine.py" "$PROJECT_ROOT/transcribe.py" "$PROJECT_ROOT/requirements.txt" "$PROJECT_ROOT/README.md" "$RUNTIME_ROOT/"
cp -R "$PROJECT_ROOT/ui/." "$RUNTIME_ROOT/ui/"
cp -R "$PROJECT_ROOT/assets/." "$RUNTIME_ROOT/assets/"

# 生成启动脚本；运行时再定位虚拟环境依赖目录。
cat > "$CONTENTS_DIR/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
DIR="\$(cd "\$(dirname "\$0")" && pwd)"
PROJECT_ROOT="\$HOME/Library/Application Support/录音转文字"
cd "\$PROJECT_ROOT" || exit 1
export MODELSCOPE_CACHE="\$HOME/.cache/modelscope"
mkdir -p "\$PROJECT_ROOT/data"

PYTHON="\$DIR/WordGrab-bin"
VENV_SITE="\$(find "\$PROJECT_ROOT/.venv/lib" -maxdepth 2 -type d -name site-packages 2>/dev/null | head -1)"
# 优先使用 App 包内的可执行文件，确保 macOS 将进程识别为 WordGrab；
# 依赖仍从完整虚拟环境读取，不影响转写与 AI 功能。
if [[ -x "\$PYTHON" && -n "\$VENV_SITE" ]]; then
  export PYTHONPATH="\$VENV_SITE\${PYTHONPATH:+:\$PYTHONPATH}"
else
  PYTHON="\$PROJECT_ROOT/.venv/bin/python"
fi
if [[ ! -x "\$PYTHON" ]]; then
  PYTHON="\$(command -v python3 || true)"
fi
if [[ -z "\$PYTHON" ]]; then
  echo "未找到 Python 3。请安装 Python 3.9 或更高版本。" >&2
  exit 1
fi

# 强制以原生 arm64 运行，避免 Finder 经 Rosetta 启动时加载 arm64 PyTorch 失败。
exec /usr/bin/arch -arm64 "\$PYTHON" "\$PROJECT_ROOT/app.py" >> "\$PROJECT_ROOT/data/app.log" 2>&1
LAUNCHER

chmod +x "$CONTENTS_DIR/MacOS/launcher"
echo "已生成：$APP_DIR"
