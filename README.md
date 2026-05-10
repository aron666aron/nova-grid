# ⚡ NovaGrid — 下一代智能网格交易机器人

> **NovaGrid** 是一款运行在 OKX 永续合约上的双向网格交易机器人。它不只是简单的网格——内置多因子信号过滤、自动参数优化、实时 Web 仪表盘，让你像量化基金一样交易。

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/OKX-%E2%9C%93-brightgreen" alt="OKX">
</p>

---

## ✨ 特性

| 特性 | 说明 |
|------|------|
| 🤖 **双向网格** | 同时做多做空，牛熊双吃 |
| 🧠 **信号过滤** | ADX/RSI/EMA/成交量 四因子智能过滤 |
| ⚡ **自动优化** | 根据波动率自动调参 |
| 🖥 **Web 仪表盘** | 实时看状态、盈亏、信号、交易记录 |
| 🛡 **风控系统** | 保证金管理、强平预警、网格跟随 |
| 🚀 **一键部署工具** | 内置 Web UI，填表单 → 点按钮 → 自动部署到任意服务器 |
| 🧩 **OpenClaw/Claude Code Skill** | `.openclaw/skills/novagrid-deploy/` 即装即用 |

---

## 🎬 30 秒快速开始

```bash
# 1. 下载项目
git clone https://github.com/你的用户名/nova-grid.git
cd nova-grid

# 2. 配置 API（只需三步）
cp .env.example .env
# 然后编辑 .env，填入你的 OKX API 密钥

# 3. 启动！
chmod +x setup.sh && ./setup.sh
source venv/bin/activate
python web_server.py
```

浏览器打开 `http://你的服务器IP:5000` → ✅ 完成！

---

## 📖 完整指南

### 🎯 什么是 NovaGrid？

NovaGrid 是一个**双向网格**策略机器人：

```
价格上涨 ──────────────►
                   ┌──────│ 做空开仓区
                   │      │
    当前价格 ──────┼──────│ ← 中间区（不开仓）
                   │      │
                   └──────│ 做多开仓区
价格下跌 ──────────────►
```

- **价格跌** → 自动做多买入（等待反弹）
- **价格涨** → 自动做空卖出（等待回落）
- **智能过滤** → 强趋势时不做逆势单

### 🔑 第一步：获取 OKX API 密钥

