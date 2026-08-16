"""
data_sources/websearch_source.py - Web公开财经爬虫兜底数据源
说明：普通Python环境没有Agent的WebSearch工具，这里用 requests + BeautifulSoup
      爬取东方财富、新浪财经等公开页面，作为 akshare 失败时的终极兜底。
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.utils import (
    get_logger, catch_exception, retry, safe_float, random_sleep,
)
from .base import (
    BaseDataSource, DataAvailability,
    GlobalIndexData, AShareMarketData, SectorData,
    StockBasicInfo, StockTechData, StockNewsData, NewsItem,
    StockFundamentalData, StockMoneyFlowData, StockRiskData,
)

logger = get_logger()


def _requests_get(url: str, timeout: int = 15, headers: dict = None) -> Optional[str]:
    try:
        import requests
    except ImportError:
        return None
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if headers:
        default_headers.update(headers)
    try:
        resp = requests.get(url, headers=default_headers, timeout=timeout)
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code == 200:
            return resp.text
    except Exception:
        return None
    return None


class WebSearchDataSource(BaseDataSource):
    """
    终极兜底数据源：
    - 爬取公开财经网页获取行情、新闻等
    - 所有数值都可能为近似值，会标记 availability=FALLBACK
    """

    @property
    def name(self) -> str:
        return "websearch"

    # ==============================================================
    # 外围指数（新浪财经网页端）
    # ==============================================================
    @catch_exception(default_return={})
    def get_global_indices(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, GlobalIndexData]:
        logger.info("[websearch] 兜底抓取外围指数...")
        result: Dict[str, GlobalIndexData] = {
            "纳斯达克": GlobalIndexData(name="纳斯达克综合指数", code="IXIC", source=self.name),
            "标普500": GlobalIndexData(name="标普500指数", code="SPX", source=self.name),
            "道琼斯": GlobalIndexData(name="道琼斯工业指数", code="DJI", source=self.name),
            "中国金龙": GlobalIndexData(name="纳斯达克中国金龙指数", code="HXC", source=self.name),
            "KOSPI": GlobalIndexData(name="KOSPI综合指数", code="KOSPI", source=self.name),
            "KOSDAQ": GlobalIndexData(name="KOSDAQ指数", code="KOSDAQ", source=self.name),
        }
        # 新浪美股行情API（JSONP格式，公开）
        symbols = {
            "纳斯达克": "http://hq.sinajs.cn/list=int_nasdaq",
            "标普500": "http://hq.sinajs.cn/list=int_sp500",
            "道琼斯": "http://hq.sinajs.cn/list=int_dji",
        }
        for key, url in symbols.items():
            try:
                txt = _requests_get(url, headers={"Referer": "https://finance.sina.com.cn/"})
                if txt and '="' in txt:
                    content = txt.split('="')[1].split('"')[0]
                    parts = content.split(",")
                    if len(parts) >= 4:
                        close = safe_float(parts[1], 0.0)
                        prev = safe_float(parts[3], 0.0)
                        pct = ((close - prev) / prev * 100) if prev > 0 else 0.0
                        gd = result[key]
                        gd.close = round(close, 2)
                        gd.prev_close = round(prev, 2)
                        gd.change_pct = round(pct, 2)
                        gd.status = "隔夜收盘"
                        gd.availability = DataAvailability.FALLBACK
            except Exception:
                continue
        return result

    # ==============================================================
    # A股大盘（简化：仅三大指数基本点位）
    # ==============================================================
    @catch_exception(default_return=AShareMarketData(source="websearch"))
    def get_a_share_market(
        self, as_of_date: Optional[date] = None
    ) -> AShareMarketData:
        logger.info("[websearch] 兜底抓取A股大盘...")
        md = AShareMarketData(source=self.name)
        url = "http://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006"
        txt = _requests_get(url, headers={"Referer": "https://finance.sina.com.cn/"})
        if txt:
            try:
                lines = [ln for ln in txt.split("\n") if ln.strip()]
                mapping = [("sh", "上证指数"), ("sz1", "深证成指"), ("sz2", "创业板指")]
                values = {}
                for ln in lines:
                    if '="' not in ln:
                        continue
                    content = ln.split('="')[1].split('"')[0]
                    parts = content.split(",")
                    if len(parts) >= 4:
                        name = parts[0]
                        values[name] = parts
                # 新浪返回格式：名称,点位,涨跌点,涨跌幅%,成交量,成交额
                if "上证指数" in values:
                    p = values["上证指数"]
                    md.sh_comp_close = safe_float(p[1])
                    md.sh_comp_change = safe_float(p[3])
                if "深证成指" in values:
                    p = values["深证成指"]
                    md.sz_comp_close = safe_float(p[1])
                    md.sz_comp_change = safe_float(p[3])
                if "创业板指" in values:
                    p = values["创业板指"]
                    md.cyb_close = safe_float(p[1])
                    md.cyb_change = safe_float(p[3])
            except Exception:
                pass
        md.availability = DataAvailability.FALLBACK if md.sh_comp_close > 0 else DataAvailability.UNAVAILABLE
        return md

    @catch_exception(default_return=([], []))
    def get_sector_ranking(
        self, top_n: int = 10, as_of_date: Optional[date] = None
    ) -> Tuple[List[SectorData], List[SectorData]]:
        return [], []

    @catch_exception(default_return={"inflow_top5": [], "outflow_top5": []})
    def get_money_flow_overview(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, Any]:
        return {"inflow_top5": [], "outflow_top5": []}

    @catch_exception(default_return=[])
    def get_stock_universe(
        self,
        min_market_cap: float,
        max_market_cap: float,
        min_price: float,
        exclude_board: Optional[List[str]] = None,
    ) -> List[StockBasicInfo]:
        # WebSearch不适合做全市场初筛，返回空让上游跳过
        logger.warning("[websearch] 不支持全市场初筛，返回空池")
        return []

    @catch_exception(default_return=StockTechData(code="000000", source="websearch"))
    def get_stock_tech(
        self, code: str, as_of_date: Optional[date] = None
    ) -> StockTechData:
        return StockTechData(code=code, source=self.name, availability=DataAvailability.UNAVAILABLE)

    @catch_exception(default_return=StockNewsData(code="000000", source="websearch"))
    def get_stock_news(
        self, code: str, name: str = "", window_hours: int = 24
    ) -> StockNewsData:
        nd = StockNewsData(code=code, source=self.name)
        # 巨潮资讯最近公告RSS（不完整，仅作兜底）
        url = f"http://www.cninfo.com.cn/new/fulltextSearch/full?searchkey={code}&sdate=&edate=&isfulltext=false&sortName=pubdate&sortType=desc&pageNum=1&pageSize=10"
        try:
            import json
            txt = _requests_get(url, timeout=10)
            if txt:
                data = json.loads(txt)
                for ann in data.get("announcements", [])[:10]:
                    title = ann.get("announcementTitle", "")
                    nd.items.append(NewsItem(
                        title=title[:80],
                        source="巨潮资讯",
                        publish_time=ann.get("announcementTime", "")[:16],
                        confidence="高",
                        category="中性",
                    ))
        except Exception:
            pass
        return nd

    @catch_exception(default_return=StockFundamentalData(code="000000", source="websearch"))
    def get_stock_fundamental(
        self, code: str, name: str = ""
    ) -> StockFundamentalData:
        return StockFundamentalData(code=code, source=self.name)

    @catch_exception(default_return=StockMoneyFlowData(code="000000", source="websearch"))
    def get_stock_money_flow(
        self, code: str, name: str = "", days: int = 5
    ) -> StockMoneyFlowData:
        return StockMoneyFlowData(code=code, source=self.name)

    @catch_exception(default_return=StockRiskData(code="000000", source="websearch"))
    def get_stock_risk(
        self, code: str, name: str = ""
    ) -> StockRiskData:
        return StockRiskData(code=code, source=self.name)
