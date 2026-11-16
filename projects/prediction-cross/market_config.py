"""
市场配置工具
帮助添加和管理监控的市场对
"""
import json
from typing import List, Dict
from dataclasses import dataclass, asdict


@dataclass
class MarketPair:
    """市场对数据结构"""
    name: str
    polymarket_url: str
    opinion_url: str
    keywords: List[str]
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "MarketPair":
        return cls(**data)


class MarketConfigManager:
    """市场配置管理器"""
    
    def __init__(self, config_file: str = "markets.json"):
        self.config_file = config_file
        self.markets: List[MarketPair] = []
        self.load()
    
    def load(self):
        """加载市场配置"""
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
                self.markets = [MarketPair.from_dict(m) for m in data]
        except FileNotFoundError:
            self.markets = []
    
    def save(self):
        """保存市场配置"""
        data = [m.to_dict() for m in self.markets]
        with open(self.config_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def add_market(self, market: MarketPair):
        """添加市场"""
        self.markets.append(market)
        self.save()
    
    def remove_market(self, name: str):
        """删除市场"""
        self.markets = [m for m in self.markets if m.name != name]
        self.save()
    
    def list_markets(self) -> List[MarketPair]:
        """列出所有市场"""
        return self.markets
    
    def generate_config_code(self) -> str:
        """生成 config.py 中的 MARKET_PAIRS 代码"""
        lines = ["# 在 config.py 中添加以下内容到 MARKET_PAIRS 列表:"]
        lines.append("")
        
        for market in self.markets:
            lines.append("    {")
            lines.append(f'        "name": "{market.name}",')
            lines.append(f'        "polymarket_url": "{market.polymarket_url}",')
            lines.append(f'        "opinion_url": "{market.opinion_url}",')
            lines.append(f'        "keywords": {market.keywords},')
            lines.append("    },")
        
        return "\n".join(lines)


def interactive_setup():
    """交互式设置"""
    print("="*60)
    print("市场配置工具")
    print("="*60)
    
    manager = MarketConfigManager()
    
    while True:
        print("\n选项:")
        print("1. 添加市场")
        print("2. 删除市场")
        print("3. 列出市场")
        print("4. 生成配置代码")
        print("5. 退出")
        
        choice = input("\n选择: ").strip()
        
        if choice == "1":
            name = input("市场名称: ").strip()
            poly_url = input("Polymarket URL: ").strip()
            opinion_url = input("Opinion URL: ").strip()
            keywords = input("关键词（逗号分隔）: ").strip().split(",")
            keywords = [k.strip() for k in keywords]
            
            market = MarketPair(name, poly_url, opinion_url, keywords)
            manager.add_market(market)
            print(f"✅ 已添加: {name}")
            
        elif choice == "2":
            markets = manager.list_markets()
            if not markets:
                print("没有可删除的市场")
                continue
            
            print("\n市场列表:")
            for i, m in enumerate(markets, 1):
                print(f"{i}. {m.name}")
            
            name = input("输入要删除的市场名称: ").strip()
            manager.remove_market(name)
            print(f"✅ 已删除: {name}")
            
        elif choice == "3":
            markets = manager.list_markets()
            if not markets:
                print("没有配置的市场")
            else:
                print("\n市场列表:")
                for i, m in enumerate(markets, 1):
                    print(f"\n{i}. {m.name}")
                    print(f"   Polymarket: {m.polymarket_url}")
                    print(f"   Opinion: {m.opinion_url}")
                    print(f"   关键词: {', '.join(m.keywords)}")
                    
        elif choice == "4":
            code = manager.generate_config_code()
            print("\n" + "="*60)
            print(code)
            print("="*60)
            
        elif choice == "5":
            break
        else:
            print("无效选项")


if __name__ == "__main__":
    interactive_setup()
