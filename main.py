"""
main.py - 每日早盘全球股票情报精选（增强版 v2.1）主入口

功能概览：
  1. Step 0：交易日校验（非交易日直接退出）
  2. Step 1：大盘环境全网实时检索
  3. Step 2：候选个股大范围筛选（30-50只）+ 5大类数据抓取
  4. Step 3：自适应权重打分排序 → TOP 5 主板股票
  5. Step 4：生成 Markdown 报告并保存 + 企业微信推送
  6. Step 5：云端记录选股清单 + 跟踪前一日选股表现

运行模式：
  - GitHub Actions 云端：  python main.py --once [--force]
  - 本地常驻定时调度：    python main.py（每日09:20自动触发）
  - 手动测试单次运行：    python main.py --once --force
  - 仅做调度配置自检：    python main.py --test-scheduler
"""
import os
import sys
import time
import random
import argparse
import traceback
from datetime import datetime, date

# 让 python -m morning_intel_v2.main 或直接 python main.py 都能找到模块
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# dotenv 提前加载
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import schedule

from config.settings import load_config, LOG_DIR
from core.utils import (
    setup_logger, get_logger, today_str, now_str, ensure_dir,
    is_trading_day, get_previous_trading_day, get_next_trading_day,
)
from core.trading_day import verify_trading_day
from core.market_overview import fetch_market_overview
from core.stock_screener import screen_candidates
from core.scoring_engine import rank_and_select
from report_generator import render_report_markdown, save_report
from push_service import WeChatWorkPusher, push_daily_report
from core.history_tracker import save_today_picks, track_previous_picks_performance

logger = setup_logger(log_dir=LOG_DIR)


# ==============================================================
# 完整六步主流程
# ==============================================================
def run_full_pipeline(config: dict, force: bool = False,
                      as_of_date: date = None) -> bool:
    """
    执行完整的早盘情报六步流程。

    Args:
        config: load_config() 返回的配置字典
        force: True=跳过交易日校验 + 忽略当日重复推送保护
        as_of_date: 历史回测模拟日期（None=真实今日）

    Returns:
        bool: 整体流程是否成功（报告生成成功就算True）
    """
    t_start = datetime.now()
    logger.info("\n" + "🚀" * 30)
    logger.info("🚀  每日早盘全球股票情报精选（增强版 v2.1）启动  时间: %s", now_str())
    if as_of_date is not None:
        logger.info("🧪 历史回测模式：模拟交易日 = %s（真实今日 = %s）",
                    as_of_date, date.today())
    logger.info("🚀" * 30)

    # -------- Step 0: 交易日校验 --------
    today = as_of_date if as_of_date is not None else date.today()
    ok, reason = verify_trading_day(today=today, force=force)
    if not ok:
        logger.info("ℹ️  Step 0 交易日校验不通过(%s)，流程直接结束。", reason)
        return False

    try:
        # -------- Step 1: 大盘环境 --------
        mctx = fetch_market_overview(as_of_date=as_of_date)

        # -------- Step 2: 候选股筛选 + 详细数据 --------
        logger.info("\n" + "#" * 60)
        logger.info("# Step 3/6: 候选股筛选（已在 stock_screener 内部分步）")
        logger.info("#" * 60)
        candidates = screen_candidates(
            config=config,
            up_sectors=mctx.up_sectors,
            down_sectors=mctx.down_sectors,
            as_of_date=as_of_date,
        )
        if not candidates:
            logger.warning("⚠️ 候选股为空，继续生成仅含大盘环境的报告")

        # -------- Step 3: 打分 + TOP N --------
        top_picks = rank_and_select(
            candidates=candidates,
            mctx=mctx,
            top_n=config.get("top_n", 5),
            min_score_threshold=config.get("min_score_threshold", 0.0),
        )

        # -------- Step 4: 报告生成 + 推送 --------
        logger.info("\n" + "#" * 60)
        logger.info("# Step 5/6: 生成报告 + 企业微信推送")
        logger.info("#" * 60)
        md = render_report_markdown(mctx, top_picks, today=today)
        report_path = save_report(md, today=today)

        # 推送（force=True 允许重复推送）
        push_ok = push_daily_report(
            config.get("webhook", ""), md, force=force,
        )

        # -------- Step 5: 历史记录 --------
        logger.info("\n" + "#" * 60)
        logger.info("# Step 6/6: 记录选股清单 + 跟踪历史表现")
        logger.info("#" * 60)
        try:
            save_today_picks(top_picks, mctx, today=today)
        except Exception as e:
            logger.warning("保存选股清单失败：%s", str(e)[:80])
        try:
            # 只在真实模式（as_of_date=None 或 ==今日）跟踪，避免回测写日志
            if as_of_date is None or as_of_date == date.today():
                track_previous_picks_performance(today=today)
        except Exception as e:
            logger.warning("跟踪前一日表现失败：%s", str(e)[:80])

        cost = (datetime.now() - t_start).total_seconds()
        logger.info("=" * 60)
        if push_ok:
            logger.info("✅  全流程完成！总耗时 %.1f 秒，选出TOP%d只",
                        cost, len(top_picks))
        else:
            logger.info("✅  全流程完成（推送=模拟/失败），总耗时 %.1f 秒，报告已保存：%s",
                        cost, report_path)
        logger.info("=" * 60)
        return True

    except Exception as e:
        cost = (datetime.now() - t_start).total_seconds()
        logger.error(
            "❌ 主流程异常，耗时%.1f秒：%s\n%s",
            cost, str(e), traceback.format_exc(),
        )
        # 尝试推送告警
        try:
            pusher = WeChatWorkPusher(config.get("webhook", ""))
            pusher.send_text(
                f"【早盘情报系统告警】{today_str()}\n"
                f"流程异常：{type(e).__name__}: {str(e)[:120]}\n"
                f"详见 logs 目录当日日志。",
                force=force,
            )
        except Exception:
            pass
        return False


