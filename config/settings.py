"""
config/settings.py - 全局配置模块
包含：路径配置、动态参数加载、内置节假日数据库
"""
import os
from datetime import date
from typing import Dict, Tuple, Set

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============ 基础路径配置 ============
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DAILY_PICKS_DIR = os.path.join(OUTPUT_DIR, "daily_picks")
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_DIR = os.path.join(BASE_DIR, "config")

for d in [OUTPUT_DIR, DAILY_PICKS_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

# logs 下的占位文件兼容
_log_keep = os.path.join(LOG_DIR, ".gitkeep")
if not os.path.exists(_log_keep):
    try:
        open(_log_keep, "w", encoding="utf-8").close()
    except Exception:
        pass


# ============ 内置A股法定节假日（2024-2026）============
_EMBEDDED_HOLIDAYS: Dict[date, str] = {
    # ===== 2024年 =====
    date(2024, 1, 1): "元旦",
    date(2024, 2, 9): "春节", date(2024, 2, 12): "春节", date(2024, 2, 13): "春节",
    date(2024, 2, 14): "春节", date(2024, 2, 15): "春节", date(2024, 2, 16): "春节",
    date(2024, 4, 4): "清明", date(2024, 4, 5): "清明",
    date(2024, 5, 1): "劳动节", date(2024, 5, 2): "劳动节", date(2024, 5, 3): "劳动节",
    date(2024, 6, 10): "端午节",
    date(2024, 9, 16): "中秋节", date(2024, 9, 17): "中秋节",
    date(2024, 10, 1): "国庆", date(2024, 10, 2): "国庆", date(2024, 10, 3): "国庆",
    date(2024, 10, 4): "国庆", date(2024, 10, 7): "国庆",
    # ===== 2025年 =====
    date(2025, 1, 1): "元旦",
    date(2025, 1, 28): "春节", date(2025, 1, 29): "春节", date(2025, 1, 30): "春节",
    date(2025, 1, 31): "春节", date(2025, 2, 3): "春节", date(2025, 2, 4): "春节",
    date(2025, 4, 4): "清明", date(2025, 4, 7): "清明",
    date(2025, 5, 1): "劳动节", date(2025, 5, 2): "劳动节", date(2025, 5, 5): "劳动节",
    date(2025, 5, 31): "端午节", date(2025, 6, 2): "端午节",
    date(2025, 10, 1): "国庆", date(2025, 10, 2): "国庆", date(2025, 10, 3): "国庆",
    date(2025, 10, 6): "国庆", date(2025, 10, 7): "国庆", date(2025, 10, 8): "国庆",
    # ===== 2026年（预估，以国务院通知为准）=====
    date(2026, 1, 1): "元旦", date(2026, 1, 2): "元旦",
    date(2026, 2, 16): "春节", date(2026, 2, 17): "春节", date(2026, 2, 18): "春节",
    date(2026, 2, 19): "春节", date(2026, 2, 20): "春节",
    date(2026, 4, 6): "清明",
    date(2026, 5, 1): "劳动节", date(2026, 5, 4): "劳动节", date(2026, 5, 5): "劳动节",
    date(2026, 6, 19): "端午节",
    date(2026, 9, 25): "中秋节",
    date(2026, 10, 1): "国庆", date(2026, 10, 2): "国庆", date(2026, 10, 5): "国庆",
    date(2026, 10, 6): "国庆", date(2026, 10, 7): "国庆", date(2026, 10, 8): "国庆",
}

# A股调休补班日（周末实际要开市的日子，2024-2026）
_EMBEDDED_MAKEUP_WORKDAYS: Dict[date, str] = {
    date(2024, 2, 4): "春节补班", date(2024, 2, 18): "春节补班",
    date(2024, 4, 7): "清明补班", date(2024, 4, 28): "劳动节补班",
    date(2024, 5, 11): "劳动节补班", date(2024, 9, 14): "中秋补班",
    date(2024, 9, 29): "国庆补班", date(2024, 10, 12): "国庆补班",
    date(2025, 1, 26): "春节补班", date(2025, 2, 8): "春节补班",
    date(2025, 4, 27): "劳动节补班", date(2025, 9, 28): "国庆补班",
    date(2025, 10, 11): "国庆补班",
}


def get_embedded_holidays() -> Tuple[Set[date], Set[date]]:
    """
    返回 (内置节假日集合, 内置补班日集合)
    同时会读取 config/holidays.txt 追加自定义日期
    """
    holidays = set(_EMBEDDED_HOLIDAYS.keys())
    makeup_days = set(_EMBEDDED_MAKEUP_WORKDAYS.keys())

    # 读取自定义 holidays.txt
    holidays_file = os.path.join(CONFIG_DIR, "holidays.txt")
    if os.path.exists(holidays_file):
        try:
            with open(holidays_file, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    try:
                        parts = s.split()
                        d = date.fromisoformat(parts[0])
                        if len(parts) >= 2 and parts[1].lower() in ("makeup", "补班"):
                            makeup_days.add(d)
                        else:
                            holidays.add(d)
                    except Exception:
                        continue
        except Exception:
            pass
    return holidays, makeup_days


# ============ 动态配置加载（从.env或环境变量）============
def load_config() -> Dict:
    """
    加载并校验所有运行时配置，返回配置字典
    """
    # Webhook 兼容多种命名（取第一个非空）
    _webhook_candidates = [
        os.getenv("WECHAT_WEBHOOK", "").strip(),
        os.getenv("QX_WECOM_WEBHOOK", "").strip(),
        os.getenv("WECOM_WEBHOOK", "").strip(),
        os.getenv("QYWX_WEBHOOK", "").strip(),
    ]
    _webhook = next((v for v in _webhook_candidates if v), "")

    def _env_bool(key: str, default: bool) -> bool:
        v = os.getenv(key, str(default)).strip().lower()
        return v in ("true", "1", "yes", "y", "on")

    def _env_float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, str(default)).strip())
        except (ValueError, TypeError):
            return default

    def _env_int(key: str, default: int) -> int:
        try:
            return int(float(os.getenv(key, str(default)).strip()))
        except (ValueError, TypeError):
            return default

    cfg = {
        # --- 推送 ---
        "webhook": _webhook,
        # --- 选股参数 ---
        "top_n": _env_int("TOP_N", 5),
        "max_stocks": _env_int("MAX_STOCKS", 200),
        "min_market_cap": _env_float("MIN_MARKET_CAP", 50.0),
        "max_market_cap": _env_float("MAX_MARKET_CAP", 1000.0),
        "min_price": _env_float("MIN_PRICE", 5.0),
        "enable_news": _env_bool("ENABLE_NEWS", True),
        "min_score_threshold": _env_float("MIN_SCORE_THRESHOLD", 0.0),
        # --- 初筛进阶门槛 ---
        "min_5d_pct": 5.0,        # 近5日最低累计涨幅%
        "max_5d_pct": 40.0,       # 近5日最高累计涨幅%
        "min_vol_ratio": 1.2,     # 近5日均量/20日均量最低放大倍数
        "min_relative_strength": 1.2,  # 个股涨幅/板块涨幅最低倍数
        # --- 数据源 ---
        "data_source_priority": [
            s.strip() for s in os.getenv(
                "DATA_SOURCE_PRIORITY", "akshare,websearch"
            ).split(",") if s.strip()
        ],
        # --- 接口 ---
        "api_min_interval": _env_float("API_MIN_INTERVAL", 1.2),
        "api_max_retries": _env_int("API_MAX_RETRIES", 2),
        "api_timeout": _env_int("API_TIMEOUT", 20),
    }
    return cfg
