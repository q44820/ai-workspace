"""
套利计算器
计算两个平台之间的对冲套利机会
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from scraper import MarketPrice

from config import POLYMARKET_FEE, OPINION_FEE, MIN_PROFIT_PERCENTAGE

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """套利机会数据结构"""
    market_name: str
    strategy: str  # 套利策略描述
    polymarket_side: str  # "YES" 或 "NO"
    opinion_side: str     # "YES" 或 "NO"
    
    # 原始价格
    polymarket_yes: float
    polymarket_no: float
    opinion_yes: float
    opinion_no: float
    
    # 套利计算
    total_cost: float       # 总成本（每单位收益）
    gross_profit: float     # 毛利润
    net_profit: float       # 净利润（扣除手续费）
    net_profit_pct: float   # 净利润百分比
    
    is_profitable: bool     # 是否有利可图
    
    # 建议投资额
    suggested_investment: float = 1000  # 建议投资金额
    expected_return: float = 0.0        # 预期回报


class ArbitrageCalculator:
    """套利计算器"""
    
    def __init__(
        self,
        polymarket_fee: float = POLYMARKET_FEE,
        opinion_fee: float = OPINION_FEE,
        min_profit_pct: float = MIN_PROFIT_PERCENTAGE
    ):
        self.polymarket_fee = polymarket_fee
        self.opinion_fee = opinion_fee
        self.min_profit_pct = min_profit_pct
    
    def calculate_arbitrage(
        self,
        market_name: str,
        poly_price: MarketPrice,
        opinion_price: MarketPrice,
        suggested_investment: float = 1000
    ) -> Optional[ArbitrageOpportunity]:
        """
        计算套利机会
        
        策略1: 在 Polymarket 买 YES + 在 Opinion 买 NO
        策略2: 在 Polymarket 买 NO + 在 Opinion 买 YES
        
        Returns:
            最优套利机会，如果没有则返回 None
        """
        if not poly_price.is_valid() or not opinion_price.is_valid():
            logger.warning("价格数据无效，无法计算套利")
            return None
        
        # 策略1: Poly YES + Opinion NO
        opportunity1 = self._calculate_strategy(
            market_name=market_name,
            strategy_name="Polymarket YES + Opinion NO",
            poly_side="YES",
            opinion_side="NO",
            poly_yes=poly_price.yes_price,
            poly_no=poly_price.no_price,
            opinion_yes=opinion_price.yes_price,
            opinion_no=opinion_price.no_price,
            suggested_investment=suggested_investment
        )
        
        # 策略2: Poly NO + Opinion YES
        opportunity2 = self._calculate_strategy(
            market_name=market_name,
            strategy_name="Polymarket NO + Opinion YES",
            poly_side="NO",
            opinion_side="YES",
            poly_yes=poly_price.yes_price,
            poly_no=poly_price.no_price,
            opinion_yes=opinion_price.yes_price,
            opinion_no=opinion_price.no_price,
            suggested_investment=suggested_investment
        )
        
        # 选择净利润更高的策略
        if opportunity1 and opportunity2:
            if opportunity1.net_profit_pct >= opportunity2.net_profit_pct:
                return opportunity1
            else:
                return opportunity2
        elif opportunity1:
            return opportunity1
        elif opportunity2:
            return opportunity2
        
        return None
    
    def _calculate_strategy(
        self,
        market_name: str,
        strategy_name: str,
        poly_side: str,
        opinion_side: str,
        poly_yes: float,
        poly_no: float,
        opinion_yes: float,
        opinion_no: float,
        suggested_investment: float
    ) -> Optional[ArbitrageOpportunity]:
        """
        计算特定策略的套利机会
        
        对冲逻辑：
        - 如果事件结果为 YES: Polymarket 赢，Opinion 输
        - 如果事件结果为 NO: Polymarket 输，Opinion 赢
        - 无论结果如何，都能获得固定收益
        """
        # 获取对应的价格
        if poly_side == "YES":
            poly_price = poly_yes
            opinion_price = opinion_no
        else:
            poly_price = poly_no
            opinion_price = opinion_yes
        
        # 计算成本
        # 为了获得 1 美元的收益：
        # - 在 Poly 投入 poly_price 买 poly_side
        # - 在 Opinion 投入 opinion_price 买 opinion_side
        total_cost = poly_price + opinion_price
        
        # 如果成本 >= 1，则无套利空间
        if total_cost >= 1.0:
            return None
        
        # 毛利润
        gross_profit = 1.0 - total_cost
        gross_profit_pct = gross_profit / total_cost * 100
        
        # 计算手续费
        # 假设我们在两个平台各投入等量资金
        # Polymarket 卖出手续费: 2%
        # Opinion 交易手续费: 2%
        total_fee = self.polymarket_fee + self.opinion_fee
        
        # 净利润 = 毛利润 - 手续费
        # 手续费基于总投资金额
        fee_cost = total_cost * total_fee
        net_profit = gross_profit - fee_cost
        net_profit_pct = (net_profit / total_cost) * 100
        
        # 计算预期回报
        expected_return = (suggested_investment / total_cost) * net_profit
        
        # 判断是否有利可图
        is_profitable = net_profit_pct > self.min_profit_pct
        
        opportunity = ArbitrageOpportunity(
            market_name=market_name,
            strategy=strategy_name,
            polymarket_side=poly_side,
            opinion_side=opinion_side,
            polymarket_yes=poly_yes,
            polymarket_no=poly_no,
            opinion_yes=opinion_yes,
            opinion_no=opinion_no,
            total_cost=total_cost,
            gross_profit=gross_profit,
            net_profit=net_profit,
            net_profit_pct=net_profit_pct,
            is_profitable=is_profitable,
            suggested_investment=suggested_investment,
            expected_return=expected_return
        )
        
        logger.info(
            f"策略 '{strategy_name}': 成本={total_cost:.4f}, "
            f"毛利={gross_profit:.4f}({gross_profit_pct:.2f}%), "
            f"净利={net_profit:.4f}({net_profit_pct:.2f}%)"
        )
        
        return opportunity
    
    def format_opportunity(self, opp: ArbitrageOpportunity) -> str:
        """格式化套利机会为可读字符串"""
        lines = [
            f"\n{'='*60}",
            f"🎯 套利机会: {opp.market_name}",
            f"{'='*60}",
            f"策略: {opp.strategy}",
            f"",
            f"价格对比:",
            f"  Polymarket - YES: {opp.polymarket_yes:.4f}, NO: {opp.polymarket_no:.4f}",
            f"  Opinion    - YES: {opp.opinion_yes:.4f}, NO: {opp.opinion_no:.4f}",
            f"",
            f"套利计算:",
            f"  总成本: {opp.total_cost:.4f} (每单位收益)",
            f"  毛利润: {opp.gross_profit:.4f} ({opp.gross_profit_pct:.2f}%)",
            f"  手续费: {(self.polymarket_fee + self.opinion_fee)*100:.0f}%",
            f"  净利润: {opp.net_profit:.4f} ({opp.net_profit_pct:.2f}%)",
            f"",
            f"投资建议:",
            f"  建议投入: ${opp.suggested_investment:.2f}",
            f"  预期回报: ${opp.expected_return:.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# 便捷函数
def quick_calculate(
    poly_yes: float,
    poly_no: float,
    opinion_yes: float,
    opinion_no: float,
    market_name: str = "未知市场"
) -> Optional[ArbitrageOpportunity]:
    """快速计算套利机会"""
    calculator = ArbitrageCalculator()
    
    poly_price = MarketPrice(
        yes_price=poly_yes,
        no_price=poly_no,
        platform="Polymarket"
    )
    opinion_price = MarketPrice(
        yes_price=opinion_yes,
        no_price=opinion_no,
        platform="Opinion"
    )
    
    return calculator.calculate_arbitrage(market_name, poly_price, opinion_price)


if __name__ == "__main__":
    # 测试计算
    opp = quick_calculate(
        poly_yes=0.65,
        poly_no=0.37,
        opinion_yes=0.58,
        opinion_no=0.44,
        market_name="测试市场"
    )
    
    if opp:
        calc = ArbitrageCalculator()
        print(calc.format_opportunity(opp))
    else:
        print("无套利机会")