# ==============================================================
# 常驻调度模式：每日 09:20（±2分钟随机抖动）
# ==============================================================
_RAN_TODAY_MARK = None  # str date iso


def _scheduled_job(config: dict):
    global _RAN_TODAY_MARK
    today_mark = date.today().isoformat()
    if _RAN_TODAY_MARK == today_mark:
        return
    jitter = random.randint(0, 120)
    logger.info("⏰ 命中每日09:20定时，随机抖动等待%d秒后启动...", jitter)
    time.sleep(jitter)
    if _RAN_TODAY_MARK == today_mark:
        return
    _RAN_TODAY_MARK = today_mark
    try:
        run_full_pipeline(config, force=False)
    except Exception as e:
        logger.error("定时任务异常：%s", str(e)[:120])


def run_daemon_mode(config: dict):
    logger.info("🌟🌟🌟  早盘情报系统 - 常驻调度模式启动 🌟🌟🌟")
    logger.info("⏰  每日触发时间：北京时间 09:20（±2分钟随机抖动）")
    logger.info("📅  非交易日（周末/法定节假日）自动跳过")
    logger.info("📝  日志目录：%s", LOG_DIR)
    webhook_ok = bool(config.get("webhook")) and "your-key" not in config.get("webhook", "")
    logger.info("🌐  Webhook状态：%s", "正式推送" if webhook_ok else "模拟模式")

    schedule.every().day.at("09:20").do(_scheduled_job, config=config)
    logger.info("✅  定时任务已注册，进入常驻轮询（Ctrl+C 退出）...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(20)
    except KeyboardInterrupt:
        logger.info("\n👋 收到退出信号，程序正常退出")
        sys.exit(0)


# ==============================================================
# 命令行入口
# ==============================================================
def main():
    parser = argparse.ArgumentParser(
        description="每日早盘全球股票情报精选（增强版 v2.1）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法：
  python main.py                       # 常驻模式，每日09:20自动执行
  python main.py --once                # 单次运行（GitHub Actions 或 手动测试）
  python main.py --once --force        # 单次运行 + 强制推送（跳过交易日校验+重复保护）
  python main.py --test-scheduler      # 仅做交易日判断 + 调度配置自检
  python main.py --reset-push          # 清除当日防重复推送保护后退出
  python main.py --once --force --as-of-date=2026-08-14   # 按指定日期模拟回测
""",
    )
    parser.add_argument("--once", action="store_true",
                        help="单次运行模式（不常驻，立即执行一次完整流程）")
    parser.add_argument("--force", action="store_true",
                        help="强制模式：跳过交易日校验 + 忽略当日重复推送保护（测试用）")
    parser.add_argument("--reset-push", action="store_true",
                        help="仅清除当日防重复推送保护，然后退出")
    parser.add_argument("--test-scheduler", action="store_true",
                        help="仅执行交易日判断 + 调度配置自检，不跑选股推送")
    parser.add_argument("--as-of-date", type=str, default=None,
                        help="🧪 历史回测模拟日期，格式YYYY-MM-DD，仅与--once搭配使用")
    args = parser.parse_args()

    # -------- 解析 as-of-date --------
    as_of_date: date | None = None
    if args.as_of_date:
        try:
            as_of_date = date.fromisoformat(args.as_of_date.strip())
        except (ValueError, AttributeError) as e:
            print(f"❌ --as-of-date 格式错误：{args.as_of_date}，必须是 YYYY-MM-DD")
            sys.exit(2)

    config = load_config()

    # -------- 子命令：reset-push --------
    if args.reset_push:
        WeChatWorkPusher(config.get("webhook", "")).reset_daily_protection()
        return

    # -------- 子命令：test-scheduler --------
    if args.test_scheduler:
        logger.info("🧪 调度配置自检：")
        base = as_of_date if as_of_date is not None else date.today()
        if as_of_date:
            logger.info("  模拟日期 as_of_date = %s", as_of_date)
        wd_names = ["一", "二", "三", "四", "五", "六", "日"]
        logger.info("  目标日期 %s 星期%s | 交易日? %s",
                    base, wd_names[base.weekday()], is_trading_day(base))
        logger.info("  前一交易日(T-1隔夜)：%s", get_previous_trading_day(base))
        logger.info("  下一个交易日：%s", get_next_trading_day(base))
        logger.info("  当前真实时间：%s", now_str())
        webhook_ok = bool(config.get("webhook")) and "your-key" not in config.get("webhook", "")
        logger.info("  Webhook：%s", "正常配置" if webhook_ok else "未配置/模拟模式")
        logger.info("  数据源优先级：%s", config["data_source_priority"])
        logger.info("  常驻触发时间：每日09:20（±2分钟随机抖动），仅交易日执行")
        logger.info("✅  调度配置自检完成")
        return

    # -------- 单次模式 --------
    if args.once:
        logger.info("🧪 单次运行模式（--once）启动...")
        success = run_full_pipeline(config, force=args.force, as_of_date=as_of_date)
        sys.exit(0 if success else 1)

    # -------- 默认：常驻调度 --------
    if as_of_date is not None:
        logger.error("❌ --as-of-date 仅支持配合 --once 使用（仅用于手动回测）")
        sys.exit(2)
    run_daemon_mode(config)


if __name__ == "__main__":
    ensure_dir(LOG_DIR)
    main()
