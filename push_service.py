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


def push_daily_report(
    webhook_url: str, markdown_content: str, force: bool = False
) -> bool:
    logger.info("=" * 60)
    logger.info("开始推送早盘报告...")
    pusher = WeChatWorkPusher(webhook_url)
    # 企业微信Markdown长度限制约4096字，超长则分段发送
    max_len = 3800
    if len(markdown_content) <= max_len:
        return pusher.send_markdown(markdown_content, force=force)
    # 分段：按一级或二级标题切
    logger.warning("报告过长(%d字符)，将分段发送", len(markdown_content))
    chunks = []
    cur = ""
    for line in markdown_content.split("\n"):
        if line.startswith("### ") and len(cur) > max_len * 0.7:
            chunks.append(cur)
            cur = line + "\n"
        else:
            cur += line + "\n"
            if len(cur) > max_len:
                chunks.append(cur[:max_len])
                cur = cur[max_len:]
    if cur:
        chunks.append(cur)
    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        header = f"【分段 {i}/{len(chunks)}】\n" if len(chunks) > 1 else ""
        ok = pusher.send_markdown(header + chunk, force=force or i > 1)
        all_ok = all_ok and ok
        time.sleep(1.0)
    return all_ok
