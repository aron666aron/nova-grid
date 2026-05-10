# -*- coding: utf-8 -*-
"""量化交易 Web 看板 - 支持运行时切换币种和模式"""
import sys, os, json, time, threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SYMBOLS, PAPER_TRADING, OKX_API_KEY, GRID
from okx_market import get_price, get_kline
from strategies.grid_bot import GridBot
from strategies.market_analyzer import MarketAnalyzer
from strategies.signal_filter import get_signal as _get_signal
from capital_manager import CapitalManager, DEFAULT_USAGE_PCT, DEFAULT_LEVERAGE

app = Flask(__name__)

_price_cache = {"BTC-USDT": 82000, "ETH-USDT": 3200, "SOL-USDT": 180}

# 市场分析引擎
market_analyzer = MarketAnalyzer()

# 资金管理引擎
capital_manager = CapitalManager()

# ─── 全局 bot 实例管理 ─────────────────────────
bot = None
bot_running = False
bot_thread = None
bot_lock = threading.Lock()

# 当前运行时配置（独立于 config.py，支持运行时修改）
runtime_config = {
    "symbol": GRID.get("symbol", "DOGE-USDT"),
    "mode": "paper" if PAPER_TRADING else "live",
    "grid_count": GRID.get("grid_count", 20),
    "price_range_pct": GRID.get("price_range_pct", 0.02),
    "amount_per_grid": GRID.get("amount_per_grid", 200),
    "check_interval": GRID.get("check_interval", 3),
    "auto_optimize": True,
    "usage_pct": DEFAULT_USAGE_PCT * 100,
    "leverage": DEFAULT_LEVERAGE,
}

# 交易日志
trade_log = []
MAX_LOG = 200

# 自动优化状态追踪（用于检测参数变化）
_last_report = {"grid_count": 0, "range_pct": 0, "amount_per_grid": 0}
_analysis_counter = 0
# 飞书 Webhook URL（可选，在仪表盘设置）
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")


def _init_bot(force_new=False):
    """获取或创建 bot 实例"""
    global bot
    rc = runtime_config
    if bot is None or force_new:
        paper = rc["mode"] == "paper"
        bot = GridBot(paper_mode=paper)
        bot.symbol = rc["symbol"]
        bot.grid_count = rc["grid_count"]
        bot.price_range_pct = rc["price_range_pct"]
        bot.amount_per_grid = rc["amount_per_grid"]
        bot.check_interval = rc["check_interval"]
    return bot


def _stop_bot():
    """停止 bot 循环"""
    global bot_running, bot_thread
    bot_running = False
    if bot_thread and bot_thread.is_alive():
        bot_thread.join(timeout=5)
    bot_thread = None


def _start_bot():
    """启动 bot 循环"""
    global bot_running, bot_thread
    with bot_lock:
        _stop_bot()
        _init_bot(force_new=True)
        bot_running = True
        bot_thread = threading.Thread(target=_bot_loop, daemon=True)
        bot_thread.start()


# ============================================================
# 页面
# ============================================================

@app.route('/')
def index():
    return render_template('dashboard.html')


# ============================================================
# API - 状态
# ============================================================

@app.route('/api/status')
def api_status():
    global bot
    if bot is None:
        return jsonify({'error': 'bot not initialized', 'running': False})
    try:
        status = bot.get_status()
    except Exception:
        sym = bot.symbol
        status = {
            'symbol': sym, 'price': _price_cache.get(sym, 0),
            'grids': bot.grid_count, 'active_positions': 0,
            'daily_trades': 0, 'total_pnl': 0,
            'price_range': [0, 0],
        }
    return jsonify({
        'mode': 'live' if not bot.paper_mode else 'paper',
        'api_configured': bool(OKX_API_KEY),
        'symbol': bot.symbol,
        'price': status.get('price'),
        'grids': status.get('grids', bot.grid_count),
        'price_lower': status.get('price_range', [0, 0])[0],
        'price_upper': status.get('price_range', [0, 0])[1],
        'active_positions': status.get('active_positions', 0),
        'long_positions': status.get('long_positions', 0),
        'short_positions': status.get('short_positions', 0),
        'daily_trades': status.get('daily_trades', 0),
        'long_pnl': status.get('long_pnl', 0),
        'short_pnl': status.get('short_pnl', 0),
        'long_fees': status.get('long_fees', 0),
        'short_fees': status.get('short_fees', 0),
        'total_pnl': status.get('total_pnl', 0),
        'total_fees': status.get('total_fees', 0),
        'net_pnl': status.get('net_pnl', 0),
        # 实盘 OKX 真实数据
        'okx_unrealized_pnl': status.get('okx_unrealized_pnl', 0),
        'okx_realized_pnl': status.get('okx_realized_pnl', 0),
        'okx_total_pnl': status.get('okx_total_pnl', 0),
        'okx_long_sz': status.get('okx_long_sz', 0),
        'okx_long_entry': status.get('okx_long_entry', 0),
        'okx_long_upl': status.get('okx_long_upl', 0),
        'okx_short_sz': status.get('okx_short_sz', 0),
        'okx_short_entry': status.get('okx_short_entry', 0),
        'okx_short_upl': status.get('okx_short_upl', 0),
        'running': bot_running,
    })


