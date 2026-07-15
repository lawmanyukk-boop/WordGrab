#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WordGrab · 桌面 GUI（pywebview）
飞书妙记风：左侧历史 + 文稿点读联动 + 播放器 + 说话人改名 + 搜索 + 导出
"""
import os, sys, json, time, uuid, shutil, threading, datetime, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
INDEX = os.path.join(DATA, "index.json")

AUDIO_EXT = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
             ".aac": "audio/aac", ".mp4": "video/mp4", ".mov": "video/quicktime",
             ".m4v": "video/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg"}

# 供 HTTP 处理器（拖拽上传）回调到 Api 实例
API_REF = None

# 转写进度共享给前端轮询（后台线程只写这个 dict，不从子线程调 evaluate_js —— 后者在 macOS 上易崩）
PROGRESS = {}                      # iid -> {stage, pct, info, status, msg, title, partial}
TRANSCRIBE_LOCK = threading.Lock()  # 序列化转写，避免并发共用同一个模型实例出错
DELETED = set()                    # 转写期间被用户删除的 iid，两阶段落盘前都要检查

# ---------- 历史存储 ----------

def load_index():
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_index(items):
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def item_dir(iid):
    return os.path.join(DATA, iid)


def load_item(iid):
    with open(os.path.join(item_dir(iid), "transcript.json"), encoding="utf-8") as f:
        return json.load(f)


def save_item(iid, data):
    with open(os.path.join(item_dir(iid), "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def audio_path_of(iid):
    d = item_dir(iid)
    for fn in os.listdir(d):
        if fn.startswith("audio"):
            return os.path.join(d, fn)
    return None


# ---------- 本地 HTTP 服务（UI + 支持 Range 的音频） ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_file(self, path, ctype):
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404); return
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(UI, "index.html"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            fn = os.path.basename(path)
            fp = os.path.join(UI, fn)
            if os.path.exists(fp):
                ct = "text/css" if fn.endswith(".css") else "application/javascript"
                return self._send_file(fp, ct + "; charset=utf-8")
        if path.startswith("/audio/"):
            iid = path.split("/audio/")[1]
            ap = audio_path_of(iid)
            if ap:
                ext = os.path.splitext(ap)[1].lower()
                return self._send_file(ap, AUDIO_EXT.get(ext, "application/octet-stream"))
        if path.startswith("/status/"):
            iid = path.split("/status/")[1]
            st = PROGRESS.get(iid) or {"status": "unknown"}
            body = json.dumps(st, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        # 拖拽上传：WebView 里拿不到文件路径，只能把字节流 POST 过来落盘再转写
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(self.path)
        if parsed.path != "/upload" or API_REF is None:
            return self.send_error(404)
        q = parse_qs(parsed.query)
        name = unquote(q.get("name", ["audio"])[0])
        length = int(self.headers.get("Content-Length", 0) or 0)
        iid = uuid.uuid4().hex[:12]
        os.makedirs(item_dir(iid), exist_ok=True)
        ext = os.path.splitext(name)[1].lower() or ".m4a"
        dst = os.path.join(item_dir(iid), "audio" + ext)
        try:
            remaining = length
            with open(dst, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception as e:
            self.send_error(500, str(e)); return
        title = os.path.splitext(os.path.basename(name))[0]
        PROGRESS[iid] = {"stage": "准备中…", "pct": None, "info": None, "status": "running",
                         "msg": "", "title": title, "partial": []}
        threading.Thread(target=API_REF._run_transcribe, args=(iid, dst, title),
                         daemon=True).start()
        body = json.dumps({"id": iid, "title": title}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port


# ---------- JS ↔ Python 桥 ----------

class Api:
    def __init__(self):
        self.window = None
        self.port = None

    # 历史列表
    def list_items(self):
        return load_index()

    # 前端埋点日志
    def log(self, msg):
        print(f"[JS] {msg}", flush=True)
        return True

    # 打开某条：返回合并后的分段 + 说话人名 + 音频地址
    def open_item(self, iid):
        import engine
        data = load_item(iid)
        spk_pending = bool(data.get("spk_pending"))
        # 初稿全部 spk=0，按说话人合并会揉成一大段，保留分块粒度
        merged = data["segments"] if spk_pending else engine.merge_by_speaker(data["segments"])
        meta = next((x for x in load_index() if x["id"] == iid), {})
        return {
            "id": iid,
            "title": meta.get("title", ""),
            "duration": meta.get("duration", 0),
            "speakers": data.get("speakers", {}),
            "segments": merged,
            "spk_pending": spk_pending,
            "audio_url": f"http://127.0.0.1:{self.port}/audio/{iid}",
        }

    # 选文件（原生对话框）
    def pick_file(self):
        import webview
        print("[bridge] pick_file 进入", flush=True)
        try:
            res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            print(f"[bridge] pick_file 返回: {res}", flush=True)
        except Exception as e:
            print(f"[bridge] pick_file 异常: {e}", flush=True)
            return None
        if res:
            return res[0] if isinstance(res, (list, tuple)) else res
        return None

    # 开始转写（后台线程；进度写入 PROGRESS，前端轮询 /status/<iid>）
    def start_transcribe(self, src_path):
        iid = uuid.uuid4().hex[:12]
        title = os.path.splitext(os.path.basename(src_path))[0]
        PROGRESS[iid] = {"stage": "准备中…", "pct": None, "info": None, "status": "running",
                        "msg": "", "title": title, "partial": []}
        threading.Thread(target=self._do_transcribe, args=(iid, src_path), daemon=True).start()
        return iid

    @staticmethod
    def _set(iid, stage, pct=None, info=None, status=None, msg=None, **extra):
        # 合并更新（partial/title 等字段跨多次 _set 保留）
        st = PROGRESS.setdefault(iid, {})
        st.update({"stage": stage, "pct": pct})
        if info is not None:
            st["info"] = {**(st.get("info") or {}), **info}
        if status is not None:
            st["status"] = status
        if msg is not None:
            st["msg"] = msg
        st.update(extra)

    def _do_transcribe(self, iid, src_path):
        # 走「导入按钮」：先把源文件复制进条目目录，再复用转写流程
        try:
            os.makedirs(item_dir(iid), exist_ok=True)
            ext = os.path.splitext(src_path)[1].lower() or ".m4a"
            dst = os.path.join(item_dir(iid), "audio" + ext)
            shutil.copy2(src_path, dst)
            title = os.path.splitext(os.path.basename(src_path))[0]
        except Exception as e:
            self._set(iid, "出错", status="error", msg=str(e))
            return
        self._run_transcribe(iid, dst, title)

    def _run_transcribe(self, iid, audio_file, title):
        # 对已落盘的 audio_file 执行转写（导入按钮 / 拖拽上传共用）
        # 两阶段：①快路径初稿（渐进出字，无说话人）→ 存盘可读；②声纹分离 → 更新说话人
        import engine
        print(f"[transcribe] start iid={iid} file={audio_file}", flush=True)
        if TRANSCRIBE_LOCK.locked():
            self._set(iid, "排队中，等待上一个转写完成…")
        with TRANSCRIBE_LOCK:
            wav = None
            try:
                def prog(stage, pct, info=None):
                    print(f"[transcribe] {iid} · {stage} pct={pct}", flush=True)
                    self._set(iid, stage, pct, info)

                dur = engine.probe_duration(audio_file)
                self._set(iid, "解码 + 响度归一化…", None, {"duration": dur}, title=title)
                wav = engine.to_wav16k(audio_file)

                # ---- 阶段一：初稿，逐块推给前端 ----
                def on_chunk(segs):
                    st = PROGRESS.get(iid) or {}
                    st["partial"] = segs
                    PROGRESS[iid] = st

                draft = engine.transcribe_draft(wav, progress=prog, on_chunk=on_chunk)
                if iid in DELETED:
                    print(f"[transcribe] item deleted during draft phase iid={iid}", flush=True)
                    self._set(iid, "已删除", status="error", msg="记录已被删除")
                    return
                if draft:
                    save_item(iid, {"speakers": {"0": "说话人1"}, "segments": draft,
                                    "spk_pending": True})
                    items = load_index()
                    items.insert(0, {
                        "id": iid, "title": title, "duration": dur,
                        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "n_speakers": 1,
                    })
                    save_index(items)
                    self._set(iid, "文稿就绪，说话人分离中…", None, status="draft")
                    print(f"[transcribe] draft ready iid={iid} segs={len(draft)}", flush=True)

                # ---- 阶段二：完整管线（含声纹分离） ----
                segments = engine.transcribe_full(wav, progress=prog)
                n_spk = len({s["spk"] for s in segments}) or 1
                speakers = {str(i): f"说话人{i + 1}" for i in range(n_spk)}

                if iid in DELETED or not os.path.isdir(item_dir(iid)):
                    # 用户在阶段二期间删除了这条记录，直接放弃
                    print(f"[transcribe] item deleted during spk phase iid={iid}", flush=True)
                    self._set(iid, "已删除", status="error", msg="记录已被删除")
                    return
                save_item(iid, {"speakers": speakers, "segments": segments})

                items = load_index()
                if not any(x["id"] == iid for x in items):
                    items.insert(0, {
                        "id": iid, "title": title, "duration": dur,
                        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "n_speakers": n_spk,
                    })
                else:
                    for x in items:
                        if x["id"] == iid:
                            x["n_speakers"] = n_spk
                save_index(items)
                print(f"[transcribe] done iid={iid} segs={len(segments)}", flush=True)
                self._set(iid, "完成", 1.0, status="done")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[transcribe-error] iid={iid} {e!r}", flush=True)
                self._set(iid, "出错", status="error", msg=str(e))
            finally:
                if wav:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass

    # 说话人改名
    def rename_speaker(self, iid, spk_index, new_name):
        data = load_item(iid)
        data.setdefault("speakers", {})[str(spk_index)] = new_name
        save_item(iid, data)
        return True

    # 记录改名
    def rename_item(self, iid, title):
        items = load_index()
        for x in items:
            if x["id"] == iid:
                x["title"] = title
        save_index(items)
        return True

    # 删除
    def delete_item(self, iid):
        DELETED.add(iid)  # 若该条还在转写，阻止后续阶段把它重新落盘
        items = [x for x in load_index() if x["id"] != iid]
        save_index(items)
        shutil.rmtree(item_dir(iid), ignore_errors=True)
        return True

    # 导出 txt（弹原生保存对话框选位置，避开桌面权限问题）
    def export_txt(self, iid):
        import webview
        data = load_item(iid)
        import engine
        merged = (data["segments"] if data.get("spk_pending")
                  else engine.merge_by_speaker(data["segments"]))
        spk = data.get("speakers", {})
        meta = next((x for x in load_index() if x["id"] == iid), {})

        def ts(ms):
            s = int(ms) // 1000
            return f"{s // 60:02d}:{s % 60:02d}"

        lines = [f"*{meta.get('title','')}*", f"*{meta.get('created','')}*", ""]
        for m in merged:
            name = spk.get(str(m["spk"]), f"说话人{m['spk']+1}")
            lines.append(f"[{ts(m['start'])}] {name}：{m['text']}")

        default = (meta.get("title") or "转写") + ".txt"
        res = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=default,
            directory=os.path.expanduser("~/Documents"))
        if not res:
            return None
        out = res if isinstance(res, str) else res[0]
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return out


def _preload_model():
    # 启动即后台加载模型（约 30-40 秒），首次转写不用再等
    try:
        import engine
        engine.get_model()
        print("[preload] 模型加载完成", flush=True)
    except Exception as e:
        print(f"[preload] 模型加载失败（转写时会重试）: {e!r}", flush=True)


def _set_dock_icon():
    # 直接用 python 跑时进程会显示 Python 的火箭图标，这里换成本应用的。
    # 必须等主运行循环启动后再设，否则 app 完成启动时会被重置回火箭。
    try:
        import AppKit
        from Foundation import NSOperationQueue
        path = next((p for p in (os.path.join(HERE, "assets", "icon.icns"),
                                 os.path.join(HERE, "icon.icns"))
                     if os.path.exists(p)), None)
        if not path:
            print("[dock] 未找到 icon.icns，跳过", flush=True)
            return

        def apply():
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(path)
            if img:
                AppKit.NSApplication.sharedApplication().setApplicationIconImage_(img)
                print("[dock] Dock 图标已设置", flush=True)
            else:
                print("[dock] icns 加载失败", flush=True)

        NSOperationQueue.mainQueue().addOperationWithBlock_(apply)
    except Exception as e:
        print(f"[dock] 设置图标失败: {e!r}", flush=True)


def main():
    global API_REF
    import webview
    api = Api()
    API_REF = api
    _set_dock_icon()
    port = start_server()
    api.port = port
    threading.Thread(target=_preload_model, daemon=True).start()
    window = webview.create_window(
        "WordGrab", url=f"http://127.0.0.1:{port}/",
        js_api=api,
        width=1120, height=740, min_size=(900, 600),
        background_color="#F5F7FB",
    )
    api.window = window
    window.events.shown += _set_dock_icon
    webview.start(debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
