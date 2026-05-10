# -*- coding: utf-8 -*-
"""OKX行情接口 - 国内直连"""
import requests
from config import OKX_API

def get_price(inst_id="BTC-USDT"):
    """获取最新价格"""
    try:
        r = requests.get(f"{OKX_API}/api/v5/market/ticker",
                         params={"instId": inst_id}, timeout=10)
        d = r.json()
        if d.get("code") == "0" and d.get("data"):
            t = d["data"][0]
            return {
                "price": float(t.get("last", 0) or 0),
                "high": float(t.get("high24h", 0) or 0),
                "low": float(t.get("low24h", 0) or 0),
                "vol": float(t.get("vol", 0) or 0),
                "amount": float(t.get("volCcy", 0) or 0),
            }
    except Exception:
        pass
    return None

def get_kline(inst_id="BTC-USDT", bar="1H", limit=100):
    """获取K线数据"""
    try:
        r = requests.get(f"{OKX_API}/api/v5/market/candles",
                         params={"instId": inst_id, "bar": bar, "limit": limit}, timeout=10)
        d = r.json()
        if d.get("code") == "0":
            return d["data"]  # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    except Exception:
        pass
    return []

def get_depth(inst_id="BTC-USDT", sz=20):
    """获取订单簿"""
    try:
        r = requests.get(f"{OKX_API}/api/v5/market/books",
                         params={"instId": inst_id, "sz": sz}, timeout=10)
        d = r.json()
        if d.get("code") == "0" and d.get("data"):
            tick = d["data"][0]
            return {"bids": tick.get("bids", []), "asks": tick.get("asks", [])}
    except Exception:
        pass
    return None
