#!/usr/bin/env bash
# 在当前项目根目录生成可双击启动的 macOS App 包。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-$PROJECT_ROOT/录音转文字.app}"
CONTENTS_DIR="$APP_DIR/Contents"

if [[ ! -f "$PROJECT_ROOT/app.py" ]]; then
  echo "找不到 app.py，请从项目目录运行此脚本。" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/assets/icon.icns" ]]; then
  echo "找不到 assets/icon.icns，无法生成 App 图标。" >&2
  exit 1
fi

mkdir -p "$CONTENTS_DIR/MacOS" "$CONTENTS_DIR/Resources"
cp "$PROJECT_ROOT/assets/icon.icns" "$CONTENTS_DIR/Resources/icon.icns"

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>录音转文字</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.voicetotext</string>
    <key>CFBundleName</key>
    <string>录音转文字</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>无需麦克风</string>
</dict>
</plist>
PLIST

# PROJECT_ROOT 在生成时展开为这台机器上的实际项目路径；其余变量留给 App 启动时解析。
cat > "$CONTENTS_DIR/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
PROJECT_ROOT="$PROJECT_ROOT"
cd "\$PROJECT_ROOT" || exit 1
export MODELSCOPE_CACHE="\$HOME/.cache/modelscope"

PYTHON="\$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "\$PYTHON" ]]; then
  PYTHON="\$(command -v python3 || true)"
fi
if [[ -z "\$PYTHON" ]]; then
  echo "未找到 Python 3。请安装 Python 3.9 或更高版本。" >&2
  exit 1
fi

mkdir -p "\$PROJECT_ROOT/data"
# 强制以原生 arm64 运行，避免 Finder 经 Rosetta 启动时加载 arm64 PyTorch 失败。
exec /usr/bin/arch -arm64 "\$PYTHON" "\$PROJECT_ROOT/app.py" >> "\$PROJECT_ROOT/data/app.log" 2>&1
LAUNCHER

chmod +x "$CONTENTS_DIR/MacOS/launcher"
echo "已生成：$APP_DIR"
