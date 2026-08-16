"""
core/stock_screener.py - 第三步：候选个股大范围筛选
流程：
  1. 初筛（get_stock_universe）：ST剔、板块剔、价格区间、市值区间
  2. 进一步条件：近5日涨幅 5%-40%，量能放大1.2倍，相对强度>1.2倍，主板限定
  3. 对每只候选股抓取5大类数据：技术/新闻/基本面/资金/风险
输出：List[StockCandidate]（包含所有字段，供打分使用）
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from core.utils import (
    get_logger, safe_float, safe_int, random_sleep, catch_exception,
)
from data_sources.manager import get_data_source_manager
from data_sources.base import (
    StockBasicInfo, StockTechData, StockNewsData,
    StockFundamentalData, StockMoneyFlowData, StockRiskData,
)

logger = get_logger()


@dataclass
class StockCandidate:
    """完整的候选股数据结构（所有打分需要的字段齐备）"""
    code: str
    name: str
    board: str = ""
    industry: str = ""
    price: float = 0.0
    market_cap_float: float = 0.0

    # 技术面
    tech: StockTechData = field(default_factory=StockTechData)
    # 新闻
    news: StockNewsData = field(default_factory=StockNewsData)
    # 基本面
    fundamental: StockFundamentalData = field(default_factory=StockFundamentalData)
    # 资金流向
    money_flow: StockMoneyFlowData = field(default_factory=StockMoneyFlowData)
    # 风险
    risk: StockRiskData = field(default_factory=StockRiskData)

    # 所属板块近5日涨幅（从领涨板块估算，用于相对强度判定）
    sector_5d_pct_est: float = 0.0

    # 最终数据来源标签
    data_source_tags: List[str] = field(default_factory=list)


def _is_main_board_only(code: str) -> bool:
    """主板限定：沪市主板60开头 / 深市主板000 001 002 003开头"""
    if code.startswith("60"):
        return True
    if code.startswith(("000", "001", "002", "003")):
        return True
    return False


def _estimate_sector_5d_pct(industry: str, up_sectors, down_sectors) -> float:
    """
    粗略估算该个股所属行业近5日涨幅：
    若行业名匹配到领涨/领跌板块则取对应值，否则取0（中性）
    """
    industry_norm = (industry or "").strip()
    if not industry_norm:
        return 0.0
    for s in (up_sectors or []):
        if s.name and (s.name[:3] in industry_norm or industry_norm[:3] in s.name):
            return s.change_pct * 1.5  # 单日 * 1.5 近似5日（粗略）
    for s in (down_sectors or []):
        if s.name and (s.name[:3] in industry_norm or industry_norm[:3] in s.name):
            return s.change_pct * 1.5
    return 0.0


@catch_exception(default_return=[])
def screen_candidates(
    config: Dict[str, Any],
    up_sectors: List = None,
    down_sectors: List = None,
    as_of_date: Optional[date] = None,
) -> List[StockCandidate]:
    """
    第三步主入口：初筛 → 进阶筛选 → 抓取完整数据
    返回 30-50 只候选股（或少于此数则返回全部实际可达）
    """
    logger.info("\n" + "#" * 60)
    logger.info("# Step 3/6: 候选个股大范围筛选 + 详细数据抓取")
    logger.info("#" * 60)
    ds = get_data_source_manager()

    # ========== 1) 初筛（akshare返回全市场满足市值/价格/板块过滤的）==========
    min_mcap = config.get("min_market_cap", 50.0)
    max_mcap = config.get("max_market_cap", 1000.0)
    min_price = config.get("min_price", 5.0)
    exclude = ["科创板", "创业板", "北交所"]

    basics: List[StockBasicInfo] = ds.get_stock_universe(
        min_market_cap=min_mcap,
        max_market_cap=max_mcap,
        min_price=min_price,
        exclude_board=exclude,
    )
    if not basics:
        logger.error("❌ 初筛股票池为空（可能接口异常），返回空列表")
        return []

    # 主板限定（再次保险过滤）
    basics = [b for b in basics if _is_main_board_only(b.code)]
    logger.info("  主板限定后：%d只", len(basics))
    if not basics:
        return []

    # ========== 2) 先取每只股票的K线技术面，再按进阶条件过滤 ==========
    max_stocks = int(config.get("max_stocks", 200))
    min_5d_pct = config.get("min_5d_pct", 5.0)
    max_5d_pct = config.get("max_5d_pct", 40.0)
    min_vol_ratio = config.get("min_vol_ratio", 1.2)
    min_relative = config.get("min_relative_strength", 1.2)

    # 按流通市值降序（优先处理大市值票，保证性能）
    basics_sorted = sorted(
        basics, key=lambda b: safe_float(b.market_cap_float, 0.0), reverse=True
    )

    # 缓存tech对象
    tech_cache: Dict[str, StockTechData] = {}
    passed_after_tech: List[tuple] = []  # (basic, tech)

    step1_total = min(len(basics_sorted), max_stocks * 2)  # 技术面处理量放宽2倍
    logger.info(
        "  开始逐只计算技术指标并过滤（目标最多%d只，先处理前%d只）...",
        max_stocks, step1_total,
    )

    for idx, b in enumerate(basics_sorted[:step1_total]):
        try:
            if (idx + 1) % 50 == 0:
                logger.info("    技术面进度 %d/%d", idx + 1, step1_total)
            t = ds.get_stock_tech(b.code, as_of_date=as_of_date)
            if t is None:
                continue
            tech_cache[b.code] = t

            # --- 进阶门槛1：近5日涨幅 5%~40% ---
            if not (min_5d_pct <= t.pct_5d <= max_5d_pct):
                continue
            # --- 进阶门槛2：量比 >= 1.2 ---
            if t.vol_ratio < min_vol_ratio:
                continue
            # --- 进阶门槛3：价格 > 5（基础版已过滤，再保险）---
            if t.price > 0 and t.price < min_price:
                continue
            # --- 进阶门槛4：相对强度（初步估算）---
            sec_5d = _estimate_sector_5d_pct(b.industry, up_sectors, down_sectors)
            t.sector_5d_pct = sec_5d
            t.relative_5d = t.pct_5d - sec_5d
            # 若行业板块5日涨幅估算为正，要求个股 > 板块*1.2
            if sec_5d > 0:
                if t.pct_5d < sec_5d * min_relative:
                    continue
            # 若板块5日为负，只要个股 > 0即可（放松）
            elif t.pct_5d <= 0:
                continue

            passed_after_tech.append((b, t))
            if len(passed_after_tech) >= max_stocks + 50:
                break
        except Exception as e:
            logger.debug("筛选 %s 异常：%s", b.code, str(e)[:80])
            continue

    logger.info(
        "  技术面进阶筛选通过：%d只（门槛：5日涨幅%.0f-%.0f%%，量比≥%.1f）",
        len(passed_after_tech), min_5d_pct, max_5d_pct, min_vol_ratio,
    )

    if not passed_after_tech:
        logger.warning("⚠️ 进阶筛选后无候选股，将从初筛中取市值靠前的%d只直接进入详细抓取阶段",
                       min(max_stocks, len(basics_sorted)))
        for b in basics_sorted[:min(max_stocks, len(basics_sorted))]:
            if b.code not in tech_cache:
                tech_cache[b.code] = ds.get_stock_tech(b.code, as_of_date=as_of_date) or StockTechData(code=b.code)
            passed_after_tech.append((b, tech_cache[b.code]))

    # 限制最多 max_stocks 进入详细抓取（按近5日涨幅从高到低取，保证赚钱效应）
    passed_after_tech.sort(key=lambda x: safe_float(x[1].pct_5d, 0.0), reverse=True)
    passed_after_tech = passed_after_tech[:max_stocks]

    # ========== 3) 详细数据抓取（新闻/基本面/资金/风险）==========
    enable_news = bool(config.get("enable_news", True))
    total_detail = len(passed_after_tech)
    logger.info(
        "  开始详细数据抓取（%d只，新闻抓取=%s）...",
        total_detail, "开启" if enable_news else "关闭",
    )

    candidates: List[StockCandidate] = []
    for i, (b, t) in enumerate(passed_after_tech):
        try:
            if (i + 1) % 20 == 0 or i == 0:
                logger.info("    详细进度 %d/%d - %s %s",
                            i + 1, total_detail, b.code, b.name)

            # 基本面
            fd = ds.get_stock_fundamental(b.code, name=b.name) or StockFundamentalData(code=b.code)
            # 合并基础信息里的PE/PB（如果更准）
            if safe_float(b.pe_ttm, 0.0) > 0 and safe_float(fd.pe_ttm, 0.0) == 0.0:
                fd.pe_ttm = safe_float(b.pe_ttm, 0.0)
            if safe_float(b.pb, 0.0) > 0 and safe_float(fd.pb, 0.0) == 0.0:
                fd.pb = safe_float(b.pb, 0.0)

            # 新闻（可关闭）
            if enable_news:
                nd = ds.get_stock_news(b.code, name=b.name, window_hours=24) or StockNewsData(code=b.code)
            else:
                nd = StockNewsData(code=b.code)

            # 资金流向
            mf = ds.get_stock_money_flow(b.code, name=b.name, days=5) or StockMoneyFlowData(code=b.code)

            # 风险事件
            rd = ds.get_stock_risk(b.code, name=b.name) or StockRiskData(code=b.code)

            cand = StockCandidate(
                code=b.code, name=b.name,
                board=b.board or "主板",
                industry=b.industry or "",
                price=t.price or b.price,
                market_cap_float=safe_float(b.market_cap_float, 0.0),
                tech=t, news=nd, fundamental=fd, money_flow=mf, risk=rd,
                sector_5d_pct_est=safe_float(t.sector_5d_pct, 0.0),
                data_source_tags=[
                    tag for tag in [t.source, nd.source, fd.source, mf.source, rd.source] if tag
                ],
            )
            candidates.append(cand)

            if (i + 1) % 5 == 0 and enable_news:
                random_sleep(0.3, 0.8)
        except Exception as e:
            logger.warning("详细抓取 %s %s 失败：%s", b.code, b.name, str(e)[:100])
            continue

    logger.info("✅ 候选个股筛选完成，共 %d 只", len(candidates))
    return candidates
