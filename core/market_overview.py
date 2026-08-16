"""
core/market_overview.py - 第二步：大盘环境全网实时检索
整合：外围隔夜美股/韩股、A50期货、A股大盘情绪、资金面、热门板块
输出：MarketOverviewContext（供后续打分+报告使用）
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from core.utils import (
    get_logger, safe_float, format_pct,
)
from data_sources.manager import get_data_source_manager
from data_sources.base import (
    GlobalIndexData, AShareMarketData, SectorData,
)

logger = get_logger()


@dataclass
class MarketOverviewContext:
    """大盘环境上下文（后续打分+报告都依赖这个）"""
    # 隔夜外围
    global_indices: Dict[str, GlobalIndexData] = field(default_factory=dict)
    # A股大盘
    a_share: AShareMarketData = field(default_factory=AShareMarketData)
    # 板块排行
    up_sectors: List[SectorData] = field(default_factory=list)
    down_sectors: List[SectorData] = field(default_factory=list)
    # 资金面
    sector_inflow: List[SectorData] = field(default_factory=list)
    sector_outflow: List[SectorData] = field(default_factory=list)
    # 市场情绪：偏强/震荡/偏弱
    sentiment: str = "震荡"
    # 用于展示的摘要（一行字符串）
    summary_global: str = ""
    summary_a_share: str = ""
    # 大小盘风格
    style_label: str = "均衡"
    # 数据来源标签
    sources: List[str] = field(default_factory=list)


def _build_global_summary(gis: Dict[str, GlobalIndexData]) -> str:
    parts = []
    for key in ["纳斯达克", "标普500", "道琼斯", "中国金龙", "KOSPI", "KOSDAQ"]:
        gd = gis.get(key)
        if not gd:
            continue
        pct = safe_float(gd.change_pct, 0.0)
        name_cn = gd.name or key
        # 精简展示：外围取关键的
        short = name_cn
        if "纳斯达克" in name_cn and key != "中国金龙":
            short = "纳指"
        elif "道琼斯" in name_cn:
            short = "道指"
        elif "标普" in name_cn:
            short = "标普500"
        elif "金龙" in name_cn:
            short = "金龙指数"
        elif key == "KOSPI":
            short = "KOSPI"
        elif key == "KOSDAQ":
            short = "KOSDAQ"
        parts.append(f"{short}{format_pct(pct)}")
    return "｜".join(parts) if parts else "暂无有效数据"


def _build_a_share_summary(am: AShareMarketData, up_sectors, down_sectors) -> str:
    parts = []
    if am.sh_comp_close > 0:
        parts.append(f"上证{round(am.sh_comp_close, 0)}({format_pct(am.sh_comp_change)})")
    if am.sz_comp_close > 0:
        parts.append(f"深成{round(am.sz_comp_close, 0)}({format_pct(am.sz_comp_change)})")
    if am.cyb_close > 0:
        parts.append(f"创业板{round(am.cyb_close, 0)}({format_pct(am.cyb_change)})")
    if am.up_count or am.down_count:
        parts.append(f"涨跌{am.up_count}:{am.down_count}")
    if am.limit_up or am.limit_down:
        parts.append(f"涨跌停{am.limit_up}:{am.limit_down}")
    if up_sectors:
        names = "、".join(s.name for s in up_sectors[:3])
        parts.append(f"领涨：{names}")
    return "｜".join(parts) if parts else "暂无有效数据"


def _detect_style(am: AShareMarketData) -> str:
    """判断大小盘风格：中证1000 vs 沪深300 近3日涨跌幅差"""
    if am.zz1000_3d_change == 0 and am.hs300_3d_change == 0:
        return "均衡"
    diff = am.zz1000_3d_change - am.hs300_3d_change
    if diff > 1.5:
        return "小盘占优"
    elif diff < -1.5:
        return "大盘占优"
    return "均衡"


def fetch_market_overview(
    as_of_date: Optional[date] = None,
    enable_sector_fund_flow: bool = True,
) -> MarketOverviewContext:
    """
    第二步主入口：获取完整的大盘环境
    Returns MarketOverviewContext
    """
    logger.info("\n" + "#" * 60)
    logger.info("# Step 2/6: 大盘环境全网实时检索")
    logger.info("#" * 60)
    ds = get_data_source_manager()
    ctx = MarketOverviewContext()

    # 1) 隔夜美股 + 韩股 + 金龙 + A50
    try:
        gis = ds.get_global_indices(as_of_date=as_of_date)
        ctx.global_indices = gis
        for k, v in gis.items():
            if v and v.source and v.source not in ctx.sources:
                ctx.sources.append(v.source)
    except Exception as e:
        logger.warning("外围指数抓取异常：%s", str(e)[:120])
    ctx.summary_global = _build_global_summary(ctx.global_indices)
    logger.info("🌍 外围隔夜：%s", ctx.summary_global)

    # 2) A股大盘整体情绪 + 涨跌家数 + 北向 + 融资 + A50 + 大小盘
    try:
        am = ds.get_a_share_market(as_of_date=as_of_date)
        ctx.a_share = am
        if am.source and am.source not in ctx.sources:
            ctx.sources.append(am.source)
    except Exception as e:
        logger.warning("A股大盘抓取异常：%s", str(e)[:120])
        am = AShareMarketData(source="")
        ctx.a_share = am

    # 3) 热门板块涨跌排行
    try:
        up, down = ds.get_sector_ranking(top_n=10, as_of_date=as_of_date)
        ctx.up_sectors = up or []
        ctx.down_sectors = down or []
    except Exception as e:
        logger.warning("板块排行抓取异常：%s", str(e)[:120])

    # 4) 资金面概览（主力净流入/出行业前5）
    if enable_sector_fund_flow:
        try:
            mf = ds.get_money_flow_overview(as_of_date=as_of_date)
            ctx.sector_inflow = mf.get("inflow_top5", []) or []
            ctx.sector_outflow = mf.get("outflow_top5", []) or []
        except Exception as e:
            logger.debug("资金流向概览异常：%s", str(e)[:80])

    # 5) 市场情绪（已在am里算出，这里二次确认）
    if ctx.a_share.sentiment_label:
        ctx.sentiment = ctx.a_share.sentiment_label
    else:
        ctx.sentiment = "震荡"
    logger.info("🇨🇳 A股情绪定性：%s", ctx.sentiment)

    # 6) 大小盘风格
    ctx.style_label = _detect_style(ctx.a_share)

    # 7) 摘要
    ctx.summary_a_share = _build_a_share_summary(
        ctx.a_share, ctx.up_sectors, ctx.down_sectors
    )
    logger.info("🇨🇳 A股大盘摘要：%s", ctx.summary_a_share)
    logger.info("  风格：%s，北向：%.2f亿(当日)，融资变化：%.2f亿",
                ctx.style_label,
                safe_float(ctx.a_share.northbound_net, 0.0),
                safe_float(ctx.a_share.margin_balance_change, 0.0))

    if not ctx.sources:
        ctx.sources.append("公开财经媒体")
    return ctx
