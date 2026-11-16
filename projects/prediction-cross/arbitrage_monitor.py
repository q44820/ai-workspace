"""
Polymarket-Opinion 套利监控主程序
稳字当头，3-5秒延迟，用完关闭标签页
"""
import os
import sys
import time
import random
import signal
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

# 确保可以导入本地模块
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    MARKET_PAIRS,
    MONITOR_INTERVAL_MIN,
    MONITOR_INTERVAL_MAX,
    MIN_PROFIT_PERCENTAGE,
    LOG_LEVEL,
    LOG_FILE,
    LOG_FORMAT,
    MAX_RETRIES,
    RETRY_DELAY,
)
from scraper import PriceScraper, MarketPrice
from arbitrage_calculator import ArbitrageCalculator, ArbitrageOpportunity
from telegram_bot import TelegramBot, telegram_bot


# 全局变量
running = True
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5


def setup_logging():
    """配置日志系统"""
    # 创建日志目录
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 配置日志格式
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return logging.getLogger(__name__)


logger = setup_logging()


def signal_handler(signum, frame):
    """信号处理 - 优雅退出"""
    global running
    logger.info(f"收到信号 {signum}，正在停止监控...")
    running = False


# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class ArbitrageMonitor:
    """套利监控器主类"""
    
    def __init__(self):
        self.calculator = ArbitrageCalculator(min_profit_pct=MIN_PROFIT_PERCENTAGE)
        self.telegram = telegram_bot
        self.market_pairs = MARKET_PAIRS
        self.opportunities_found = 0
        self.checks_performed = 0
        
        # 用于去重警报（避免重复发送相同机会）
        self.last_alert_hash = ""
        self.alert_cooldown = 300  # 5分钟冷却期
        self.last_alert_time = 0
    
    def _get_random_interval(self) -> float:
        """获取随机监控间隔"""
        return random.uniform(MONITOR_INTERVAL_MIN, MONITOR_INTERVAL_MAX)
    
    def _opportunity_hash(self, opp: ArbitrageOpportunity) -> str:
        """生成套利机会的唯一标识"""
        return f"{opp.market_name}_{opp.strategy}_{opp.net_profit_pct:.2f}"
    
    def _should_send_alert(self, opp: ArbitrageOpportunity) -> bool:
        """判断是否应该发送警报（去重）"""
        current_hash = self._opportunity_hash(opp)
        current_time = time.time()
        
        # 如果哈希相同且未过冷却期，则不发送
        if current_hash == self.last_alert_hash:
            if current_time - self.last_alert_time < self.alert_cooldown:
                return False
        
        self.last_alert_hash = current_hash
        self.last_alert_time = current_time
        return True
    
    async def _monitor_single_pair(
        self, 
        pair: Dict, 
        retry_count: int = 0
    ) -> Optional[ArbitrageOpportunity]:
        """
        监控单个市场对
        
        Args:
            pair: 市场配置字典
            retry_count: 当前重试次数
            
        Returns:
            套利机会（如果有）
        """
        name = pair["name"]
        poly_url = pair["polymarket_url"]
        opinion_url = pair["opinion_url"]
        
        logger.info(f"\n{'='*60}")
        logger.info(f"正在监控: {name}")
        logger.info(f"{'='*60}")
        
        try:
            # 使用 scraper 抓取价格
            async with PriceScraper() as scraper:
                poly_price, opinion_price = await scraper.scrape_prices(
                    poly_url, 
                    opinion_url
                )
            
            # 检查是否有错误
            if poly_price.error:
                logger.warning(f"Polymarket 抓取错误: {poly_price.error}")
            if opinion_price.error:
                logger.warning(f"Opinion 抓取错误: {opinion_price.error}")
            
            # 如果两个平台都有错误，尝试重试
            if poly_price.error and opinion_price.error and retry_count < MAX_RETRIES:
                logger.info(f"两个平台都失败，{RETRY_DELAY}秒后重试 ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY)
                return await self._monitor_single_pair(pair, retry_count + 1)
            
            # 计算套利机会
            opportunity = self.calculator.calculate_arbitrage(
                market_name=name,
                poly_price=poly_price,
                opinion_price=opinion_price
            )
            
            if opportunity:
                logger.info(self.calculator.format_opportunity(opportunity))
                
                if opportunity.is_profitable:
                    self.opportunities_found += 1
                    logger.info(f"🚀 发现有利可图的套利机会！净利润: {opportunity.net_profit_pct:.2f}%")
                    return opportunity
                else:
                    logger.info(f"💤 净利润 {opportunity.net_profit_pct:.2f}% 低于阈值 {MIN_PROFIT_PERCENTAGE}%")
            else:
                logger.info("💤 无套利机会")
            
            return None
            
        except Exception as e:
            logger.error(f"监控 {name} 时出错: {e}")
            
            # 尝试重试
            if retry_count < MAX_RETRIES:
                logger.info(f"{RETRY_DELAY}秒后重试 ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY)
                return await self._monitor_single_pair(pair, retry_count + 1)
            
            return None
    
    async def _send_arbitrage_alert(self, opp: ArbitrageOpportunity, pair: Dict):
        """发送套利警报"""
        try:
            success = await self.telegram.send_arbitrage_alert(
                market_name=opp.market_name,
                polymarket_yes=opp.polymarket_yes,
                polymarket_no=opp.polymarket_no,
                opinion_yes=opp.opinion_yes,
                opinion_no=opp.opinion_no,
                profit_percentage=opp.net_profit_pct,
                strategy=opp.strategy,
                polymarket_url=pair.get("polymarket_url", ""),
                opinion_url=pair.get("opinion_url", ""),
            )
            
            if success:
                logger.info("✅ Telegram 警报发送成功")
            else:
                logger.warning("❌ Telegram 警报发送失败")
                
        except Exception as e:
            logger.error(f"发送 Telegram 警报时出错: {e}")
    
    async def run_monitor_cycle(self):
        """运行一轮监控周期"""
        global consecutive_errors
        
        self.checks_performed += 1
        logger.info(f"\n{'#'*60}")
        logger.info(f"开始第 {self.checks_performed} 轮监控")
        logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'#'*60}")
        
        profitable_opportunities = []
        
        for pair in self.market_pairs:
            if not running:
                break
            
            try:
                opportunity = await self._monitor_single_pair(pair)
                
                if opportunity and opportunity.is_profitable:
                    profitable_opportunities.append((opportunity, pair))
                
                # 重置连续错误计数
                consecutive_errors = 0
                
                # 市场之间的随机延迟
                await asyncio.sleep(random.uniform(2, 4))
                
            except Exception as e:
                logger.error(f"处理市场对时出错: {e}")
                consecutive_errors += 1
                
                # 如果连续错误太多，发送警报
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    await self.telegram.send_error_alert(
                        f"连续 {consecutive_errors} 次监控失败",
                        "系统可能遇到问题，请检查"
                    )
                    consecutive_errors = 0
        
        # 发送最有价值的套利机会警报
        if profitable_opportunities:
            # 按净利润排序
            profitable_opportunities.sort(
                key=lambda x: x[0].net_profit_pct, 
                reverse=True
            )
            
            best_opp, best_pair = profitable_opportunities[0]
            
            if self._should_send_alert(best_opp):
                await self._send_arbitrage_alert(best_opp, best_pair)
        
        logger.info(f"\n第 {self.checks_performed} 轮监控完成")
        logger.info(f"发现 {len(profitable_opportunities)} 个有利可图的机会")
    
    async def run(self):
        """主运行循环"""
        global running
        
        logger.info("="*60)
        logger.info("Polymarket-Opinion 套利监控系统启动")
        logger.info("="*60)
        logger.info(f"监控市场对数量: {len(self.market_pairs)}")
        logger.info(f"最小净利润阈值: {MIN_PROFIT_PERCENTAGE}%")
        logger.info(f"监控间隔: {MONITOR_INTERVAL_MIN}-{MONITOR_INTERVAL_MAX} 秒")
        logger.info("="*60)
        
        # 发送启动通知
        await self.telegram.send_startup_notification()
        
        try:
            while running:
                await self.run_monitor_cycle()
                
                if not running:
                    break
                
                # 等待下一轮
                interval = self._get_random_interval()
                logger.info(f"\n⏱️  {interval:.1f} 秒后开始下一轮监控...")
                
                # 分段等待，以便更快响应停止信号
                waited = 0
                while waited < interval and running:
                    await asyncio.sleep(1)
                    waited += 1
                    
        except Exception as e:
            logger.error(f"监控循环出错: {e}")
            await self.telegram.send_error_alert(str(e), "监控循环异常")
        finally:
            logger.info("="*60)
            logger.info("套利监控系统停止")
            logger.info(f"总检查次数: {self.checks_performed}")
            logger.info(f"发现机会数: {self.opportunities_found}")
            logger.info("="*60)
            
            await self.telegram.send_shutdown_notification()
            await self.telegram.close()


async def main():
    """主入口"""
    monitor = ArbitrageMonitor()
    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("用户中断监控")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
        sys.exit(1)
