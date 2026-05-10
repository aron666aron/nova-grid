# -*- coding: utf-8 -*-
"""量化交易系统 - 主入口"""
import sys, os, json, time
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SYMBOLS, PAPER_TRADING, HTX_API_KEY
from strategies.grid_bot import GridBot
from backtest.backtest_engine import BacktestEngine
from arb_monitor import ArbMonitor

# 实盘交易模块（按需导入）
if not PAPER_TRADING:
    from htx_trade import get_accounts, get_balance, get_open_orders, cancel_order

def cmd_status():
    """系统状态"""
    bot = GridBot()
    status = bot.get_status()
    mode = '实盘交易' if not PAPER_TRADING else '模拟交易'
    api_status = '✅ 已配置' if HTX_API_KEY else '❌ 未配置（仅支持模拟）'
    lines = [
        "\n🤖 量化交易系统状态",
        "=" * 40,
        f" 模式: {mode}",
        f" API Key: {api_status}",
        f" 交易对: {status['symbol'].upper()}",
        f" 当前价: ${status['price']:,.2f}" if status['price'] else " 当前价: 获取失败",
        f" 网格数: {status['grids']}",
        f" 价格区间: {status['price_range'][0]:,.0f} ~ {status['price_range'][1]:,.0f}",
        f" 活跃仓位: {status['active_positions']}",
        f" 日内交易: {status['daily_trades']}",
        f" 总盈亏: {status['total_pnl']} USDT",
        "=" * 40,
    ]
    if not PAPER_TRADING:
        try:
            bal = get_balance()
            lines.append(f"\n💰 账户余额:")
            for cur, amt in bal.items():
                lines.append(f"   {cur.upper()}: {amt}")
        except Exception as e:
            lines.append(f"\n⚠️  无法获取余额: {e}")
    return "\n".join(lines)

def cmd_run(ticks=5, interval=10):
    """运行网格交易"""
    bot = GridBot()
    mode_text = '🔴实盘模式' if not PAPER_TRADING else '🟢模拟模式'
    print(f"\n🚀 启动网格交易 ({mode_text}) - {ticks}个tick, 间隔{interval}秒")
    print(f"交易对: {bot.symbol.upper()}")
    print(f"网格: {bot.price_lower:,.0f} ~ {bot.price_upper:,.0f} ({bot.grid_count}格)")
    if not PAPER_TRADING:
        print("⚠️  这是真金白银交易！请确认参数正确！")

    for i in range(ticks):
        result = bot.run_tick()
        ts = datetime.now().strftime("%H:%M:%S")
        if result.get("actions"):
            for action in result["actions"]:
                print(f"  [{ts}] {json.dumps(action, ensure_ascii=False)}")
        else:
            print(f"  [{ts}] 价格=${result.get('price','?'):,.2f} 无操作")
        if i < ticks - 1:
            time.sleep(interval)

    status = bot.get_status()
    return f"\n✅ 完成 | 总盈亏: {status['total_pnl']} USDT | 交易: {status['daily_trades']}笔"

def cmd_backtest():
    """运行回测"""
    engine = BacktestEngine("btcusdt")
    if not engine.load_data(period="4hour", size=500):
        return "回测失败: 无法获取K线数据"

    result = engine.run_grid_backtest(grid_count=10)
    report = engine.summary()
    engine.save_report()
    return report

def cmd_arb():
    """套利扫描"""
    monitor = ArbMonitor()
    data = monitor.scan()
    return monitor.summary(data)

def cmd_account():
    """查询账户信息（实盘专用）"""
    if PAPER_TRADING:
        return "❌ 当前是模拟模式，无法查询真实账户"
    lines = ["\n🏦 HTX 账户信息", "=" * 40]
    try:
        accts = get_accounts()
        for a in accts:
            lines.append(f"  账户ID: {a['id']} | 类型: {a['type']} | 状态: {a['state']}")
        lines.append("")
        bal = get_balance()
        lines.append("💰 余额:")
        for cur, amt in sorted(bal.items()):
            lines.append(f"  {cur.upper()}: {amt}")
        lines.append("")
        orders = get_open_orders()
        lines.append(f"📋 当前委托单: {len(orders)} 笔")
        for o in orders[:5]:
            lines.append(f"  {o['symbol']} {o['type']} price={o['price']} amount={o['amount']} id={o['id']}")
    except Exception as e:
        lines.append(f"❌ 查询失败: {e}")
    lines.append("=" * 40)
    return "\n".join(lines)


def cmd_web():
    """启动 Web 看板"""
    import subprocess
    web_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_server.py')
    print("\n🌐 正在启动 Web 看板...")
    print("   地址: http://localhost:5000")
    print("   按 Ctrl+C 停止\n")
    subprocess.run([sys.executable, web_path])


def cmd_help():
    return """
量化交易系统 - 可用命令:
  status    - 查看系统状态（含账户余额）
  run       - 运行网格交易（模拟/实盘取决于配置）
  backtest  - 回测网格策略
  arb       - 套利机会扫描
  account   - 查询HTX账户信息（实盘）
  web       - 启动 Web 看板（浏览器实时监控）
  help      - 显示此帮助"""

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    commands = {
        "status": cmd_status,
        "run": lambda: cmd_run(),
        "backtest": cmd_backtest,
        "arb": cmd_arb,
        "account": cmd_account,
        "web": cmd_web,
        "help": cmd_help,
    }

    func = commands.get(cmd)
    if func:
        print(func())
    else:
        print(f"未知命令: {cmd}")
        print(cmd_help())
