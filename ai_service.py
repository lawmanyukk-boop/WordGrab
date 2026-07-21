"""WordGrab AI summary support for OpenAI-compatible services."""

import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request


KEY_FILE = os.path.expanduser("~/Library/Application Support/录音转文字/data/.ai_api_key")
CHUNK_CHAR_LIMIT = 12000
# 中转服务高峰期常瞬时断连/超时；这类错误自动重试而非直接失败。
_MAX_ATTEMPTS = 3
_RETRY_HTTP_STATUS = {500, 502, 503, 504}


class AiServiceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_base_url(value):
    url = str(value or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise AiServiceError("AI_INVALID_URL", "接口地址需要以 https:// 或 http:// 开头")
    return url


def save_api_key(value):
    key = str(value or "").strip()
    if not key:
        raise AiServiceError("AI_KEY_MISSING", "请输入 API Key")
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    temporary = KEY_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        file.write(key)
    os.chmod(temporary, 0o600)
    os.replace(temporary, KEY_FILE)
    return key[-4:]


def load_api_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return ""


def delete_api_key():
    try:
        os.remove(KEY_FILE)
    except FileNotFoundError:
        pass


def _request_json(url, key, method="GET", payload=None, timeout=60):
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error = None
    for attempt in range(_MAX_ATTEMPTS):
        # 每次重试都要新建 Request：urllib 的 Request 对象不可安全复用。
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            detail = ""
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
                detail = str(parsed.get("error", {}).get("message") or parsed.get("message") or "")
            except Exception:
                pass
            # 5xx 是服务端临时故障，值得重试；4xx 是请求本身的问题，立即失败。
            if status in _RETRY_HTTP_STATUS and attempt < _MAX_ATTEMPTS - 1:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            messages = {
                401: ("AI_AUTH_FAILED", "API Key 无效或已经失效"),
                403: ("AI_ACCESS_DENIED", "当前 API Key 没有访问权限"),
                404: ("AI_NOT_FOUND", "接口地址或模型名称不正确"),
                429: ("AI_RATE_LIMITED", "请求过于频繁、余额不足或额度已用完"),
            }
            code, message = messages.get(status, ("AI_PROVIDER_ERROR", "AI 服务暂时不可用"))
            if detail and status not in {401, 403}:
                message += "：" + detail[:160]
            raise AiServiceError(code, message) from exc
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError, socket.timeout) as exc:
            # RemoteDisconnected(HTTPException)、连接重置、读超时（Py3.9 的
            # socket.timeout 不是 TimeoutError）等瞬时网络错误：重试。
            last_error = exc
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
        except (ValueError, UnicodeDecodeError) as exc:
            raise AiServiceError("AI_INVALID_RESPONSE", "AI 服务返回了无法识别的数据") from exc

    # 重试用尽，把最后一次的真实原因翻译成可读提示（不再显示笼统的“内部错误”）。
    reason = getattr(last_error, "reason", last_error)
    timeout_types = (TimeoutError, socket.timeout)
    if isinstance(last_error, timeout_types) or isinstance(reason, timeout_types):
        raise AiServiceError("AI_TIMEOUT",
                             f"AI 服务多次响应超时（已重试 {_MAX_ATTEMPTS} 次），"
                             "该模型生成较慢，建议换用更快的模型或缩短录音") from last_error
    raise AiServiceError("AI_NETWORK_ERROR",
                         f"与 AI 服务的连接被多次中断（已重试 {_MAX_ATTEMPTS} 次），"
                         "可能是服务繁忙或网络不稳定，请稍后再试") from last_error


def list_models(base_url, key):
    data = _request_json(normalize_base_url(base_url) + "/models", key, timeout=20)
    models = []
    for item in data.get("data", []) if isinstance(data, dict) else []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id:
            models.append(str(model_id))
    return sorted(set(models), key=str.lower)