# ============================================================
# API - 运行时配置
# ============================================================

@app.route('/api/config', methods=['GET'])
def api_get_config():
    return jsonify({
        **runtime_config,
        'available_symbols': list(SYMBOLS.keys()),
    })


@app.route('/api/config', methods=['POST'])
def api_set_config():
    """更新配置并重启 bot"""
    data = request.json
    if not data:
        return jsonify({'error': 'no data'}), 400

    changed = False
    for key in ('symbol', 'mode', 'grid_count', 'price_range_pct', 'amount_per_grid', 'check_interval', 'auto_optimize', 'usage_pct', 'leverage', 'feishu_webhook'):
        if key in data:
            val = data[key]
            if key == 'grid_count' or key == 'amount_per_grid' or key == 'check_interval':
                val = int(val)
            elif key == 'price_range_pct':
                val = float(val)
            runtime_config[key] = val
            changed = True

    # 验证币种
    if runtime_config['symbol'] not in SYMBOLS:
        return jsonify({'error': f"不支持的币种: {runtime_config['symbol']}"}), 400

    # 验证模式
    if runtime_config['mode'] not in ('paper', 'live'):
        return jsonify({'error': 'mode 必须是 paper 或 live'}), 400

    if changed:
        trade_log.clear()
        _start_bot()

    return jsonify({'status': 'ok', 'config': dict(runtime_config)})


# ============================================================
# API - 市场分析
# ============================================================

