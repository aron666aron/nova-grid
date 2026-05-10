# -*- coding: utf-8 -*-
"""OKX 交易模块 - 签名下单"""
import json, time, hmac, base64, hashlib, logging
from datetime import datetime, timezone
import requests
from config import OKX_API, PAPER_TRADING

logger = logging.getLogger('okx_trade')

class OKXSigner:
    def __init__(self, api_key, secret_key, passphrase):
        self.api_key = api_key
        self.secret_key = secret_key.encode('utf-8')
        self.passphrase = passphrase

    def sign(self, timestamp, method, request_path, body=''):
        msg = f"{timestamp}{method.upper()}{request_path}{body}"
        mac = hmac.new(self.secret_key, msg.encode('utf-8'), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    def headers(self, timestamp, method, request_path, body=''):
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': self.sign(timestamp, method, request_path, body),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
        }

def _get_signer():
    """获取签名器（从环境变量/配置读取密钥）"""
    from config import OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE
    return OKXSigner(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE)

def _timestamp():
    """获取 ISO 8601 时间戳"""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def _request(method, path, body=None):
    """发送签名请求"""
    signer = _get_signer()
    ts = _timestamp()
    body_str = json.dumps(body) if body else ''
    url = f"{OKX_API}{path}"
    hdrs = signer.headers(ts, method, path, body_str)
    
    try:
        if method == 'GET':
            r = requests.get(url, headers=hdrs, timeout=15)
        elif method == 'POST':
            r = requests.post(url, headers=hdrs, data=body_str, timeout=15)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        result = r.json()
        if result.get('code') != '0':
            logger.warning(f"OKX API error: {result.get('msg', 'unknown')} (code={result.get('code')})")
        return result
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return {'code': '-1', 'msg': str(e)}

# ---- Public API ----

def get_account():
    """获取账户资产"""
    return _request('GET', '/api/v5/account/balance')

def get_positions(inst_type='SWAP'):
    """获取持仓信息"""
    return _request('GET', f'/api/v5/account/positions?instType={inst_type}')

def place_order(inst_id, side, sz, ord_type='market', td_mode='cash', pos_side=None, paper_mode_override=None):
    """
    下单
    :param inst_id:  交易对 e.g. BTC-USDT
    :param side:     buy / sell
    :param sz:       数量
    :param ord_type: market / limit
    :param td_mode:  cash / cross / isolated
    :param pos_side: 仓位方向 (long/short, 仅合约需要)
    :param paper_mode_override: 强制覆盖 PAPER_TRADING 设置
    """
    is_paper = paper_mode_override if paper_mode_override is not None else PAPER_TRADING
    if is_paper:
        logger.info(f"[模拟交易] {side.upper()} {sz} {inst_id} ({ord_type})")
        return {'code': '0', 'msg': '模拟交易成功', 'data': [{'ordId': 'MOCK-' + str(int(time.time()))}]}
    
    body = {
        'instId': inst_id,
        'tdMode': td_mode,
        'side': side,
        'ordType': ord_type,
        'sz': str(sz),
    }
    if pos_side:
        body['posSide'] = pos_side
    
    return _request('POST', '/api/v5/trade/order', body)

def set_leverage(inst_id, lever, mgn_mode='cross'):
    """设置杠杆（永续合约用）"""
    body = {'instId': inst_id, 'lever': str(lever), 'mgnMode': mgn_mode}
    return _request('POST', '/api/v5/account/set-leverage', body)


def swap_order(inst_id, side, sz, pos_side, ord_type='limit', leverage=3):
    """
    永续合约下单（USDT-M SWAP）
    :param inst_id:  e.g. DOGE-USDT-SWAP
    :param side:     buy / sell
    :param sz:       合约张数
    :param pos_side: long / short
    :param ord_type: limit / market
    :param leverage: 杠杆倍数
    """
    # 先设置杠杆
    set_leverage(inst_id, leverage, mgn_mode='cross')
    return place_order(inst_id, side, sz, ord_type=ord_type, td_mode='cross', pos_side=pos_side)


def cancel_order(inst_id, ord_id):
    """撤销订单"""
    body = {'instId': inst_id, 'ordId': ord_id}
    return _request('POST', '/api/v5/trade/cancel-order', body)

def get_order(inst_id, ord_id):
    """查询订单"""
    return _request('GET', f'/api/v5/trade/order?instId={inst_id}&ordId={ord_id}')

def get_open_orders(inst_id=None):
    """获取当前挂单"""
    path = '/api/v5/trade/orders-pending'
    if inst_id:
        path += f'?instId={inst_id}'
    return _request('GET', path)

def get_fills(inst_id=None, ord_id=None):
    """获取成交明细"""
    params = []
    if inst_id: params.append(f'instId={inst_id}')
    if ord_id:  params.append(f'ordId={ord_id}')
    path = '/api/v5/trade/fills'
    if params:
        path += '?' + '&'.join(params)
    return _request('GET', path)

def get_instruments(inst_type='SPOT'):
    """获取交易产品信息"""
    return _request('GET', f'/api/v5/public/instruments?instType={inst_type}')

def get_ticker(inst_id):
    """获取行情"""
    return _request('GET', f'/api/v5/market/ticker?instId={inst_id}')

# ---- Convenience ----

def check_connection():
    """测试 API 连接和权限"""
    r = get_account()
    if r.get('code') == '0':
        data = r.get('data', [])
        if data:
            details = data[0].get('details', [])
            total_equity = data[0].get('totalEq', '0')
            coins = [{'ccy': d['ccy'], 'eq': d['eq'], 'availBal': d.get('availBal', '0')} for d in details]
            return {'ok': True, 'totalEq': total_equity, 'coins': coins}
        return {'ok': True, 'totalEq': '0', 'coins': []}
    else:
        return {'ok': False, 'msg': r.get('msg', 'unknown error')}

def quick_buy(inst_id, usdt_amount):
    """
    现价买入（按 USDT 金额）
    例: quick_buy('BTC-USDT', 10) → 买 10 USDT 的 BTC
    """
    # 先获取最新价
    ticker = get_ticker(inst_id)
    if ticker.get('code') != '0':
        # Fallback to market module
        try:
            from okx_market import get_price
            price_data = get_price(inst_id)
            price = float(price_data['price'])
        except:
            return {'code': '-1', 'msg': 'Unable to get price'}
    else:
        price = float(ticker['data'][0]['last'])
    
    sz = round(usdt_amount / price, 8)
    return place_order(inst_id, 'buy', sz, ord_type='market')

# Test
if __name__ == '__main__':
    r = check_connection()
    print('Connection test:', 'OK' if r.get('ok') else 'FAIL')
    if r.get('ok'):
        print('  Total Eq:', r['totalEq'])
        for c in r.get('coins', []):
            print(f"  {c['ccy']}: {c['eq']} (avail: {c['availBal']})")
