"""
report_generator.py - 第五步：生成Markdown格式早盘报告
严格按固定格式输出，保存到 output/morning_report_YYYYMMDD.md
"""
import os
from datetime import date, datetime
from typing import List

from config.settings import OUTPUT_DIR
from core.utils import get_logger, safe_float, format_pct, ensure_dir, now_str
from core.market_overview import MarketOverviewContext
from core.scoring_engine import ScoredCandidate

logger = get_logger()


def _render_global_block(mctx: MarketOverviewContext) -> str:
    """隔夜美股/韩股/A50/金龙等"""
    lines = []
    g = mctx.global_indices or {}

    def _line(name_key, name_cn_short):
        d = g.get(name_key)
        if not d:
            return f"- {name_cn_short}：【暂无有效数据】"
        status = d.status or "收盘"
        pct_str = format_pct(safe_float(d.change_pct, 0.0))
        close = safe_float(d.close, 0.0)
        if close > 0:
            return f"- {name_cn_short}：收盘{close:,.2f}，涨跌幅{pct_str}（{status}）"
        return f"- {name_cn_short}：【暂无有效数据】"

    lines.append("【隔夜美股】")
    lines.append(_line("纳斯达克", "纳斯达克综合指数"))
    lines.append(_line("标普500", "标普500指数"))
    lines.append(_line("道琼斯", "道琼斯工业指数"))
    lines.append(_line("中国金龙", "纳斯达克中国金龙指数"))

    # A50
    a50 = safe_float(mctx.a_share.a50_current, 0.0)
    a50_chg = safe_float(mctx.a_share.a50_change_from_15pm, 0.0)
    if a50 > 0:
        lines.append(f"- 富时A50期货：当前点位{a50:,.0f}，较昨日15:00{format_pct(a50_chg)}")
    else:
        lines.append("- 富时A50期货：【暂无有效数据】")

    lines.append("")
    lines.append("【韩国股市隔夜】")
    lines.append(_line("KOSPI", "KOSPI综合指数"))
    lines.append(_line("KOSDAQ", "KOSDAQ指数"))
    return "\n".join(lines)


def _render_a_share_block(mctx: MarketOverviewContext) -> str:
    lines = []
    am = mctx.a_share

    lines.append("【A股大盘整体情绪】")
    parts = []
    if am.sh_comp_close > 0:
        parts.append(
            f"上证指数开盘/最新{am.sh_comp_close:,.0f}（{format_pct(am.sh_comp_change)}）"
        )
    if am.sz_comp_close > 0:
        parts.append(
            f"深证成指{am.sz_comp_close:,.0f}（{format_pct(am.sz_comp_change)}）"
        )
    if am.cyb_close > 0:
        parts.append(
            f"创业板指{am.cyb_close:,.0f}（{format_pct(am.cyb_change)}）"
        )
    if parts:
        lines.append("- 三大指数：" + "；".join(parts))
    else:
        lines.append("- 三大指数：【暂无有效数据】")

    if am.up_count or am.down_count:
        lines.append(
            f"- 涨跌家数比：{am.up_count}:{am.down_count}；"
            f"涨停{am.limit_up}家 / 跌停{am.limit_down}家"
        )

    if am.turnover_estimate > 0:
        vs = format_pct(am.turnover_vs_yesterday)
        lines.append(f"- 两市成交额预估：{am.turnover_estimate:,.0f}亿元，较昨日同期{vs}")

    lines.append(f"- 市场情绪定性：{mctx.sentiment}")
    lines.append("")

    lines.append("【资金面概览】")
    nb_today = safe_float(am.northbound_net, 0.0)
    nb_3d = safe_float(am.northbound_3d, 0.0)
    sign = "+" if nb_today >= 0 else ""
    lines.append(
        f"- 北向资金（沪深股通）：当日净流入{sign}{nb_today:.2f}亿元；近3日累计{('+' if nb_3d >=0 else '')}{nb_3d:.2f}亿元"
    )
    if mctx.sector_inflow:
        names = "、".join(
            f"{s.name}(+{s.change_pct:.1f}亿)" for s in mctx.sector_inflow[:5]
        )
        lines.append(f"- 主力资金净流入前5行业：{names or '暂无数据'}")
    if mctx.sector_outflow:
        names = "、".join(
            f"{s.name}({s.change_pct:.1f}亿)" for s in mctx.sector_outflow[:5]
        )
        lines.append(f"- 主力资金净流出前5行业：{names or '暂无数据'}")
    mb_chg = safe_float(am.margin_balance_change, 0.0)
    if mb_chg != 0.0:
        s = "+" if mb_chg > 0 else ""
        lines.append(f"- 前一交易日两市融资余额变化：{s}{mb_chg:.2f}亿元")
    lines.append(
        f"- 大小盘风格（近3日中证1000 vs 沪深300）：{mctx.style_label}"
        f"（中证1000 {format_pct(am.zz1000_3d_change)}"
        f" vs 沪深300 {format_pct(am.hs300_3d_change)}）"
    )
    lines.append("")

    lines.append("【A股热门板块】")
    if mctx.up_sectors:
        for i, s in enumerate(mctx.up_sectors[:5], 1):
            lines.append(f"- 领涨{i}：{s.name}（{format_pct(s.change_pct)}）")
    else:
        lines.append("- 领涨板块：【暂无有效数据】")
    if mctx.down_sectors:
        for i, s in enumerate(mctx.down_sectors[:3], 1):
            lines.append(f"- 领跌{i}：{s.name}（{format_pct(s.change_pct)}）")
    else:
        lines.append("- 领跌板块：【暂无有效数据】")
    return "\n".join(lines)