def _chat(base_url, key, model, messages, max_tokens=3000):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        data = _request_json(normalize_base_url(base_url) + "/chat/completions", key,
                             method="POST", payload=payload, timeout=240)
    except AiServiceError as exc:
        # 部分兼容服务不接受 response_format，自动降级一次。
        if exc.code not in {"AI_PROVIDER_ERROR"}:
            raise
        payload.pop("response_format", None)
        data = _request_json(normalize_base_url(base_url) + "/chat/completions", key,
                             method="POST", payload=payload, timeout=240)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiServiceError("AI_INVALID_RESPONSE", "AI 服务没有返回有效内容") from exc


def test_connection(base_url, key, model):
    text = _chat(base_url, key, model, [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": '请只返回 {"message":"连接成功"}'},
    ], max_tokens=80)
    return bool(text)


def _format_time(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def transcript_blocks(data):
    speakers = data.get("speakers") or {}
    blocks = []
    current = []
    current_size = 0
    for index, segment in enumerate(data.get("segments") or []):
        speaker_index = int(segment.get("spk", 0) or 0)
        speaker = speakers.get(str(speaker_index), f"说话人{speaker_index + 1}")
        line = (f"[seg-{index}|{_format_time(segment.get('start'))}|{speaker}] "
                f"{str(segment.get('text') or '').strip()}")
        if current and current_size + len(line) > CHUNK_CHAR_LIMIT:
            blocks.append("\n".join(current))
            current, current_size = [], 0
        current.append(line)
        current_size += len(line) + 1
    if current:
        blocks.append("\n".join(current))
    return blocks


def _schema_instruction(template_config=None):
    common = (
        '只返回JSON。所有source_segment_ids必须来自输入中的seg-N，不能编造。'
        '不确定的信息不要补写。空内容使用空数组或空字符串。'
    )
    config = template_config if isinstance(template_config, dict) else {}
    detail = config.get("detail", "standard")
    detail_rules = {
        "concise": "使用精简模式，但仍需要覆盖录音中的重要结论和行动。",
        "standard": "使用标准详细度，完整覆盖主要议题、结论、决策和行动。",
        "detailed": "使用详细模式，尽可能完整提取事实、依据、分歧、决策、行动和待确认事项。",
    }.get(detail, "使用标准详细度。")
    custom = ""
    if config:
        focus = "、".join(str(x) for x in config.get("focus", []) if str(x).strip())
        custom = (
            f'\n当前分析模板：{config.get("name") or "自定义模板"}。'
            f'\n分析目标：{config.get("objective") or "根据录音内容进行完整分析"}。'
            + (f'\n重点关注：{focus}。' if focus else "")
            + (f'\n补充要求：{config.get("instructions")}。' if config.get("instructions") else "")
            + f'\n{detail_rules}'
        )
    return common + custom + (
        '先自动识别内容类型：meeting/interview/lecture/call/memo/other。'
        '根据类型调整侧重：会议突出决策、行动、分歧；访谈突出问答主线和原话；'
        '讲座突出知识脉络和概念；通话突出诉求、处理结果和态度；备忘突出想法和提醒。'
        '所有事实、章节、要点和原话尽量关联source_segment_ids；时间使用输入中的mm:ss。'
        '无明确行动项或金句时使用空数组，不得凑数。'
        '格式：{'
        '"overview":{"title":"不超过15字","type":"meeting|interview|lecture|call|memo|other",'
        '"duration":"","speakers":[""]},'
        '"one_line_summary":"不超过40字",'
        '"summary":"完整的多段分析，段落之间用换行分隔",'
        '"chapters":[{"time":"mm:ss","title":"","source_segment_ids":[]}],'
        '"key_points":[{"text":"","time":"mm:ss","source_segment_ids":[]}],'
        '"action_items":[{"task":"","owner":"","due":"未明确","status":"todo|doing|done",'
        '"source_segment_ids":[]}],'
        '"keywords":[""],'
        '"highlights":[{"quote":"原文引用","time":"mm:ss","source_segment_ids":[]}],'
        '"decisions":{"decided":[{"text":"","time":"mm:ss","source_segment_ids":[]}],'
        '"disagreements":[{"text":"","time":"mm:ss","source_segment_ids":[]}],'
        '"open":[{"text":"","time":"mm:ss","source_segment_ids":[]}]},'
        '"suggestions":[{"text":"","source_segment_ids":[]}]}'
    )


def _parse_json(text):
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    # 兼容部分推理模型在 JSON 前后附加解释或 <think> 内容。
    first, last = raw.find("{"), raw.rfind("}")
    if first >= 0 and last > first:
        raw = raw[first:last + 1]
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise AiServiceError("AI_INVALID_RESPONSE", "AI 没有返回有效的结构化总结") from exc
    if not isinstance(result, dict):
        raise AiServiceError("AI_INVALID_RESPONSE", "AI 总结格式不正确")
    return result


def _chat_json(base_url, key, model, messages, schema_instruction, max_tokens):
    raw = _chat(base_url, key, model, messages, max_tokens=max_tokens)
    try:
        return _parse_json(raw)
    except AiServiceError as first_error:
        # 兼容中转服务或推理模型偶尔返回解释文字、残缺转义等非标准 JSON。
        # 第二次只修复格式，不重新分析原始录音，避免引入新事实。
        repair_prompt = (
            "把下面内容修复为严格有效的JSON。只修复格式，不增加、删除或改写事实；"
            "缺失且无法恢复的字段用空字符串或空数组。只返回JSON，不要Markdown。\n"
            + schema_instruction + "\n\n待修复内容：\n" + str(raw)[:24000]
        )
        try:
            repaired = _chat(base_url, key, model, [
                {"role": "system", "content": "你是JSON格式修复器，只返回有效JSON。"},
                {"role": "user", "content": repair_prompt},
            ], max_tokens=max_tokens)
            return _parse_json(repaired)
        except AiServiceError:
            raise first_error


def _quality_issues(result, transcript_chars, detail="standard"):
    """拦截“JSON正确但内容敷衍”的结果。数量要求会随原文长度和详细度调整。"""
    if not isinstance(result, dict):
        return ["结果结构不完整"]
    scale = 0.7 if detail == "concise" else (1.35 if detail == "detailed" else 1.0)
    if transcript_chars < 900:
        targets = (120, 2, 3)
    elif transcript_chars < 4500:
        targets = (360, 4, 6)
    else:
        targets = (600, 5, 8)
    min_summary = max(80, int(targets[0] * scale))
    min_chapters = max(1, int(targets[1] * scale))
    min_points = max(2, int(targets[2] * scale))
    issues = []
    summary = str(result.get("summary") or "").replace("\n", "").strip()
    chapters = result.get("chapters") if isinstance(result.get("chapters"), list) else []
    points = result.get("key_points") if isinstance(result.get("key_points"), list) else []
    decisions = result.get("decisions") if isinstance(result.get("decisions"), dict) else {}
    substantive = sum(bool(result.get(key)) for key in ("action_items", "keywords", "highlights", "suggestions"))
    substantive += sum(bool(decisions.get(key)) for key in ("decided", "disagreements", "open"))
    if len(summary) < min_summary: issues.append(f"综合分析少于{min_summary}字")
    if len(chapters) < min_chapters: issues.append(f"章节少于{min_chapters}个")
    if len(points) < min_points: issues.append(f"关键要点少于{min_points}条")
    if transcript_chars >= 900 and substantive < 3: issues.append("决策、行动、关键词、风险或建议等栏目缺失过多")
    return issues


def generate_summary(data, template_config, base_url, key, model, progress=None, cancelled=None):
    template_config = template_config if isinstance(template_config, dict) else {}
    blocks = transcript_blocks(data)
    if not blocks:
        raise AiServiceError("AI_EMPTY_TRANSCRIPT", "当前文稿没有可总结的内容")
    instruction = _schema_instruction(template_config)
    partials = []
    total_steps = len(blocks) + (1 if len(blocks) > 1 else 0)
    for index, block in enumerate(blocks):
        if cancelled and cancelled():
            raise AiServiceError("AI_CANCELLED", "总结已取消")
        if progress:
            progress(index, total_steps, f"正在分析第 {index + 1}/{len(blocks)} 部分")
        speakers = list((data.get("speakers") or {}).values())
        duration_ms = float(data.get("duration") or 0) * 1000
        if not duration_ms and data.get("segments"):
            duration_ms = max(float(segment.get("end") or segment.get("start") or 0)
                              for segment in data.get("segments") or [])
        duration = _format_time(duration_ms)
        prompt = ("你是严谨的中文录音内容分析助手。根据下面带时间与段落编号的文稿，"
                  "自动识别对话类型并生成可直接使用的分析。语言跟随原文，不编造信息；"
                  "不确定内容标注待确认。\n" + instruction
                  + f"\n\n录音时长：{duration}\n参与人：{json.dumps(speakers, ensure_ascii=False)}"
                  + "\n\n文稿：\n" + block)
        partials.append(_chat_json(base_url, key, model, [
            {"role": "system", "content": "你只依据用户提供的文稿工作，并返回有效JSON。"},
            {"role": "user", "content": prompt},
        ], instruction, max_tokens=6200))
    if len(partials) == 1:
        result = partials[0]
    else:
        if progress:
            progress(len(blocks), total_steps, "正在合并总结")
        merge_prompt = (
            "合并以下分段总结，去重并保留最终确认的信息。不得新增原文中没有的事实。\n"
            + instruction + "\n\n分段结果：\n"
            + json.dumps(partials, ensure_ascii=False)
        )
        result = _chat_json(base_url, key, model, [
            {"role": "system", "content": "你负责合并录音分段总结，只返回有效JSON。"},
            {"role": "user", "content": merge_prompt},
        ], instruction, max_tokens=7000)
    transcript_chars = sum(len(str(segment.get("text") or "")) for segment in data.get("segments") or [])
    issues = _quality_issues(result, transcript_chars, template_config.get("detail", "standard"))
    if issues:
        if cancelled and cancelled():
            raise AiServiceError("AI_CANCELLED", "总结已取消")
        if progress:
            progress(total_steps, total_steps + 1, "正在补充不完整的分析")
        full_text = "\n".join(blocks)
        supplement_prompt = (
            "下面的分析虽然格式正确，但完整度不合格：" + "；".join(issues)
            + "。请重新检查全文，在不编造事实的前提下补齐缺失内容。"
            "对原文已明确的标准、结论、决定、负责人、后续安排和待确认事项不得留空。\n"
            + instruction + "\n\n当前结果：\n" + json.dumps(result, ensure_ascii=False)
            + "\n\n原文：\n" + full_text[:30000]
        )
        result = _chat_json(base_url, key, model, [
            {"role": "system", "content": "你负责检查并补全录音分析，只返回有效JSON。"},
            {"role": "user", "content": supplement_prompt},
        ], instruction, max_tokens=7000)
        remaining = _quality_issues(result, transcript_chars, template_config.get("detail", "standard"))
        if remaining:
            result["_quality_warning"] = "分析可能不完整：" + "；".join(remaining)
    valid_ids = {f"seg-{i}" for i, _ in enumerate(data.get("segments") or [])}
    _clean_source_ids(result, valid_ids)
    return result


def _clean_source_ids(value, valid_ids):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_segment_ids" and isinstance(child, list):
                value[key] = [item for item in child if item in valid_ids]
            else:
                _clean_source_ids(child, valid_ids)
    elif isinstance(value, list):
        for child in value:
            _clean_source_ids(child, valid_ids)


def load_summary(directory):
    path = os.path.join(directory, "ai_summary.json")
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def save_summary(directory, payload):
    path = os.path.join(directory, "ai_summary.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
