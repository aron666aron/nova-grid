---
name: novagrid-deploy
description: One-click deployment of a full-featured DOGE-USDT grid trading bot to any Linux server. Use when asked about deploying grid trading bots, OKX quant trading, or one-click bot deployment with web dashboard. Supports SSH password or key auth, auto-installs dependencies, handles port configuration.
---

# NovaGrid Deploy 🚀

> Deploy a complete dual-grid quantitative trading bot (DOGE-USDT on OKX) to any Linux server with one click.

## Quick Start

The NovaGrid Deployer is a web-based tool that:

1. **Live demo**: `http://YOUR_SERVER:5000/deploy/`
2. Fill in server IP/port/password + OKX API keys + trading config
3. Click deploy — bot runs at port 5002 with full dashboard

## Architecture

```
┌─────────────────────┐       port 5000       ┌──────────────────────┐
│  Web Server (Flask)  │◄──── user browser ───►│  Deploy Page         │
│  /deploy/            │                       │  (deploy.html)       │
│                      │  spawn deploy process │                      │
│  deploy_worker.py    │──────────────────────►│  SSH to target       │
│                      │                       │  Upload dist/ + cfg │
│                      │                       │  Install deps       │
│                      │                       │  Start bot @5002    │
└─────────────────────┘                       └──────────────────────┘
```

## Files

### `references/`
- **`deploy.html`** — Form-based web UI (hosted at `/deploy/` endpoint)
- **`deploy_worker.py`** — Backend SSH deployment engine (paramiko)
- **`dist/`** — Full bot distribution (web_server.py, strategies/, config templates)

### `scripts/`
- **`setup.sh`** — One-liner to install the deployer on your existing bot web server

## Bot Dashboard Features

- **Live price** (DOGE-USDT) with auto-refresh
- **Grid range** display (bidirectional: long below, short above)
- **Active positions** tracking (long count, short count, fees, PnL)
- **Trade log** with auto-scroll
- **Start / Stop** controls
- **Runtime config** (symbol, grid count, price range, side)
- **OKX real-time data** (long/short entry prices, unrealized PnL)

## Setup Instructions

### Prerequisites
1. A Linux server (Ubuntu 22.04+ recommended) with Python 3.10+
2. Port 5000 open in firewall/security group
3. An OKX API key with Trade + Read permissions
4. Root SSH access (or sudo-capable user)

### Installation

```bash
# Clone
git clone https://github.com/aron666aron/nova-grid.git
cd nova-grid

# Install dependencies
pip install -r requirements.txt

# Start the web server (port 5000)
python web_server.py
```

Then open `http://YOUR_SERVER:5000/deploy/` in browser.

### Deploy to Another Server

Fill the form:
- **Server**: IP, port (22), root password or SSH key path
- **OKX**: API Key, Secret, Passphrase, symbol (default DOGE-USDT)
- **Trading**: Grids (12-20), range % (1-3%), paper/live mode
- Click **Start Deploy** — watch progress in real time

Your bot will be accessible at `http://TARGET_SERVER:5002/`

### Manual Port Fix

If 5002 is not accessible, add an inbound rule in your cloud provider's security group:
- Port: 5002, Protocol: TCP, Source: `0.0.0.0/0`

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `passphrase incorrect` | Recreate OKX API key and set a known passphrase |
| Port 5002 unreachable | Open in cloud firewall / security group |
| SSH auth failed | Check password or SSH key permissions |
| Deploy hangs at 50% | Server needs `pip3`, `python3`, should be Ubuntu/Debian |
| 500 error on dashboard | Check `/root/novagrid/logs/dashboard.log` on target |
