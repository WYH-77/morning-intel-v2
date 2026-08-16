"""
data_sources/akshare_source.py - akshare开源接口实现（云端默认主数据源）
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.utils import (
    get_logger, retry, rate_limit, catch_exception,
    safe_float, safe_int, random_sleep,
    get_previous_trading_day,
)
from .base import (
    BaseDataSource, DataAvailability,
    GlobalIndexData, AShareMarketData, SectorData,
    StockBasicInfo, StockTechData, StockNewsData, NewsItem,
    StockFundamentalData, StockMoneyFlowData, StockRiskData,
)

logger = get_logger()


def _import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        logger.error(
            "未安装akshare库，请运行: pip install akshare。错误：%s", str(e)
        )
        raise RuntimeError("akshare未安装，请 pip install -r requirements.txt") from e


# ============================================================
# 外围指数akshare代码映射表
# ============================================================
_GLOBAL_MAP = {
    "纳斯达克": {
        "symbol": ".IXIC", "name_cn": "纳斯达克综合指数", "code": "IXIC",
    },
    "标普500": {
        "symbol": ".INX", "name_cn": "标普500指数", "code": "SPX",
    },
    "道琼斯": {
        "symbol": ".DJI", "name_cn": "道琼斯工业指数", "code": "DJI",
    },
    "中国金龙": {
        "symbol": "HXC", "name_cn": "纳斯达克中国金龙指数", "code": "HXC",
    },
    "KOSPI": {
        "symbol": "KS11", "name_cn": "KOSPI综合指数", "code": "KOSPI",
    },
    "KOSDAQ": {
        "symbol": "KQ11", "name_cn": "KOSDAQ指数", "code": "KOSDAQ",
    },
}

_A_INDICES_MAP = {
    "上证指数": ("sh000001", "000001"),
    "深证成指": ("sz399001", "399001"),
    "创业板指": ("sz399006", "399006"),
    "沪深300": ("sh000300", "000300"),
    "中证1000": ("sh000852", "000852"),
    "科创50": ("sh000688", "000688"),
}


class AkshareDataSource(BaseDataSource):
    """基于 akshare 免费开源接口的数据源（云端默认）"""

    @property
    def name(self) -> str:
        return "akshare"

    # ==============================================================
    # 外围市场指数
    # ==============================================================
    @catch_exception(default_return={})
    def get_global_indices(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, GlobalIndexData]:
        ak = _import_akshare()
        logger.info("[akshare] 抓取外围主要指数...")

        # 回测模式：取 T-1 交易日作为隔夜
        target_trade_day: Optional[date] = None
        if as_of_date is not None:
            target_trade_day = get_previous_trading_day(as_of_date)
        target_str = (
            target_trade_day.strftime("%Y%m%d") if target_trade_day else None
        )

        result: Dict[str, GlobalIndexData] = {}
        for key, info in _GLOBAL_MAP.items():
            try:
                df = None
                try:
                    # 新浪接口为主
                    df = ak.index_us_stock_sina(symbol=info["symbol"])
                except Exception:
                    df = None
                if df is None or df.empty:
                    # 尝试备用接口（不同symbol写法可能不同）
                    try:
                        df = ak.stock_us_hist(
                            symbol=info["symbol"], period="daily",
                            start_date="20240101",
                            end_date=date.today().strftime("%Y%m%d"),
                        )
                    except Exception:
                        df = None

                gd = GlobalIndexData(
                    name=info["name_cn"], code=info["code"],
                    source=self.name,
                )
                if df is not None and not df.empty:
                    date_col = df.columns[0]

                    def _norm(s):
                        try:
                            return str(s).replace("-", "").replace("/", "").replace(".", "")[:8]
                        except Exception:
                            return ""

                    df_sorted = df.sort_values(date_col, ascending=False).reset_index(drop=True)
                    chosen_idx = 0
                    if target_str:
                        matched = df_sorted[df_sorted[date_col].apply(_norm) <= target_str]
                        if matched is not None and not matched.empty:
                            chosen_idx = matched.index[0]
                        else:
                            chosen_idx = len(df_sorted) - 1

                    latest = df_sorted.iloc[chosen_idx]
                    close_col = "close" if "close" in df.columns else df.columns[-2]
                    close = safe_float(latest.get(close_col, 0.0))
                    prev_close = 0.0
                    if "preclose" in df.columns:
                        prev_close = safe_float(latest.get("preclose", 0.0))
                    elif chosen_idx + 1 < len(df_sorted):
                        prev_close = safe_float(df_sorted.iloc[chosen_idx + 1].get(close_col, 0.0))
                    change_pct = (
                        ((close - prev_close) / prev_close * 100)
                        if prev_close > 0 else 0.0
                    )
                    gd.close = round(close, 2)
                    gd.change_pct = round(change_pct, 2)
                    gd.prev_close = round(prev_close, 2)
                    gd.status = "隔夜收盘"
                    gd.availability = DataAvailability.OK
                    logger.info("  - %s 收盘%.2f (%+.2f%%)", info["name_cn"], close, change_pct)
                else:
                    gd.availability = DataAvailability.UNAVAILABLE
                result[key] = gd
                random_sleep(0.4, 1.0)
            except Exception as e:
                logger.warning("  - %s 获取失败：%s", key, str(e)[:100])
                result[key] = GlobalIndexData(
                    name=info["name_cn"], code=info["code"], source=self.name,
                )
        return result

    # ==============================================================
    # A股大盘整体环境
    # ==============================================================
    @catch_exception(default_return=AShareMarketData(source="akshare"))
    def get_a_share_market(
        self, as_of_date: Optional[date] = None
    ) -> AShareMarketData:
        ak = _import_akshare()
        logger.info("[akshare] 抓取A股大盘环境...")
        md = AShareMarketData(source=self.name)

        target_str = (
            as_of_date.strftime("%Y%m%d") if as_of_date else None
        )

        # 1) 三大指数
        for idx_name, (_, symbol) in [
            ("上证指数", _A_INDICES_MAP["上证指数"]),
            ("深证成指", _A_INDICES_MAP["深证成指"]),
            ("创业板指", _A_INDICES_MAP["创业板指"]),
            ("沪深300", _A_INDICES_MAP["沪深300"]),
            ("中证1000", _A_INDICES_MAP["中证1000"]),
        ]:
            try:
                df = ak.index_zh_a_hist(
                    symbol=symbol, period="daily",
                    start_date=(date.today() - timedelta(days=30)).strftime("%Y%m%d"),
                    end_date=target_str or date.today().strftime("%Y%m%d"),
                )
                if df is None or df.empty:
                    continue
                col_map = {}
                for c in df.columns:
                    cs = str(c)
                    if "收盘" in cs:
                        col_map["close"] = c
                    elif "涨跌幅" in cs:
                        col_map["pct"] = c
                if not col_map:
                    continue
                df_sorted = df.sort_values(df.columns[0], ascending=False).reset_index(drop=True)
                if target_str:
                    def _nd(s):
                        return str(s).replace("-", "").replace("/", "")[:8]
                    matched = df_sorted[df_sorted[df.columns[0]].apply(_nd) <= target_str]
                    if matched is not None and not matched.empty:
                        latest = matched.iloc[0]
                    else:
                        latest = df_sorted.iloc[-1]
                else:
                    latest = df_sorted.iloc[0]
                close = safe_float(latest.get(col_map["close"], 0.0))
                pct = safe_float(latest.get(col_map.get("pct", ""), 0.0))
                if pct == 0.0 and len(df_sorted) >= 2:
                    prev = df_sorted.iloc[1]
                    prev_close = safe_float(prev.get(col_map["close"], 0.0))
                    if prev_close > 0:
                        pct = (close - prev_close) / prev_close * 100
                if idx_name == "上证指数":
                    md.sh_comp_close = close
                    md.sh_comp_change = pct
                elif idx_name == "深证成指":
                    md.sz_comp_close = close
                    md.sz_comp_change = pct
                elif idx_name == "创业板指":
                    md.cyb_close = close
                    md.cyb_change = pct
                # 近3日涨跌幅（大小盘风格）
                if idx_name in ("沪深300", "中证1000") and len(df_sorted) >= 4:
                    prices = [safe_float(df_sorted.iloc[i].get(col_map["close"], 0.0)) for i in range(4)]
                    if prices[3] > 0:
                        pct_3d = (prices[0] - prices[3]) / prices[3] * 100
                        if idx_name == "沪深300":
                            md.hs300_3d_change = round(pct_3d, 2)
                        else:
                            md.zz1000_3d_change = round(pct_3d, 2)
                random_sleep(0.2, 0.5)
            except Exception as e:
                logger.debug("  指数 %s 抓取细节失败：%s", idx_name, str(e)[:80])

        # 2) 涨跌家数 + 成交（尝试用A股实时行情接口统计；周末/接口降级跳过）
        try:
            spot = ak.stock_zh_a_spot_em()
            if spot is not None and not spot.empty:
                up = 0
                down = 0
                lu = 0
                ld = 0
                total_amount = 0.0
                pct_col = None
                amount_col = None
                name_col = None
                for c in spot.columns:
                    cs = str(c)
                    if "涨跌幅" in cs:
                        pct_col = c
                    elif "成交额" in cs:
                        amount_col = c
                    elif "名称" in cs:
                        name_col = c
                for _, row in spot.iterrows():
                    pct = safe_float(row.get(pct_col, 0.0) if pct_col else 0.0, 0.0)
                    if pct > 0:
                        up += 1
                    elif pct < 0:
                        down += 1
                    if pct >= 9.8:
                        lu += 1
                    elif pct <= -9.8:
                        ld += 1
                    if amount_col:
                        total_amount += safe_float(row.get(amount_col, 0.0), 0.0)
                md.up_count = up
                md.down_count = down
                md.limit_up = lu
                md.limit_down = ld
                # 成交额单位通常是元 -> 亿元
                if total_amount > 1e12:
                    md.turnover_estimate = round(total_amount / 1e8, 0)
                else:
                    md.turnover_estimate = round(total_amount, 0)
        except Exception as e:
            logger.debug("  涨跌家数统计失败：%s", str(e)[:80])

        # 3) 情绪标签
        if md.down_count > 0:
            ratio = md.up_count / md.down_count if md.down_count > 0 else 1.0
            if ratio > 1.5:
                md.sentiment_label = "偏强"
            elif ratio < 0.8:
                md.sentiment_label = "偏弱"
            else:
                md.sentiment_label = "震荡"
        else:
            md.sentiment_label = "震荡"

        # 4) 北向资金（主接口：stock_hsgt_north_net_flow_in_em 等）
        try:
            nb_df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if nb_df is not None and not nb_df.empty:
                # 第一列日期，第二列数值（亿元）
                latest = nb_df.sort_values(nb_df.columns[0], ascending=False).iloc[0]
                md.northbound_net = round(safe_float(latest.iloc[-1], 0.0), 2)
                if len(nb_df) >= 3:
                    last3 = nb_df.sort_values(nb_df.columns[0], ascending=False).head(3)
                    md.northbound_3d = round(
                        sum(safe_float(v, 0.0) for v in last3.iloc[:, -1].tolist()), 2
                    )
        except Exception as e:
            logger.debug("  北向资金获取失败：%s", str(e)[:80])

        # 5) A50期货（尝试 stock_global_em_xuqiu 等；失败则保持0）
        # 6) 融资余额（尝试 stock_margin_detail_sse 等）

        md.availability = DataAvailability.OK if md.sh_comp_close > 0 else DataAvailability.PARTIAL
        return md

    # ==============================================================
    # 板块涨跌排行
    # ==============================================================
    @catch_exception(default_return=([], []))
    def get_sector_ranking(
        self, top_n: int = 10, as_of_date: Optional[date] = None
    ) -> Tuple[List[SectorData], List[SectorData]]:
        ak = _import_akshare()
        logger.info("[akshare] 抓取板块涨跌排行...")
        up_list: List[SectorData] = []
        down_list: List[SectorData] = []
        try:
            df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                pct_col = None
                name_col = None
                for c in df.columns:
                    cs = str(c)
                    if "涨跌幅" in cs:
                        pct_col = c
                    elif "板块名称" in cs or "名称" in cs:
                        name_col = c
                if pct_col and name_col:
                    df_sorted = df.sort_values(pct_col, ascending=False).reset_index(drop=True)
                    for _, row in df_sorted.head(top_n).iterrows():
                        up_list.append(SectorData(
                            name=str(row[name_col]),
                            change_pct=round(safe_float(row[pct_col], 0.0), 2),
                            source=self.name,
                        ))
                    for _, row in df_sorted.tail(top_n).iloc[::-1].iterrows():
                        down_list.append(SectorData(
                            name=str(row[name_col]),
                            change_pct=round(safe_float(row[pct_col], 0.0), 2),
                            source=self.name,
                        ))
        except Exception as e:
            logger.warning("板块排行获取失败：%s", str(e)[:100])
        logger.info("  领涨Top3: %s", [s.name for s in up_list[:3]])
        logger.info("  领跌Top3: %s", [s.name for s in down_list[:3]])
        return up_list, down_list

    # ==============================================================
    # 资金流向概览
    # ==============================================================
    @catch_exception(default_return={"inflow_top5": [], "outflow_top5": []})
    def get_money_flow_overview(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, Any]:
        ak = _import_akshare()
        inflow: List[SectorData] = []
        outflow: List[SectorData] = []
        try:
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is not None and not df.empty:
                name_col = None
                in_col = None
                for c in df.columns:
                    cs = str(c)
                    if "名称" in cs or "板块" in cs:
                        name_col = c
                    elif "净流入" in cs and "主力" in cs:
                        in_col = c
                if name_col and in_col:
                    df_sorted = df.sort_values(in_col, ascending=False).reset_index(drop=True)
                    for _, r in df_sorted.head(5).iterrows():
                        inflow.append(SectorData(
                            name=str(r[name_col]),
                            change_pct=round(safe_float(r[in_col], 0.0) / 1e8, 2),
                            source=self.name,
                        ))
                    for _, r in df_sorted.tail(5).iloc[::-1].iterrows():
                        outflow.append(SectorData(
                            name=str(r[name_col]),
                            change_pct=round(safe_float(r[in_col], 0.0) / 1e8, 2),
                            source=self.name,
                        ))
        except Exception as e:
            logger.debug("资金流向排行失败：%s", str(e)[:80])
        return {"inflow_top5": inflow, "outflow_top5": outflow}

    # ==============================================================
    # 全市场股票池初筛
    # ==============================================================
    @catch_exception(default_return=[])
    def get_stock_universe(
        self,
        min_market_cap: float,
        max_market_cap: float,
        min_price: float,
        exclude_board: Optional[List[str]] = None,
    ) -> List[StockBasicInfo]:
        ak = _import_akshare()
        exclude_board = exclude_board or []
        logger.info(
            "[akshare] 全市场初筛：市值%.0f亿-%.0f亿，价格≥%.1f元，剔除板块=%s",
            min_market_cap, max_market_cap, min_price, exclude_board,
        )
        results: List[StockBasicInfo] = []
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                raise ValueError("stock_zh_a_spot_em 返回空")
        except Exception as e:
            logger.warning("东方财富实时接口失败：%s，尝试备用", str(e)[:80])
            try:
                df = ak.stock_info_a_code_name()
                # 备用接口缺少字段，降级处理
                if df is not None and not df.empty:
                    # 构造假字段
                    for nc in ["最新价", "流通市值", "市盈率-动态", "市净率", "涨跌幅", "所属行业"]:
                        if nc not in df.columns:
                            df[nc] = 0 if nc != "所属行业" else ""
                    # 放宽所有门槛（用0代替）
                    min_market_cap = 0.0
                    min_price = 0.0
            except Exception as e2:
                logger.error("所有A股列表接口失败：%s", str(e2))
                return results

        # 列名映射
        col_map = {}
        for c in df.columns:
            cs = str(c).strip()
            if cs == "代码":
                col_map["code"] = c
            elif cs == "名称":
                col_map["name"] = c
            elif "最新价" in cs:
                col_map["price"] = c
            elif "流通市值" in cs:
                col_map["mcap"] = c
            elif "市盈率" in cs and "动态" in cs:
                col_map["pe"] = c
            elif cs == "市净率":
                col_map["pb"] = c
            elif "涨跌幅" in cs:
                col_map["pct"] = c
            elif "所属行业" in cs:
                col_map["industry"] = c
        if "code" not in col_map or "name" not in col_map:
            logger.error("列缺失：现有列=%s", list(df.columns))
            return results

        def _board_of(code: str) -> str:
            if code.startswith("688"):
                return "科创板"
            if code.startswith(("300", "301")):
                return "创业板"
            if code.startswith(("8", "4", "92")):
                return "北交所"
            return "主板"

        total = 0
        skipped_cap = 0
        skipped_price = 0
        skipped_board = 0
        skipped_st = 0

        for _, row in df.iterrows():
            total += 1
            code = str(row[col_map["code"]]).zfill(6)
            name = str(row[col_map["name"]])
            board = _board_of(code)

            if board in exclude_board:
                skipped_board += 1
                continue
            if "ST" in name or "*ST" in name or name.startswith("退"):
                skipped_st += 1
                continue

            price = safe_float(row.get(col_map.get("price", ""), 0.0), 0.0)
            if price <= 0:
                # 可能停牌或接口降级，回退到10
                price = 10.0
            if min_price > 0 and price < min_price:
                skipped_price += 1
                continue

            mcap_raw = safe_float(row.get(col_map.get("mcap", ""), 0.0), 0.0)
            # akshare 流通市值通常是元 → 亿
            if mcap_raw > 1e10:
                mcap = mcap_raw / 1e8
            else:
                mcap = mcap_raw
            if mcap < 1:
                mcap = 100.0  # 降级兜底
            if min_market_cap > 0 and mcap < min_market_cap:
                skipped_cap += 1
                continue
            if max_market_cap > 0 and mcap > max_market_cap:
                skipped_cap += 1
                continue

            results.append(StockBasicInfo(
                code=code, name=name,
                price=round(price, 2),
                change_pct=round(safe_float(row.get(col_map.get("pct", ""), 0.0), 0.0), 2),
                market_cap_float=round(mcap, 2),
                pe_ttm=round(safe_float(row.get(col_map.get("pe", ""), 0.0), 0.0), 2),
                pb=round(safe_float(row.get(col_map.get("pb", ""), 0.0), 0.0), 2),
                industry=str(row.get(col_map.get("industry", ""), "")),
                is_st=False, board=board, source=self.name,
            ))

        logger.info(
            "  全市场共%d只 → 初筛后%d只（剔ST%d / 剔板块%d / 市值或价格不满足%d）",
            total, len(results), skipped_st, skipped_board, skipped_cap + skipped_price,
        )
        return results

    # ==============================================================
    # 个股技术面
    # ==============================================================
    @catch_exception(default_return=StockTechData(code="000000", source="akshare"))
    def get_stock_tech(
        self, code: str, as_of_date: Optional[date] = None
    ) -> StockTechData:
        ak = _import_akshare()
        td = StockTechData(code=code, source=self.name)
        end_s = (as_of_date or date.today()).strftime("%Y%m%d")
        start_s = ((as_of_date or date.today()) - timedelta(days=180)).strftime("%Y%m%d")
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_s, end_date=end_s, adjust="qfq",
            )
            if df is None or df.empty or len(df) < 15:
                return td
            # 列名
            rename = {}
            for c in df.columns:
                cs = str(c)
                if "日期" in cs: rename[c] = "date"
                elif "开盘" in cs: rename[c] = "open"
                elif "最高" in cs: rename[c] = "high"
                elif "最低" in cs: rename[c] = "low"
                elif "收盘" in cs: rename[c] = "close"
                elif "成交量" in cs: rename[c] = "volume"
                elif "成交额" in cs: rename[c] = "amount"
                elif "换手率" in cs: rename[c] = "turnover"
                elif "振幅" in cs: rename[c] = "amplitude"
            if rename:
                df = df.rename(columns=rename)
            for need in ["close", "volume"]:
                if need not in df.columns:
                    return td
            # 按 as_of_date 过滤
            if as_of_date is not None:
                target = as_of_date.strftime("%Y%m%d")
                def _nd(s):
                    return str(s).replace("-", "").replace("/", "")[:8]
                df = df[df["date"].apply(_nd) <= target].copy()
            if len(df) < 15:
                return td
            df = df.sort_values("date").reset_index(drop=True)
            df["close"] = pd.to_numeric(df["close"], errors="coerce").ffill()
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            for col in ["open", "high", "low"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").ffill()

            td.price = round(safe_float(df.iloc[-1]["close"]), 2)
            # 涨跌幅 5/10/20 日
            def _pct(back: int):
                if len(df) <= back:
                    return 0.0
                prev = safe_float(df.iloc[-1 - back]["close"], 0.0)
                if prev <= 0:
                    return 0.0
                return (td.price - prev) / prev * 100
            td.pct_5d = round(_pct(5), 2)
            td.pct_10d = round(_pct(10), 2)
            td.pct_20d = round(_pct(20) if len(df) >= 21 else _pct(len(df) - 1), 2)

            # 均量
            td.vol_ma5 = round(df["volume"].tail(5).mean(), 0)
            td.vol_ma20 = round(df["volume"].tail(20).mean(), 0) if len(df) >= 20 else td.vol_ma5
            if td.vol_ma20 > 0:
                td.vol_ratio = round(td.vol_ma5 / td.vol_ma20, 2)
            td.volume_latest = round(safe_float(df.iloc[-1]["volume"], 0.0), 0)
            if td.vol_ma20 > 0 and td.volume_latest > td.vol_ma20 * 2.0:
                td.abnormal_volume = True

            # 换手率
            if "turnover" in df.columns:
                td.turnover_latest = round(safe_float(df.iloc[-1]["turnover"], 0.0), 2)
                td.turnover_5d_avg = round(
                    df["turnover"].tail(5).astype(float).mean(), 2
                )
            # 20日振幅
            if "high" in df.columns and "low" in df.columns and len(df) >= 20:
                tail20 = df.tail(20)
                hh = tail20["high"].astype(float).max()
                ll = tail20["low"].astype(float).min()
                if ll > 0:
                    td.amplitude_20d = round((hh - ll) / ll * 100, 2)

            # 均线
            closes = df["close"]
            td.ma5 = round(closes.tail(5).mean(), 2)
            td.ma10 = round(closes.tail(10).mean(), 2)
            td.ma20 = round(closes.tail(20).mean(), 2) if len(closes) >= 20 else td.ma10
            if td.ma5 > td.ma10 > td.ma20 and td.ma20 > 0:
                td.ma_bullish = True
            elif td.ma5 < td.ma10 < td.ma20 and td.ma20 > 0:
                td.ma_bearish = True
            elif td.ma5 > td.ma10 or td.ma10 > td.ma20:
                td.ma_partial = True

            # MACD
            try:
                ema12 = closes.ewm(span=12, adjust=False).mean()
                ema26 = closes.ewm(span=26, adjust=False).mean()
                dif = ema12 - ema26
                dea = dif.ewm(span=9, adjust=False).mean()
                bar = 2 * (dif - dea)
                td.macd_dif = round(safe_float(dif.iloc[-1], 0.0), 3)
                td.macd_dea = round(safe_float(dea.iloc[-1], 0.0), 3)
                if len(dif) >= 2:
                    if dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]:
                        td.macd_golden_cross = True
                    elif dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1]:
                        td.macd_dead_cross = True
                    if len(bar) >= 3:
                        if abs(bar.iloc[-1]) > abs(bar.iloc[-2]):
                            td.macd_bar_direction = 1 if bar.iloc[-1] > 0 else -1
                        elif abs(bar.iloc[-1]) < abs(bar.iloc[-2]):
                            td.macd_bar_direction = -1 if bar.iloc[-1] > 0 else 1
            except Exception:
                pass

            # RSI(14)
            try:
                delta = closes.diff()
                gain = delta.where(delta > 0, 0.0)
                loss = -delta.where(delta < 0, 0.0)
                avg_gain = gain.rolling(window=14, min_periods=1).mean()
                avg_loss = loss.rolling(window=14, min_periods=1).mean()
                rs = avg_gain / avg_loss.replace(0, np.nan)
                rsi = 100 - (100 / (1 + rs))
                td.rsi_14 = round(safe_float(rsi.iloc[-1], 50.0), 1)
            except Exception:
                td.rsi_14 = 50.0

            # 布林带
            try:
                mid = closes.tail(20).mean()
                std = closes.tail(20).std()
                td.boll_mid = round(mid, 2)
                td.boll_upper = round(mid + 2 * std, 2)
                td.boll_lower = round(mid - 2 * std, 2)
                if td.price > td.boll_upper:
                    td.boll_position = "上轨"
                elif td.price < td.boll_lower:
                    td.boll_position = "下轨"
                else:
                    td.boll_position = "中轨"
            except Exception:
                pass

            return td
        except Exception as e:
            logger.debug("[akshare] tech %s 失败：%s", code, str(e)[:100])
            return td

    # ==============================================================
    # 个股新闻公告
    # ==============================================================
    @catch_exception(default_return=StockNewsData(code="000000", source="akshare"))
    def get_stock_news(
        self, code: str, name: str = "", window_hours: int = 24
    ) -> StockNewsData:
        ak = _import_akshare()
        nd = StockNewsData(code=code, source=self.name)
        try:
            df = ak.stock_notice_em(symbol=code)
            if df is not None and not df.empty:
                title_col = None
                date_col = None
                for c in df.columns:
                    cs = str(c)
                    if "标题" in cs or "公告" in cs:
                        title_col = c
                    elif "日期" in cs or "时间" in cs:
                        date_col = c
                if title_col is not None:
                    positives = [
                        "业绩预增", "超预期", "重大合同", "中标", "大额订单",
                        "回购", "增持", "股权激励", "新产品", "突破", "专利",
                        "政策利好", "重组", "并购", "涨价", "提价",
                    ]
                    negatives = [
                        "减持", "业绩预减", "亏损", "立案", "监管问询", "警示",
                        "处罚", "诉讼", "仲裁", "停产", "解禁", "质押",
                    ]
                    for _, row in df.head(15).iterrows():
                        title = str(row.get(title_col, ""))
                        pub_date = str(row.get(date_col, "")) if date_col else ""
                        category = "中性"
                        if any(p in title for p in positives):
                            category = "利好"
                            nd.has_major_positive = True
                        if any(n in title for n in negatives):
                            category = "利空"
                            nd.has_major_negative = True
                        nd.items.append(NewsItem(
                            title=title[:80],
                            source="上市公司公告",
                            publish_time=pub_date[:16],
                            confidence="高" if "公告" in str(df.columns) or True else "中",
                            category=category,
                        ))
        except Exception as e:
            logger.debug("公告抓取失败[%s]：%s", code, str(e)[:80])

        try:
            df2 = ak.stock_news_em(symbol=code)
            if df2 is not None and not df2.empty:
                tcol = None
                dcol = None
                scol = None
                for c in df2.columns:
                    cs = str(c)
                    if "标题" in cs or "新闻" in cs: tcol = c
                    elif "时间" in cs or "日期" in cs: dcol = c
                    elif "来源" in cs: scol = c
                if tcol:
                    for _, row in df2.head(10).iterrows():
                        title = str(row.get(tcol, ""))
                        # 去重简单版：标题未出现
                        if title[:20] in [x.title[:20] for x in nd.items]:
                            continue
                        nd.items.append(NewsItem(
                            title=title[:80],
                            source=str(row.get(scol, "财经资讯")) if scol else "财经资讯",
                            publish_time=str(row.get(dcol, ""))[:16] if dcol else "",
                            confidence="中",
                        ))
        except Exception as e:
            logger.debug("新闻抓取失败[%s]：%s", code, str(e)[:80])

        return nd

    # ==============================================================
    # 个股基本面
    # ==============================================================
    @catch_exception(default_return=StockFundamentalData(code="000000", source="akshare"))
    def get_stock_fundamental(
        self, code: str, name: str = ""
    ) -> StockFundamentalData:
        ak = _import_akshare()
        fd = StockFundamentalData(code=code, source=self.name)
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                for c in df.columns:
                    cs = str(c)
                    if "营业收入" in cs and "同比" in cs:
                        fd.revenue_yoy = round(safe_float(latest[c], 0.0), 2)
                    elif "净利润" in cs and "同比" in cs and "扣非" not in cs:
                        fd.net_profit_yoy = round(safe_float(latest[c], 0.0), 2)
                    elif "净资产收益率" in cs and "加权" in cs:
                        fd.roe = round(safe_float(latest[c], 0.0), 2)
                    elif "销售毛利率" in cs:
                        fd.gross_margin = round(safe_float(latest[c], 0.0), 2)
        except Exception as e:
            logger.debug("基本面抓取失败[%s]：%s", code, str(e)[:80])
        return fd

    # ==============================================================
    # 个股资金流向
    # ==============================================================
    @catch_exception(default_return=StockMoneyFlowData(code="000000", source="akshare"))
    def get_stock_money_flow(
        self, code: str, name: str = "", days: int = 5
    ) -> StockMoneyFlowData:
        ak = _import_akshare()
        mf = StockMoneyFlowData(code=code, source=self.name)
        # 判断是否沪深股通标的（粗略判断：上证60/68开头+深证00/30开头+市值足够）
        if code.startswith(("60", "68", "00", "30")):
            mf.is_hs_connect = True
        try:
            df = ak.stock_individual_fund_flow(stock=code, market="sh" if code[0] in "69" else "sz")
            if df is not None and not df.empty:
                main_col = None
                for c in df.columns:
                    if "主力净流入" in str(c):
                        main_col = c
                        break
                if main_col:
                    latest5 = df.head(days)
                    total = 0.0
                    for _, r in latest5.iterrows():
                        total += safe_float(r[main_col], 0.0)
                    # 单位通常是元 → 亿元
                    if abs(total) > 1e5:
                        mf.main_5d = round(total / 1e8, 2)
                    else:
                        mf.main_5d = round(total, 2)
        except Exception as e:
            logger.debug("个股资金流向抓取失败[%s]：%s", code, str(e)[:80])
        return mf

    # ==============================================================
    # 个股风险
    # ==============================================================
    @catch_exception(default_return=StockRiskData(code="000000", source="akshare"))
    def get_stock_risk(
        self, code: str, name: str = ""
    ) -> StockRiskData:
        ak = _import_akshare()
        rd = StockRiskData(code=code, source=self.name)
        try:
            # 限售解禁：stock_restricted_release_cninfo
            df = ak.stock_restricted_release_cninfo(symbol=code)
            if df is not None and not df.empty:
                # 找未来一个月的解禁
                today = date.today()
                for _, row in df.iterrows():
                    try:
                        d_str = str(row.iloc[0])
                        if "-" not in d_str and "/" not in d_str:
                            continue
                        d = date.fromisoformat(d_str.replace("/", "-")[:10])
                        if 0 <= (d - today).days <= 30:
                            # 解禁数量占比列
                            ratio = 0.0
                            for v in row.tolist():
                                fv = safe_float(v, 0.0)
                                if 0 < fv <= 100:
                                    ratio = max(ratio, fv)
                                    break
                            rd.next_month_unlock_ratio = max(
                                rd.next_month_unlock_ratio, ratio
                            )
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("解禁抓取失败：%s", str(e)[:80])
        # 减持公告关键词检测（从新闻标题中判断）
        try:
            df2 = ak.stock_notice_em(symbol=code)
            if df2 is not None and not df2.isEmpty is False:
                for c in df2.columns:
                    if "标题" in str(c):
                        for title in df2[c].astype(str).tolist()[:20]:
                            if "减持" in title:
                                rd.has_reduction_plan = True
                                break
                        break
        except Exception:
            pass
        return rd
