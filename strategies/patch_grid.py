"""修补 grid_bot.py — 只修改 3 处：
1. run_tick: 加止损 + 网格跟踪
2. _get_equity_approx: 新增方法
3. _force_close: 实盘关 OKX
"""
import os

path = os.path.expanduser("~/quant-bot/strategies/grid_bot.py")
with open(path, "r") as f:
    code = f.read()

# 1. run_tick 止损 + 网格跟踪
old = '''        result["msg"] = "达到每日交易上限"
            return result

        # 首次运行/参数变更/价格大幅偏离 → 重建网格
        if not self.grids or self._need_rebuild or abs(current_price - self.center_price) / max(self.center_price, 1e-8) > self.price_range_pct * 0.7:'''

new = '''        result["msg"] = "达到每日交易上限"
            return result

        # ── 止损 ──
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
            stop_loss_pct = 0.15
            stop_loss_amt = self._get_equity_approx() * stop_loss_pct
            if total_upl < -stop_loss_amt and total_upl < 0:
                logger.warning(f"止损触发: UPL=${total_upl:.4f} < -${stop_loss_amt:.4f}")
                result["msg"] = f"止损 - 浮亏${total_upl:.4f}"
                for key in list(self.positions.keys()):
                    self._force_close(int(key), current_price)
                result["actions"].append({"action": "STOP_LOSS", "reason": f"upl=${total_upl:.4f}"})
                self.save_state()
                return result

        # ── 网格跟踪 ──
        trail_pct = self.price_range_pct * 0.6
        if self.grids and abs(current_price - self.center_price) / max(self.center_price, 1e-8) > trail_pct:
            self._need_rebuild = True

        if not self.grids or self._need_rebuild or abs(current_price - self.center_price) / max(self.center_price, 1e-8) > self.price_range_pct * 0.8:'''

code = code.replace(old, new)
assert old not in code, "patch 1 failed"

# 2. _get_equity_approx (add before _sync_positions_from_okx)
old2 = '''    def _sync_positions_from_okx(self):'''
new2 = '''    def _get_equity_approx(self):
        try:
            from okx_trade import _request
            r = _request('GET', '/api/v5/account/balance?ccy=USDT')
            if r.get('code') == '0':
                return float(r['data'][0].get('totalEq', 0))
        except:
            pass
        return 14.0

    def _sync_positions_from_okx(self):'''

code = code.replace(old2, new2)
assert old2 not in code, "patch 2 failed"

# 3. _force_close - add OKX close
old3 = '''        side = pos["side"]

        fee_rate = FEES["default"]
        fee = amount * current_price * fee_rate'''
new3 = '''        side = pos["side"]

        if pos.get('live') and not pos.get('synced_from_okx'):
            try:
                from okx_trade import place_order
                swap_id = SWAP_INSTRUMENTS.get(self.symbol, self.symbol)
                sz = _get_swap_sz(amount)
                if side == 'LONG':
                    place_order(swap_id, 'sell', sz, ord_type='market',
                                td_mode='cross', pos_side='long', paper_mode_override=False)
                else:
                    place_order(swap_id, 'buy', sz, ord_type='market',
                                td_mode='cross', pos_side='short', paper_mode_override=False)
                logger.info(f"[OKX强平] {side} {sz}张")
            except Exception as e:
                logger.error(f"强平OKX失败: {e}")

        fee_rate = FEES["default"]
        fee = amount * current_price * fee_rate'''

code = code.replace(old3, new3)
assert old3 not in code, "patch 3 failed"

with open(path, "w") as f:
    f.write(code)
print(f"OK: {len(code)} chars patched")