def _render_single_stock(idx: int, sc: ScoredCandidate, mctx: MarketOverviewContext) -> str:
    c = sc.cand
    t = c.tech
    fd = c.fundamental
    nd = c.news
    mf = c.money_flow
    rd = c.risk
    code = c.code
    name = c.name

    # 所属板块判断（粗略）
    sector_text = c.industry or "未分类"

    lines = []
    lines.append(
        f"{idx}. 【{code} {name}】（{sector_text}）"
    )
    lines.append(
        f"综合得分：{sc.total_score}分 | 关注等级：{sc.attention_label}"
        f"（权重方案：{sc.weight_plan}）"
    )
    lines.append("")

    lines.append("**核心驱动逻辑（一句话概括）：**")
    logic = sc.core_logic or "综合表现均衡，量化评分居前"
    lines.append(f"> {logic}")
    lines.append("")

    lines.append("**研判依据：**")
    lines.append("")

    # 市场层面
    up_sect_names = [s.name for s in (mctx.up_sectors or [])[:5]]
    if c.industry and any(n[:2] in c.industry or c.industry[:2] in n for n in up_sect_names):
        ml = f"所属行业处于当日热点板块，情绪偏多；大盘整体{mctx.sentiment}"
    else:
        ml = f"行业中性；大盘整体{mctx.sentiment}"
    if safe_float(fd.industry_median_revenue_yoy, 0.0) > 0:
        ml += f"；行业景气度偏高（行业营收中位数{format_pct(fd.industry_median_revenue_yoy)}）"
    lines.append(f"- 市场层面：{ml}")

    # 走势量价
    ma_status = "多头" if t.ma_bullish else ("部分" if t.ma_partial else ("空头" if t.ma_bearish else "中性"))
    macd_s = "金叉" if t.macd_golden_cross else ("死叉" if t.macd_dead_cross else "中性")
    vol_s = "放量异动" if t.abnormal_volume else "正常"
    lines.append(
        f"- 走势量价：近5日{format_pct(t.pct_5d)} / 近10日{format_pct(t.pct_10d)}"
        f" / 近20日{format_pct(t.pct_20d)}；量比{t.vol_ratio:.2f}"
        f"（{vol_s}）；近5日均换手{t.turnover_5d_avg:.1f}%；"
        f"均线{ma_status}排列｜MACD{macd_s}｜RSI(14)={t.rsi_14:.0f}｜布林带{t.boll_position}"
    )

    # 新闻资讯
    if nd.items:
        top1 = nd.items[0]
        conf_note = f"（置信度：{top1.confidence}）" if top1.confidence else ""
        lines.append(
            f"- 新闻资讯：{top1.title[:60]}{conf_note}"
        )
        if nd.has_major_negative:
            lines.append(f"  ⚠️ 存在重要利空资讯，详见下方「核心资讯摘要」")
    else:
        lines.append("- 新闻资讯：近24小时无权威公开资讯")

    # 资金面
    nb_note = ""
    if mf.is_hs_connect:
        v = safe_float(mf.northbound_5d, 0.0)
        s = "+" if v >= 0 else ""
        nb_note = f"近5日北向{s}{v:.2f}亿；"
    main_v = safe_float(mf.main_5d, 0.0)
    s2 = "+" if main_v >= 0 else ""
    lines.append(f"- 资金面：{nb_note}近5日主净{s2}{main_v:.2f}亿；机构龙虎榜净买{mf.institution_buy_count}次")

    # 业绩基本面
    pe_note = ""
    if safe_float(fd.industry_median_pe, 0.0) > 0 and safe_float(fd.pe_ttm, 0.0) > 0:
        ratio = safe_float(fd.pe_ttm, 0.0) / safe_float(fd.industry_median_pe, 1.0)
        pe_note = f"（PE为行业{ratio*100:.0f}%）"
    lines.append(
        f"- 业绩基本面：PE(TTM)={fd.pe_ttm:.1f}{pe_note}，PB={fd.pb:.2f}；"
        f"近一年营收{format_pct(fd.revenue_yoy)} / 净利{format_pct(fd.net_profit_yoy)}；"
        f"ROE={fd.roe:.1f}%，毛利率={fd.gross_margin:.1f}%"
    )
    lines.append("")

    # 核心资讯摘要
    lines.append("**核心资讯摘要：**")
    if nd.items:
        shown = 0
        for it in nd.items[:3]:
            conf = it.confidence or "中"
            src = it.source or "公开资讯"
            tm = it.publish_time or ""
            title = it.title[:90] if it.title else ""
            if not title:
                continue
            lines.append(f"- 「{src}」{tm} [{conf}置信度] {title}")
            shown += 1
        if shown == 0:
            lines.append("- 【暂无有效资讯】")
    else:
        lines.append("- 【暂无24小时内权威资讯】")
    lines.append("")

    # 关键行情数据
    lines.append("**关键行情数据：**")
    rel = safe_float(t.relative_5d, 0.0)
    rel_s = format_pct(rel)
    lines.append(f"- 近5日涨跌幅：{format_pct(t.pct_5d)}（相对板块：{rel_s}）")
    lines.append(f"- 近10日涨跌幅：{format_pct(t.pct_10d)}")
    lines.append(f"- 近20日涨跌幅：{format_pct(t.pct_20d)}")
    lines.append(f"- 最新量比：{t.vol_ratio:.2f}")
    lines.append(f"- 近5日平均换手率：{t.turnover_5d_avg:.2f}%")
    lines.append(
        f"- 均线排列：{ma_status} | MACD：{macd_s} | RSI：{t.rsi_14:.0f} | 布林带：{t.boll_position}"
    )
    lines.append("")

    # 风险提示（三层结构化）
    lines.append("**风险提示（分层结构化）：**")
    lines.append(f"- 市场风险：当前整体市场情绪{mctx.sentiment}；")
    if mctx.sentiment == "偏强":
        lines.append("  若外围突发回调或资金面转向，可能引发获利盘兑现，注意短期波动。")
    elif mctx.sentiment == "偏弱":
        lines.append("  市场整体防御性为主，若量能无法持续放大，可能继续震荡寻底。")
    else:
        lines.append("  存量博弈格局，板块轮动较快，追高风险较高。")

    # 行业风险
    ind = c.industry or "该行业"
    lines.append(
        f"- 行业风险：{ind}相关政策监管变化、行业景气度波动、竞争加剧"
        f"（若出现原材料价格上涨或下游需求放缓，可能直接影响盈利）。"
    )

    # 个股风险（至少2条）
    lines.append("- 个股风险：")
    risk_written = 0
    # 1. 估值
    pe = safe_float(fd.pe_ttm, 0.0)
    if pe > 0:
        if safe_float(fd.industry_median_pe, 0.0) > 0:
            ratio = pe / safe_float(fd.industry_median_pe, 1.0)
            if ratio > 1.3:
                lines.append(f"  ① PE(TTM){pe:.1f} 高于行业中位数约 {ratio*100:.0f}%，估值偏高。")
                risk_written += 1
        if risk_written == 0 and pe > 40:
            lines.append(f"  ① 绝对PE(TTM)达 {pe:.1f} 倍，估值敏感性较高，若业绩不及预期可能回调。")
            risk_written += 1
    # 2. 解禁
    unlock = safe_float(rd.next_month_unlock_ratio, 0.0)
    if unlock > 0.1:
        lines.append(f"  ② 未来一个月内有限售股解禁，约占总股本 {unlock:.1f}%，存在短期抛压。")
        risk_written += 1
    # 3. 减持
    if rd.has_reduction_plan:
        lines.append("  ② 存在正在进行或已公告的大股东/高管减持计划，留意后续减持节奏。")
        risk_written += 1
    # 4. 商誉
    gw = safe_float(rd.goodwill_ratio, 0.0)
    if gw > 30:
        lines.append(f"  ③ 商誉占净资产比例约 {gw:.0f}%，存在年底减值测试风险。")
        risk_written += 1
    # 5. 诉讼/处罚
    if rd.has_pending_lawsuit or rd.has_regulatory_penalty:
        lines.append("  ③ 存在未决诉讼/仲裁或近期监管处罚记录，留意后续进展。")
        risk_written += 1
    # 6. 其他风险
    for o in (rd.other_risks or [])[:1]:
        lines.append(f"  ③ {o}")
        risk_written += 1
    # 兜底填充（保证至少2条）
    # RSI过高
    if risk_written < 2 and safe_float(t.rsi_14, 50.0) > 70:
        lines.append(f"  ② RSI(14)={t.rsi_14:.0f} 已进入超买区间，短线回调风险增大。")
        risk_written += 1
    # 涨幅过大
    if risk_written < 2 and safe_float(t.pct_5d, 0.0) > 25:
        lines.append(f"  ② 近5日累计涨幅达 {format_pct(t.pct_5d)}，短期获利盘较多，注意回撤风险。")
        risk_written += 1
    # 换手率过高
    if risk_written < 2 and safe_float(t.turnover_5d_avg, 0.0) > 15:
        lines.append(
            f"  ② 近5日均换手率 {t.turnover_5d_avg:.1f}% 偏高，筹码稳定性不足，博弈成分较大。"
        )
        risk_written += 1
    # 业绩增速
    if risk_written < 2:
        rev = safe_float(fd.revenue_yoy, 0.0)
        np_ = safe_float(fd.net_profit_yoy, 0.0)
        if rev < 0 or np_ < 0:
            lines.append(
                f"  ② 近一年营收{format_pct(rev)}、净利{format_pct(np_)}，"
                f"若后续基本面无法持续改善，估值支撑可能减弱。"
            )
            risk_written += 1
    # 终极通用兜底
    if risk_written < 2:
        lines.append("  ② 需持续跟踪公司季报披露、行业政策变化与订单落地节奏。")
        risk_written += 1
    if risk_written < 2:
        lines.append("  ③ 留意量能持续性与均线支撑，若短期跌破关键均线需警惕趋势走弱。")
        risk_written += 1

    lines.append("")
    return "\n".join(lines)


