"""
push_service.py - 企业微信推送 + 防重复保护
"""
import os
import json
import time
from datetime import date
from typing import Dict, Any, Optional, List

from core.utils import (
    get_logger, retry, catch_exception,
)

logger = get_logger()

_LAST_PUSH_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", ".last_push_date_v21"
)


class WeChatWorkPusher:
    def __init__(self, webhook_url: str, timeout: int = 20):
        if not webhook_url or "your-key" in webhook_url:
            logger.warning(
                "⚠️ Webhook未配置！进入模拟模式，仅打印不推送。"
            )
            self.mock_mode = True
            self.webhook_url = None
        else:
            self.mock_mode = False
            self.webhook_url = webhook_url
        self.timeout = timeout

    @retry(max_retries=3, initial_delay=5.0, backoff=2.0)
    def _send_raw(self, payload: Dict) -> bool:
        if self.mock_mode:
            logger.info("[模拟推送] payload预览：\n%s",
                        json.dumps(payload, ensure_ascii=False, indent=2)[:800])
            return True
        import requests
        headers = {"Content-Type": "application/json; charset=utf-8"}
        resp = requests.post(
            self.webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, timeout=self.timeout,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode", -1) != 0:
            raise RuntimeError(
                f"企业微信返回错误：errcode={result.get('errcode')} "
                f"errmsg={result.get('errmsg', '')}"
            )
        return True

    def _already_pushed_today(self) -> bool:
        today = date.today().isoformat()
        if not os.path.exists(_LAST_PUSH_FILE):
            return False
        try:
            with open(_LAST_PUSH_FILE, "r", encoding="utf-8") as f:
                return f.read().strip() == today
        except Exception:
            return False

    def _mark_pushed_today(self):
        os.makedirs(os.path.dirname(_LAST_PUSH_FILE), exist_ok=True)
        try:
            with open(_LAST_PUSH_FILE, "w", encoding="utf-8") as f:
                f.write(date.today().isoformat())
        except Exception as e:
            logger.warning("写入推送日期文件失败：%s", str(e))

    def reset_daily_protection(self):
        try:
            if os.path.exists(_LAST_PUSH_FILE):
                os.remove(_LAST_PUSH_FILE)
                logger.info("✅ 已清除当日防重复推送保护")
        except Exception as e:
            logger.warning("清除失败：%s", str(e))

    @catch_exception(default_return=False)
    def send_markdown(self, content: str, force: bool = False) -> bool:
        if not force and self._already_pushed_today():
            logger.info("ℹ️ 今日已推送，跳过（force=True可强制）")
            return False
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        ok = self._send_raw(payload)
        if ok and not self.mock_mode:
            self._mark_pushed_today()
            logger.info("✅ 企业微信Markdown推送成功")
        return ok

    @catch_exception(default_return=False)
    def send_text(self, content: str, force: bool = False) -> bool:
        if not force and self._already_pushed_today():
            return False
        payload = {
            "msgtype": "text",
            "text": {"content": content, "mentioned_list": []},
        }
        ok = self._send_raw(payload)
        if ok and not self.mock_mode:
            self._mark_pushed_today()
        return ok


def _utf8_truncate(text: str, max_bytes: int) -> str:
    """
    把字符串按 UTF-8 字节数截断，保证不把单个中文字符切成两半。
    企业微信 markdown.content 限制 4096 字节，超出 → errcode 40058。
    """
    if not text:
        return ""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    # 从右往左找最近的 UTF-8 起始字节（1xxxxxxx 是多字节中间，直接跳过）
    cut = max_bytes
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut].decode("utf-8", errors="ignore")


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def push_daily_report(
    webhook_url: str, markdown_content: str, force: bool = False
) -> bool:
    logger.info("=" * 60)
    logger.info("开始推送早盘报告...")
    pusher = WeChatWorkPusher(webhook_url)

    # 企业微信群机器人 Markdown：**content 字节数严格 ≤ 4096 字节（UTF-8）**
    # 我们给分段 header + Markdown 结构余量约 500 字节 → 正文上限 3400 字节
    BODY_MAX_BYTES = 3400
    HARD_LIMIT_BYTES = 4000  # 最终整体（含 header）硬上限 4000 < 4096

    total_bytes = _utf8_len(markdown_content)
    if total_bytes <= BODY_MAX_BYTES:
        return pusher.send_markdown(markdown_content, force=force)

    logger.warning(
        "报告过长(%d字符 / %d字节)，按 BODY_MAX_BYTES=%d 分段发送",
        len(markdown_content), total_bytes, BODY_MAX_BYTES,
    )
    chunks: List[str] = []
    cur = ""
    cur_bytes = 0

    for line in markdown_content.split("\n"):
        line_with_nl = line + "\n"
        line_bytes = _utf8_len(line_with_nl)

        # 优先在"### 小节标题"处切分，保证语义完整
        if line.startswith("### ") and cur_bytes > BODY_MAX_BYTES * 0.7:
            chunks.append(cur)
            cur = line_with_nl
            cur_bytes = line_bytes
            continue
        if cur_bytes + line_bytes <= BODY_MAX_BYTES:
            cur += line_with_nl
            cur_bytes += line_bytes
        else:
            # 当前行塞不下了，先把 cur 输出为一个 chunk
            if cur:
                chunks.append(cur)
            # 单行特别长（比如一整个大表格），单独按字节截断
            if line_bytes <= BODY_MAX_BYTES:
                cur = line_with_nl
                cur_bytes = line_bytes
            else:
                # 按字节拆分这一行
                remaining = line_with_nl
                while _utf8_len(remaining) > BODY_MAX_BYTES:
                    piece = _utf8_truncate(remaining, BODY_MAX_BYTES)
                    chunks.append(piece)
                    remaining = remaining[len(piece):]
                cur = remaining
                cur_bytes = _utf8_len(cur)

    if cur:
        chunks.append(cur)

    # 兜底：任何单 chunk 若字节超过 BODY_MAX_BYTES，强制再按字节切
    safe_chunks: List[str] = []
    for c in chunks:
        cb = _utf8_len(c)
        if cb <= BODY_MAX_BYTES:
            safe_chunks.append(c)
        else:
            idx = 0
            while idx < len(c):
                piece = _utf8_truncate(c[idx:], BODY_MAX_BYTES)
                if not piece:
                    break
                safe_chunks.append(piece)
                idx += len(piece)
    chunks = safe_chunks

    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            header = f"【早盘情报 分段 {i}/{len(chunks)}】\n"
        else:
            header = ""
        to_send = header + chunk
        # 最终保险：整体字节数必须 < 4000（< 官方 4096 硬上限）
        if _utf8_len(to_send) > HARD_LIMIT_BYTES:
            to_send = _utf8_truncate(to_send, HARD_LIMIT_BYTES)
        # 第 1 块和外层保持同样 force 参数；后续块必须 force=true
        # （否则会因为今日已推送标记而被拦住）
        ok = pusher.send_markdown(to_send, force=(force if i == 1 else True))
        all_ok = all_ok and ok
        time.sleep(1.0)
    if not all_ok:
        logger.warning("⚠️ 有部分分段推送失败（详见上方单条日志）")
    else:
        logger.info("✅  %d 段 Markdown 全部推送成功", len(chunks))
    return all_ok
