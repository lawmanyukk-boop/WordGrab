# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置文件 - 用于 Windows 平台打包

import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
# FunASR 的注册器和 TorchScript 装饰器都会在运行时读取源码。冻结包必须
# 同时携带 .py 文件，否则组件虽然在 PYZ 中，注册和 CIF 初始化仍会失败。
funasr_datas = collect_data_files('funasr', include_py_files=True)
funasr_hiddenimports = [
    'funasr.models.seaco_paraformer.model',
    'funasr.models.paraformer_streaming.model',
    'funasr.models.fsmn_vad_streaming.model',
    'funasr.models.ct_transformer.model',
    'funasr.models.campplus.model',
    'funasr.frontends.wav_frontend',
    'funasr.tokenizer.char_tokenizer',
    'funasr.models.sanm.encoder',
    'funasr.models.scama.encoder',
    'funasr.models.ct_transformer_streaming.encoder',
    'funasr.models.paraformer.decoder',
    'funasr.models.paraformer.cif_predictor',
    'funasr.models.bicif_paraformer.cif_predictor',
]
modelscope_datas = collect_data_files(
    'modelscope', includes=['utils/ast_index_file.py'], include_py_files=True
)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui', 'ui'),
        ('assets', 'assets'),
        ('README.md', '.'),
        ('LICENSE', '.'),
    ] + funasr_datas + modelscope_datas,
    hiddenimports=funasr_hiddenimports + [
        'modelscope',
        'modelscope.version',
        'torch',
        'torchaudio',
        'webview',
        'soundfile',
        'sounddevice',
        'numpy',
        'imageio_ffmpeg',
        'docx',
        'reportlab',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WordGrab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 不显示控制台窗口（纯 GUI 应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon_1024.png',  # Windows 上建议转成 .ico 格式
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WordGrab',
)

# macOS 使用标准 .app 包；Windows 继续输出 dist/WordGrab 文件夹，供 CI 压缩。
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='WordGrab.app',
        icon='assets/icon.icns',
        bundle_identifier='com.local.wordgrab',
        info_plist={
            'CFBundleShortVersionString': '1.3.2',
            'CFBundleVersion': '1.3.2',
        },
    )