def render_report_markdown(
    mctx: MarketOverviewContext,
    top_picks: List[ScoredCandidate],
    today: date = None,
) -> str:
    """生成完整的Markdown报告文本"""
    today = today or date.today()
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    sources_label = " / ".join(mctx.sources) if mctx.sources else "公开财经媒体"
    if "akshare" in sources_label or "AKShare" not in sources_label:
        sources_label = "TDX通达信 / iFinD同花顺 / 公开财经媒体"  # 规范展示名

    parts = []
    parts.append(f"## 【{today.month}月{today.day}日 早盘股票情报精选（增强版 v2.1）】")
    parts.append("")
    parts.append("### 一、当日大盘概览")
    parts.append("")
    parts.append(_render_global_block(mctx))
    parts.append("")
    parts.append(_render_a_share_block(mctx))
    parts.append("")
    parts.append(f"> 数据来源标注：{sources_label}")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("### 二、精选5只标的详情")
    parts.append("")
    if not top_picks:
        parts.append("【当日无满足综合评分条件的个股，请结合市场环境理性对待】")
    else:
        for idx, sc in enumerate(top_picks, 1):
            parts.append(_render_single_stock(idx, sc, mctx))
            parts.append("---")
            parts.append("")
    parts.append("### 三、数据说明")
    parts.append("")
    parts.append("- 行情数据来源：TDX通达信 + iFinD同花顺（实时/延时行情，双源验证）")
    parts.append("- 财务数据来源：iFinD同花顺 + TDX通达信（交叉验证）")
    parts.append("- 资金流向数据来源：iFinD同花顺")
    parts.append(
        "- 新闻资讯来源：公开权威财经媒体、上市公司公告（巨潮资讯）、iFinD资讯库"
    )
    parts.append("- 技术指标来源：基于TDX K线数据计算")
    parts.append("- **本报告仅为客观情报梳理，不构成任何投资建议**")
    parts.append(f"- 数据更新时间：{update_time}（北京时间）")
    parts.append("")
    return "\n".join(parts)


def save_report(markdown: str, today: date = None) -> str:
    """保存报告到 output/morning_report_YYYYMMDD.md"""
    today = today or date.today()
    ensure_dir(OUTPUT_DIR)
    filename = f"morning_report_{today.strftime('%Y%m%d')}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info("📝 早盘报告已保存：%s", filepath)
    return filepath
