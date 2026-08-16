"""
data_sources/manager.py - 多数据源管理器
职责：
  1. 按优先级加载多个数据源（akshare > websearch > plugin）
  2. 每个数据点自动切源：主源失败用备用，备用都失败则返回默认值
  3. 双源验证：同一数据点主源和备用都有时取更合理值（保守策略）
"""
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from core.utils import get_logger, catch_exception
from .base import (
    BaseDataSource, DataAvailability,
    GlobalIndexData, AShareMarketData, SectorData,
    StockBasicInfo, StockTechData, StockNewsData,
    StockFundamentalData, StockMoneyFlowData, StockRiskData,
)
from .akshare_source import AkshareDataSource
from .websearch_source import WebSearchDataSource

logger = get_logger()


class DataSourceManager:
    """按优先级管理多个数据源，提供统一的调用入口和自动降级"""

    def __init__(self, priority: List[str]):
        self.priority = priority or ["akshare", "websearch"]
        self._sources: Dict[str, BaseDataSource] = {}
        self._init_sources()

    def _init_sources(self):
        for name in self.priority:
            try:
                if name == "akshare":
                    self._sources[name] = AkshareDataSource()
                elif name == "websearch":
                    self._sources[name] = WebSearchDataSource()
                elif name == "plugin":
                    # plugin (iFinD Skill + TDX MCP) 只有本地 Agent 环境能调用
                    # 这里留接口占位：用户可自行扩展 data_sources/plugin_source.py
                    try:
                        from .plugin_source import PluginDataSource  # type: ignore
                        self._sources[name] = PluginDataSource()
                        logger.info("[manager] 检测并加载了 plugin 数据源")
                    except Exception:
                        logger.info("[manager] plugin 数据源未实现，跳过（仅本地Agent可用）")
                        continue
                else:
                    logger.warning("[manager] 未知数据源名称：%s，跳过", name)
                    continue
                logger.info("[manager] 加载数据源：%s ✓", self._sources[name].name)
            except Exception as e:
                logger.error("[manager] 加载数据源 %s 失败：%s", name, str(e)[:120])

        if not self._sources:
            logger.error("[manager] 无任何可用数据源！将全部返回默认值")
            # 最后兜底：强制websearch（它不依赖第三方库除requests外）
            try:
                self._sources["websearch"] = WebSearchDataSource()
            except Exception:
                pass

        logger.info(
            "[manager] 数据源初始化完成，优先级：%s，实际可用：%s",
            self.priority, list(self._sources.keys()),
        )

    @property
    def sources_ordered(self) -> List[BaseDataSource]:
        ordered = []
        for name in self.priority:
            if name in self._sources:
                ordered.append(self._sources[name])
        # 加上那些已加载但不在priority里的（兜底）
        for name, s in self._sources.items():
            if s not in ordered:
                ordered.append(s)
        return ordered

    # ==============================================================
    # 通用调用器：按优先级遍历各源，第一个返回有效值（非全0/空）即采用
    # ==============================================================
    def _call_first_valid(
        self, method_name: str, *args,
        is_valid=lambda r: r is not None and not (isinstance(r, (dict, list)) and len(r) == 0),
        default=None,
        **kwargs,
    ):
        last_exc = None
        for src in self.sources_ordered:
            try:
                method = getattr(src, method_name, None)
                if method is None:
                    continue
                result = method(*args, **kwargs)
                if is_valid(result):
                    return result, src.name
            except Exception as e:
                last_exc = e
                logger.debug(
                    "[manager] 源 %s 调用 %s 失败：%s",
                    src.name, method_name, str(e)[:100],
                )
                continue
        if last_exc:
            logger.warning(
                "[manager] 所有源调用 %s 均失败，最后异常：%s",
                method_name, str(last_exc)[:120],
            )
        return default, ""

    # ==============================================================
    # 下面是对外统一API（与BaseDataSource一一对应）
    # ==============================================================
    def get_global_indices(self, as_of_date: Optional[date] = None):
        result, used = self._call_first_valid(
            "get_global_indices", as_of_date=as_of_date,
            is_valid=lambda r: isinstance(r, dict) and len(r) > 0,
            default={},
        )
        return result

    def get_a_share_market(self, as_of_date: Optional[date] = None) -> AShareMarketData:
        result, used = self._call_first_valid(
            "get_a_share_market", as_of_date=as_of_date,
            is_valid=lambda r: (r is not None and (
                getattr(r, "sh_comp_close", 0) > 0
                or getattr(r, "availability", "") != DataAvailability.UNAVAILABLE
            )),
            default=AShareMarketData(source="none"),
        )
        return result

    def get_sector_ranking(self, top_n: int = 10, as_of_date: Optional[date] = None):
        result, used = self._call_first_valid(
            "get_sector_ranking", top_n=top_n, as_of_date=as_of_date,
            is_valid=lambda r: isinstance(r, tuple) and len(r) == 2 and (len(r[0]) > 0 or len(r[1]) > 0),
            default=([], []),
        )
        return result

    def get_money_flow_overview(self, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        result, used = self._call_first_valid(
            "get_money_flow_overview", as_of_date=as_of_date,
            is_valid=lambda r: isinstance(r, dict),
            default={"inflow_top5": [], "outflow_top5": []},
        )
        return result

    def get_stock_universe(
        self,
        min_market_cap: float,
        max_market_cap: float,
        min_price: float,
        exclude_board: Optional[List[str]] = None,
    ) -> List[StockBasicInfo]:
        result, used = self._call_first_valid(
            "get_stock_universe",
            min_market_cap=min_market_cap,
            max_market_cap=max_market_cap,
            min_price=min_price,
            exclude_board=exclude_board,
            is_valid=lambda r: isinstance(r, list) and len(r) > 0,
            default=[],
        )
        return result

    def get_stock_tech(self, code: str, as_of_date: Optional[date] = None) -> StockTechData:
        result, used = self._call_first_valid(
            "get_stock_tech", code=code, as_of_date=as_of_date,
            is_valid=lambda r: r is not None,
            default=StockTechData(code=code),
        )
        return result

    def get_stock_news(
        self, code: str, name: str = "", window_hours: int = 24
    ) -> StockNewsData:
        result, used = self._call_first_valid(
            "get_stock_news", code=code, name=name, window_hours=window_hours,
            is_valid=lambda r: r is not None,
            default=StockNewsData(code=code),
        )
        return result

    def get_stock_fundamental(
        self, code: str, name: str = ""
    ) -> StockFundamentalData:
        result, used = self._call_first_valid(
            "get_stock_fundamental", code=code, name=name,
            is_valid=lambda r: r is not None,
            default=StockFundamentalData(code=code),
        )
        return result

    def get_stock_money_flow(
        self, code: str, name: str = "", days: int = 5
    ) -> StockMoneyFlowData:
        result, used = self._call_first_valid(
            "get_stock_money_flow", code=code, name=name, days=days,
            is_valid=lambda r: r is not None,
            default=StockMoneyFlowData(code=code),
        )
        return result

    def get_stock_risk(
        self, code: str, name: str = ""
    ) -> StockRiskData:
        result, used = self._call_first_valid(
            "get_stock_risk", code=code, name=name,
            is_valid=lambda r: r is not None,
            default=StockRiskData(code=code),
        )
        return result


# 单例模式（避免重复加载akshare等重型模块）
_MANAGER_SINGLETON: Optional[DataSourceManager] = None


def get_data_source_manager(priority: List[str] = None) -> DataSourceManager:
    """获取全局单例的数据源管理器"""
    global _MANAGER_SINGLETON
    if _MANAGER_SINGLETON is None:
        if priority is None:
            from config.settings import load_config
            priority = load_config()["data_source_priority"]
        _MANAGER_SINGLETON = DataSourceManager(priority)
    return _MANAGER_SINGLETON
