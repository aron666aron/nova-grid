# -*- coding: utf-8 -*-
"""回测引擎 - 用历史K线数据验证策略"""
import json, os
from datetime import datetime
from config import DATA_DIR, BACKTEST_DIR, SYMBOLS
from htx_market import get_kline

class BacktestEngine:
    def __init__(self, symbol="btcusdt"):
        self.symbol = symbol
        self.data = []
        self.results = []

    def load_data(self, period="4hour", size=500):
        """加载历史K线数据"""
        klines = get_kline(self.symbol, period=period, size=size)
        if klines:
            self.data = klines
            print(f"加载 {len(klines)} 根K线 ({period})")
            return True
        return False

    def run_grid_backtest(self, price_lower=None, price_upper=None, grid_count=10):
        """回测网格策略"""
        if not self.data:
            print("请先加载数据")
            return

        close_prices = [k["close"] for k in reversed(self.data)]

        if price_lower is None:
            price_lower = min(close_prices) * 1.05
        if price_upper is None:
            price_upper = max(close_prices) * 0.95

        step = (price_upper - price_lower) / grid_count
        grids = [round(price_lower + step * i, 2) for i in range(grid_count + 1)]

        capital = 0
        position = 0
        trades = []

        for price in close_prices:
            for i, grid in enumerate(grids):
                if abs(price - grid) / grid < 0.001:
                    if position == 0:
                        position = grid
                        trades.append({"type": "BUY", "price": price, "grid": grid})
                    elif price > position * 1.005:
                        pnl = price - position
                        capital += pnl
                        trades.append({"type": "SELL", "price": price, "pnl": round(pnl, 2)})
                        position = 0

        self.results = {
            "symbol": self.symbol,
            "period": f"{len(close_prices)}根K线",
            "price_range": [round(price_lower, 2), round(price_upper, 2)],
            "total_trades": len(trades),
            "total_pnl": round(capital, 4),
            "trades": trades[-20:],  # 最近20笔
        }
        return self.results

    def save_report(self):
        """保存回测报告"""
        if not self.results:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(BACKTEST_DIR, f"report_{self.symbol}_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"回测报告已保存: {path}")
        return path

    def summary(self):
        """打印回测摘要"""
        if not self.results:
            return "无回测结果"
        r = self.results
        lines = [
            f"\n{'='*40}",
            f" 回测报告 - {r['symbol']}",
            f"{'='*40}",
            f" 数据范围: {r['period']}",
            f" 价格区间: {r['price_range'][0]} ~ {r['price_range'][1]}",
            f" 交易次数: {r['total_trades']}",
            f" 总盈亏:   {r['total_pnl']} USDT",
            f"{'='*40}",
        ]
        return "\n".join(lines)
