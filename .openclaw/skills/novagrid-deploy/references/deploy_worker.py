#!/usr/bin/env python3
"""deploy_worker.py - 后台部署完整网格交易机器人"""
import json, os, sys, time, base64, glob

def log(t, c="i"):
    with open(os.path.expanduser("~/novagrid/logs/deploy_progress.txt"), "a") as f:
        f.write(json.dumps({"t": t, "c": c}, ensure_ascii=False) + "\n")

config_file = os.path.expanduser("~/novagrid/deploy_config.json")
if not os.path.exists(config_file):
    log("\u274c No config found", "e")
    sys.exit(1)

with open(config_file) as f:
    d = json.load(f)

srv = d["server"]
okx = d["okx"]
tr = d["trading"]
DEPLOY_PORT = 5002

import paramiko

log("\U0001f50c Connecting to {}:{}...".format(srv["ip"], srv["port"]))
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    has_pw = bool(srv.get("pass", ""))
    c.connect(srv["ip"], port=srv.get("port", 22), username=srv["user"],
              password=srv.get("pass", "") if has_pw else None,
              timeout=15, allow_agent=not has_pw, look_for_keys=not has_pw)
except Exception as e:
    log("\u274c Failed: {}".format(str(e)[:80]), "e")
    sys.exit(1)

log("\u2705 Connected", "s")

_, o, _ = c.exec_command("echo $HOME")
home = o.read().decode().strip()
if not home: home = "/root"
BASE = home + "/novagrid"
log("Home: {}".format(home), "i")

c.exec_command("mkdir -p {}/strategies {}/logs {}/data".format(BASE, BASE, BASE))

# ─── 上传源码文件 ────────────────────────
def upload_file(local_path, remote_path):
    """Upload any file via base64 + exec_command"""
    with open(local_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    full_remote = "{}/{}".format(BASE, remote_path) if not remote_path.startswith("/") else remote_path
    c.exec_command("echo '{}' | base64 -d > {}".format(b64, full_remote))
    return full_remote

# Dist directory (same folder as this script)
dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

# Upload all files from dist/
if os.path.isdir(dist):
    for root, dirs, files in os.walk(dist):
        for fname in files:
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, dist)
            upload_file(local, rel)
    log("\U0001f4e6 Bot files uploaded", "s")
else:
    log("\u26a0\ufe0f dist/ not found, cannot deploy", "e")
    sys.exit(1)

c.exec_command("touch {}/strategies/__init__.py".format(BASE))

# ─── 生成 .env ──────────────────────────
env_text = ("OKX_API_KEY={}\nOKX_SECRET_KEY={}\nOKX_PASSPHRASE={}\n"
            "OKX_API=https://www.okx.com\n").format(okx["key"], okx["secret"], okx["passphrase"])
c.exec_command("cat > {}/.env << 'EOF'\n".format(BASE) + env_text + "EOF")
log("\u2705 API keys set", "s")

# ─── 生成 config.py ─────────────────────
pct = tr["range"] / 100
paper = "True" if tr["mode"] == "paper" else "False"
sym = okx["symbol"]
gs = tr["grids"]
lev = tr.get("leverage", 3)

cfg = """# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv
load_dotenv()

OKX_API = os.environ.get("OKX_API", "https://www.okx.com")
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")

SYMBOLS = {
    "BTC-USDT": {"name": "BTC/USDT", "min_amount": 0.0001, "price_precision": 1, "amount_precision": 4},
    "ETH-USDT": {"name": "ETH/USDT", "min_amount": 0.001, "price_precision": 1, "amount_precision": 4},
    "SOL-USDT": {"name": "SOL/USDT", "min_amount": 0.01, "price_precision": 1, "amount_precision": 4},
    "__SYM__": {"name": "__SYM__", "min_amount": 1.0, "price_precision": 5, "amount_precision": 0},
}

GRID = {"symbol":"__SYM__","grid_count":__GS__,"price_range_pct":__PCT__,
        "amount_per_grid":200,"check_interval":3,"side":"dual","leverage":__LEV__}

FEES = {"maker":0.0002,"taker":0.0005,"default":0.0002}
RISK = {"max_daily_trades":500,"min_interval":1}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR,"data")
LOG_DIR = os.path.join(BASE_DIR,"logs")
os.makedirs(DATA_DIR,exist_ok=True)
os.makedirs(LOG_DIR,exist_ok=True)
PAPER_TRADING = __PAPER__
""".replace("__SYM__", sym).replace("__GS__", str(gs)).replace("__PCT__", str(pct))
cfg = cfg.replace("__PAPER__", paper).replace("__LEV__", str(lev))
c.exec_command("cat > {}/config.py << 'EOF'\n".format(BASE) + cfg + "EOF")
log("\u2705 Config generated", "s")

# ─── 安装依赖 ────────────────────────────
log("\U0001f4e6 Installing Python packages...")
_, out, err = c.exec_command("cd {0} && pip3 install -r requirements.txt --break-system-packages --ignore-installed -q 2>&1; echo 'EXIT_CODE:'$?".format(BASE))
all_text = (out.read().decode() + err.read().decode()).strip()
if "EXIT_CODE:0" in all_text:
    log("\u2705 Dependencies installed", "s")
else:
    log("\u26a0\ufe0f pip: {}".format(all_text[:120]), "w")
    time.sleep(5)
    log("\u2705 Continuing...", "s")

# ─── 启动 ────────────────────────────────
log("\U0001f680 Starting bot...")
c.exec_command("cd {0} && nohup python3 -u web_server.py > logs/dashboard.log 2>&1 &".format(BASE))
time.sleep(5)

_, out, _ = c.exec_command("lsof -i:{0} 2>/dev/null | grep LISTEN".format(DEPLOY_PORT))
if out.read().decode().strip():
    _, ip_out, _ = c.exec_command("curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'")
    ip = ip_out.read().decode().strip() or srv["ip"]
    url = "http://{}:{}".format(ip, DEPLOY_PORT)
    log("\u2705 Success! Dashboard at {}".format(url), "s")
    log(url, "url")
else:
    _, dump, _ = c.exec_command("tail -8 {}/logs/dashboard.log".format(BASE))
    log("\u26a0\ufe0f Logs: {}".format(dump.read().decode().strip()[:200]), "w")

c.close()
