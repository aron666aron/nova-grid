# -*- coding: utf-8 -*-
"""
资金管理引擎 - 根据账户余额自动计算最优仓位

核心逻辑：
  1. 获取 OKX 账户余额
  2. 按风险比例分配可用资金
  3. 结合杠杆和网格数计算每格数量
  4. 输出资金使用报告

公式：
  deployed = equity × usage_pct
  max_active = grid_count × 0.5  (约一半网格同时有持仓)
  margin_per_position = deployed / max_active
  amount_per_grid = margin_per_position × leverage / current_price
"""
import logging, math
from config import FEES, RISK
from okx_trade import get_account, _request
from okx_market import get_price

logger = logging.getLogger("capital_manager")

# 默认风险参数
DEFAULT_USAGE_PCT = 0.7    # 上限可用 70% 资金
DEFAULT_LEVERAGE = 3        # 永续合约杠杆倍数
MIN_MARGIN_PER_POS = 1      # 每格最低保证金 1 USDT
MIN_GRID_COUNT_CAPITAL = 4  # 资金约束下最低网格数


class CapitalManager:
    def __init__(self):
        self.cache = {}  # {symbol: {result, timestamp}}

    def _parse_balance(self, raw):
        """解析 OKX 余额响应"""
        if not raw or raw.get("code") != "0" or not raw.get("data"):
            return None
        data = raw["data"][0]
        total_eq = float(data.get("totalEq", 0))
        # 找 USDT 余额
        usdt_detail = None
        for d in data.get("details", []):
            if d.get("ccy") == "USDT":
                usdt_detail = d
                break
        if usdt_detail:
            available = float(usdt_detail.get("availEq", usdt_detail.get("availBal", 0)))
        else:
            available = 0
        return {"equity": total_eq, "available": available}

    def analyze(self, symbol, grid_count=20, usage_pct=None, leverage=None):
        """主入口：获取余额 → 计算最优仓位"""
        usage_pct = usage_pct if usage_pct is not None else DEFAULT_USAGE_PCT
        leverage = leverage if leverage is not None else DEFAULT_LEVERAGE

        # 获取余额
        try:
            raw = get_account()
            bal = self._parse_balance(raw)
        except Exception as e:
            logger.warning(f"获取账户余额失败: {e}")
            return self._fallback(symbol, grid_count)

        if not bal:
            return self._fallback(symbol, grid_count)

        equity = bal.get("equity", 0)
        available = bal.get("available", 0)

        # 检查资金账户是否有钱没转过来
        funding_msg = None
        try:
            fb = _request('GET', '/api/v5/asset/balances')
            if fb.get('code') == '0':
                for fd in fb.get('data', []):
                    if fd.get('ccy') == 'USDT':
                        fb_bal = float(fd.get('bal', 0))
                        fb_avail = float(fd.get('availBal', 0))
                        if fb_avail > 0.1 and fb_avail > equity:
                            funding_msg = f"资金账户有 ${fb_avail:.2f} USDT，需转至交易账户"
                        break
        except Exception:
            pass

        if equity <= 0.01:
            logger.warning("账户余额为 0")
            if funding_msg:
                warn_result = self._fallback(symbol, grid_count)
                warn_result["warnings"] = [funding_msg] + warn_result["warnings"]
                return warn_result
            return self._fallback(symbol, grid_count)

        # 获取当前价格
        try:
            market = get_price(symbol)
            price = market["price"] if market else None
        except Exception:
            price = None

        if not price:
            return self._fallback(symbol, grid_count)

        return self._calculate(symbol, price, equity, available, grid_count, usage_pct, leverage, funding_msg)

    def _calculate(self, symbol, price, equity, available, grid_count, usage_pct, leverage, funding_msg=None):
        """核心计算"""
        # 可部署资金
        deployed = equity * usage_pct
        deployed = min(deployed, available * 0.95)  # 不超过可用余额的 95%

        # 预计同时持仓数量（约一半网格）
        max_active = max(int(grid_count * 0.5), 1)

        # 每格保证金
        margin_per = deployed / max_active

        # 杠杆后购买力
        buying_power = margin_per * leverage

        # 每格数量
        amount = int(buying_power / price) if price > 0 else 0
        amount = max(amount, 1)

        # 实际使用的总保证金
        total_margin_used = margin_per * max_active
        actual_usage_pct = total_margin_used / equity if equity > 0 else 0

        # 计算最大可承受价格波动
        liquidation_buffer = self._estimate_liquidation_risk(price, leverage)

        # 根据资金量推荐最大网格数（保证每格保证金 >= $1）
        max_groups_by_capital = int((deployed / MIN_MARGIN_PER_POS) * 1.8)  # 每格位置数量 ≈ grids * 0.55
        max_groups_by_capital = max(max_groups_by_capital // 2 * 2, MIN_GRID_COUNT_CAPITAL)
        suggested_grids = min(grid_count, max_groups_by_capital)

        result = {
            "equity": round(equity, 2),
            "available": round(available, 2),
            "deployed": round(deployed, 2),
            "usage_pct": round(usage_pct * 100, 1),
            "leverage": leverage,
            "max_active_positions": max_active,
            "margin_per_position": round(margin_per, 4),
            "suggested_grid_count": suggested_grids,
            "market_recommended_grids": grid_count,
            "buying_power_per_position": round(buying_power, 4),
            "amount_per_grid": amount,
            "margin_value": round(amount * price, 4),
            "total_margin_deployed": round(total_margin_used, 2),
            "actual_usage_pct": round(actual_usage_pct * 100, 1),
            "liquidation_buffer_pct": round(liquidation_buffer, 1),
            "current_price": round(price, 8),
            "recommendations": [],
            "warnings": [],
        }

        # 生成建议和警告
        if margin_per < MIN_MARGIN_PER_POS:
            result["warnings"].append(f"每格保证金 ${margin_per:.2f} 过低 (<${MIN_MARGIN_PER_POS})，建议降低网格数或提高杠杆")

        if leverage > 5:
            result["warnings"].append(f"杠杆 {leverage}× 较高，注意强平风险")

        if result["actual_usage_pct"] > 80:
            result["warnings"].append(f"资金使用率 {result['actual_usage_pct']}% 过高，建议降低 usage_pct")

        if equity < 10:
            result["warnings"].append("余额不足 $10，网格交易效率较低，建议至少 $50")

        if equity >= 50:
            result["recommendations"].append("资金充足，当前配置合理")

        result["recommendations"].append(f"建议每格做 {result['amount_per_grid']} {symbol.split('-')[0]}")

        if funding_msg:
            result["recommendations"].insert(0, funding_msg)
            result["warnings"].append("充值后需从资金账户转至交易账户才可交易")

        logger.info(f"[{symbol}] 余额 ${equity} → 每格{amount} {symbol.split('-')[0]} "
                     f"(保证金 ${margin_per:.2f}, 杠杆{leverage}×, 使用率{result['actual_usage_pct']}%)")
        return result

    def _estimate_liquidation_risk(self, price, leverage):
        """估算强平缓冲距离（%）"""
        # 5x → 20% 反向 → 强平（简化模型）
        return 100 / leverage

    def _fallback(self, symbol, grid_count):
        """降级：返回默认推荐"""
        # 获取价格做参考
        try:
            market = get_price(symbol)
            price = market["price"] if market else 0.1
        except Exception:
            price = 0.1

        return {
            "equity": 0,
            "available": 0,
            "deployed": 0,
            "usage_pct": 0,
            "leverage": DEFAULT_LEVERAGE,
            "max_active_positions": max(int(grid_count * 0.5), 1),
            "margin_per_position": 0,
            "buying_power_per_position": 0,
            "amount_per_grid": 200,  # 默认值
            "margin_value": 0,
            "total_margin_deployed": 0,
            "actual_usage_pct": 0,
            "liquidation_buffer_pct": round(100 / DEFAULT_LEVERAGE, 1),
            "current_price": round(price, 8),
            "recommendations": ["无法获取余额，使用默认每格数量 200"],
            "warnings": ["无法连接 OKX 账户，请检查 API 权限"],
        }
