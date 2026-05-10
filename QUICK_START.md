# ⚡ NovaGrid 极速上手指南（3 分钟）

> 如果你完全不懂技术也能搞定，只需按以下步骤操作。

---

## 第 1 步：搞一台服务器

推荐阿里云香港轻量应用服务器（¥24/月），选 Ubuntu 系统。

> 国内服务器无法访问 OKX API，**必须用香港或海外服务器**。

购买后记下 **服务器 IP**，SSH 登录密码会发到你的手机/邮箱。

## 第 2 步：登录服务器

```bash
# Windows 用户打开 PowerShell 或 CMD
ssh root@你的服务器IP
# 输入密码（粘贴进去，不会显示）
```

## 第 3 步：下载 NovaGrid

```bash
git clone https://github.com/你的用户名/nova-grid.git
cd nova-grid
```

## 第 4 步：配置 API 密钥

```bash
cp .env.example .env
nano .env
```

把文件内容改成：

```
OKX_API_KEY=你的API_Key
OKX_SECRET_KEY=你的Secret_Key
OKX_PASSPHRASE=你的通行短语
```

按 `Ctrl+X` → `Y` → `Enter` 保存退出。

## 第 5 步：一键部署

```bash
./setup.sh
```

等待安装完成（约 1 分钟）。

## 第 6 步：启动

```bash
source venv/bin/activate
nohup ./venv/bin/python web_server.py > logs/dashboard.log 2>&1 &
```

## 第 7 步：打开仪表盘

浏览器访问: `http://你的服务器IP:5000`

你会看到：

| 区块 | 说明 |
|------|------|
| 📊 状态 | 运行中、模式、价格 |
| 💰 盈亏 | 赚了多少钱 |
| ⚙ 配置 | 调参数的地方 |
| 🧠 信号 | 市场分析结果 |

注意右上角**模式**：默认是 `paper`（模拟），别急着改 `live`。

## 第 8 步：以后每次登录

```bash
ssh root@你的服务器IP
cd ~/nova-grid
source venv/bin/activate
python web_server.py    # 如果已经后台运行了就不用再启动
```

---

## 📺 查看日志

```bash
tail -f logs/dashboard.log    # 实时看
tail -100 logs/dashboard.log  # 看最近 100 行
```

## 🛑 停止

```bash
./stop.sh
```

---

## ❓ 卡住了？

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `git clone` 失败 | 没装 git | `apt install git -y` |
| `pip install` 报错 | 网络问题 | 多试几次 |
| 页面打不开 | 防火墙没开放端口 | `ufw allow 5000` |
| 登录不上服务器 | 密码不对 | 阿里云控制台重置密码 |

---

如有任何问题，欢迎提 GitHub Issue！
