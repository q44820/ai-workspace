"""
Telegram 警报模块
用于发送套利机会通知
"""
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram Bot 警报类"""
    
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session
    
    async def send_message(
        self, 
        message: str, 
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ) -> bool:
        """
        发送 Telegram 消息
        
        Args:
            message: 消息内容
            parse_mode: 解析模式 (HTML, Markdown, MarkdownV2)
            disable_notification: 是否禁用通知声音
            
        Returns:
            发送是否成功
        """
        if not self.token or not self.chat_id:
            logger.warning("Telegram 配置不完整，跳过发送")
            return False
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("ok"):
                        logger.info("Telegram 消息发送成功")
                        return True
                    else:
                        logger.error(f"Telegram API 错误: {result}")
                else:
                    logger.error(f"Telegram HTTP 错误: {response.status}")
        except Exception as e:
            logger.error(f"发送 Telegram 消息失败: {e}")
        
        return False
    
    async def send_arbitrage_alert(
        self,
        market_name: str,
        polymarket_yes: float,
        polymarket_no: float,
        opinion_yes: float,
        opinion_no: float,
        profit_percentage: float,
        strategy: str,
        polymarket_url: str = "",
        opinion_url: str = "",
    ) -> bool:
        """
        发送套利警报
        
        Args:
            market_name: 市场名称
            polymarket_yes: Polymarket YES 价格
            polymarket_no: Polymarket NO 价格
            opinion_yes: Opinion YES 价格
            opinion_no: Opinion NO 价格
            profit_percentage: 净利润百分比
            strategy: 套利策略描述
            polymarket_url: Polymarket 链接
            opinion_url: Opinion 链接
            
        Returns:
            发送是否成功
        """
        emoji = "🚀" if profit_percentage > 2 else "⚡" if profit_percentage > 1 else "💰"
        
        message = f"""
{emoji} <b>套利机会发现！</b> {emoji}

<b>市场:</b> {market_name}

<b>📊 价格对比:</b>
├─ Polymarket YES: <code>{polymarket_yes:.4f}</code>
├─ Polymarket NO: <code>{polymarket_no:.4f}</code>
├─ Opinion YES: <code>{opinion_yes:.4f}</code>
└─ Opinion NO: <code>{opinion_no:.4f}</code>

<b>🎯 套利策略:</b>
{strategy}

<b>💵 净利润:</b> <code>{profit_percentage:.2f}%</code>

<b>🔗 链接:</b>
<a href="{polymarket_url}">Polymarket</a> | <a href="{opinion_url}">Opinion</a>

<i>时间: {self._get_timestamp()}</i>
"""
        return await self.send_message(message, disable_notification=False)
    
    async def send_error_alert(self, error_message: str, context: str = "") -> bool:
        """发送错误警报"""
        message = f"""
⚠️ <b>监控异常</b>

<b>错误:</b> <code>{error_message}</code>
<b>上下文:</b> {context or 'N/A'}

<i>时间: {self._get_timestamp()}</i>
"""
        return await self.send_message(message, disable_notification=True)
    
    async def send_startup_notification(self) -> bool:
        """发送启动通知"""
        message = f"""
✅ <b>套利监控系统已启动</b>

系统开始监控 Polymarket 和 Opinion 价格差异。

<i>时间: {self._get_timestamp()}</i>
"""
        return await self.send_message(message, disable_notification=True)
    
    async def send_shutdown_notification(self, reason: str = "") -> bool:
        """发送关闭通知"""
        message = f"""
🛑 <b>套利监控系统已停止</b>

<b>原因:</b> {reason or '用户手动停止'}

<i>时间: {self._get_timestamp()}</i>
"""
        return await self.send_message(message, disable_notification=True)
    
    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    async def close(self):
        """关闭会话"""
        if self.session and not self.session.closed:
            await self.session.close()


# 单例实例
telegram_bot = TelegramBot()


async def test_telegram():
    """测试 Telegram 连接"""
    bot = TelegramBot()
    success = await bot.send_message("🧪 测试消息 - 套利监控系统连接正常")
    await bot.close()
    return success


if __name__ == "__main__":
    # 测试 Telegram 连接
    result = asyncio.run(test_telegram())
    print(f"Telegram 测试: {'成功' if result else '失败'}")
