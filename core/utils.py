"""
core/utils.py - 通用工具函数模块
包含：日志系统、重试装饰器、限流装饰器、异常捕获、安全类型转换、随机睡眠等
"""
import os
import sys
import time
import random
import logging
import functools
import traceback
from datetime import datetime, date, timedelta
from logging.handlers import TimedRotatingFileHandler

from config.settings import LOG_DIR

# ============================================================
# 一、日志系统（按日滚动 + 控制台双输出，保留30天）
# ============================================================
_LOGGER_INITIALIZED = False


def setup_logger(name: str = "morning_intel", log_dir: str = None) -> logging.Logger:
    global _LOGGER_INITIALIZED
    logger = logging.getLogger(name)
    if _LOGGER_INITIALIZED:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 控制台
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    # 文件
    log_dir = log_dir or LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    fh = TimedRotatingFileHandler(
        os.path.join(log_dir, f"{name}.log"),
        when="midnight", interval=1, backupCount=30, encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    fh.suffix = "%Y-%m-%d"
    logger.addHandler(fh)

    _LOGGER_INITIALIZED = True
    logger.info("=" * 60)
    logger.info("日志系统初始化完成，日志目录：%s", log_dir)
    logger.info("=" * 60)
    return logger


def get_logger() -> logging.Logger:
    global _LOGGER_INITIALIZED
    if not _LOGGER_INITIALIZED:
        return setup_logger()
    return logging.getLogger("morning_intel")


# ============================================================
# 二、交易日判断（加载内置 + 自定义 holidays.txt）
# ============================================================
def _load_calendar():
    from config.settings import get_embedded_holidays
    h, m = get_embedded_holidays()
    return h, m

_HOLIDAYS, _MAKEUP_DAYS = _load_calendar()


def is_trading_day(check_date: date = None) -> bool:
    """判断指定日期是否为A股交易日"""
    if check_date is None:
        check_date = date.today()
    if check_date in _MAKEUP_DAYS:
        return True
    if check_date in _HOLIDAYS:
        return False
    return check_date.weekday() < 5  # 0-4 = 周一至周五


def get_previous_trading_day(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    d = from_date - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def get_next_trading_day(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    d = from_date + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


# ============================================================
# 三、网络重试装饰器（指数退避）
# ============================================================
def retry(max_retries: int = 2, initial_delay: float = 2.0,
          backoff: float = 2.0, jitter: bool = True,
          exceptions: tuple = (Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            delay = initial_delay
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt >= max_retries:
                        logger.error(
                            "函数 %s 重试%d次后仍然失败：%s",
                            func.__name__, max_retries, str(e),
                        )
                        raise
                    st = delay * (backoff ** attempt)
                    if jitter:
                        st += random.uniform(0, delay * 0.5)
                    logger.warning(
                        "函数 %s 第%d次失败：%s，%.2f秒后重试（%d/%d）",
                        func.__name__, attempt + 1, str(e)[:120],
                        st, attempt + 1, max_retries,
                    )
                    time.sleep(st)
            raise last_exception  # pragma: no cover
        return wrapper
    return decorator


# ============================================================
# 四、限流装饰器（滑动窗口计数）
# ============================================================
_RATE_BUCKETS = {}


def rate_limit(calls: int = 10, period: int = 60, bucket_key: str = None):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            key = bucket_key or func.__name__
            now = time.time()
            last_reset, tokens = _RATE_BUCKETS.get(key, (now, calls))
            if now - last_reset >= period:
                last_reset = now
                tokens = calls
            if tokens <= 0:
                wt = period - (now - last_reset) + 0.5
                logger.warning("%s 触发限流：%d次/%d秒，等待%.2fs",
                               key, calls, period, wt)
                time.sleep(wt)
                last_reset = time.time()
                tokens = calls
            tokens -= 1
            _RATE_BUCKETS[key] = (last_reset, tokens)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# 五、全局异常捕获装饰器
# ============================================================
def catch_exception(default_return=None, log_traceback: bool = True):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            try:
                return func(*args, **kwargs)
            except Exception as e:
                msg = "函数 %s 执行异常：%s" % (func.__name__, str(e))
                if log_traceback:
                    logger.error("%s\n%s", msg, traceback.format_exc())
                else:
                    logger.error(msg)
                return default_return
        return wrapper
    return decorator


# ============================================================
# 六、其他工具
# ============================================================
def random_sleep(min_sec: float = 0.5, max_sec: float = 1.5):
    time.sleep(random.uniform(min_sec, max_sec))


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and (value != value)):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and (value != value)):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def today_str(fmt: str = "%Y-%m-%d") -> str:
    return datetime.now().strftime(fmt)


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now().strftime(fmt)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def format_pct(v: float, ndigits: int = 2) -> str:
    """格式化百分比，自带正负号"""
    v = round(v, ndigits)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{ndigits}f}%"
