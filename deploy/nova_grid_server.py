# -*- coding: utf-8 -*-
"""NovaGrid - 一键部署版"""
import hashlib, base64, hmac, json, os, sys, time, threading, requests, random
from datetime import datetime

PORT = 5002

# ─── 读配置 ────────────────────────────
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
ENV = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip()

SYMBOL = "DOGE-USDT"
GRID_COUNT = 12
RANGE_PCT = 0.02  # 2%

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.py")
if os.path.exists(CONFIG_FILE):
    try:
        import importlib.util as iu
        spec = iu.spec_from_file_location("cfg", CONFIG_FILE)
        mod = iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        g = mod.GRID if hasattr(mod, "GRID") else {}
        SYMBOL = g.get("symbol", SYMBOL)
        GRID_COUNT = g.get("grid_count", GRID_COUNT)
        RANGE_PCT = g.get("price_range_pct", RANGE_PCT)
    except:
        pass

# ─── OKX API 签名 ──────────────────────
def okx_req(method, path, body=None):
    api_key = ENV.get("OKX_API_KEY", "")
    secret = ENV.get("OKX_SECRET_KEY", "")
    passphrase = ENV.get("OKX_PASSPHRASE", "")
    ts = str(time.time())
    body_str = json.dumps(body) if body else ""
    msg = ts + method.upper() + path + body_str
    sig = base64.b64encode(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()
    headers = {"OK-ACCESS-KEY": api_key, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts,
               "OK-ACCESS-PASSPHRASE": passphrase, "Content-Type": "application/json"}
    r = requests.request(method, "https://www.okx.com" + path, headers=headers, json=body, timeout=10)
    return r.json()

# ─── 网格策略引擎 ──────────────────────
class GridEngine:
    def __init__(self):
        self.symbol = SYMBOL
        self.grid_count = GRID_COUNT
        self.range_pct = RANGE_PCT
        self.center_price = 0.108
        self.grid_prices = []
        self.positions = {}  # {idx: {"side":"LONG"/"SHORT", "entry":x}}
        self.pnl = 0.0
        self.fees = 0.0
        self.trades = 0
        self.running = False
        self.thread = None
        self.prev_price = None

    def get_price(self):
        try:
            r = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={self.symbol}", timeout=5)
            return float(r.json()["data"][0]["last"])
        except:
            return None

    def build_grids(self, center):
        half = center * self.range_pct
        low, high = center - half, center + half
        step = (high - low) / self.grid_count
        self.grid_prices = [round(low + step * i, 8) for i in range(self.grid_count + 1)]
        self.center_price = center
        self.long_range = set(range(0, self.grid_count // 2))
        self.short_range = set(range(self.grid_count // 2, self.grid_count + 1))

    def run_tick(self):
        price = self.get_price()
        if not price:
            return
        if not self.grid_prices or abs(price - self.center_price) / max(self.center_price, 0.001) > self.range_pct * 0.8:
            self.build_grids(price)
            self.prev_price = price
            return

        idx = min(range(len(self.grid_prices)), key=lambda i: abs(price - self.grid_prices[i]))
        if self.prev_price and self._get_idx(self.prev_price) != idx:
            if price < self.prev_price:
                # 下跌 → 检查做多
                for i in range(self._get_idx(price), self._get_idx(self.prev_price) + 1):
                    if i in self.long_range and str(i) not in self.positions:
                        self.open_long(i, self.grid_prices[i])
            else:
                # 上涨 → 检查做空
                for i in range(self._get_idx(self.prev_price) + 1, self._get_idx(price) + 1):
                    if i in self.short_range and str(i) not in self.positions:
                        self.open_short(i, self.grid_prices[i])

            # 平仓检查
            for key in list(self.positions.keys()):
                pos = self.positions[key]
                i = int(key)
                if pos["side"] == "LONG" and i + 1 < len(self.grid_prices) and idx >= i + 1:
                    self.close_long(i, price)
                elif pos["side"] == "SHORT" and i - 1 >= 0 and idx <= i - 1:
                    self.close_short(i, price)

        self.prev_price = price

    def _get_idx(self, price):
        return min(range(len(self.grid_prices)), key=lambda i: abs(price - self.grid_prices[i]))

    def open_long(self, idx, gprice):
        self.positions[str(idx)] = {"side": "LONG", "entry": gprice, "ts": datetime.now().isoformat()}
        self.trades += 1

    def close_long(self, idx, price):
        pos = self.positions.pop(str(idx), None)
        if not pos: return
        profit = (price - pos["entry"]) * 100  # 假设每格100个币
        fee = (pos["entry"] * 100 + price * 100) * 0.001
        self.pnl += profit
        self.fees += fee
        self.trades += 1

    def open_short(self, idx, gprice):
        self.positions[str(idx)] = {"side": "SHORT", "entry": gprice, "ts": datetime.now().isoformat()}
        self.trades += 1

    def close_short(self, idx, price):
        pos = self.positions.pop(str(idx), None)
        if not pos: return
        profit = (pos["entry"] - price) * 100
        fee = (pos["entry"] * 100 + price * 100) * 0.001
        self.pnl += profit
        self.fees += fee
        self.trades += 1

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread: self.thread.join(timeout=3)

    def _loop(self):
        while self.running:
            try: self.run_tick()
            except: pass
            time.sleep(3)

    def status(self):
        price = self.get_price()
        return {"symbol": self.symbol, "price": price, "grids": self.grid_count,
                "active_positions": len(self.positions),
                "long_positions": sum(1 for p in self.positions.values() if p["side"] == "LONG"),
                "short_positions": sum(1 for p in self.positions.values() if p["side"] == "SHORT"),
                "total_pnl": round(self.pnl, 4), "total_fees": round(self.fees, 4),
                "net_pnl": round(self.pnl - self.fees, 4), "trades": self.trades,
                "grid_low": min(self.grid_prices) if self.grid_prices else 0,
                "grid_high": max(self.grid_prices) if self.grid_prices else 0,
                "center": self.center_price, "running": self.running}

# ─── Flask ──────────────────────────────
import flask
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)
engine = GridEngine()

@app.route("/")
def index():
    gs = engine.grid_count
    h = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>NovaGrid</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f0f23;color:#e0e0e0;margin:0;padding:16px;max-width:800px;margin:0 auto}
h1{color:#e94560;font-size:1.8rem;margin:0 0 20px;display:flex;align-items:center;gap:8px}
h1 span{font-size:0.9rem;color:#666;font-weight:normal}
.card{background:#1a1a3e;border-radius:12px;padding:20px;margin:10px 0}
.card h2{margin:0 0 15px;color:#e94560;font-size:1.2rem}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a2a4e}
.row:last-child{border:0}
.label{color:#888}
.val{font-weight:bold}
.green{color:#66bb6a}
.red{color:#e94560}
.gold{color:#ffd700}
.controls{display:flex;gap:10px;margin-top:15px}
.controls button{flex:1;padding:12px;border:none;border-radius:8px;font-size:1rem;cursor:pointer;font-weight:bold}
.btn-start{background:#66bb6a;color:#fff}
.btn-start:hover{background:#4caf50}
.btn-stop{background:#e94560;color:#fff}
.btn-stop:hover{background:#d32f2f}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:0.8rem}
.badge-green{background:#66bb6a33;color:#66bb6a}
.badge-red{background:#e9456033;color:#e94560}
</style></head>
<body>
<h1>&#x26a1; NovaGrid <span>v1.0</span></h1>
<div class=card>
<h2>&#x1f4ca; Status</h2>
<div class=row><span class=label>Symbol</span><span class=val>""" + SYMBOL + """</span></div>
<div class=row><span class=label>Price</span><span class=val id=price>$--</span></div>
<div class=row><span class=label>Grid Range</span><span class=val id=grange>--</span></div>
<div class=row><span class=label>Grids</span><span class=val>""" + str(gs) + """</span></div>
<div class=row><span class=label>Active</span><span class=val id=active>0</span>
<span class=val id=pos_detail style="font-size:0.85rem;color:#888"></span></div>
<div class=row><span class=label>Trades</span><span class=val id=trades>0</span></div>
<div class=row><span class=label>PnL</span><span class=val id=pnl>$0.0000</span></div>
<div class=row><span class=label>Fees</span><span class=val id=fees>$0.0000</span></div>
<div class=row><span class=label>Net</span><span class=val id=net>$0.0000</span></div>
<div class=row><span class=label>Status</span><span id=run_badge class=badge badge-red>&#x23f8; Stopped</span></div>
</div>
<div class=controls>
<button class=btn-start onclick="act('start')">&#x25b6; Start</button>
<button class=btn-stop onclick="act('stop')">&#x23f9; Stop</button>
</div>
<script>
function act(a){fetch('/api/'+a).then(r=>r.json()).then(d=>{if(d.ok)refresh()})}
function refresh(){fetch('/api/status').then(r=>r.json()).then(d=>{
 document.getElementById('price').textContent='$'+(d.price||'--');
 document.getElementById('grange').textContent='$'+d.grid_low+' ~ $'+d.grid_high;
 document.getElementById('active').textContent=d.active_positions;
 document.getElementById('pos_detail').textContent='L:'+d.long_positions+' S:'+d.short_positions;
 document.getElementById('trades').textContent=d.trades;
 document.getElementById('pnl').textContent='$'+d.total_pnl.toFixed(4);
 document.getElementById('fees').textContent='$'+d.total_fees.toFixed(4);
 document.getElementById('net').textContent='$'+d.net_pnl.toFixed(4);
 var b=document.getElementById('run_badge');
 if(d.running){b.textContent='\\u25b6 Running';b.className='badge badge-green'}
 else{b.textContent='\\u23f8 Stopped';b.className='badge badge-red'}
}).catch(function(){})}
refresh();setInterval(refresh,5000);
</script>
</body></html>"""
    return render_template_string(h)

@app.route("/api/status")
def api_status():
    return jsonify(engine.status())

@app.route("/api/start")
def api_start():
    engine.start()
    return jsonify({"ok": True})

@app.route("/api/stop")
def api_stop():
    engine.stop()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
