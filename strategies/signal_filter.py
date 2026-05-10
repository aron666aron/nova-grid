# -*- coding: utf-8 -*-
"""
多因子信号过滤器 (Multi-Factor Signal Filter)

仿幻方量化思路：不依赖单一指标，综合多个因子输出方向偏斜信号。

核心：
  1. 趋势因子 (ADX + 方向) — 是否顺势
  2. 动量因子 (RSI) — 超买/超卖反转信号
  3. 成交量因子 — 放量确认/缩量警惕
  4. 交叉因子 (价格 vs EMA) — 趋势跟随
  5. 资金费率因子 — 多空情绪

输出：
  偏斜系数 bias ∈ [-1, +1]
    +1 → 强烈偏多（网格多做多）
     0 → 中性（对称网格）
    -1 → 强烈偏空（网格多做空）
"""
import logging, math
from datetime import datetime
from okx_market import get_kline, get_price

logger = logging.getLogger("signal_filter")

# ── 缓存 ──
_cache = {"result": None, "ts": 0, "ttl": 60}  # 60 秒缓存


def get_signal(symbol="DOGE-USDT", force_refresh=False):
    """获取多因子信号"""
    now = datetime.now().timestamp()

    if not force_refresh and _cache["result"] and (now - _cache["ts"]) < _cache["ttl"]:
        return _cache["result"]

    try:
        klines = get_kline(symbol, bar="5m", limit=100)
    except Exception as e:
        logger.warning(f"获取K线失败: {e}")
        if _cache["result"]:
            return _cache["result"]
        return {"bias": 0, "confidence": "low", "factors": {}, "summary": "数据不足，回退中性"}

    if not klines or len(klines) < 30:
        logger.warning(f"K线不足: {len(klines or [])}")
        return {"bias": 0, "confidence": "low", "factors": {}, "summary": "数据不足，回退中性"}

    # ── 解析数据 ──
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    current_price = closes[-1]

    # 实时价格校正：如果 ticker 与 K线收盘价差异大，刷新 current_price
    try:
        ticker = get_price(symbol)
        if ticker and ticker.get("price"):
            tp = ticker["price"]
            diff_pct = abs(tp - current_price) / max(current_price, 1e-8)
            if diff_pct > 0.003:  # 偏差超过0.3%就校正
                current_price = tp
                factors["_ticker_corrected"] = True
                factors["_ticker_diff_pct"] = round(diff_pct * 100, 2)
    except Exception:
        pass

    factors = {}
    scores = []

    # ─── 1. 趋势因子 (ADX + 方向) ───
    trend, adx = _calc_adx(highs, lows, closes, 14)
    factors["trend"] = {"direction": trend, "adx": round(adx, 1)}
    if adx > 30:
        # 强趋势 → 强力偏向趋势方向
        factor_score = 0.8 if trend == "up" else (-0.8 if trend == "down" else 0)
        factor_weight = 0.35
    elif adx > 20:
        factor_score = 0.4 if trend == "up" else (-0.4 if trend == "down" else 0)
        factor_weight = 0.25
    else:
        factor_score = 0
        factor_weight = 0.15  # 弱趋势下降低权重
    scores.append(("trend", factor_score, factor_weight))

    # ─── 2. RSI 动量因子 (14周期) ───
    rsi = _calc_rsi(closes, 14)
    factors["rsi"] = round(rsi, 1)
    if rsi > 70:
        rsi_score = -0.6   # 超买 → 偏空（网格少做多）
        rsi_weight = 0.20
    elif rsi < 30:
        rsi_score = 0.6    # 超卖 → 偏多
        rsi_weight = 0.20
    elif rsi > 60:
        rsi_score = -0.2   # 偏高
        rsi_weight = 0.10
    elif rsi < 40:
        rsi_score = 0.2    # 偏低
        rsi_weight = 0.10
    else:
        rsi_score = 0
        rsi_weight = 0.05
    scores.append(("rsi", rsi_score, rsi_weight))

    # ─── 3. 成交量因子 ───
    vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    recent_vol = sum(volumes[-3:]) / 3  # 最近3根平均
    vol_ratio = recent_vol / max(vol_ma20, 1e-10)
    factors["volume_ratio"] = round(vol_ratio, 2)

    if vol_ratio > 1.5:
        # 放量 → 确认当前方向
        if trend == "up":
            vol_score = 0.5
        elif trend == "down":
            vol_score = -0.5
        else:
            vol_score = 0
        vol_weight = 0.15
    elif vol_ratio < 0.5:
        # 缩量 → 趋势可能衰竭，反向权重
        if trend == "up":
            vol_score = -0.3
        elif trend == "down":
            vol_score = 0.3
        else:
            vol_score = 0
        vol_weight = 0.10
    else:
        vol_score = 0
        vol_weight = 0.05
    scores.append(("volume", vol_score, vol_weight))

    # ─── 4. EMA 交叉因子 ───
    ema9 = _calc_ema(closes, 9)
    ema21 = _calc_ema(closes, 21)
    factors["ema9"] = round(ema9, 6)
    factors["ema21"] = round(ema21, 6)

    if current_price > ema9 > ema21:
        ema_score = 0.5      # 多头排列
    elif current_price < ema9 < ema21:
        ema_score = -0.5     # 空头排列
    elif current_price > ema9 and current_price > ema21:
        ema_score = 0.2      # 偏多
    elif current_price < ema9 and current_price < ema21:
        ema_score = -0.2     # 偏空
    else:
        ema_score = 0
    factors["ema_signal"] = "bullish" if ema_score > 0 else ("bearish" if ema_score < 0 else "mixed")
    scores.append(("ema", ema_score, 0.20))

    # ─── 5. 价格位置因子 (在最近波动区间的位置) ───
    high_20 = max(closes[-20:])
    low_20 = min(closes[-20:])
    range_20 = high_20 - low_20 if high_20 != low_20 else 1
    position = (current_price - low_20) / range_20  # 0=最低, 1=最高
    factors["range_position"] = round(position, 2)

    if position > 0.8:
        # 接近区间顶部 → 可能有阻力
        position_score = -0.3
    elif position < 0.2:
        # 接近区间底部 → 可能有支撑
        position_score = 0.3
    else:
        position_score = 0
    factors["range_signal"] = "near_top" if position > 0.8 else ("near_bottom" if position < 0.2 else "middle")
    scores.append(("position", position_score, 0.15))

    # ── 计算加权偏斜 ──
    total_weight = sum(w for _, _, w in scores)
    weighted_bias = sum(score * weight for _, score, weight in scores) / max(total_weight, 0.01)

    # 限制范围 [-1, +1]
    bias = max(-1.0, min(1.0, weighted_bias))

    # 置信度评估
    if adx > 25 and abs(rsi - 50) > 10:
        confidence = "high"
    elif adx > 20 or abs(rsi - 50) > 15:
        confidence = "medium"
    else:
        confidence = "low"

    # 自然语言总结
    summary = _generate_summary(bias, factors, confidence)

    result = {
        "bias": round(bias, 3),
        "confidence": confidence,
        "signal": "long" if bias > 0.3 else ("short" if bias < -0.3 else "neutral"),
        "factors": factors,
        "summary": summary,
        "price": current_price,
        "analyzed_at": datetime.now().strftime("%H:%M:%S"),
    }

    _cache["result"] = result
    _cache["ts"] = now
    return result


