"""
core/history_tracker.py - 第六步：云端记录选股结果及历史表现
职责：
  1. 保存当日 TOP5 选股到 output/daily_picks/stock_picks_YYYYMMDD.md
  2. 读取前一日 stock_picks 文件，追踪今日表现，追加到 historical_performance.log
"""
import os
from datetime import date, timedelta
from typing import List, Optional, Tuple

from config.settings import DAILY_PICKS_DIR
from core.utils import (
    get_logger, ensure_dir, safe_float, get_previous_trading_day,
    is_trading_day, today_str,
)
from data_sources.manager import get_data_source_manager
from core.scoring_engine import ScoredCandidate
from core.market_overview import MarketOverviewContext

logger = get_logger()

HIST_LOG_FILE = os.path.join(DAILY_PICKS_DIR, "historical_performance.log")


def save_today_picks(
    top_picks: List[ScoredCandidate],
    mctx: MarketOverviewContext,
    today: Optional[date] = None,
) -> str:
    """
    保存当日选股清单 + 大盘情绪 + 关注等级分布
    返回 文件路径
    """
    today = today or date.today()
    ensure_dir(DAILY_PICKS_DIR)
    filepath = os.path.join(
        DAILY_PICKS_DIR, f"stock_picks_{today.strftime('%Y%m%d')}.md"
    )

    # 关注等级分布
    bucket = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for s in top_picks:
        bucket[s.attention_stars] = bucket.get(s.attention_stars, 0) + 1

    lines = []
    lines.append(f"# {today.year}年{today.month}月{today.day}日 早盘精选股票")
    lines.append("")
    lines.append("【当日选股清单】")
    lines.append("")
    for s in top_picks:
        lines.append(f"{s.cand.code} {s.cand.name}")
    if not top_picks:
        lines.append("（当日无满足综合评分条件的个股）")
    lines.append("")
    lines.append(f"【当日大盘情绪】：{mctx.sentiment}")
    bucket_txt = "、".join(
        f"{stars}星{cnt}只" for stars, cnt in sorted(bucket.items(), reverse=True) if cnt > 0
    )
    lines.append(f"【当日关注等级分布】：{bucket_txt or '无'}")
    lines.append("")
    lines.append(f"_生成时间：{today.isoformat()}_")
    lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("✅ 当日选股清单已保存：%s", filepath)
    return filepath


def _read_picks_from_file(path: str) -> List[Tuple[str, str]]:
    """解析 stock_picks_YYYYMMDD.md，返回 [(code, name), ...]"""
    picks: List[Tuple[str, str]] = []
    if not os.path.exists(path):
        return picks
    try:
        with open(path, "r", encoding="utf-8") as f:
            in_section = False
            for line in f:
                s = line.strip()
                if s.startswith("【当日选股清单】"):
                    in_section = True
                    continue
                if in_section and s.startswith("【"):
                    break
                if in_section and s and not s.startswith("_") and not s.startswith("（"):
                    # 格式: 000001 平安银行
                    parts = s.split()
                    if len(parts) >= 2 and len(parts[0]) == 6 and parts[0].isdigit():
                        picks.append((parts[0], parts[1]))
    except Exception as e:
        logger.warning("解析选股清单失败 %s：%s", path, str(e)[:100])
    return picks


def _fetch_today_intraday_perf(code: str, as_of: date) -> Optional[dict]:
    """
    跟踪前一日选股的当日表现：开盘价、收盘价、最高、最低
    返回 dict 或 None
    """
    try:
        ds = get_data_source_manager()
        tech = ds.get_stock_tech(code=code, as_of_date=as_of)
        if tech is None or tech.price <= 0:
            return None
        # 用K线数据：tech只返回了最新价，无法得到开高低
        # 改用 akshare 的 stock_zh_a_hist 直接取当日K线（由manager调用）
        try:
            import akshare as ak
            from datetime import datetime as _dt
            end_s = as_of.strftime("%Y%m%d")
            start_s = (as_of - timedelta(days=10)).strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_s, end_date=end_s, adjust="qfq",
            )
            if df is None or df.empty:
                return None
            latest = df.sort_values(df.columns[0], ascending=False).iloc[0]
            # 列名
            def _c(keywords):
                for c in df.columns:
                    if all(k in str(c) for k in keywords):
                        return c
                return None
            open_ = safe_float(latest.get(_c(["开盘"]) or df.columns[1], 0.0))
            high = safe_float(latest.get(_c(["最高"]) or df.columns[2], 0.0))
            low = safe_float(latest.get(_c(["最低"]) or df.columns[3], 0.0))
            close = safe_float(latest.get(_c(["收盘"]) or df.columns[4], 0.0))
            if open_ <= 0 or close <= 0:
                return None
            pct_open_close = (close - open_) / open_ * 100
            max_up = (high - open_) / open_ * 100 if open_ > 0 else 0.0
            max_down = (low - open_) / open_ * 100 if open_ > 0 else 0.0
            return {
                "open": round(open_, 2),
                "close": round(close, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "pct_oc": round(pct_open_close, 2),
                "max_up": round(max_up, 2),
                "max_down": round(max_down, 2),
            }
        except Exception as e:
            logger.debug("取当日K线失败 %s：%s", code, str(e)[:80])
            return None
    except Exception as e:
        logger.debug("跟踪表现失败 %s：%s", code, str(e)[:80])
        return None


def track_previous_picks_performance(
    today: Optional[date] = None,
) -> int:
    """
    加载前一个交易日的stock_picks文件，跟踪当日表现并追加到历史日志。
    返回：跟踪成功并写入的记录条数
    """
    today = today or date.today()
    # 取上一个交易日（T-1）的选股结果
    prev_trade_day = get_previous_trading_day(today)
    prev_path = os.path.join(
        DAILY_PICKS_DIR, f"stock_picks_{prev_trade_day.strftime('%Y%m%d')}.md"
    )
    if not os.path.exists(prev_path):
        logger.info("ℹ️  前一交易日(%s)选股清单不存在，跳过历史跟踪", prev_trade_day)
        return 0
    picks = _read_picks_from_file(prev_path)
    if not picks:
        logger.info("ℹ️  前一交易日(%s)选股清单为空，跳过历史跟踪", prev_trade_day)
        return 0

    logger.info(
        "📈 开始跟踪前一交易日(%s)选股的今日(%s)表现（共%d只）...",
        prev_trade_day, today, len(picks),
    )
    ensure_dir(DAILY_PICKS_DIR)
    log_lines = []
    ok_cnt = 0
    for code, name in picks:
        perf = _fetch_today_intraday_perf(code, as_of=today)
        if not perf:
            # 取不到就跳过，但打个占位（避免日志断档）
            log_lines.append(
                f"{today.isoformat()} | {code} {name} | 数据缺失"
            )
            continue
        log_lines.append(
            f"{today.isoformat()} | {code} {name} "
            f"| O{perf['open']:.2f} C{perf['close']:.2f} "
            f"| 当日涨跌幅{perf['pct_oc']:+.2f}% "
            f"| 最高涨幅{perf['max_up']:+.2f}% "
            f"| 最大回撤{perf['max_down']:+.2f}%"
        )
        ok_cnt += 1

    if log_lines:
        try:
            with open(HIST_LOG_FILE, "a", encoding="utf-8") as f:
                f.write("\n".join(log_lines) + "\n")
            logger.info("✅ 历史表现日志已追加：%s（成功%d只）", HIST_LOG_FILE, ok_cnt)
        except Exception as e:
            logger.warning("写入历史日志失败：%s", str(e)[:80])
    return ok_cnt
