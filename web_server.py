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
from strategies.profit_optimizer import ProfitOptimizer
from strategies.data_sync import start_sync, get_sync_data
from capital_manager import CapitalManager, DEFAULT_USAGE_PCT, DEFAULT_LEVERAGE

app = Flask(__name__)

_price_cache = {"BTC-USDT": 82000, "ETH-USDT": 3200, "SOL-USDT": 180}

# 市场分析引擎
market_analyzer = MarketAnalyzer()

# 资金管理引擎
capital_manager = CapitalManager()

# 盈利优化引擎
profit_optimizer = ProfitOptimizer()

# ─── 全局 bot 实例管理 ─────────────────────────

# 启动 OKX 数据同步（每 30 秒）
start_sync(interval=30)

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
    "take_profit_grids": GRID.get("take_profit_grids", 2),
    "auto_optimize": False,
    "usage_pct": DEFAULT_USAGE_PCT * 100,
    "leverage": DEFAULT_LEVERAGE,
}

# 交易日志
trade_log = []
MAX_LOG = 200


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
        bot.take_profit_grids = rc["take_profit_grids"]
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
    for key in ('symbol', 'mode', 'grid_count', 'price_range_pct', 'amount_per_grid', 'check_interval', 'take_profit_grids', 'auto_optimize', 'usage_pct', 'leverage'):
        if key in data:
            val = data[key]
            if key == 'grid_count' or key == 'amount_per_grid' or key == 'check_interval' or key == 'take_profit_grids':
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

@app.route('/api/profit_analysis')
def api_profit_analysis():
    """盈利分析报告"""
    report = profit_optimizer.get_report()
    # 附加当前 bot 参数
    global bot
    if bot:
        report['current_params'] = {
            'symbol': bot.symbol,
            'grid_count': bot.grid_count,
            'price_range_pct': bot.price_range_pct,
            'amount_per_grid': bot.amount_per_grid,
        }
    else:
        report['current_params'] = dict(runtime_config)
    return jsonify(report)


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


@app.route('/api/trade_history')
def api_trade_history():
    """OKX 今日真实成交历史"""
    sync = get_sync_data()
    if not sync:
        return jsonify({'ok': False, 'trades': [], 'msg': '数据同步尚未完成'})
    return jsonify({
        'ok': True,
        'count': sync['today']['trade_count'],
        'trades': sync['today']['trades'],
        'pnl': sync['today']['pnl'],
        'fees': sync['today']['fees'],
        'net_pnl': sync['today']['net_pnl'],
    })


@app.route('/api/account_summary')
def api_account_summary():
    """OKX 账户汇总 + 权益趋势"""
    sync = get_sync_data()
    if not sync:
        return jsonify({'ok': False, 'msg': '数据同步尚未完成'})
    return jsonify({
        'ok': True,
        'account': sync['account'],
        'positions': sync['positions'],
        'pending_orders': sync['pending_orders'],
        'today': sync['today'],
        'equity_history': sync['equity_history'],
        'timestamp': sync['timestamp'],
    })


@app.route('/deploy', methods=['GET'], strict_slashes=False)
def serve_deploy():
    """NovaGrid 一键部署页面"""
    return render_template('deploy.html')