def _calc_adx(highs, lows, closes, period=14):
    """计算 ADX 和趋势方向"""
    if len(highs) < period + 1:
        return "neutral", 0

    tr_list, plus_dm_list, minus_dm_list = [], [], []

    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr_list.append(max(hl, hc, lc))

        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        plus_dm_list.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm_list.append(down_move if down_move > up_move and down_move > 0 else 0)

    tr_ema = _calc_ema_simple(tr_list, period)
    plus_dm_ema = _calc_ema_simple(plus_dm_list, period)
    minus_dm_ema = _calc_ema_simple(minus_dm_list, period)

    if tr_ema == 0:
        return "neutral", 0

    plus_di = 100 * plus_dm_ema / tr_ema
    minus_di = 100 * minus_dm_ema / tr_ema
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0

    # ADX = smoothed DX
    # For simplicity, use the single-period DX as ADX approximation
    adx = min(dx, 100)

    if plus_di > minus_di and plus_di - minus_di > 3:
        trend = "up"
    elif minus_di > plus_di and minus_di - plus_di > 3:
        trend = "down"
    else:
        trend = "neutral"

    return trend, adx


def _calc_rsi(closes, period=14):
    """RSI 计算"""
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_ema(closes, period):
    """EMA 计算 - 只返回最后一个值"""
    if len(closes) < period:
        return closes[-1]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _calc_ema_simple(values, period):
    """简单 EMA 辅助函数（用于 ADX）"""
    if not values:
        return 0
    data = values[-period:] if len(values) >= period else values
    multiplier = 2 / (period + 1)
    ema = sum(data[:min(period, len(data))]) / min(period, len(data))
    for v in data[min(period, len(data)):]:
        ema = (v - ema) * multiplier + ema
    return ema


def _generate_summary(bias, factors, confidence):
    """生成自然语言总结"""
    parts = []

    if bias > 0.5:
        parts.append("强烈偏多")
    elif bias > 0.2:
        parts.append("偏多")
    elif bias > -0.2:
        parts.append("中性")
    elif bias > -0.5:
        parts.append("偏空")
    else:
        parts.append("强烈偏空")

    if confidence == "high":
        parts.append("（高置信度）")
    elif confidence == "medium":
        parts.append("（中等置信度）")

    # 关键因子说明
    factor_notes = []
    t = factors.get("trend", {})
    if t.get("adx", 0) > 30:
        factor_notes.append(f"强{ {'up':'上涨','down':'下跌','neutral':'震荡'}.get(t.get('direction',''),'')}趋势(ADX={t['adx']})")
    if factors.get("rsi", 0) > 70:
        factor_notes.append("RSI超买")
    elif factors.get("rsi", 0) < 30:
        factor_notes.append("RSI超卖")

    if factor_notes:
        parts.append(" | " + ", ".join(factor_notes))

    return "".join(parts)
