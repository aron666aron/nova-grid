# -*- coding: utf-8 -*-
"""
智能盈利优化器

以净利润为目标，持续跟踪交易盈利指标，
动态调整网格参数以最大化净利润。
"""
import logging
from collections import deque
from datetime import datetime

logger = logging.getLogger("profit_optimizer")


# ─── 默认阈值 ──────────────────────────
MIN_PROFIT_TARGET = 0.01        # 每笔最⼩目标净利润 $0.01
MAX_FEE_RATIO = 0.40            # 手续费/毛利比 >40% 报警
MIN_WIN_RATE = 35.0             # 最低胜率 35%
MAX_CONSECUTIVE_LOSSES = 3      # 允许连续亏损次数
HIGH_PROFIT_THRESHOLD = 0.05    # 高利润阈值 $0.05
HIGH_TRADE_FREQ = 20            # 每小时 20 笔以上算高频
EMERGENCY_UPL_THRESHOLD = -1.40 # 浮亏超过此值紧急减仓
TRADE_HISTORY_SIZE = 200        # 保留最近 200 笔交易历史


class ProfitOptimizer:
    """智能盈利优化器"""

    def __init__(self):
        self.trade_history = deque(maxlen=TRADE_HISTORY_SIZE)
        self.consecutive_losses = 0
        self.total_gross_profit = 0.0
        self.total_fees_paid = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.param_change_history = []  # 参数变更日志
        self.last_analysis_time = None
        self.last_param_update = None

    # ─── 记录交易 ────────────────────────

    def record_trade(self, side, gross_profit, total_fees, entry_price, exit_price, amount):
        """记录⼀笔已完成的交易"""
        net_profit = gross_profit - total_fees
        is_win = net_profit >= 0

        trade = {
            "time": datetime.now().isoformat(),
            "side": side,
            "gross_profit": round(gross_profit, 8),
            "total_fees": round(total_fees, 8),
            "net_profit": round(net_profit, 8),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "amount": amount,
            "is_win": is_win,
        }
        self.trade_history.append(trade)

        self.total_gross_profit += gross_profit
        self.total_fees_paid += total_fees
        self.total_trades += 1

        if is_win:
            self.winning_trades += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    # ─── 指标计算 ────────────────────────

    @property
    def win_rate(self):
        """胜率（%）"""
        if self.total_trades == 0:
            return 0.0
        return round(self.winning_trades / self.total_trades * 100, 1)

    @property
    def avg_gross_profit(self):
        """平均毛利润/笔"""
        if self.total_trades == 0:
            return 0.0
        return round(self.total_gross_profit / self.total_trades, 6)

    @property
    def avg_net_profit(self):
        """平均净利润/笔"""
        if self.total_trades == 0:
            return 0.0
        total_net = self.total_gross_profit - self.total_fees_paid
        return round(total_net / self.total_trades, 6)

    @property
    def fee_to_profit_ratio(self):
        """手续费占总毛利百分比"""
        if self.total_gross_profit == 0:
            return 0.0
        return round(self.total_fees_paid / self.total_gross_profit, 4)

    @property
    def profit_per_trade_history(self):
        """每笔净利润列表（最近 50 笔）"""
        return [t["net_profit"] for t in list(self.trade_history)[-50:]]

    @property
    def recent_trade_frequency(self):
        """过去 1 小时的交易频率"""
        if not self.trade_history:
            return 0
        now = datetime.now()
        one_hour_ago = now.timestamp() - 3600
        recent = [t for t in self.trade_history
                  if datetime.fromisoformat(t["time"]).timestamp() >= one_hour_ago]
        return len(recent)  # 笔/小时

    # ─── 智能决策分析 ────────────────────

    def analyze(self, bot, okx_data=None):
        """
        分析当前盈利状况，返回参数变更建议

        Parameters:
            bot: GridBot 实例（用于读取当前参数）
            okx_data: dict，包含 'unrealized_pnl' 等 OKX 真实数据

        Returns:
            dict: 建议的参数变更，格式如:
                {"price_range_pct": 0.06, "grid_count": 6, ...}
                如果无变更返回 {}
        """
        suggestions = {}
        reasons = []
        urgency = "normal"  # "normal" | "high" | "critical"

        if self.total_trades < 5:
            # 交易样本⾜够再分析
            return {}

        # ── 指标值缓存 ──
        anp = self.avg_net_profit
        fpr = self.fee_to_profit_ratio
        wr = self.win_rate
        cl = self.consecutive_losses
        tf = self.recent_trade_frequency

        # ── 规则 1: 每笔利润太低 ──
        if anp < MIN_PROFIT_TARGET:
            new_range = bot.price_range_pct * 1.15
            new_grids = max(int(bot.grid_count * 0.85), 4)
            suggestions["price_range_pct"] = round(new_range, 4)
            suggestions["grid_count"] = new_grids
            reasons.append(f"每笔利润 ${anp:.4f} < $0.01，扩大范围 15% → ±{new_range*100:.1f}%，减少网格 → {new_grids} 格")
            logger.info(f"[ProfitOpt] 低利润: avg_net=${anp:.4f}, 扩范围→±{new_range*100:.1f}%, 减网格→{new_grids}")

        # ── 规则 2: 手续费占比过高 ──
        if fpr > MAX_FEE_RATIO:
            new_range = bot.price_range_pct * 1.20
            suggestions["price_range_pct"] = round(new_range, 4)
            reasons.append(f"手续费占 ${fpr*100:.1f}% > 40%，扩大范围 20% → ±{new_range*100:.1f}% 减少交易频率")
            logger.info(f"[ProfitOpt] ⾼手续费: fee_ratio={fpr*100:.1f}%, 扩范围→±{new_range*100:.1f}%")

        # ── 规则 3: 低胜率 + 连续亏损 ──
        if wr < MIN_WIN_RATE and cl > MAX_CONSECUTIVE_LOSSES:
            new_amount = max(int(bot.amount_per_grid * 0.8), 10)
            suggestions["amount_per_grid"] = new_amount
            reasons.append(f"胜率 {wr}% < 35%，连续亏损 {cl} 次，减仓 20% → {new_amount} DOGE/格")
            logger.info(f"[ProfitOpt] 低胜率+连续亏损: win_rate={wr}%, losses={cl}, 减仓→{new_amount}")

        # ── 规则 4: 高利润 + ⾼频交易 ──
        if anp > HIGH_PROFIT_THRESHOLD and tf > HIGH_TRADE_FREQ:
            new_range = bot.price_range_pct * 0.90
            suggestions["price_range_pct"] = round(new_range, 4)
            reasons.append(f"每笔 ${anp:.4f} > $0.05 且 $tf 笔/时 > 20，略微缩小范围 10% → ±{new_range*100:.1f}% 捕获更多交易")
            logger.info(f"[ProfitOpt] ⾼利润+⾼频: anp=${anp:.4f}, freq={tf}/h, 缩范围→±{new_range*100:.1f}%")

        # ── 规则 5: 紧急浮亏 ──
        if okx_data and okx_data.get("unrealized_pnl", 0) < EMERGENCY_UPL_THRESHOLD:
            new_amount = max(int(bot.amount_per_grid * 0.5), 5)
            suggestions["amount_per_grid"] = new_amount
            urgency = "critical"
            reasons.append(f"浮亏 ${okx_data['unrealized_pnl']:.2f} < $-1.40，紧急减半 → {new_amount} DOGE/格")
            logger.warning(f"[ProfitOpt] 紧急减仓: upl=${okx_data['unrealized_pnl']:.2f}, 减半→{new_amount}")

        self.last_analysis_time = datetime.now()

        if suggestions:
            self.last_param_update = {
                "time": datetime.now().isoformat(),
                "params": dict(suggestions),
                "reasons": reasons,
                "urgency": urgency,
            }
            self.param_change_history.append(self.last_param_update)
            # 保留最近 20 条变更记录
            if len(self.param_change_history) > 20:
                self.param_change_history[:] = self.param_change_history[-20:]

        return {
            "suggestions": suggestions,
            "reasons": reasons,
            "urgency": urgency,
        }

    # ─── 报告 ────────────────────────────

    def get_report(self):
        """返回当前分析报告 dict"""
        recent = list(self.trade_history)[-50:] if self.trade_history else []
        wins_in_recent = sum(1 for t in recent if t["is_win"])
        losses_in_recent = len(recent) - wins_in_recent

        return {
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "consecutive_losses": self.consecutive_losses,
            "avg_gross_profit": self.avg_gross_profit,
            "avg_net_profit": self.avg_net_profit,
            "total_gross_profit": round(self.total_gross_profit, 4),
            "total_fees_paid": round(self.total_fees_paid, 4),
            "total_net_profit": round(self.total_gross_profit - self.total_fees_paid, 4),
            "fee_to_profit_ratio": self.fee_to_profit_ratio,
            "recent_trade_frequency": self.recent_trade_frequency,
            "recent_trades": {
                "count": len(recent),
                "wins": wins_in_recent,
                "losses": losses_in_recent,
                "recent_win_rate": round(wins_in_recent / max(len(recent), 1) * 100, 1),
            },
            "param_change_history": self.param_change_history[-10:],
            "last_analysis": self.last_analysis_time.isoformat() if self.last_analysis_time else None,
            "status": self._get_status_label(),
        }

    def _get_status_label(self):
        """生成状态标签"""
        if self.total_trades < 5:
            return "collecting"
        if self.win_rate < 30:
            return "underperforming"
        if self.fee_to_profit_ratio > 0.4:
            return "fee_heavy"
        if self.avg_net_profit > 0.05 and self.win_rate > 50:
            return "profitable"
        return "stable"

    def reset(self):
        """重置所有统计数据"""
        self.trade_history.clear()
        self.consecutive_losses = 0
        self.total_gross_profit = 0.0
        self.total_fees_paid = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.param_change_history = []
        self.last_analysis_time = None
