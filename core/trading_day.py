"""
core/trading_day.py - 交易日校验模块
功能：校验今日是否为A股交易日，非交易日直接终止流程
"""
import sys
from datetime import date
from typing import Tuple

from core.utils import (
    get_logger, is_trading_day, today_str,
)

logger = get_logger()


def verify_trading_day(today: date = None, force: bool = False) -> Tuple[bool, str]:
    """
    校验当天是否为A股交易日
    Returns:
        (is_trading: bool, reason: str)
    """
    today = today or date.today()
    if force:
        logger.info("🔓 强制模式（--force/force_push=true）：跳过交易日校验")
        return True, "force模式跳过"

    wd = today.weekday()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_label = weekday_names[wd]

    if wd >= 5:
        msg = f"今日({today.isoformat()} {weekday_label})为周末，非A股交易日，流程终止。"
        logger.info("ℹ️  " + msg)
        return False, msg

    if not is_trading_day(today):
        msg = f"今日({today.isoformat()} {weekday_label})为法定节假日，A股休市，流程终止。"
        logger.info("ℹ️  " + msg)
        return False, msg

    logger.info("📅 今日(%s %s)为A股交易日，继续执行早盘情报流程...",
                today.isoformat(), weekday_label)
    return True, "交易日"


if __name__ == "__main__":
    ok, reason = verify_trading_day()
    print(f"校验结果：ok={ok}, reason={reason}")
    print(f"今日：{today_str()}")
    if not ok:
        sys.exit(0)