1. 打开 [OKX API 管理](https://www.okx.com/account/my-api)
2. 点击「创建新的 API Key」
3. 权限勾选 **交易** + **读取**
4. 设置一个**通行短语（Passphrase）**——请记住它
5. 创建成功后，你会得到：
   ```
   API Key:        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   Secret Key:     xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   Passphrase:     你设置的短语
   ```

> ⚠️ **安全提示**
> - API 密钥能直接操作你的资金！不要泄露给任何人
> - 建议设置 IP 白名单，只允许你的服务器访问
> - 创建专用的 API Key，不要和其他应用共用
> - .env 文件不要提交到 GitHub（已在 .gitignore 中排除）

### 📝 第二步：配置 .env

```bash
cp .env.example .env
```

用文本编辑器打开 `.env`，填入你的密钥：

```ini
OKX_API_KEY=你的_API_KEY
OKX_SECRET_KEY=你的_SECRET_KEY
OKX_PASSPHRASE=你的_PASSPHRASE
OKX_API=https://www.okx.com
```

### 🔧 第三步：一键部署

**Linux 服务器（推荐）：**

```bash
# 安装虚拟环境和依赖（全程自动）
./setup.sh

# 激活环境
source venv/bin/activate

# 启动机器人（带 Web 界面）
python web_server.py
```

**Windows 电脑：**

```bash
# 创建虚拟环境
python -m venv venv

# 激活
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动
python web_server.py
```

### 🚀 第四步：后台运行

```bash
# 使用 nohup 后台运行（服务器断开也不影响）
nohup ./venv/bin/python web_server.py > logs/dashboard.log 2>&1 &

# 查看日志
tail -f logs/dashboard.log

# 停止
kill $(lsof -t -i:5000)
```

---

## 🌐 Web 仪表盘

启动后访问 `http://你的服务器IP:5000`，界面一目了然：

### 📊 首页面板

| 区块 | 内容 |
|------|------|
| 状态 | 运行状态、模式（模拟/实盘）、价格、网格范围 |
| 盈亏 | 总盈亏、做多/做空分别盈亏、手续费 |
| 信号 | 实时信号偏向值、置信度、各因子明细 |
| 配置 | 网格数、范围、杠杆、自动优化开关 |

### 🔌 API 接口（方便二次开发）

| 接口 | 方法 | 说明 | 示例返回 |
|------|------|------|----------|
| `/api/status` | GET | 运行状态 | `{"running":true,"mode":"live","price":0.1085,...}` |
| `/api/config` | GET | 当前配置 | `{"grid_count":12,"price_range_pct":0.015,...}` |
| `/api/config` | POST | 修改配置 | 发送 JSON 即可生效 |
| `/api/signal` | GET | 实时信号 | `{"bias":-0.3,"confidence":"medium","factors":{...}}` |
| `/api/trade_log` | GET | 交易记录 | `[{"time":"12:00","action":"OPEN_LONG","price":0.1085}]` |
| `/api/capital_analysis` | GET | 资金分析 | `{"equity":14.36,"usage_pct":70,...}` |

**修改配置示例：**
```bash
curl -X POST http://IP:5000/api/config \
  -H "Content-Type: application/json" \
  -d '{"mode":"live","grid_count":12,"price_range_pct":0.015}'
```

---

## 🧠 策略原理（写给想深入了解的你）

### 网格交易

NovaGrid 在价格区间内均匀设置 N 条网格线，将资金分成 N 份：

- **下跌** → 每穿过一条网格线，买入一份（做多）
- **上涨** → 每穿过一条网格线，卖出一份（做空）
- **反弹/回落** → 网格线自动反向平仓，赚取差价

### 信号过滤器

NovaGrid 在开仓前先问自己一个问题：**"现在开这个方向的单，有数据支持吗？"**

| 因子 | 权重 | 判断逻辑 |
|------|------|----------|
| ADX 趋势 | 35% | >30 = 有趋势，<20 = 震荡 |
| RSI | 25% | >70 = 超买（偏空），<30 = 超卖（偏多） |
| EMA 交叉 | 20% | 快线在慢线上方 = 偏多 |
| 成交量比 | 10% | 放量 = 趋势延续 |
| 价格位置 | 10% | 价格在 Bollinger 带上下沿 |

**决策逻辑：**
- 信号极度偏空（bias < -0.5）→ 禁止开任何多单
- 信号极度偏多（bias > 0.5）→ 禁止开任何空单
- 信号中性 → 双向自由开仓

### 自动优化

每隔约 10 分钟，NovaGrid 自动分析市场：

```
当前波动率 0.9% → 建议网格范围: ±1.8%~±2.7%
当前资金 $14.36 → 建议网格数: 8~18
每格利润 ≈ 步长 × 数量 × 杠杆 - 手续费
```

---

## ⚙️ 参数调优指南

| 场景 | 建议范围 | 建议网格数 |
|------|----------|------------|
| 🟢 波动小（<1%） | ±1.5%~±2% | 10~16 |
| 🟡 波动中等（1%~2%） | ±2%~±3% | 12~20 |
| 🔴 波动大（>2%） | ±3%~±5% | 12~16 |
| 🆕 新手入门（小额资金 $10~$50） | ±2%~±3% | 8~12 |
| 🎯 激进（想高频交易） | ±1%~±1.5% | 14~20 |

**经验法则：**
- 网格范围 ≥ 日均波动率的 2 倍
- 每格最小利润 ≥ 手续费的 2 倍
- 保证金使用率 ≤ 70%

---

## ❓ 常见问题

### Q: 会亏钱吗？

**会。** 网格交易不是印钞机。

**主要亏损场景：**
- **单边行情** → 价格一路突破网格，全部仓位被套
  - 应对：开自动优化 + 信号过滤 + 网格跟随
- **高手续费吃掉利润** → 频繁交易但步长/费率比 < 2
  - 应对：检查每格利润是否覆盖手续费
  - 建议最低步长/费率 > 2
- **强平** → 保证金不足被交易所强平
  - 应对：杠杆 ≤ 3x，保证金使用率 ≤ 70%

### Q: 为什么不交易？

检查以下三点：

| 检查项 | 方法 |
|--------|------|
| 价格是否在网格范围内？ | `curl http://IP:5000/api/status` |
| 信号是否在过滤？ | `curl http://IP:5000/api/signal` |
| 资金是否足够？ | `curl http://IP:5000/api/capital_analysis` |

最常见原因：**价格波动太小，没穿越网格线**。等待市场波动或缩小网格范围。

### Q: 需要什么配置的服务器？

| 配置 | 推荐 |
|------|------|
| CPU | 1 核即可 |
| 内存 | 512MB ~ 1GB |
| 系统 | Ubuntu 20.04+ / Debian / CentOS |
| 网络 | 能连接 OKX API（国内需香港/海外服务器） |
| 月费 | 阿里云香港轻量应用服务器 ≈ ¥24/月 |

### Q: 支持哪些币种？

默认支持 DOGE-USDT，可以自行在 `config.py` 中添加：

```python
SYMBOLS = {
    "DOGE-USDT": {...},
    "BTC-USDT": {...},
    "ETH-USDT": {...},
    "SOL-USDT": {...},
}
```

---

## 🛡 安全

- ✅ `PAPER_TRADING = True` 默认开启模拟模式
- ✅ API 密钥只在运行时加载，不会写入日志
- ✅ 所有交易都是限价单（maker 费率）
- ✅ 保证金使用率限制 70%
- ✅ `.env` 文件已被 .gitignore 排除
- ✅ 无外部网络请求（除 OKX API 外）

---

## 📜 免责声明

**加密货币交易有极高风险。** NovaGrid 是一个开源工具，仅用于学习和研究。

- 使用前请充分理解网格交易的风险
- 建议使用不影响生活的闲置资金
- 开发者不对使用本软件造成的任何损失负责
- 过往表现不代表未来收益

---

## 🚀 One-Click Deploy Tool

NovaGrid 内置了**零门槛部署工具**——不需要会 Linux，不需要输入命令行，全在网页上完成。

### 架构

```
┌─ 已有 Web 服务器（端口 5000） ─────────────────┐
│                                                  │
│  /deploy/  → 部署表单（填 IP/密码/API 密钥）    │
│  POST /api/deploy/start → 启动后台部署线程      │
│  GET  /api/deploy/progress → 实时显示部署进度   │
│                                                  │
│  deploy_worker.py  →  SSH 连目标 → 上传文件     │
│                     → 装依赖 → 启动 @5002       │
└──────────────────────────────────────────────────┘
```

### 使用方式

1. 运行 NovaGrid（`python web_server.py`）
2. 浏览器打开 `http://你的服务器IP:5000/deploy/`
3. 填写目标服务器信息 + OKX API 密钥 + 交易参数
4. 点击「开始部署」→ 全程实时进度条
5. 部署完成后访问 `http://目标服务器IP:5002/` 看仪表盘

### 部署文件

| 文件 | 说明 |
|------|------|
| `deploy/deploy.html` | 部署表单前端页面 |
| `deploy/deploy_worker.py` | 后台部署引擎（SSH + paramiko） |
| `deploy/dist/` | 完整机器人发行包（自动上传） |
| `deploy/setup.sh` | 服务器初始化脚本 |

> 部署工具自动上传整个 `dist/` 目录到目标服务器，包含网格引擎、Web 仪表盘、信号过滤器、资金管理等所有模块。

---

## 🧩 OpenClaw / Claude Code Skill

NovaGrid Deploy 以 [AgentSkill](https://clawhub.ai) 形式提供，可被 OpenClaw 和 Claude Code 直接安装使用。

### 安装

```bash
# 从 OpenClaw
openclaw skill install novagrid-deploy

# 或手动克隆 repo
cd ~/.openclaw/skills/
git clone https://github.com/aron666aron/nova-grid.git novagrid-deploy
openclaw skill reload
```

技能文件位于 `.openclaw/skills/novagrid-deploy/`，包含完整的 deploy 文件和部署文档。

---

## 🤝 贡献

欢迎提交 Issues 和 Pull Requests！

---

## 📄 许可证

[MIT License](LICENSE)

---

<p align="center">
  <b>NovaGrid</b> — 让网格交易更智能
</p>