@app.route('/api/deploy/test', methods=['POST'])
def deploy_test():
    """测试 SSH 连接"""
    data = request.json
    if not data:
        return jsonify({'error': 'no data'})
    try:
        import paramiko
        ip = data.get('ip')
        port = int(data.get('port', 22))
        user = data.get('user')
        password = data.get('pass')
        if not all([ip, user, password]):
            return jsonify({'status': 'error', 'message': '请填写完整信息'})
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=port, username=user, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command('uname -a')
        result = stdout.read().decode().strip()
        client.close()
        return jsonify({'status': 'ok', 'message': '连接成功', 'system': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/deploy/start', methods=['POST'])
def deploy_start():
    """一键部署 NovaGrid"""
    data = request.json
    if not data:
        return jsonify({'error': 'no data'})
    try:
        from deploy_worker import deploy_novagrid
        result = deploy_novagrid(data)
        return jsonify(result)
    except ImportError:
        return jsonify({'status': 'error', 'message': '部署模块未找到'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

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
            # 将已完成交易记录到盈利优化器
            if action.get('action') in ('SELL', 'BUY_COVER'):
                net_pnl = action.get('net_pnl')
                total_fee = action.get('total_fee')
                if net_pnl is not None and total_fee is not None:
                    pnl_val = action.get('pnl', net_pnl + total_fee)
                    profit_optimizer.record_trade(
                        side=action.get('side', 'UNKNOWN'),
                        gross_profit=pnl_val,
                        total_fees=total_fee,
                        entry_price=action.get('entry_price', 0),
                        exit_price=action.get('price', 0),
                        amount=action.get('amount', 0),
                    )
        if not result.get('actions'):
            trade_log.append({'time': ts, 'price': result.get('price'), 'action': 'HOLD'})
            if len(trade_log) > MAX_LOG:
                trade_log[:] = trade_log[-MAX_LOG:]

        # 自动优化：每 10 个 tick 检查一次
        auto_optimize_counter += 1
        if runtime_config.get('auto_optimize') and auto_optimize_counter >= 10:
            auto_optimize_counter = 0
            try:
                # ─── 1. 市场分析 ───
                market_analysis = market_analyzer.analyze(runtime_config['symbol'])
                
                # ─── 2. 资金约束 ───
                cap = None
                try:
                    cap = capital_manager.analyze(
                        runtime_config['symbol'],
                        market_analysis['optimal_grid_count'] if market_analysis else runtime_config['grid_count'],
                        usage_pct=runtime_config.get('usage_pct', 70) / 100,
                        leverage=runtime_config.get('leverage', 3)
                    )
                except Exception:
                    pass

                # ─── 3. 盈利优化分析 ───
                # 从 bot 状态获取 OKX 数据
                okx_data = None
                try:
                    bot_status = bot.get_status()
                    if bot_status.get('okx_unrealized_pnl') is not None:
                        okx_data = {
                            'unrealized_pnl': bot_status['okx_unrealized_pnl'],
                            'realized_pnl': bot_status.get('okx_realized_pnl', 0),
                        }
                except Exception:
                    pass

                profit_result = profit_optimizer.analyze(bot, okx_data)
                profit_suggestions = profit_result.get('suggestions', {}) if profit_result else {}

                # ─── 4. 合并所有建议 ───
                # 优先级：盈利优化 > 市场分析 > 资金约束
                new_params = {}

                # 从市场分析获取基础参数
                if market_analysis:
                    market_grids = market_analysis['optimal_grid_count']
                    market_range = market_analysis['optimal_range_pct'] / 100

                    # 资金约束下的网格数
                    if cap and cap.get('suggested_grid_count', 0) > 0:
                        if cap['suggested_grid_count'] < market_grids:
                            market_grids = max(cap['suggested_grid_count'] // 2 * 2, 4)
                            logger.info(f"[AutoOpt] 资金约束: grid_count {market_analysis['optimal_grid_count']} → {market_grids}")

                    new_params = {
                        'grid_count': market_grids,
                        'price_range_pct': market_range,
                    }

                    if cap and cap.get('amount_per_grid', 0) > 0:
                        new_params['amount_per_grid'] = cap['amount_per_grid']

                # 盈利优化建议覆盖（如果有）
                if profit_suggestions:
                    logger.info(f"[AutoOpt] 盈利优化建议: {profit_suggestions}, 紧迫度: {profit_result.get('urgency', 'normal')}")
                    # 利润优化建议只覆盖市场分析所提供的基础参数
                    for k, v in profit_suggestions.items():
                        new_params[k] = v

                # ─── 5. 应用（仅显著变化时） ───
                if new_params:
                    range_changed = abs(new_params.get('price_range_pct', runtime_config['price_range_pct']) - runtime_config['price_range_pct']) > 0.002
                    grids_changed = new_params.get('grid_count', runtime_config['grid_count']) != runtime_config['grid_count']
                    amount_changed = new_params.get('amount_per_grid', runtime_config.get('amount_per_grid')) != runtime_config.get('amount_per_grid')
                    if range_changed or grids_changed or amount_changed:
                        for k, v in new_params.items():
                            runtime_config[k] = v
                        bot.update_params(new_params)
                        logger.info(f"[AutoOpt] 参数已更新: {new_params}")
            except Exception as e:
                logger.error(f"[AutoOpt] 优化异常: {e}")
                import traceback
                traceback.print_exc()

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
    port = int(os.environ.get('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=False)
