"""
套利价格抓取器
使用 Playwright 进行浏览器自动化
"""
import random
import logging
import asyncio
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from config import (
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT,
    NAVIGATION_DELAY_MIN,
    NAVIGATION_DELAY_MAX,
    USER_AGENTS,
    POLYMARKET_SELECTORS,
    OPINION_SELECTORS,
    MAX_RETRIES,
    RETRY_DELAY,
)

logger = logging.getLogger(__name__)


@dataclass
class MarketPrice:
    """市场价格数据结构"""
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    market_title: str = ""
    platform: str = ""
    url: str = ""
    error: Optional[str] = None
    
    def is_valid(self) -> bool:
        """检查价格是否有效"""
        return (
            self.yes_price is not None 
            and self.no_price is not None
            and 0 < self.yes_price <= 1
            and 0 < self.no_price <= 1
        )
    
    def get_implied_probability(self) -> float:
        """获取隐含概率总和（检查是否有套利空间）"""
        if self.is_valid():
            return self.yes_price + self.no_price
        return 0.0


class PriceScraper:
    """价格抓取器 - 稳字当头，用完即关"""
    
    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
    
    async def _init_browser(self) -> BrowserContext:
        """初始化浏览器"""
        self.playwright = await async_playwright().start()
        
        # 随机选择用户代理
        user_agent = random.choice(USER_AGENTS)
        
        self.browser = await self.playwright.chromium.launch(
            headless=BROWSER_HEADLESS,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--window-size=1920,1080',
            ]
        )
        
        context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
        )
        
        # 设置默认超时
        context.set_default_timeout(BROWSER_TIMEOUT)
        
        return context
    
    async def _random_delay(self, min_sec: float = None, max_sec: float = None):
        """随机延迟 - 稳字当头"""
        min_sec = min_sec or NAVIGATION_DELAY_MIN
        max_sec = max_sec or NAVIGATION_DELAY_MAX
        delay = random.uniform(min_sec, max_sec)
        logger.debug(f"延迟 {delay:.2f} 秒")
        await asyncio.sleep(delay)
    
    async def _safe_close_page(self, page: Optional[Page]):
        """安全关闭页面"""
        if page:
            try:
                await page.close()
                logger.debug("页面已关闭")
            except Exception as e:
                logger.debug(f"关闭页面时出错: {e}")
    
    async def _extract_price(self, page: Page, selectors: list) -> Optional[float]:
        """
        从页面提取价格
        尝试多个选择器直到成功
        """
        for selector in selectors:
            try:
                # 等待元素出现
                element = await page.wait_for_selector(selector, timeout=5000)
                if element:
                    text = await element.text_content()
                    if text:
                        # 清理价格文本
                        text = text.strip().replace('$', '').replace('¢', '')
                        text = text.replace(',', '')
                        
                        # 尝试解析为浮点数
                        try:
                            price = float(text)
                            # 如果是美分，转换为美元
                            if price > 1:
                                price = price / 100
                            return price
                        except ValueError:
                            continue
            except Exception as e:
                logger.debug(f"选择器 {selector} 失败: {e}")
                continue
        
        return None
    
    async def _scrape_polymarket(self, url: str, context: BrowserContext) -> MarketPrice:
        """
        抓取 Polymarket 价格
        """
        page = None
        result = MarketPrice(platform="Polymarket", url=url)
        
        try:
            page = await context.new_page()
            logger.info(f"正在打开 Polymarket: {url}")
            
            # 导航到页面
            await page.goto(url, wait_until="networkidle")
            await self._random_delay(3, 5)
            
            # 提取标题
            try:
                title_selectors = POLYMARKET_SELECTORS["market_title"].split(', ')
                for selector in title_selectors:
                    title_elem = await page.query_selector(selector)
                    if title_elem:
                        result.market_title = await title_elem.text_content() or ""
                        result.market_title = result.market_title.strip()
                        break
            except Exception as e:
                logger.warning(f"提取标题失败: {e}")
            
            # 提取 YES 价格
            yes_selectors = POLYMARKET_SELECTORS["yes_price"].split(', ')
            result.yes_price = await self._extract_price(page, yes_selectors)
            
            # 提取 NO 价格
            no_selectors = POLYMARKET_SELECTORS["no_price"].split(', ')
            result.no_price = await self._extract_price(page, no_selectors)
            
            if result.is_valid():
                logger.info(f"Polymarket 价格 - YES: {result.yes_price:.4f}, NO: {result.no_price:.4f}")
            else:
                result.error = "价格提取失败或无效"
                logger.warning(f"Polymarket 价格无效: YES={result.yes_price}, NO={result.no_price}")
            
        except Exception as e:
            result.error = str(e)
            logger.error(f"Polymarket 抓取错误: {e}")
        finally:
            await self._safe_close_page(page)
        
        return result
    
    async def _scrape_opinion(self, url: str, context: BrowserContext) -> MarketPrice:
        """
        抓取 Opinion 价格
        注意：需要根据实际网站结构调整选择器
        """
        page = None
        result = MarketPrice(platform="Opinion", url=url)
        
        try:
            page = await context.new_page()
            logger.info(f"正在打开 Opinion: {url}")
            
            # 导航到页面
            await page.goto(url, wait_until="networkidle")
            await self._random_delay(3, 5)
            
            # 处理可能的弹窗或 Cookie 提示
            try:
                # 尝试接受 cookies（如果有）
                cookie_buttons = [
                    'button:has-text("Accept")',
                    'button:has-text("Accept All")',
                    'button:has-text("I Accept")',
                    '[data-testid="accept-cookies"]',
                ]
                for btn in cookie_buttons:
                    try:
                        await page.click(btn, timeout=3000)
                        logger.debug("已接受 cookies")
                        await asyncio.sleep(1)
                        break
                    except:
                        pass
            except:
                pass
            
            # 提取标题
            try:
                title_selectors = OPINION_SELECTORS["market_title"].split(', ')
                for selector in title_selectors:
                    title_elem = await page.query_selector(selector)
                    if title_elem:
                        result.market_title = await title_elem.text_content() or ""
                        result.market_title = result.market_title.strip()
                        break
            except Exception as e:
                logger.warning(f"提取标题失败: {e}")
            
            # 提取 YES 价格
            yes_selectors = OPINION_SELECTORS["yes_price"].split(', ')
            result.yes_price = await self._extract_price(page, yes_selectors)
            
            # 提取 NO 价格
            no_selectors = OPINION_SELECTORS["no_price"].split(', ')
            result.no_price = await self._extract_price(page, no_selectors)
            
            if result.is_valid():
                logger.info(f"Opinion 价格 - YES: {result.yes_price:.4f}, NO: {result.no_price:.4f}")
            else:
                result.error = "价格提取失败或无效"
                logger.warning(f"Opinion 价格无效: YES={result.yes_price}, NO={result.no_price}")
            
        except Exception as e:
            result.error = str(e)
            logger.error(f"Opinion 抓取错误: {e}")
        finally:
            await self._safe_close_page(page)
        
        return result
    
    async def scrape_prices(
        self, 
        polymarket_url: str, 
        opinion_url: str
    ) -> Tuple[MarketPrice, MarketPrice]:
        """
        抓取两个平台的价格
        
        Returns:
            (Polymarket价格, Opinion价格)
        """
        context = None
        
        try:
            # 初始化浏览器
            context = await self._init_browser()
            
            # 顺序抓取（避免同时打开多个页面）
            # 先抓 Polymarket
            poly_price = await self._scrape_polymarket(polymarket_url, context)
            await self._random_delay(2, 4)
            
            # 再抓 Opinion
            opinion_price = await self._scrape_opinion(opinion_url, context)
            
            return poly_price, opinion_price
            
        except Exception as e:
            logger.error(f"抓取过程中出错: {e}")
            return (
                MarketPrice(platform="Polymarket", error=str(e)),
                MarketPrice(platform="Opinion", error=str(e))
            )
        finally:
            # 清理资源 - 用完即关
            if context:
                try:
                    await context.close()
                    logger.debug("浏览器上下文已关闭")
                except Exception as e:
                    logger.debug(f"关闭上下文时出错: {e}")
    
    async def close(self):
        """关闭浏览器"""
        if self.browser:
            try:
                await self.browser.close()
                logger.debug("浏览器已关闭")
            except Exception as e:
                logger.debug(f"关闭浏览器时出错: {e}")
        
        if self.playwright:
            try:
                await self.playwright.stop()
                logger.debug("Playwright 已停止")
            except Exception as e:
                logger.debug(f"停止 Playwright 时出错: {e}")
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()


async def test_scraper():
    """测试价格抓取器"""
    # 测试 URLs（使用示例 URL，需要替换为实际的）
    test_pairs = [
        {
            "poly_url": "https://polymarket.com/event/example",
            "opinion_url": "https://opinion.org/market/example",
        }
    ]
    
    async with PriceScraper() as scraper:
        for pair in test_pairs:
            poly, opinion = await scraper.scrape_prices(
                pair["poly_url"],
                pair["opinion_url"]
            )
            print(f"\nPolymarket: {poly}")
            print(f"Opinion: {opinion}")


if __name__ == "__main__":
    asyncio.run(test_scraper())
