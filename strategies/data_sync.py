# -*- coding: utf-8 -*-
"""
OKX 真实数据同步模块

独立线程运行，每 30 秒拉一次 OKX 真实数据（持仓、挂单、成交、账户），
写入 data/okx_sync.json，供前端 API 和 bot 内部使用。
"""
import json, os, time, logging, threading
from datetime import datetime

logger = logging.getLogger("data_sync")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SYNC_FILE = os.path.join(DATA_DIR, "okx_sync.json")
os.makedirs(DATA_DIR, exist_ok=True)

# 历史权益点（用于趋势图）
EQUITY_HISTORY = []
MAX_EQUITY_POINTS = 480  # 480 点 × 30 秒 = 4 小时


def _okx_get(path):
    """调用 OKX API，返回解析后的 dict"""
    try:
        from okx_trade import _request
        return _request('GET', path)
    except Exception as e:
        logger.warning(f"OKX API 失败: {path} - {e}")
        return None


def _do_sync():
    """执行一次完整同步"""
    try:
        swap_id = "DOGE-USDT-SWAP"
        now = datetime.now().isoformat()

        # ─── 1. 账户余额 ───
        balance = _okx_get('/api/v5/account/balance?ccy=USDT')
        eq = 0.0
        frozen = 0.0
        avail = 0.0
        upl = 0.0
        if balance and balance.get('code') == '0':
            for d in balance.get('data', []):
                for det in d.get('details', []):
                    eq = float(det.get('eq', 0))
                    frozen = float(det.get('frozenBal', 0))
                    avail = float(det.get('availBal', 0))
                    upl = float(det.get('upl', 0))

        # 记录权益历史
        if eq > 0:
            EQUITY_HISTORY.append({'t': now, 'eq': eq})
            if len(EQUITY_HISTORY) > MAX_EQUITY_POINTS:
                EQUITY_HISTORY[:] = EQUITY_HISTORY[-MAX_EQUITY_POINTS:]

        # ─── 2. OKX 真实持仓 ───
        positions_raw = _okx_get(f'/api/v5/account/positions?instId={swap_id}')
        real_positions = []
        upl_total = 0.0
        realized_total = 0.0
        if positions_raw and positions_raw.get('code') == '0':
            for p in positions_raw.get('data', []):
                sz = float(p.get('pos', 0))
                if sz <= 0:
                    continue
                side = p['posSide']
                entry = float(p.get('avgPx', 0))
                pos_upl = float(p.get('upl', 0))
                pos_realized = float(p.get('realizedPnl', 0))
                upl_total += pos_upl
                realized_total += pos_realized
                real_positions.append({
                    'side': side,
                    'size': sz,
                    'doge_amount': int(sz * 1000),
                    'entry_price': entry,
                    'unrealized_pnl': round(pos_upl, 4),
                    'realized_pnl': round(pos_realized, 4),
                })

        # ─── 3. OKX 未成交挂单 ───
        orders_raw = _okx_get(f'/api/v5/trade/orders-pending?instId={swap_id}&instType=SWAP')
        pending_orders = []
        if orders_raw and orders_raw.get('code') == '0':
            for o in orders_raw.get('data', []):
                pending_orders.append({
                    'ordId': o['ordId'],
                    'side': o['side'],
                    'posSide': o.get('posSide', ''),
                    'sz': float(o['sz']),
                    'px': float(o.get('px', 0)),
                    'state': o['state'],
                    'cTime': o.get('cTime', ''),
                })

        # ─── 4. 今日成交历史 ───
        today_ts = int(time.time()) - 86400
        fills_raw = _okx_get(f'/api/v5/trade/fills?instId={swap_id}&instType=SWAP&begin={today_ts}000')
        today_trades = []
        today_pnl = 0.0
        today_fees = 0.0
        if fills_raw and fills_raw.get('code') == '0':
            for f in fills_raw.get('data', []):
                ts_raw = f.get('ts', '0')
                if len(ts_raw) > 10:
                    ts_local = time.strftime('%H:%M:%S', time.localtime(int(ts_raw[:10])))
                else:
                    ts_local = ts_raw
                fill_sz = float(f.get('fillSz', 0))
                fill_px = float(f.get('fillPx', 0))
                fee = abs(float(f.get('fee', 0)))
                pnl = float(f.get('pnl', 0)) if f.get('pnl') else 0
                today_pnl += pnl
                today_fees += fee
                today_trades.append({
                    'time': ts_local,
                    'side': f['side'],
                    'posSide': f.get('posSide', ''),
                    'fillSz': fill_sz,
                    'fillPx': fill_px,
                    'fee': round(fee, 6),
                    'pnl': round(pnl, 6),
                })

        # ─── 输出 ───
        sync_data = {
            'timestamp': now,
            'account': {
                'equity': round(eq, 4),
                'frozen': round(frozen, 4),
                'available': round(avail, 4),
                'unrealized_pnl': round(upl, 4),
            },
            'positions': real_positions,
            'pending_orders': pending_orders,
            'today': {
                'trade_count': len(today_trades),
                'trades': today_trades[-100:],  # 最近 100 笔
                'pnl': round(today_pnl, 6),
                'fees': round(today_fees, 6),
                'net_pnl': round(today_pnl - today_fees, 6),
            },
            'equity_history': EQUITY_HISTORY[-200:],  # 最近 200 点
        }

        with open(SYNC_FILE, 'w', encoding='utf-8') as f:
            json.dump(sync_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"同步异常: {e}")


# ─── 读取同步数据（供 API 使用） ───

def get_sync_data():
    if os.path.exists(SYNC_FILE):
        try:
            with open(SYNC_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None


# ─── 后台线程 ───

_sync_thread = None
_sync_running = False


def start_sync(interval=30):
    global _sync_thread, _sync_running
    if _sync_running:
        return
    _sync_running = True
    _sync_thread = threading.Thread(target=_sync_loop, args=(interval,), daemon=True)
    _sync_thread.start()
    logger.info(f"数据同步已启动（间隔 {interval}s）")


def stop_sync():
    global _sync_running
    _sync_running = False


def _sync_loop(interval):
    while _sync_running:
        _do_sync()
        for _ in range(interval):
            if not _sync_running:
                break
            time.sleep(1)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    start_sync(10)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_sync()
