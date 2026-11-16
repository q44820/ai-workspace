"""
配置文件 - Polymarket-Opinion 套利监控
"""
import os
from dataclasses import dataclass
from typing import List, Dict

# ==========================================
# Telegram 配置
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "你的BotToken")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "你的ChatID")

# ==========================================
# 监控配置
# ==========================================
# 监控间隔（秒）
MONITOR_INTERVAL_MIN = 30  # 最小间隔
MONITOR_INTERVAL_MAX = 60  # 最大间隔

# 套利阈值
MIN_PROFIT_PERCENTAGE = 0.5  # 最小净利润百分比（%）

# 手续费配置
POLYMARKET_FEE = 0.02  # Polymarket 手续费 (2%)
OPINION_FEE = 0.02     # Opinion 手续费 (2%)

# 最大重试次数
MAX_RETRIES = 3
RETRY_DELAY = 5  # 重试间隔（秒）

# 浏览器配置
BROWSER_HEADLESS = True  # 无头模式
BROWSER_TIMEOUT = 30000  # 页面加载超时（毫秒）
NAVIGATION_DELAY_MIN = 3  # 导航后最小延迟（秒）
NAVIGATION_DELAY_MAX = 5  # 导航后最大延迟（秒）

# ==========================================
# 市场配置 - 需要监控的市场对
# ==========================================
# 格式: (Polymarket URL, Opinion URL, 市场名称)
MARKET_PAIRS: List[Dict] = [
    {
        "name": "比特币价格预测示例",
        "polymarket_url": "https://polymarket.com/event/bitcoin-price-example",
        "opinion_url": "https://opinion.org/market/bitcoin-price-example",
        # 可选：事件关键词用于匹配验证
        "keywords": ["bitcoin", "btc", "price"],
    },
    # 添加更多市场对...
    # {
    #     "name": "以太坊价格预测",
    #     "polymarket_url": "https://polymarket.com/event/ethereum-price",
    #     "opinion_url": "https://opinion.org/market/ethereum-price",
    #     "keywords": ["ethereum", "eth", "price"],
    # },
]

# ==========================================
# 用户代理列表（轮换使用）
# ==========================================
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# ==========================================
# CSS 选择器配置
# ==========================================
# Polymarket 选择器
POLYMARKET_SELECTORS = {
    "yes_price": '[data-cy="outcome-button-Yes"] .price, [data-cy="outcome-Yes"] .price, .outcome-yes .price',
    "no_price": '[data-cy="outcome-button-No"] .price, [data-cy="outcome-No"] .price, .outcome-no .price',
    "market_title": 'h1, [data-cy="market-title"], .market-title',
    "price_buttons": '.outcome-button, [data-cy^="outcome-button"]',
}

# Opinion 选择器（需要根据实际网站结构调整）
OPINION_SELECTORS = {
    "yes_price": '.outcome-yes .price, [data-outcome="yes"] .price, .yes-price',
    "no_price": '.outcome-no .price, [data-outcome="no"] .price, .no-price',
    "market_title": 'h1, .market-title, .event-title',
    "price_buttons": '.outcome-btn, .market-option',
}

# ==========================================
# 日志配置
# ==========================================
LOG_LEVEL = "INFO"
LOG_FILE = "logs/arbitrage_monitor.log"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5
