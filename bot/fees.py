"""
fees.py — คำนวณค่าธรรมเนียม Binance TH + ขนาดไม้ (position sizing) สำหรับ crypto-bot

ค่าธรรมเนียม Binance TH Spot (ตรวจ 2026-11-08 จาก FAQ ทางการ):
  - คู่เทียบคริปโต เช่น BTC/USDT : 0.10% ต่อครั้ง (รวม VAT, ไม่แยก maker/taker)
  - คู่เทียบเงินบาท เช่น BTC/THB  : 0.25% ต่อครั้ง (รวม VAT)
  - หักค่าธรรมเนียมจาก "เหรียญที่ได้รับ" (ซื้อ BTC → หักเป็น BTC, ขาย → หักเป็น USDT)
ถ้า Binance TH เปลี่ยนอัตรา ให้แก้แค่ค่าใน FEE_RATES
"""
from dataclasses import dataclass

FEE_RATES = {"BTCUSDT": 0.0010, "BTCTHB": 0.0025}


@dataclass
class TradePlan:
    entry: float          # ราคาเข้า
    stop: float           # ราคาตัดขาดทุน
    target: float         # ราคาทำกำไร
    notional: float       # เงินที่ใช้ซื้อ (USDT)
    fee_rate: float = FEE_RATES["BTCUSDT"]

    def qty_received(self) -> float:
        """จำนวน BTC ที่ได้จริงหลังหักค่าธรรมเนียมฝั่งซื้อ"""
        return self.notional / self.entry * (1 - self.fee_rate)

    def pnl_at(self, exit_price: float) -> dict:
        qty = self.qty_received()
        gross_proceeds = qty * exit_price
        sell_fee = gross_proceeds * self.fee_rate
        buy_fee = self.notional * self.fee_rate  # มูลค่าเทียบเป็น USDT
        net = gross_proceeds - sell_fee - self.notional
        return {"exit": exit_price, "buy_fee": buy_fee, "sell_fee": sell_fee,
                "total_fee": buy_fee + sell_fee, "net_pnl": net}

    def summary(self) -> dict:
        win, loss = self.pnl_at(self.target), self.pnl_at(self.stop)
        gross_rr = (self.target - self.entry) / (self.entry - self.stop)
        net_rr = win["net_pnl"] / -loss["net_pnl"]
        return {
            "gross_rr": gross_rr, "net_rr": net_rr,
            "win_usdt": win["net_pnl"], "loss_usdt": loss["net_pnl"],
            "fees_if_win": win["total_fee"], "fees_if_loss": loss["total_fee"],
            "breakeven_winrate_gross": 1 / (1 + gross_rr),
            "breakeven_winrate_net": 1 / (1 + net_rr),
            "breakeven_move_pct": (1 / (1 - self.fee_rate) ** 2 - 1) * 100,
        }


def position_size(capital: float, risk_pct: float, entry: float, stop: float,
                  fee_rate: float = FEE_RATES["BTCUSDT"], min_notional: float = 5.0) -> float:
    """
    คืนจำนวน USDT ที่ควรใช้ซื้อ เพื่อให้ถ้าโดน stop จะเสียไม่เกิน risk_pct ของทุน (รวมค่าธรรมเนียมแล้ว)
    Spot ไม่มีเลเวอเรจ → ใช้ได้ไม่เกินทุนที่มี
    คืน 0 ถ้าขนาดไม้ต่ำกว่าขั้นต่ำของกระดาน (min_notional ควรอ่านจาก exchangeInfo จริง)
    """
    risk_usdt = capital * risk_pct
    # ขาดทุนต่อ 1 USDT ที่ลง เมื่อโดน stop (รวมค่าธรรมเนียมทั้งสองฝั่ง)
    loss_per_usdt = 1 - (1 - fee_rate) ** 2 * (stop / entry)
    size = min(risk_usdt / loss_per_usdt, capital)
    return 0.0 if size < min_notional else size


if __name__ == "__main__":
    CAP, RISK = 300.0, 0.01
    E = 100_000.0  # ราคาสมมติ ใช้แค่ดูสัดส่วน %
    # (ชื่อ, ระยะ SL %, ระยะ TP %) — ตัวอย่างระยะทั่วไปของแต่ละกลยุทธ์
    cases = [
        ("1 EMA Trend pullback", 3.0, 6.0),
        ("2 S/R Flip", 1.5, 3.0),
        ("3 Order Block (SL แคบ)", 0.9, 1.8),
        ("4 Divergence", 2.0, 4.0),
        ("5 Range box", 1.2, 2.4),
    ]
    print(f"ทุน ${CAP:.0f} | เสี่ยงต่อไม้ {RISK*100:.0f}% = ${CAP*RISK:.2f} | ค่าธรรมเนียม BTC/USDT 0.10%/ครั้ง\n")
    for name, sl, tp in cases:
        stop, target = E * (1 - sl / 100), E * (1 + tp / 100)
        size = position_size(CAP, RISK, E, stop)
        s = TradePlan(E, stop, target, size).summary()
        print(f"{name:26s} SL {sl:>3}% TP {tp:>3}% | ไม้ละ ${size:6.2f} | "
              f"ชนะ +${s['win_usdt']:.2f} แพ้ ${s['loss_usdt']:.2f} | "
              f"R:R 1:{s['gross_rr']:.2f} → หลังค่าธรรมเนียม 1:{s['net_rr']:.2f} | "
              f"ต้องชนะ ≥{s['breakeven_winrate_net']*100:.1f}% (เดิม {s['breakeven_winrate_gross']*100:.1f}%)")
    print(f"\nราคาต้องขึ้นอย่างน้อย {(1/(1-0.001)**2-1)*100:.3f}% ถึงจะเท่าทุน (BTC/USDT)")
    print(f"ถ้าใช้คู่ BTC/THB (0.25%) ต้องขึ้น {(1/(1-0.0025)**2-1)*100:.3f}%")
