# -*- coding: utf-8 -*-
"""
市场分析引擎 - 波动率/趋势检测 + 网格参数自动优化

核心指标:
  ATR (Average True Range)  → 波动率
  ADX (Average Directional Index) → 趋势强度
  +DI/-DI → 短/长期方向

输出:
  动态推荐网格参数（范围、格数、步长）
"""
import logging, math
from datetime import datetime
from okx_market import get_kline

logger = logging.getLogger("market_analyzer")

# 手续费参数（用于最小步长计算）
FEE_ROUNDTRIP = 0.0004  # 0.04% (maker 0.02% × 2)
MIN_PROFIT_MULTIPLIER = 3  # 每格利润至少是手续费的 3 倍
MIN_STEP_PCT = FEE_ROUNDTRIP * MIN_PROFIT_MULTIPLIER  # 0.12%

# 默认参数范围
MIN_GRID_COUNT = 4
MAX_GRID_COUNT = 60
MIN_RANGE_PCT = 0.005  # 0.5%
MAX_RANGE_PCT = 0.10   # 10%


class MarketAnalyzer:
    def __init__(self):
        self.cache = {}  # {symbol: {result, timestamp}}

    def analyze(self, symbol):
        """主入口：获取市场数据 → 分析 → 输出最优参数"""
        now = datetime.now().timestamp()
        cached = self.cache.get(symbol)
        # 缓存 5 分钟
        if cached and (now - cached["ts"]) < 300:
            return cached["result"]

        try:
            klines = get_kline(symbol, bar="1H", limit=50)
        except Exception as e:
            logger.warning(f"获取K线失败: {e}")
            return None

        if not klines or len(klines) < 20:
            logger.warning(f"K线数据不足: {len(klines or [])}")
            return None

        # OKX candle format: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        current_price = float(klines[-1][4])
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]

        # ─── 计算指标 ───

        # ATR (period=14)
        tr_values = []
        for i in range(1, len(klines)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr_values.append(max(hl, hc, lc))

        atr_14 = self._ema(tr_values, 14)[-1] if len(tr_values) >= 14 else sum(tr_values[-14:]) / 14
        volatility_pct = atr_14 / current_price

        # ADX + 趋势方向 (period=14)
        trend, adx = self._calc_adx(highs, lows, closes, 14)

        # ─── 优化参数 ───

        # 范围 = 2× ATR（覆盖 95% 价格波动）
        range_pct = min(max(volatility_pct * 2, MIN_RANGE_PCT), MAX_RANGE_PCT)

        # 最小步长确保每笔利润 > 手续费的 3 倍
        min_step = MIN_STEP_PCT

        # 网格数 = 范围 / 最小步长（取偶数）
        ideal_count = int(range_pct / min_step)
        grid_count = min(max(ideal_count // 2 * 2, MIN_GRID_COUNT), MAX_GRID_COUNT)
        actual_step_pct = range_pct / grid_count

        # 趋势调整
        if adx > 25:
            # 强趋势 → 扩大范围 50% 减少被趋势吃掉的风险
            range_pct = min(range_pct * 1.5, MAX_RANGE_PCT)
            grid_count = min(max(int(range_pct / actual_step_pct) // 2 * 2, MIN_GRID_COUNT), MAX_GRID_COUNT)
            actual_step_pct = range_pct / grid_count

        result = {
            "current_price": round(current_price, 8),
            "volatility_pct": round(volatility_pct * 100, 2),
            "atr": round(atr_14, 8),
            "trend": trend,  # "up" / "down" / "neutral"
            "adx": round(adx, 1),
            "optimal_range_pct": round(range_pct * 100, 1),
            "optimal_grid_count": grid_count,
            "optimal_step_pct": round(actual_step_pct * 100, 3),
            "step_vs_fee_ratio": round(actual_step_pct / FEE_ROUNDTRIP, 1),
            "optimal_amount_per_doge": self._suggest_amount(current_price),
            "analyzed_at": datetime.now().strftime("%H:%M:%S"),
        }

        self.cache[symbol] = {"result": result, "ts": now}
        logger.info(f"[{symbol}] 波动率={result['volatility_pct']}% "
                     f"趋势={trend}(ADX={adx:.1f}) "
                     f"推荐: ±{result['optimal_range_pct']}% / {result['optimal_grid_count']}格")
        return result

    def _ema(self, values, period):
        """指数移动平均"""
        if not values:
            return []
        multiplier = 2 / (period + 1)
        ema = [values[0]]
        for v in values[1:]:
            ema.append((v - ema[-1]) * multiplier + ema[-1])
        return ema

    def _calc_adx(self, highs, lows, closes, period=14):
        """计算 ADX 和趋势方向"""
        if len(highs) < period + 1:
            return "neutral", 0

        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr_list.append(max(hl, hc, lc))

            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]
            plus_dm_list.append(up_move if up_move > down_move and up_move > 0 else 0)
            minus_dm_list.append(down_move if down_move > up_move and down_move > 0 else 0)

        # Smooth with EMA
        tr_ema = self._ema(tr_list, period)[-1]
        plus_dm_ema = self._ema(plus_dm_list, period)[-1]
        minus_dm_ema = self._ema(minus_dm_list, period)[-1]

        if tr_ema == 0:
            return "neutral", 0

        plus_di = 100 * plus_dm_ema / tr_ema
        minus_di = 100 * minus_dm_ema / tr_ema
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0

        # ADX = EMA of DX
        dx_values = []
        for i in range(period + 1, len(tr_list) + 1):
            tr_ema_i = self._ema(tr_list[:i], period)[-1]
            pd_ema_i = self._ema(plus_dm_list[:i], period)[-1]
            md_ema_i = self._ema(minus_dm_list[:i], period)[-1]
            if tr_ema_i > 0 and (pd_ema_i + md_ema_i) > 0:
                dx_i = 100 * abs(pd_ema_i - md_ema_i) / (pd_ema_i + md_ema_i)
            else:
                dx_i = 0
            dx_values.append(dx_i)

        adx = self._ema(dx_values, period)[-1] if len(dx_values) >= period else sum(dx_values[-14:]) / 14
        adx = min(adx, 100)

        # 趋势方向
        if plus_di > minus_di and plus_di - minus_di > 5:
            trend = "up"
        elif minus_di > plus_di and minus_di - plus_di > 5:
            trend = "down"
        else:
            trend = "neutral"

        return trend, adx

    def _suggest_amount(self, price):
        """根据价格推荐每格数量"""
        if price > 10000:    # BTC
            return 0.001
        elif price > 1000:   # ETH
            return 0.01
        elif price > 100:    # SOL
            return 0.5
        elif price > 1:      # 
            return 20
        else:                # DOGE
            return 200
