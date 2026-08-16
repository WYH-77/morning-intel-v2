"""
data_sources/base.py - 数据源抽象基类
定义所有数据源需要实现的接口，以及返回数据的统一数据结构
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


class DataAvailability:
    """数据可用性标记"""
    OK = "ok"                     # 正常
    PARTIAL = "partial"           # 部分字段缺失
    FALLBACK = "fallback"         # 使用了兜底数据（非主源）
    UNAVAILABLE = "unavailable"   # 完全不可用（所有字段0/None）


# ============================================================
# 统一返回结构：外围指数
# ============================================================
@dataclass
class GlobalIndexData:
    """外围主要指数数据（美股/韩股）"""
    name: str                              # 中文名，如"纳斯达克综合指数"
    code: str                              # 代码标识，如"IXIC"
    close: float = 0.0                     # 收盘点位
    change_pct: float = 0.0                # 涨跌幅%
    prev_close: float = 0.0                # 昨收
    volume: Optional[float] = None         # 成交量（部分指数可能无）
    status: str = "收盘"                   # 盘前/盘中/盘后/收盘
    source: str = ""                       # 数据来源标签
    availability: str = DataAvailability.UNAVAILABLE


# ============================================================
# 统一返回结构：A股大盘环境
# ============================================================
@dataclass
class AShareMarketData:
    """A股大盘指数 + 情绪面数据"""
    # 三大指数
    sh_comp_close: float = 0.0             # 上证收盘
    sh_comp_change: float = 0.0            # 上证涨跌幅%
    sz_comp_close: float = 0.0             # 深证成指
    sz_comp_change: float = 0.0
    cyb_close: float = 0.0                 # 创业板指
    cyb_change: float = 0.0

    # 涨跌家数
    up_count: int = 0
    down_count: int = 0
    limit_up: int = 0
    limit_down: int = 0

    # 成交金额预估
    turnover_estimate: float = 0.0         # 亿元
    turnover_vs_yesterday: float = 0.0     # 较昨日同期%

    # 富时A50期货
    a50_current: float = 0.0
    a50_change_from_15pm: float = 0.0      # 较昨日15:00涨跌幅%

    # 北向资金
    northbound_net: float = 0.0            # 当日净流入/出 亿元
    northbound_3d: float = 0.0             # 近3日累计

    # 融资余额（前日）
    margin_balance: float = 0.0            # 亿元
    margin_balance_change: float = 0.0     # 变化额

    # 大小盘风格
    zz1000_3d_change: float = 0.0          # 中证1000 近3日
    hs300_3d_change: float = 0.0           # 沪深300 近3日

    # 市场情绪标签：偏强/震荡/偏弱
    sentiment_label: str = "震荡"

    # 元信息
    source: str = ""
    availability: str = DataAvailability.UNAVAILABLE


# ============================================================
# 统一返回结构：板块行情
# ============================================================
@dataclass
class SectorData:
    name: str
    change_pct: float = 0.0
    lead_stock: str = ""                    # 领涨股
    turnover: float = 0.0
    source: str = ""


# ============================================================
# 统一返回结构：个股基础信息（初筛阶段）
# ============================================================
@dataclass
class StockBasicInfo:
    code: str                               # 6位数字
    name: str
    price: float = 0.0
    change_pct: float = 0.0                 # 当日%
    market_cap_float: float = 0.0           # 流通市值（亿元）
    market_cap_total: float = 0.0           # 总市值
    pe_ttm: float = 0.0
    pb: float = 0.0
    industry: str = ""
    is_st: bool = False
    board: str = ""                         # 主板/创业板/科创板/北交所
    source: str = ""


# ============================================================
# 统一返回结构：个股K线技术面
# ============================================================
@dataclass
class StockTechData:
    code: str
    # 涨跌幅
    pct_5d: float = 0.0
    pct_10d: float = 0.0
    pct_20d: float = 0.0
    # 量能
    vol_ma5: float = 0.0
    vol_ma20: float = 0.0
    vol_ratio: float = 0.0                  # 5日均量/20日均量
    volume_latest: float = 0.0
    abnormal_volume: bool = False           # 单日量能>2倍均量
    # 换手率
    turnover_5d_avg: float = 0.0            # %
    turnover_latest: float = 0.0
    # 波动率
    amplitude_20d: float = 0.0              # 近20日振幅%
    # 均线
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma_bullish: bool = False                # 5>10>20 多头
    ma_partial: bool = False                # 5>10 或 10>20
    ma_bearish: bool = False
    # MACD
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_golden_cross: bool = False
    macd_dead_cross: bool = False
    macd_bar_direction: int = 0             # 1放大/-1缩小/0持平
    # RSI(14)
    rsi_14: float = 50.0
    # 布林带
    boll_upper: float = 0.0
    boll_mid: float = 0.0
    boll_lower: float = 0.0
    boll_position: str = "中轨"             # 上轨/中轨/下轨
    price: float = 0.0
    # 板块对比
    sector_5d_pct: float = 0.0
    relative_5d: float = 0.0                # 个股5日涨幅 - 板块5日涨幅
    source: str = ""


# ============================================================
# 统一返回结构：个股资讯事件
# ============================================================
@dataclass
class NewsItem:
    title: str
    source: str = ""                       # 来源媒体
    publish_time: str = ""                 # YYYY-MM-DD HH:MM
    confidence: str = "中"                 # 高/中/低
    category: str = "其他"                 # 利好/利空/中性
    summary: str = ""
    url: str = ""


@dataclass
class StockNewsData:
    code: str
    items: List[NewsItem] = field(default_factory=list)
    has_major_positive: bool = False
    has_major_negative: bool = False
    source: str = ""


# ============================================================
# 统一返回结构：个股基本面
# ============================================================
@dataclass
class StockFundamentalData:
    code: str
    pe_ttm: float = 0.0
    pb: float = 0.0
    revenue_yoy: float = 0.0               # 近一年营收同比%
    net_profit_yoy: float = 0.0            # 近一年净利润同比%
    roe: float = 0.0                       # 近一年ROE%
    gross_margin: float = 0.0              # 毛利率%
    industry_median_pe: float = 0.0
    industry_median_revenue_yoy: float = 0.0
    source: str = ""


# ============================================================
# 统一返回结构：资金流向
# ============================================================
@dataclass
class StockMoneyFlowData:
    code: str
    northbound_5d: float = 0.0             # 近5日北向净买入 亿元
    main_5d: float = 0.0                    # 近5日主力净流入 亿元
    institution_buy_count: int = 0          # 近5日机构龙虎榜净买入次数
    is_hs_connect: bool = False
    source: str = ""


# ============================================================
# 统一返回结构：风险事件
# ============================================================
@dataclass
class StockRiskData:
    code: str
    next_month_unlock_ratio: float = 0.0   # 未来一个月限售解禁占总股本%
    has_reduction_plan: bool = False
    has_pending_lawsuit: bool = False
    has_regulatory_penalty: bool = False
    goodwill_ratio: float = 0.0            # 商誉/净资产%
    other_risks: List[str] = field(default_factory=list)
    source: str = ""


# ============================================================
# 数据源抽象基类
# ============================================================
class BaseDataSource(ABC):
    """
    所有数据源必须实现的接口清单
    - 每个方法返回对应dataclass或其列表
    - 所有方法都应该容忍异常，内部兜底，不抛到外层（最差返回空/默认值）
    - source 字段必须填入，标识数据来自哪个源
    """

    # ---- 数据源元信息 ----
    @property
    @abstractmethod
    def name(self) -> str:
        """返回数据源名称，如 'akshare' / 'ifind' / 'tdx' / 'websearch'"""
        ...

    # ---- 外围市场 ----
    @abstractmethod
    def get_global_indices(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, GlobalIndexData]:
        """
        返回：{
            "纳斯达克": GlobalIndexData(...),
            "标普500": ...,
            "道琼斯": ...,
            "中国金龙": ...,
            "KOSPI": ...,
            "KOSDAQ": ...,
        }
        """
        ...

    # ---- A股大盘环境 ----
    @abstractmethod
    def get_a_share_market(
        self, as_of_date: Optional[date] = None
    ) -> AShareMarketData:
        """获取A股大盘整体环境"""
        ...

    @abstractmethod
    def get_sector_ranking(
        self, top_n: int = 10, as_of_date: Optional[date] = None
    ) -> Tuple[List[SectorData], List[SectorData]]:
        """
        返回 (领涨板块列表, 领跌板块列表)
        """
        ...

    @abstractmethod
    def get_money_flow_overview(
        self, as_of_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        资金面概览：主力净流入前5/流出前5行业
        返回 {"inflow_top5": [SectorData,...], "outflow_top5": [...]}
        """
        ...

    # ---- 个股初筛 ----
    @abstractmethod
    def get_stock_universe(
        self,
        min_market_cap: float,
        max_market_cap: float,
        min_price: float,
        exclude_board: Optional[List[str]] = None,  # ["创业板","科创板","北交所"]
    ) -> List[StockBasicInfo]:
        """
        初筛候选股票池（不含ST、停牌）
        """
        ...

    # ---- 个股详细数据 ----
    @abstractmethod
    def get_stock_tech(
        self, code: str, as_of_date: Optional[date] = None
    ) -> StockTechData:
        ...

    @abstractmethod
    def get_stock_news(
        self, code: str, name: str = "", window_hours: int = 24
    ) -> StockNewsData:
        ...

    @abstractmethod
    def get_stock_fundamental(
        self, code: str, name: str = ""
    ) -> StockFundamentalData:
        ...

    @abstractmethod
    def get_stock_money_flow(
        self, code: str, name: str = "", days: int = 5
    ) -> StockMoneyFlowData:
        ...

    @abstractmethod
    def get_stock_risk(
        self, code: str, name: str = ""
    ) -> StockRiskData:
        ...
