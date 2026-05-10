# -*- coding: utf-8 -*-
"""
双向网格策略（永续合约 USDT-M）

核心逻辑：
  下半区做多 (LONG)：  价格跌到网格 → 买入 → 涨到上一格 → 卖出获利
  上半区做空 (SHORT)： 价格涨到网格 → 做空 → 跌到下一格 → 平仓获利

两侧独立运行，价格震荡时双倍盈利。
"""
import json, os, time, logging, math, random
from datetime import datetime
from config import GRID, RISK, FEES, DATA_DIR, LOG_DIR, PAPER_TRADING
from okx_market import get_price
from okx_trade import place_order, set_leverage

# 永续合约映射（现货符号 → 永续合约符号）
SWAP_INSTRUMENTS = {
    "BTC-USDT": "BTC-USDT-SWAP",
    "ETH-USDT": "ETH-USDT-SWAP",
    "SOL-USDT": "SOL-USDT-SWAP",
    "DOGE-USDT": "DOGE-USDT-SWAP",
}

logger = logging.getLogger("grid_bot")


def _get_swap_sz(amount_doge):
    """将 DOGE 数量转为永续合约张数（1 张 = 1000 DOGE）"""
    sz = round(amount_doge / 1000, 2)
    return max(sz, 0.01)


class GridBot:
    """双向网格交易机器人"""

    def __init__(self, paper_mode=None):
        self.paper_mode = PAPER_TRADING if paper_mode is None else paper_mode
        self._need_rebuild = False
        g = GRID
        self.symbol = g.get("symbol", "BTC-USDT")
        self.grid_count = g.get("grid_count", 20)
        self.price_range_pct = g.get("price_range_pct", 0.02)
        self.amount_per_grid = g.get("amount_per_grid", 200)
        self.check_interval = g.get("check_interval", 3)
        self.side_mode = g.get("side", "dual")  # "long" | "short" | "dual"

        # 网格数据
        self.center_price = 0.108
        self.grids = []               # 所有网格价格 [p0, p1, ..., pN]
        self.long_indices = set()     # 做多网格索引（下半区）
        self.short_indices = set()    # 做空网格索引（上半区）

        # 持仓：{str(idx): {"side": "LONG"/"SHORT", "entry_price": x, "amount": y, ...}}
        self.positions = {}

        # 状态
        self.prev_price = None
        self.daily_trades = 0
        self.long_pnl = 0.0
        self.short_pnl = 0.0
        self.long_fees = 0.0
        self.short_fees = 0.0

        self.state_file = os.path.join(DATA_DIR, f"grid_{self.symbol}_dual.json")
        self._load_state()
        # 标记：是否已将 OKX 持仓同步到网格
        self._okx_synced = False

    # ────────────────────────────────
    # 网格管理
    # ────────────────────────────────

    def _update_grids(self, center):
        """重建网格。下半做多，上半做空。"""
        half_range = center * self.price_range_pct
        lower = center - half_range
        upper = center + half_range

        # 生成 N+1 个价格节点 (grid_count=20 → 21线, 20间隙)
        node_count = self.grid_count  # 格数 = 线数-1
        step = (upper - lower) / node_count
        grid_prices = [lower + step * i for i in range(node_count + 1)]

        self.grids = [round(p, 8) for p in grid_prices]
        self.center_price = center

        # 分配方向
        mid = node_count // 2
        self.long_indices = set(range(0, mid))
        self.short_indices = set(range(mid, node_count + 1))

        logger.info(f"重建网格 [LONG{len(self.long_indices)} + SHORT{len(self.short_indices)}] "
                     f"${lower:.5f} ~ ${upper:.5f}  步长:{step:.6f}")

    def _get_grid_index(self, price):
        """找到最近的网格线索引"""
        if not self.grids:
            return None
        diffs = [abs(price - g) for g in self.grids]
        return diffs.index(min(diffs))

    # ────────────────────────────────
    # 状态持久化
    # ────────────────────────────────

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.positions = {k: v for k, v in state.get("positions", {}).items()}
                # 实盘模式下重置所有 PnL/Trades — 不继承模拟数据
                if self.paper_mode:
                    self.long_pnl = state.get("long_pnl", 0.0)
                    self.short_pnl = state.get("short_pnl", 0.0)
                    self.long_fees = state.get("long_fees", 0.0)
                    self.short_fees = state.get("short_fees", 0.0)
                    self.daily_trades = state.get("daily_trades", 0)
                else:
                    self.long_pnl = 0.0
                    self.short_pnl = 0.0
                    self.long_fees = 0.0
                    self.short_fees = 0.0
                    self.daily_trades = 0
                self.center_price = state.get("center_price", 0.108)
                logger.info(f"状态恢复: LONG={self._count_side('LONG')} SHORT={self._count_side('SHORT')} "
                             f"PnL(L)={self.long_pnl:.4f} PnL(S)={self.short_pnl:.4f}")
            except Exception:
                logger.warning("状态文件损坏，重新开始")

    def save_state(self):
        state = {
            "positions": self.positions,
            "long_pnl": self.long_pnl,
            "short_pnl": self.short_pnl,
            "long_fees": self.long_fees,
            "short_fees": self.short_fees,
            "daily_trades": self.daily_trades,
            "center_price": self.center_price,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _count_side(self, side):
        return sum(1 for p in self.positions.values() if p["side"] == side)

    # ────────────────────────────────
    # 价格模拟
    # ────────────────────────────────

    def _sim_price(self, real_price):
        """模拟模式下加噪音，噪音匹配网格步长"""
        if not self.paper_mode:
            return real_price
        if len(self.grids) >= 2:
            step = self.grids[1] - self.grids[0]
            noise = step * 0.5 * random.uniform(-1, 1)
        else:
            noise = 0
        return real_price + noise

    # ────────────────────────────────
    # 核心 TICK
    # ────────────────────────────────

    def run_tick(self):
        """执行一次 tick"""
        market = get_price(self.symbol)
        if not market:
            logger.error("获取行情失败")
            return {"status": "error", "msg": "行情获取失败"}

        current_price = self._sim_price(market["price"])
        result = {"status": "ok", "price": current_price, "actions": []}

        # ─── 风控：止损 ───
        if not self.paper_mode:
            total_upl = 0.0
            try:
                from okx_trade import _request
                rp = _request('GET', f'/api/v5/account/positions')
                if rp.get('code') == '0':
                    for p in rp['data']:
                        total_upl += float(p.get('upl', 0))
            except:
                pass
            stop_loss_pct = 0.15  # 本金 15%
            stop_loss_amt = self._get_equity_approx() * stop_loss_pct
            if total_upl < -stop_loss_amt and total_upl < 0:
                logger.warning(f"止损触发: UPL=${total_upl:.4f} < -${stop_loss_amt:.4f}")
                result["msg"] = f"止损 - 浮亏${total_upl:.4f}"
                for key in list(self.positions.keys()):
                    self._force_close(int(key), current_price)
                result["actions"].append({"action": "STOP_LOSS", "reason": f"upl=${total_upl:.4f}"})
                self.save_state()
                return result

        # 风控
        if self.daily_trades >= RISK["max_daily_trades"]:
            result["msg"] = "达到每日交易上限"
            return result

        # 网格跟踪：价格平移超过 0.3% → 重建网格（跟随趋势）
        grid_trail_pct = self.price_range_pct * 0.6
        if self.grids and abs(current_price - self.center_price) / max(self.center_price, 1e-8) > grid_trail_pct:
            self._need_rebuild = True

        # 首次运行/参数变更/价格大幅偏离 → 重建网格
        if not self.grids or self._need_rebuild or abs(current_price - self.center_price) / max(self.center_price, 1e-8) > self.price_range_pct * 0.8:
            if self._need_rebuild or not self.grids:
                self._update_grids(current_price)
                self._need_rebuild = False
                # 网格建好后：从 OKX 同步真实持仓
                if not self.paper_mode and not self._okx_synced:
                    self._sync_positions_from_okx()
                    self._okx_synced = True
            # 强平所有旧持仓（仅非OKX同步的持仓）
            for key in list(self.positions.keys()):
                pos = self.positions[key]
                if pos.get('synced_from_okx'):
                    continue  # OKX 真实持仓不强制平仓
                self._force_close(int(key), current_price)
            self.prev_price = current_price
            return result

        curr_idx = self._get_grid_index(current_price)
        if curr_idx is None:
            self.prev_price = current_price
            return result

        fee_rate = FEES["default"]

        # ─── 1️⃣ 平仓检查：LONG 涨到目标位 / SHORT 跌到目标位 ───
        for key in list(self.positions.keys()):
            pos = self.positions[key]
            entry_grid = int(key)
            side = pos["side"]

            if side == "LONG":
                target = entry_grid + 1
                if curr_idx >= target and target < len(self.grids):
                    self._close_long(entry_grid, current_price, fee_rate, result)
            elif side == "SHORT":
                target = entry_grid - 1
                if curr_idx <= target and target >= 0:
                    self._close_short(entry_grid, current_price, fee_rate, result)

        # ─── 2️⃣ 开仓检查 ───
        if self.prev_price is not None and self._get_grid_index(self.prev_price) is not None:
            prev_idx = self._get_grid_index(self.prev_price)

            if curr_idx != prev_idx:
                if current_price < self.prev_price:
                    # 价格下跌 → 检查 LONG 入场
                    for idx in range(curr_idx, prev_idx + 1):
                        if idx in self.long_indices and str(idx) not in self.positions:
                            # 多因子过滤：强空头信号时不加多仓
                            if signal_bias < -0.5:
                                continue
                            if signal_bias < -0.3 and signal_confidence == "high":
                                continue
                            if signal_bias < -0.4:
                                if idx % 2 == 0:
                                    continue
                            self._open_long(idx, self.grids[idx], fee_rate, result)
                else:
                    # 价格上涨 → 检查 SHORT 入场
                    for idx in range(prev_idx + 1, curr_idx + 1):
                        if idx in self.short_indices and str(idx) not in self.positions:
                            # 多因子过滤：强多头信号时不加空仓
                            if signal_bias > 0.5:
                                continue
                            if signal_bias > 0.3 and signal_confidence == "high":
                                continue
                            if signal_bias > 0.4:
                                if idx % 2 == 0:
                                    continue
                            self._open_short(idx, self.grids[idx], fee_rate, result)

        self.prev_price = current_price
        self.save_state()
        return result

    # ────────────────────────────────
    # LONG 操作
    # ────────────────────────────────

    def _open_long(self, grid_idx, grid_price, fee_rate, result):
        """开多仓 — 买入"""
        amount = self.amount_per_grid
        fee = amount * grid_price * fee_rate
        entry_price = grid_price

        if not self.paper_mode:
            swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
            sz = _get_swap_sz(amount)
            try:
                set_leverage(swap_id, 3, 'cross')
                resp = place_order(swap_id, 'buy', sz, ord_type='market',
                                   td_mode='cross', pos_side='long',
                                   paper_mode_override=False)
                if resp.get('code') == '0':
                    logger.info(f"[实盘LONG BUY] #{grid_idx} ${grid_price:.5f} {sz}张")
                else:
                    logger.error(f"实盘开多失败: {resp.get('msg')}")
                    return
            except Exception as e:
                logger.error(f"实盘开多异常: {e}")
                return

        self.long_fees += fee
        self.positions[str(grid_idx)] = {
            "side": "LONG", "entry_price": entry_price, "amount": amount,
            "time": datetime.now().isoformat(), "fee": round(fee, 8),
            "live": not self.paper_mode,
        }
        self.daily_trades += 1
        logger.info(f"[LONG  BUY ] #{grid_idx} @ ${entry_price:.5f} fee=${fee:.6f}")
        result["actions"].append({
            "action": "BUY", "side": "LONG", "time": datetime.now().strftime("%H:%M:%S"),
            "grid": grid_idx, "price": entry_price, "amount": amount, "fee": round(fee, 8),
        })

    def _close_long(self, grid_idx, current_price, fee_rate, result):
        """平多仓 — 卖出获利"""
        pos = self.positions.pop(str(grid_idx), None)
        if not pos:
            return
        entry = pos["entry_price"]
        amount = pos["amount"]
        buy_fee = pos.get("fee", 0)

        sell_price = current_price
        if not self.paper_mode and pos.get("live"):
            swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
            sz = _get_swap_sz(amount)
            try:
                resp = place_order(swap_id, 'sell', sz, ord_type='market',
                                   td_mode='cross', pos_side='long',
                                   paper_mode_override=False)
                if resp.get('code') != '0':
                    logger.error(f"实盘平多失败: {resp.get('msg')}")
                    self.positions[str(grid_idx)] = pos
                    return
                logger.info(f"[实盘LONG SELL] #{grid_idx} ${sell_price:.5f}")
            except Exception as e:
                logger.error(f"实盘平多异常: {e}")
                self.positions[str(grid_idx)] = pos
                return

        sell_fee = amount * sell_price * fee_rate
        total_fee = buy_fee + sell_fee
        self.long_fees += sell_fee

        gross = (sell_price - entry) * amount
        net = gross - total_fee
        self.long_pnl += gross
        self.daily_trades += 1

        logger.info(f"[LONG SELL] #{grid_idx} @ ${sell_price:.5f} "
                     f"毛:+${gross:.4f} 费:${total_fee:.6f} 净:+${net:.4f}")
        result["actions"].append({
            "action": "SELL", "side": "LONG", "time": datetime.now().strftime("%H:%M:%S"),
            "grid": grid_idx, "price": sell_price,
            "entry_price": entry, "amount": amount,
            "pnl": round(gross, 4), "fee": round(sell_fee, 8),
            "buy_fee": round(buy_fee, 8), "total_fee": round(total_fee, 8),
            "net_pnl": round(net, 4),
        })

    # ────────────────────────────────
    # SHORT 操作
    # ────────────────────────────────

    def _open_short(self, grid_idx, grid_price, fee_rate, result):
        """开空仓 — 做空（高价卖出，等低价买回）"""
        amount = self.amount_per_grid
        fee = amount * grid_price * fee_rate
        entry_price = grid_price

        if not self.paper_mode:
            swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
            sz = _get_swap_sz(amount)
            try:
                set_leverage(swap_id, 3, 'cross')
                resp = place_order(swap_id, 'sell', sz, ord_type='market',
                                   td_mode='cross', pos_side='short',
                                   paper_mode_override=False)
                if resp.get('code') == '0':
                    logger.info(f"[实盘SHORT SELL] #{grid_idx} ${grid_price:.5f} {sz}张")
                else:
                    logger.error(f"实盘开空失败: {resp.get('msg')}")
                    return
            except Exception as e:
                logger.error(f"实盘开空异常: {e}")
                return

        self.short_fees += fee
        self.positions[str(grid_idx)] = {
            "side": "SHORT", "entry_price": entry_price, "amount": amount,
            "time": datetime.now().isoformat(), "fee": round(fee, 8),
            "live": not self.paper_mode,
        }
        self.daily_trades += 1
        logger.info(f"[SHORT SELL] #{grid_idx} @ ${entry_price:.5f} fee=${fee:.6f}")
        result["actions"].append({
            "action": "SELL_SHORT", "side": "SHORT", "time": datetime.now().strftime("%H:%M:%S"),
            "grid": grid_idx, "price": entry_price, "amount": amount, "fee": round(fee, 8),
        })

    def _close_short(self, grid_idx, current_price, fee_rate, result):
        """平空仓 — 买回（低价买回，赚差价）"""
        pos = self.positions.pop(str(grid_idx), None)
        if not pos:
            return
        entry = pos["entry_price"]
        amount = pos["amount"]
        sell_fee = pos.get("fee", 0)

        buy_price = current_price
        if not self.paper_mode and pos.get("live"):
            swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
            sz = _get_swap_sz(amount)
            try:
                resp = place_order(swap_id, 'buy', sz, ord_type='market',
                                   td_mode='cross', pos_side='short',
                                   paper_mode_override=False)
                if resp.get('code') != '0':
                    logger.error(f"实盘平空失败: {resp.get('msg')}")
                    self.positions[str(grid_idx)] = pos
                    return
                logger.info(f"[实盘SHORT BUY] #{grid_idx} ${buy_price:.5f}")
            except Exception as e:
                logger.error(f"实盘平空异常: {e}")
                self.positions[str(grid_idx)] = pos
                return

        buy_fee = amount * buy_price * fee_rate
        total_fee = sell_fee + buy_fee
        self.short_fees += buy_fee

        # 做空利润 = 卖出价 - 买入价
        gross = (entry - buy_price) * amount
        net = gross - total_fee
        self.short_pnl += gross
        self.daily_trades += 1

        logger.info(f"[SHORT BUY ] #{grid_idx} @ ${buy_price:.5f} "
                     f"毛:+${gross:.4f} 费:${total_fee:.6f} 净:+${net:.4f}")
        result["actions"].append({
            "action": "BUY_COVER", "side": "SHORT", "time": datetime.now().strftime("%H:%M:%S"),
            "grid": grid_idx, "price": buy_price,
            "entry_price": entry, "amount": amount,
            "pnl": round(gross, 4), "fee": round(buy_fee, 8),
            "sell_fee": round(sell_fee, 8), "total_fee": round(total_fee, 8),
            "net_pnl": round(net, 4),
        })

    # ────────────────────────────────
    # 强平（网格重建时使用）
    # ────────────────────────────────

    def _force_close(self, grid_idx, current_price):
        """强平：实盘模式同时关 OKX 持仓，内部模式只清内存"""
        pos = self.positions.pop(str(grid_idx), None)
        if not pos:
            return
        entry = pos["entry_price"]
        amount = pos["amount"]
        side = pos["side"]

        # 实盘：同时关掉 OKX 真实持仓
        if pos.get('live') and not pos.get('synced_from_okx'):
            try:
                swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
                sz = _get_swap_sz(amount)
                if side == 'LONG':
                    place_order(swap_id, 'sell', sz, ord_type='market',
                                td_mode='cross', pos_side='long', paper_mode_override=False)
                else:
                    place_order(swap_id, 'buy', sz, ord_type='market',
                                td_mode='cross', pos_side='short', paper_mode_override=False)
                logger.info(f"[OKX强平] {side} {sz}张 @${current_price:.5f}")
            except Exception as e:
                logger.error(f"强平OKX失败: {e}")

        fee_rate = FEES["default"]
        fee = amount * current_price * fee_rate

        if side == "LONG":
            gross = (current_price - entry) * amount
            self.long_pnl += gross
            self.long_fees += fee + pos.get("fee", 0)
        else:
            gross = (entry - current_price) * amount
            self.short_pnl += gross
            self.short_fees += fee + pos.get("fee", 0)

        net = gross - fee - pos.get("fee", 0)
        logger.info(f"[FORCE CLOSE] #{grid_idx} {side} @ ${current_price:.5f} net=${net:.4f}")

    # ────────────────────────────────
    # OKX 持仓同步
    # ────────────────────────────────

    def _get_equity_approx(self):
        """获取账户大致净值"""
        try:
            from okx_trade import _request
            r = _request('GET', '/api/v5/account/balance?ccy=USDT')
            if r.get('code') == '0':
                return float(r['data'][0].get('totalEq', 0))
        except:
            pass
        return 14.0  # fallback

    def _sync_positions_from_okx(self):
        """从 OKX 读取永续合约真实持仓，映射到最近的网格索引"""
        try:
            from okx_trade import _request
            swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
            r = _request('GET', f'/api/v5/account/positions?instId={swap_id}')
            if r.get('code') != '0':
                logger.warning(f"获取OKX持仓失败: {r.get('msg')}")
                return
            okx_positions = r.get('data', [])
            synced = 0
            for p in okx_positions:
                sz = float(p.get('pos', 0))
                if sz <= 0:
                    continue
                amount = int(sz * 1000)  # 张数 → DOGE
                entry = float(p.get('avgPx', 0))
                side = 'SHORT' if p.get('posSide') == 'short' else 'LONG'
                # 检查是否已有对应仓位
                existing = [k for k, v in self.positions.items()
                            if v['side'] == side and abs(v['amount'] - amount) / max(amount, 1) < 0.1]
                if existing:
                    continue
                # 映射到最近的网格索引
                if self.grids:
                    diffs = [abs(entry - g) for g in self.grids]
                    grid_idx = diffs.index(min(diffs))
                else:
                    grid_idx = 0
                # 判断方向是否匹配
                if (side == 'SHORT' and grid_idx not in self.short_indices) or \
                   (side == 'LONG' and grid_idx not in self.long_indices):
                    # 方向不匹配：交换方向映射
                    # 如果价格在上半区但开的是LONG → 映射到下半区边缘
                    # 简单处理：分配到最近的可接受方向
                    if side == 'LONG':
                        grid_idx = max(self.long_indices) if self.long_indices else 0
                    else:
                        grid_idx = min(self.short_indices) if self.short_indices else len(self.grids) - 1
                self.positions[str(grid_idx)] = {
                    'side': side, 'entry_price': entry, 'amount': amount,
                    'time': datetime.now().isoformat(), 'fee': 0,
                    'live': True, 'synced_from_okx': True,
                }
                logger.info(f"[OKX同步] #{grid_idx} {side} {amount}DOGE @ ${entry:.5f}")
                synced += 1
            if synced > 0:
                logger.info(f"从OKX同步了 {synced} 个持仓")
        except Exception as e:
            logger.error(f"同步OKX持仓异常: {e}")

    # ────────────────────────────────
    # 状态查询
    # ────────────────────────────────

    def update_params(self, params):
        """运行时更新参数（不清除仓位）"""
        changed = False
        if "symbol" in params and params["symbol"] != self.symbol:
            self.symbol = params["symbol"]
            changed = True
        if "grid_count" in params:
            self.grid_count = int(params["grid_count"])
            changed = True
        if "price_range_pct" in params:
            self.price_range_pct = float(params["price_range_pct"])
            changed = True
        if "amount_per_grid" in params:
            self.amount_per_grid = int(params["amount_per_grid"])
            changed = True
        if "check_interval" in params:
            self.check_interval = int(params["check_interval"])
            changed = True
        if changed:
            self._need_rebuild = True
        logger.info(f"参数已更新: grids={self.grid_count} range={self.price_range_pct*100:.1f}% amount={self.amount_per_grid}")

    def get_status(self):
        try:
            market = get_price(self.symbol)
        except Exception:
            market = None
        s = {
            "symbol": self.symbol,
            "price": market["price"] if market else None,
            "grids": len(self.grids),
            "price_range": [self.grids[0], self.grids[-1]] if self.grids else [0, 0],
            "active_positions": len(self.positions),
            "long_positions": self._count_side("LONG"),
            "short_positions": self._count_side("SHORT"),
            "daily_trades": self.daily_trades,
            "long_pnl": round(self.long_pnl, 4),
            "short_pnl": round(self.short_pnl, 4),
            "long_fees": round(self.long_fees, 4),
            "short_fees": round(self.short_fees, 4),
            "total_pnl": round(self.long_pnl + self.short_pnl, 4),
            "total_fees": round(self.long_fees + self.short_fees, 4),
            "net_pnl": round((self.long_pnl + self.short_pnl) - (self.long_fees + self.short_fees), 4),
            "paper_trading": self.paper_mode,
        }
        # 实盘模式：追加 OKX 真实盈亏数据
        if not self.paper_mode:
            try:
                from okx_trade import _request
                swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
                r = _request('GET', f'/api/v5/account/positions?instId={swap_id}')
                if r.get('code') == '0':
                    upl_total = 0.0
                    realized_total = 0.0
                    for p in r['data']:
                        sz = float(p.get('pos', 0))
                        if sz == 0:
                            continue
                        upl = float(p.get('upl', 0))
                        realized = float(p.get('realizedPnl', 0))
                        upl_total += upl
                        realized_total += realized
                        s[f"okx_{p['posSide']}_sz"] = float(p.get('pos', 0))
                        s[f"okx_{p['posSide']}_entry"] = float(p.get('avgPx', 0))
                        s[f"okx_{p['posSide']}_upl"] = round(upl, 4)
                    s['okx_unrealized_pnl'] = round(upl_total, 4)
                    s['okx_realized_pnl'] = round(realized_total, 4)
                    s['okx_total_pnl'] = round(upl_total + realized_total, 4)
            except Exception:
                pass
        return s