@app.route('/api/market_analysis')
def api_market_analysis():
    """市场分析 + 参数推荐"""
    sym = runtime_config['symbol']
    result = market_analyzer.analyze(sym)
    if not result:
        # 降级：返回当前配置作为推荐
        result = {
            'current_price': 0,
            'volatility_pct': 0,
            'atr': 0,
            'trend': 'unknown',
            'adx': 0,
            'optimal_range_pct': runtime_config['price_range_pct'] * 100,
            'optimal_grid_count': runtime_config['grid_count'],
            'optimal_step_pct': 0,
            'step_vs_fee_ratio': 0,
            'optimal_amount_per_doge': 200,
            'analyzed_at': '--',
        }
    # 获取资金约束，计算协调后的推荐
    constrained_grids = result.get('optimal_grid_count', runtime_config['grid_count'])
    constrained_range = result.get('optimal_range_pct', runtime_config['price_range_pct'] * 100)
    try:
        cap = capital_manager.analyze(
            sym,
            constrained_grids,
            usage_pct=runtime_config.get('usage_pct', 70) / 100,
            leverage=runtime_config.get('leverage', 3)
        )
        if cap and 'suggested_grid_count' in cap:
            mg = constrained_grids
            cg = cap['suggested_grid_count']
            if cg < mg:
                step = constrained_range / mg if mg > 0 else 1
                constrained_grids = max(cg // 2 * 2, 4)
                constrained_range = round(step * constrained_grids, 1)
    except Exception:
        cap = None

    return jsonify({
        'analysis': result,
        'capital': cap,
        'constrained': {
            'grid_count': constrained_grids,
            'range_pct': constrained_range,
        },
        'current': {
            'symbol': runtime_config['symbol'],
            'mode': runtime_config['mode'],
            'range_pct': runtime_config['price_range_pct'] * 100,
            'grid_count': runtime_config['grid_count'],
        },
        'auto_optimize': runtime_config.get('auto_optimize', False),
    })


# ============================================================
# API - 资金分析
# ============================================================

@app.route('/api/capital_analysis')
def api_capital_analysis():
    """账户余额 + 资金分配建议"""
    sym = runtime_config['symbol']
    gc = runtime_config['grid_count']
    usage = runtime_config.get('usage_pct', DEFAULT_USAGE_PCT * 100) / 100
    lev = runtime_config.get('leverage', DEFAULT_LEVERAGE)
    result = capital_manager.analyze(sym, gc, usage_pct=usage, leverage=lev)
    return jsonify({
        'analysis': result,
        'current_config': {
            'usage_pct': usage * 100,
            'leverage': lev,
        }
    })


# ============================================================
# API - 其他
# ============================================================

@app.route('/api/positions')
def api_positions():
    global bot
    if not bot:
        return jsonify([])
    positions = []
    for key, pos in sorted(bot.positions.items(), key=lambda x: int(x[0])):
        grid_price = bot.grids[int(key)] if bot.grids and int(key) < len(bot.grids) else 0
        positions.append({
            'grid': int(key),
            'price': grid_price,
            'side': pos.get('side'),
            'entry_price': pos.get('entry_price'),
            'amount': pos.get('amount'),
            'time': pos.get('time'),
        })
    return jsonify(positions)


@app.route('/api/grids')
def api_grids():
    global bot
    if not bot:
        return jsonify({'grids': [], 'lower': 0, 'upper': 0, 'current_price': 0})
    try:
        market = get_price(bot.symbol)
    except Exception:
        market = None
    current_price = (market or {}).get('price') or _price_cache.get(bot.symbol, 0)
    grids = []
    for i, price in enumerate(bot.grids):
        pos = bot.positions.get(str(i))
        grids.append({
            'index': i,
            'price': price,
            'has_position': pos is not None,
            'side': pos.get('side') if pos else None,
        })
    lower = bot.grids[0] if bot.grids else 0
    upper = bot.grids[-1] if bot.grids else 0
    return jsonify({'current_price': current_price, 'grids': grids, 'lower': lower, 'upper': upper})


@app.route('/api/kline')
def api_kline():
    symbol = request.args.get('symbol', 'btcusdt')
    period = request.args.get('period', '1hour')
    size = int(request.args.get('size', '100'))
    try:
        data = get_kline(symbol, period=period, size=size)
    except Exception:
        data = []
    klines = []
    for k in data:
        klines.append({
            'time': datetime.fromtimestamp(k['id']).strftime('%m-%d %H:%M'),
            'open': k['open'], 'high': k['high'],
            'low': k['low'], 'close': k['close'], 'vol': k['vol'],
        })
    return jsonify(klines)


@app.route('/api/signal')
def api_signal():
    """多因子信号过滤"""
    sym = runtime_config['symbol']
    sig = _get_signal(sym, force_refresh=True)
    return jsonify({
        'signal': sig,
        'running_bot': bot_running,
    })


@app.route('/api/trade_log')
def api_trade_log():
    return jsonify(list(reversed(trade_log[-100:])))


@app.route('/api/balance')
def api_balance():
    global bot
    if not bot or bot.paper_mode:
        return jsonify({'error': '模拟模式'})
    try:
        from okx_trade import get_account
        bal = get_account()
        return jsonify(bal)
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/control', methods=['POST'])
def api_control():
    action = request.json.get('action')
    if action == 'start':
        _start_bot()
        return jsonify({'status': 'started'})
    elif action == 'stop':
        _stop_bot()
        return jsonify({'status': 'stopped'})
    return jsonify({'status': 'no-op'})


# ============================================================
# 飞书 Webhook 通知
# ============================================================

def _send_feishu_notify(msg, webhook_url=None):
    url = webhook_url or runtime_config.get("feishu_webhook", "")
    if not url:
        return False
    try:
        import urllib.request
        payload = json.dumps({"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": "🤖 NovaGrid 优化报告"},
                       "template": "blue"},
            "elements": [{"tag": "markdown", "content": msg}]
        }}).encode()
        req = urllib.request.Request(url, data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def _run_optimize():
    """运行一次完整优化分析，返回是否有参数变更"""
    global _last_report, _analysis_counter
    sym = runtime_config["symbol"]

    try:
        analysis = market_analyzer.analyze(sym)
    except Exception:
        return False
    if not analysis:
        return False

    market_grids = analysis.get("optimal_grid_count", runtime_config["grid_count"])
    market_range = analysis.get("optimal_range_pct", runtime_config["price_range_pct"] * 100) / 100

    # 资金约束
    try:
        cap = capital_manager.analyze(
            sym, market_grids,
            usage_pct=runtime_config.get("usage_pct", 70) / 100,
            leverage=runtime_config.get("leverage", 3))
    except Exception:
        cap = None

    # 计算最终参数
    final_grids = market_grids
    if cap and cap.get("suggested_grid_count", 0) > 0:
        final_grids = min(market_grids, cap["suggested_grid_count"])
        final_grids = max(final_grids // 2 * 2, 4)

    final_range = market_range
    final_amount = runtime_config["amount_per_grid"]
    if cap and cap.get("amount_per_grid", 0) > 0:
        final_amount = cap["amount_per_grid"]

    new_params = {"grid_count": final_grids, "price_range_pct": final_range}
    if cap and cap.get("amount_per_grid", 0) > 0:
        new_params["amount_per_grid"] = final_amount

    # 检测变化
    range_changed = abs(final_range - _last_report["range_pct"]) > 0.001
    grids_changed = final_grids != _last_report["grid_count"]
    amount_changed = final_amount != _last_report["amount_per_grid"]
    anything_changed = range_changed or grids_changed or amount_changed

    # 更新报告缓存
    _last_report = {"grid_count": final_grids, "range_pct": final_range, "amount_per_grid": final_amount}
    
    auto_enabled = runtime_config.get("auto_optimize", False)

    if anything_changed:
        # 构造通知消息
        changes = []
        if grids_changed:
            changes.append(f"网格数: {_last_report['grid_count']} → {final_grids}")
        if amount_changed:
            changes.append(f"每格数量: {_last_report['amount_per_grid']} → {final_amount} DOGE")
        if range_changed:
            changes.append(f"范围: {_last_report['range_pct']*100:.1f}% → {final_range*100:.1f}%")

        msg_lines = [
            f"**{sym}** 当前价格 **${analysis.get('current_price', 0):.5f}**",
            f"📊 波动率: {analysis.get('volatility_pct', 0)}%  |  ADX: {analysis.get('adx', 0)}  |  趋势: {analysis.get('trend', '--')}",
            f"""
📋 **推荐调整：**""",
        ] + [f"  • {c}" for c in changes]
        
        if auto_enabled:
            msg_lines.append(f"\n✅ 已自动应用调整")
        else:
            msg_lines.append(f"\n⚙️ 自动优化已关闭，请在仪表盘手动应用")

        msg = "\n".join(msg_lines)
        _send_feishu_notify(msg)
        print(f"[OPTIMIZE] {changes}")

        if auto_enabled:
            for k, v in new_params.items():
                runtime_config[k] = v
            bot.update_params(new_params)
            return True

    return False


# ============================================================
# 后台循环
# ============================================================

def _bot_loop():
    global bot_running
    auto_optimize_counter = 0
    while bot_running:
        try:
            result = bot.run_tick()
        except Exception as e:
            import traceback
            traceback.print_exc()
            time.sleep(bot.check_interval)
            continue

        ts = datetime.now().strftime('%H:%M:%S')
        for action in result.get('actions', []):
            entry = {**action, 'time': ts}
            trade_log.append(entry)
            if len(trade_log) > MAX_LOG:
                trade_log[:] = trade_log[-MAX_LOG:]
        if not result.get('actions'):
            trade_log.append({'time': ts, 'price': result.get('price'), 'action': 'HOLD'})
            if len(trade_log) > MAX_LOG:
                trade_log[:] = trade_log[-MAX_LOG:]

        # 自动优化：每 600 个 tick (~30分钟) 做一次完整分析
        auto_optimize_counter += 1
        if auto_optimize_counter >= 600:
            auto_optimize_counter = 0
            try:
                _run_optimize()
            except Exception:
                pass

        time.sleep(bot.check_interval)


# ============================================================
# 启动
# ============================================================

def _auto_start():
    _init_bot()
    global bot_running, bot_thread
    bot_running = True
    bot_thread = threading.Thread(target=_bot_loop, daemon=True)
    bot_thread.start()
    print("  Bot auto-started (paper mode)")

if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("  Dual Grid Bot Dashboard")
    print("  URL: http://localhost:5000")
    print(f"  Symbol: {runtime_config['symbol']}, Mode: {runtime_config['mode']}")
    print("=" * 50 + "\n")
    _auto_start()
    PORT = int(os.environ.get('PORT', 5002))
app.run(host='0.0.0.0', port=PORT, debug=False)
