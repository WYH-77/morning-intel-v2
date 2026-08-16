"""
core/scoring_engine.py - 第四步：综合权重打分排序（自适应增强版）
按市场情绪动态调整五大类权重：
  情绪偏强：技术 40 | 资讯 25 | 资金 10 | 基本面 15 | 市场环境 10
  情绪震荡：技术 30 | 资讯 25 | 资金 15 | 基本面 20 | 市场环境 10
  情绪偏弱：技术 20 | 资讯 20 | 资金 10 | 基本面 35 | 市场环境 15
输出：评分后的 StockCandidate 扩展字段列表（ScoredCandidate）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.utils import get_logger, safe_float
from core.market_overview import MarketOverviewContext
from core.stock_screener import StockCandidate
from data_sources.base import SectorData

logger = get_logger()


# 动态权重表（sum必须==100%）
WEIGHT_BY_SENTIMENT: Dict[str, Dict[str, float]] = {
    "偏强": {
        "market_env": 10.0,
        "tech": 40.0,
        "news": 25.0,
        "fundamental": 15.0,
        "money": 10.0,
    },
    "震荡": {
        "market_env": 10.0,
        "tech": 30.0,
        "news": 25.0,
        "fundamental": 20.0,
        "money": 15.0,
    },
    "偏弱": {
        "market_env": 15.0,
        "tech": 20.0,
        "news": 20.0,
        "fundamental": 35.0,
        "money": 10.0,
    },
}


@dataclass
class ScoredCandidate:
    cand: StockCandidate
    # 各分项得分（原始分，已0-100归一）
    market_env_score: float = 0.0
    tech_score: float = 0.0
    news_score: float = 0.0
    fundamental_score: float = 0.0
    money_score: float = 0.0
    # 总分（按权重加权后）
    total_score: float = 0.0
    # 权重方案名
    weight_plan: str = "震荡"
    # 最终关注等级
    attention_stars: int = 3
    attention_label: str = "★★★☆☆"
    # 核心驱动逻辑（一句话）
    core_logic: str = ""
    # 亮点词列表（用于展示）
    highlights: List[str] = field(default_factory=list)


# ==============================================================
# A. 市场环境分项（0~100，权重10%-15%）
# ==============================================================
def _score_market_env(
    cand: StockCandidate, mctx: MarketOverviewContext
) -> Tuple[float, List[str]]:
    score = 50.0  # 基准分
    highlights: List[str] = []
    industry = cand.industry or ""
    code = cand.code

    # 1) 所属板块是否为当日领涨前3/前5/热点
    def _rank_in_sectors(sectors: List[SectorData]) -> int:
        if not sectors or not industry:
            return -1
        for idx, s in enumerate(sectors):
            sn = s.name[:3]
            if sn in industry or industry[:3] in sn:
                return idx
        return -1

    up_rank = _rank_in_sectors(mctx.up_sectors or [])
    down_rank = _rank_in_sectors(mctx.down_sectors or [])
    if up_rank >= 0 and up_rank < 3:
        score += 15
        highlights.append("所属板块领涨前3")
    elif up_rank >= 0 and up_rank < 5:
        score += 10
        highlights.append("所属板块领涨前5")
    elif up_rank >= 0:
        score += 5
        highlights.append("所属板块热点")

    if down_rank >= 0 and down_rank < 5:
        penalty = max(0, 10 - down_rank)
        score -= penalty

    # 2) 大盘情绪
    if mctx.sentiment == "偏强":
        score += 5
    elif mctx.sentiment == "偏弱":
        score -= 5

    # 3) 行业景气度（简化：近一季度营收增速中位数 → 用fundamental的营收增速估算）
    rev = safe_float(cand.fundamental.industry_median_revenue_yoy, 0.0)
    if rev > 0:
        score += 5
    elif rev < -10:
        score -= 5

    return max(0.0, min(100.0, score)), highlights


# ==============================================================
# B. 量价技术走势（0~100，权重20%-40%）
# ==============================================================
def _score_tech(
    cand: StockCandidate,
) -> Tuple[float, List[str]]:
    score = 50.0
    highlights: List[str] = []
    t = cand.tech

    # 1) 近5日涨幅强度（相对板块）
    rel5 = safe_float(t.relative_5d, 0.0)
    if rel5 >= 5.0:
        score += 10
        highlights.append(f"跑赢板块{rel5:.0f}%+")
    elif rel5 > 0:
        score += 5
    elif -5 < rel5 <= 0:
        score -= 5
    else:
        score -= 10

    # 2) 量能配合度：量比+近5日涨幅方向
    vr = safe_float(t.vol_ratio, 1.0)
    pct5 = safe_float(t.pct_5d, 0.0)
    if pct5 > 0 and vr >= 1.2:
        score += 10
        highlights.append("放量上涨")
    elif pct5 > 0:
        score += 5
    elif pct5 < 0 and vr >= 1.2:
        score -= 5
    elif pct5 < 0:
        score -= 10

    # 3) 换手率健康度（近5日均）
    trn = safe_float(t.turnover_5d_avg, 0.0)
    if 3.0 <= trn <= 8.0:
        score += 8
        highlights.append(f"换手健康{trn:.0f}%")
    elif (1.0 <= trn < 3.0) or (8.0 < trn <= 15.0):
        score += 4
    elif trn > 15.0 or trn < 1.0:
        score -= 4

    # 4) 波动率稳定性（近20日振幅）
    amp = safe_float(t.amplitude_20d, 0.0)
    if amp <= 0:
        pass  # 数据缺失不扣
    elif amp < 30:
        score += 7
    elif 30 <= amp <= 50:
        score += 3
    else:
        score -= 3

    # 5) 均线多头
    if t.ma_bullish:
        score += 5
        highlights.append("均线多头")
    elif t.ma_partial:
        score += 2
    elif t.ma_bearish:
        score -= 5

    # 6) MACD
    if t.macd_golden_cross and t.macd_bar_direction > 0:
        score += 4
        highlights.append("MACD金叉")
    elif t.macd_dead_cross:
        score -= 4

    # 7) RSI
    rsi = safe_float(t.rsi_14, 50.0)
    if 40 <= rsi <= 70:
        score += 3
    elif rsi > 70:
        score -= 2
    elif rsi < 30:
        score += 1  # 超卖仅作反弹参考，加1分不加分太多

    # 8) 布林带
    bp = t.boll_position
    if bp == "上轨":
        score += 3
        highlights.append("布林上轨(强势)")
    elif bp == "下轨":
        score -= 3

    # 9) 放量异动（单日放量>2倍）如果不是上涨型则扣
    if t.abnormal_volume and pct5 < 0:
        score -= 5

    return max(0.0, min(100.0, score)), highlights


# ==============================================================
# C. 最新资讯事件（0~100，权重20%-25%）
# ==============================================================
def _score_news(
    cand: StockCandidate,
) -> Tuple[float, List[str]]:
    score = 40.0  # 无重大资讯的基准
    highlights: List[str] = []
    nd = cand.news
    items = nd.items or []

    major_pos_cnt = 0
    general_pos_cnt = 0
    neg_cnt = 0

    pos_kw_good = [
        "业绩预增", "超预期", "大增", "扭亏为盈",
        "重大合同", "中标", "大额订单", "签约",
        "重组", "并购", "资产注入", "分拆上市",
        "政策利好", "纳入", "产品上市", "技术突破",
    ]
    pos_kw_general = [
        "机构调研", "回购", "增持", "股权激励", "分红", "专利", "行业利好",
    ]
    neg_kw = [
        "减持", "业绩预减", "亏损", "立案", "调查",
        "监管", "问询", "警示", "处罚",
        "诉讼", "仲裁", "违约", "停产",
        "解禁", "质押", "下修",
    ]

    for it in items[:15]:
        title = (it.title or "") + (it.summary or "")
        # 置信度权重
        cw = {"高": 1.0, "中": 0.8, "低": 0.5}.get(it.confidence or "中", 0.8)
        # 低置信度额外扣分
        low_conf_penalty = -3 if it.confidence == "低" else 0
        matched = False
        for kw in pos_kw_good:
            if kw in title:
                major_pos_cnt += 1
                score += 25 * cw
                if kw not in highlights:
                    highlights.append(kw)
                matched = True
                break
        if not matched:
            for kw in pos_kw_general:
                if kw in title:
                    general_pos_cnt += 1
                    score += 15 * cw
                    if kw not in highlights:
                        highlights.append(kw)
                    matched = True
                    break
        if not matched:
            for kw in neg_kw:
                if kw in title:
                    neg_cnt += 1
                    score -= 15 * cw
                    if f"(风险){kw}" not in highlights:
                        highlights.append(f"(风险){kw}")
                    matched = True
                    break
        score += low_conf_penalty

    nd.has_major_positive = major_pos_cnt > 0
    nd.has_major_negative = neg_cnt > 0

    return max(0.0, min(100.0, score)), highlights[:6]


# ==============================================================
# D. 业绩基本面（0~100，权重15%-35%）
# ==============================================================
def _score_fundamental(
    cand: StockCandidate,
) -> Tuple[float, List[str]]:
    score = 50.0
    highlights: List[str] = []
    fd = cand.fundamental

    # 1) 营收+利润增速
    rev = safe_float(fd.revenue_yoy, 0.0)
    np_ = safe_float(fd.net_profit_yoy, 0.0)
    if rev > 20 and np_ > 20:
        score += 10
        highlights.append(f"营收利润双增{min(rev, np_):.0f}%+")
    elif rev > 10 and np_ > 10:
        score += 7
    elif (rev > 0) != (np_ > 0):
        score += 3
    elif rev < 0 and np_ < 0:
        score -= 5

    # 2) 估值合理性（对比行业中位数）
    pe = safe_float(fd.pe_ttm, 0.0)
    industry_pe = safe_float(fd.industry_median_pe, 0.0)
    if industry_pe > 0 and pe > 0:
        ratio = pe / industry_pe
        if ratio <= 1.0:
            score += 5
            highlights.append("PE低于行业")
        elif ratio <= 1.3:
            score += 3
        elif ratio <= 1.5:
            score += 1
        else:
            score -= 2
    elif pe > 0:
        # 无行业PE时用绝对PE粗略
        if pe <= 20:
            score += 4
        elif pe <= 40:
            score += 2
        elif pe > 60:
            score -= 2

    # 3) 盈利能力
    roe = safe_float(fd.roe, 0.0)
    gm = safe_float(fd.gross_margin, 0.0)
    if roe > 15 and gm > 30:
        score += 5
        highlights.append(f"ROE{roe:.0f}%&毛利{gm:.0f}%")
    elif roe > 10 or gm > 20:
        score += 3

    return max(0.0, min(100.0, score)), highlights


# ==============================================================
# E. 资金面（0~100，权重10%-15%）
# ==============================================================
def _score_money(
    cand: StockCandidate,
) -> Tuple[float, List[str]]:
    score = 50.0
    highlights: List[str] = []
    mf = cand.money_flow

    # 1) 北向近5日
    if mf.is_hs_connect:
        nb = safe_float(mf.northbound_5d, 0.0)
        if nb > 1.0:
            score += 5
            highlights.append(f"北向净买{nb:.1f}亿")
        elif nb > 0.2:
            score += 3
        elif nb < -1.0:
            score -= 3

    # 2) 主力近5日
    main_mf = safe_float(mf.main_5d, 0.0)
    if main_mf > 0:
        score += 3
    elif main_mf < -1.0:
        score -= 2

    # 3) 机构龙虎榜净买入次数
    if mf.institution_buy_count > 0:
        score += min(2, mf.institution_buy_count)
        highlights.append(f"龙虎榜净买{mf.institution_buy_count}次")

    return max(0.0, min(100.0, score)), highlights


# ==============================================================
# 综合：单只股打分
# ==============================================================
def score_single_candidate(
    cand: StockCandidate, mctx: MarketOverviewContext,
) -> ScoredCandidate:
    # 权重
    weights = WEIGHT_BY_SENTIMENT.get(mctx.sentiment, WEIGHT_BY_SENTIMENT["震荡"])

    market_env_raw, me_hl = _score_market_env(cand, mctx)
    tech_raw, t_hl = _score_tech(cand)
    news_raw, n_hl = _score_news(cand)
    fund_raw, f_hl = _score_fundamental(cand)
    money_raw, m_hl = _score_money(cand)

    weighted_total = (
        market_env_raw * weights["market_env"] / 100.0
        + tech_raw * weights["tech"] / 100.0
        + news_raw * weights["news"] / 100.0
        + fund_raw * weights["fundamental"] / 100.0
        + money_raw * weights["money"] / 100.0
    ) * 1.0  # 归一到百分制

    # 亮点合并（正向优先，再风险）
    all_pos_hl = []
    all_risk_hl = []
    for h in me_hl + t_hl + n_hl + f_hl + m_hl:
        if h.startswith("(风险)"):
            if h not in all_risk_hl:
                all_risk_hl.append(h)
        else:
            if h not in all_pos_hl:
                all_pos_hl.append(h)
    highlights = (all_pos_hl[:4] + all_risk_hl[:2]) or ["综合得分较高"]

    scored = ScoredCandidate(
        cand=cand,
        market_env_score=round(market_env_raw, 1),
        tech_score=round(tech_raw, 1),
        news_score=round(news_raw, 1),
        fundamental_score=round(fund_raw, 1),
        money_score=round(money_raw, 1),
        total_score=round(weighted_total, 1),
        weight_plan=mctx.sentiment,
        highlights=highlights,
    )

    # 关注等级
    ts = scored.total_score
    if ts >= 85:
        scored.attention_stars = 5
        scored.attention_label = "★★★★★"
    elif ts >= 75:
        scored.attention_stars = 4
        scored.attention_label = "★★★★☆"
    elif ts >= 60:
        scored.attention_stars = 3
        scored.attention_label = "★★★☆☆"
    else:
        scored.attention_stars = 2
        scored.attention_label = "★★☆☆☆"

    # 核心驱动逻辑（一句话）
    logic_parts = []
    if all_pos_hl:
        logic_parts.append("+".join(all_pos_hl[:2]))
    else:
        logic_parts.append("综合面均衡")
    scored.core_logic = "；".join(logic_parts)
    return scored


# ==============================================================
# 全流程排序 + 选TOP N
# ==============================================================
def rank_and_select(
    candidates: List[StockCandidate],
    mctx: MarketOverviewContext,
    top_n: int = 5,
    min_score_threshold: float = 0.0,
) -> List[ScoredCandidate]:
    logger.info("\n" + "#" * 60)
    logger.info("# Step 4/6: 综合权重打分排序（情绪=%s）", mctx.sentiment)
    logger.info("#" * 60)
    if not candidates:
        logger.warning("无候选股，打分结果为空")
        return []

    w = WEIGHT_BY_SENTIMENT.get(mctx.sentiment, WEIGHT_BY_SENTIMENT["震荡"])
    logger.info(
        "  权重方案【%s】：环境%.0f%% 技术%.0f%% 资讯%.0f%% 基本面%.0f%% 资金%.0f%%",
        mctx.sentiment, w["market_env"], w["tech"], w["news"], w["fundamental"], w["money"],
    )

    scored_list: List[ScoredCandidate] = []
    for c in candidates:
        try:
            scored_list.append(score_single_candidate(c, mctx))
        except Exception as e:
            logger.warning("打分 %s %s 异常：%s", c.code, c.name, str(e)[:80])
            continue

    if not scored_list:
        return []

    # 统计
    scores = [s.total_score for s in scored_list]
    logger.info(
        "  打分完成：均值%.1f 最高%.1f 最低%.1f  阈值≥%.0f",
        sum(scores) / len(scores), max(scores), min(scores), min_score_threshold,
    )
    # 分数段
    buckets = [(0, 60), (60, 75), (75, 85), (85, 101)]
    labels = ["<60分(二星)", "60-74(三星)", "75-84(四星)", "≥85(五星)"]
    for (lo, hi), lb in zip(buckets, labels):
        cnt = sum(1 for s in scores if lo <= s.total_score < hi) if False else sum(
            1 for s in scored_list if lo <= s.total_score < hi
        )
        logger.info("    %s：%d只", lb, cnt)

    # 阈值过滤
    if min_score_threshold > 0:
        passed = [s for s in scored_list if s.total_score >= min_score_threshold]
        if not passed:
            logger.warning("  无个股达到阈值%.0f，放宽取前%d只", min_score_threshold, top_n)
            passed = scored_list
    else:
        passed = scored_list

    # 排序：总分降序，同分按技术分高者优先
    passed.sort(
        key=lambda s: (round(s.total_score, 2), round(s.tech_score, 2)),
        reverse=True,
    )

    # TOP N + 主板校验（硬约束）
    top_picks: List[ScoredCandidate] = []
    for s in passed:
        code = s.cand.code
        if code.startswith("60") or code.startswith(("000", "001", "002", "003")):
            top_picks.append(s)
        if len(top_picks) >= top_n:
            break

    # 如果主板筛选后不足（极端情况），直接放宽
    if len(top_picks) < top_n:
        top_picks = passed[:top_n]

    logger.info("✅ 最终选出TOP%d：", len(top_picks))
    for i, s in enumerate(top_picks, 1):
        logger.info(
            "  %d. %s %s(%s) 总分%.1f【%s】环境%.0f 技%.0f 讯%.0f 基%.0f 资%.0f｜%s",
            i, s.attention_label, s.cand.name, s.cand.code, s.total_score,
            s.weight_plan,
            s.market_env_score, s.tech_score, s.news_score,
            s.fundamental_score, s.money_score,
            "、".join(s.highlights[:3]),
        )

    return top_picks
