#!/usr/bin/env python3
"""
自动策略优化器 - 每 30 分钟运行一次
分析市场 + 检查策略表现 + 自动调整
"""
import json, urllib.request, sys, os, logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from okx_market import get_price, get_kline
from config import FEES

API = 'http://localhost:5000/api'
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'optimizer.log')
os.makedirs(os.path.dirname(LOG), exist_ok=True)

logging.basicConfig(filename=LOG, level=logging.INFO,
                    format='%(asctime)s [OPT] %(message)s')
log = logging.getLogger('optimizer')

def api_get(path):
    try:
        return json.loads(urllib.request.urlopen(f'{API}{path}').read())
    except Exception as e:
        log.error(f"API获取失败 /api{path}: {e}")
        return None

def api_post(path, data):
    try:
        req = urllib.request.Request(f'{API}{path}',
                                     data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'},
                                     method='POST')
        return json.loads(urllib.request.urlopen(req).read())
    except Exception as e:
        log.error(f"API POST失败 /api{path}: {e}")
        return None

def main():
    # 1. 获取当前状态
    cfg = api_get('/config')
    status = api_get('/status')
    if not cfg or not status:
        log.error("无法获取配置/状态")
        return

    mode = cfg.get('mode', 'paper')
    symbol = cfg.get('symbol', 'DOGE-USDT')
    price = status.get('price', 0)
    trades = status.get('daily_trades', 0)
    okx_pnl = status.get('okx_total_pnl', 0)
    log.info(f"当前: {mode} {symbol} @${price} 交易={trades} PnL=${okx_pnl}")

    # 2. 获取 K 线分析波动率
    kline = get_kline(symbol, '5m', limit=24)  # 2小时数据
    if not kline or len(kline) < 6:
        log.warning(f"K线数据不足: {len(kline) if kline else 0}")
        # 降级：使用默认推荐
        new_params = _default_recommendation(cfg)
    else:
        new_params = _analyze_adjust(cfg, kline, trades)

    if new_params:
        log.info(f"推荐调整: {new_params}")
        resp = api_post('/config', new_params)
        log.info(f"应用结果: {resp}")

def _analyze_adjust(cfg, kline, trades):
    """分析 K 线 + 近期表现，推荐参数调整"""
    prices = [float(c[4]) for c in kline]  # close prices
    highs = [float(c[2]) for c in kline]   # high
    lows = [float(c[3]) for c in kline]    # low

    current_price = prices[-1]
    volatility = (max(highs) - min(lows)) / current_price * 100

    # 计算 ATR
    true_ranges = []
    for i in range(1, len(kline)):
        high = float(kline[i][2])
        low = float(kline[i][3])
        prev_close = float(kline[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    atr = sum(true_ranges) / len(true_ranges) / current_price * 100 if true_ranges else 0.3
    log.info(f"波动率={volatility:.3f}% ATR={atr:.3f}% 交易={trades}")

    # 最优网格策略
    # 步长至少是手续费的 3 倍 (0.02% * 3 = 0.06%)
    min_step_pct = FEES['default'] * 3 * 100  # 0.06%

    # 网格范围 = 1.5 × ATR（确保价格在范围内运动的概率足够）
    range_pct = max(atr * 1.5, min_step_pct * 4)
    range_pct = min(range_pct, 5.0)  # 不超过 5%

    # 网格数 = range / min_step, 向下取偶
    ideal_grids = int(range_pct / min_step_pct)
    ideal_grids = max(ideal_grids // 2 * 2, 4)

    current_grids = cfg.get('grid_count', 20)
    current_range = cfg.get('price_range_pct', 0.02) * 100

    # 如果近期没交易且波动率也低 → 减少网格数（加宽间距，提高触发概率）
    if trades == 0 and volatility < atr * 0.5:
        ideal_grids = max(ideal_grids // 2, 4)
        log.info(f"无交易+低波动: 缩减网格至 {ideal_grids}")

    # 如果波动率很高 → 加宽范围
    if volatility > 3.0:
        range_pct = min(volatility * 1.2, 10.0)
        ideal_grids = int(range_pct / min_step_pct)
        ideal_grids = max(ideal_grids // 2 * 2, 4)
        log.info(f"高波动({volatility:.1f}%): 范围扩大至 ±{range_pct:.3f}%")

    # 资金约束
    # 用小账户时限制最大网格数防止爆仓
    max_grids_by_balance = 30

    final_grids = min(ideal_grids, max_grids_by_balance)
    final_range = range_pct / 100

    # 只有当变化显著时才调整
    if abs(final_grids - current_grids) < 2 and abs(final_range - current_range) < 0.005:
        log.info(f"参数已最优，无需调整 ({final_grids}格 ±{range_pct:.3f}%)")
        return None

    return {
        'grid_count': final_grids,
        'price_range_pct': round(final_range, 5),
    }

def _default_recommendation(cfg):
    """K线数据不足时使用默认推荐"""
    current_grids = cfg.get('grid_count', 20)
    current_range = cfg.get('price_range_pct', 0.02) * 100
    if current_grids == 12 and abs(current_range - 1.5) < 0.5:
        return None
    return {'grid_count': 12, 'price_range_pct': 0.015}

if __name__ == '__main__':
    main()
