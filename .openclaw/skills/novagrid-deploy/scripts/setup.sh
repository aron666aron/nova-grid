#!/bin/bash
# NovaGrid Deployer Setup - Run on the server that hosts the bot dashboard
set -e

SERVER_DIR="/home/admin/quant-bot"
DIST_DIR="/home/admin/novagrid/dist"
DEPLOY_DIR="/home/admin/novagrid"
DEPLOY_HTML="$DEPLOY_DIR/deploy.html"
DEPLOY_WORKER="$DEPLOY_DIR/deploy_worker.py"
WEB_SERVER="$SERVER_DIR/web_server.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}[+] NovaGrid Deployer Setup${NC}"

# Check Python
command -v python3 >/dev/null 2>&1 || { echo -e "${RED}Error: python3 required${NC}"; exit 1; }

# Ensure dist directory with bot files
if [ ! -d "$DIST_DIR" ]; then
    echo -e "${YELLOW}[!] Creating dist directory...${NC}"
    mkdir -p "$DIST_DIR/strategies"
    echo -e "${YELLOW}[!] Place bot source files in $DIST_DIR/${NC}"
    echo "  web_server.py, okx_market.py, okx_trade.py, capital_manager.py"
    echo "  strategies/grid_bot.py, strategies/market_analyzer.py, strategies/signal_filter.py"
    echo "  templates/dashboard.html"
fi

# Add deploy endpoint to web_server.py
if ! grep -q "/deploy/" "$WEB_SERVER" 2>/dev/null; then
    echo -e "${YELLOW}[!] Adding /deploy/ route to web_server.py...${NC}"
    echo -e "\n${YELLOW}[!] Manual patch needed. Add these lines to your web_server.py:${NC}"
    echo "
from flask import send_from_directory
DEPLOY_DIR = \"$DEPLOY_DIR\"

@app.route('/deploy/')
def deploy_page():
    return send_from_directory(DEPLOY_DIR, 'deploy.html')

@app.route('/api/deploy/start', methods=['POST'])
def deploy_start():
    import subprocess, json
    data = request.json
    with open(os.path.join(DEPLOY_DIR, 'deploy_config.json'), 'w') as f:
        json.dump(data, f, ensure_ascii=False)
    subprocess.Popen(['python3', os.path.join(DEPLOY_DIR, 'deploy_worker.py')],
                     cwd=DEPLOY_DIR)
    return jsonify({'status': 'started'})

@app.route('/api/deploy/progress')
def deploy_progress():
    import json
    progress_file = os.path.join(DEPLOY_DIR, 'logs', 'deploy_progress.txt')
    if not os.path.exists(progress_file):
        return jsonify({'status': 'waiting'})
    with open(progress_file) as f:
        lines = [l.strip() for l in f if l.strip() and l != 'EOF']
    entries = [json.loads(l) for l in lines]
    return jsonify({'status': 'running', 'entries': entries})
"
fi

# Create deploy directory
mkdir -p "$DEPLOY_DIR/logs"

echo -e "${GREEN}[+] Setup complete!${NC}"
echo -e "${GREEN}[+] Visit http://YOUR_SERVER:5000/deploy/ to deploy bots${NC}"
echo -e "${YELLOW}[!] Ensure deploy.html and deploy_worker.py are in $DEPLOY_DIR/${NC}"
echo -e "${YELLOW}[!] Ensure port 5002 is open in firewall${NC}"
