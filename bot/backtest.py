#!/usr/bin/env python3
"""
backtest.py — Backtest crypto-bot (Python ล้วน ไม่ต้องติดตั้งไลบรารีเพิ่ม)

วิธีรัน (ใน Terminal ของ VS Code, อยู่ในโฟลเดอร์ bot/):
    python backtest.py                      # รอบ 2: A/B บน 1H, กลยุทธ์ 1–5 บน 4H, cost 0.08% และ 0.12%
    python backtest.py --tf 1h              # บังคับทุกกลยุทธ์รันบน 1H (แบบรอบ 1)
    python backtest.py --costs 0.0012       # ต้นทุนเดียว
    python backtest.py --synthetic          # ทดสอบโค้ดด้วยข้อมูลสุ่ม (ไม่ต้องใช้เน็ต — ผลไม่มีความหมาย)

ผลลัพธ์: พิมพ์ตารางบนจอ + บันทึก results/summary.md และ results/trades_*.csv

กติกาจำลอง (ตั้งใจให้ "มองโลกแง่ร้าย" ไว้ก่อน):
  - สัญญาณคิดจากแท่งที่ปิดแล้ว → เข้าที่ราคาเปิดของแท่งถัดไป (ไม่มองอนาคต)
  - Swing (Fractal 3,3) ใช้ได้หลังแท่งขวาที่ 3 ปิดแล้วเท่านั้น
  - กรอง Daily ใช้ค่าของ "วันก่อนหน้าที่ปิดแล้ว"
  - ถ้าแท่งเดียวกันโดนทั้ง SL และ TP → นับว่าโดน SL
  - ต้นทุน (ค่าธรรมเนียม+slippage) หักทุกออเดอร์ ทั้งขาเข้าและขาออก
  - Spot: Long อย่างเดียว, ไม่มีเลเวอเรจ, ถือได้ทีละ 1 ไม้, แต่ละเหรียญใช้บัญชีแยก $200
"""
import argparse, csv, json, math, os, random, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SOURCES = [  # ลองตามลำดับ ถ้าที่แรกมีข้อมูลย้อนหลังไม่พอจะไปที่ถัดไป
    ("Binance TH", "https://api.binance.th/api/v1/klines"),
    ("Binance (global public data)", "https://data-api.binance.vision/api/v3/klines"),
    ("Binance (global)", "https://api.binance.com/api/v3/klines"),
]

# =====================================================================
# 1) ข้อมูล
# =====================================================================
def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-bot-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _download_from(base, symbol, interval, start_ms, end_ms):
    rows, t = [], start_ms
    window = 7 * DAY_MS if "binance.th" in base else None   # Binance TH: ขอได้ครั้งละไม่เกิน 7 วัน
    while t < end_ms:
        e = min(t + window - 1, end_ms) if window else end_ms
        url = f"{base}?symbol={symbol}&interval={interval}&startTime={t}&endTime={e}&limit=1000"
        batch = _get(url)
        if not batch:
            if window and e < end_ms:      # ช่วงนี้ไม่มีข้อมูล (เช่น ก่อนกระดานเปิด) → ข้ามไปช่วงถัดไป
                t = e + 1
                continue
            break
        rows.extend(batch)
        t = batch[-1][0] + HOUR_MS
        time.sleep(0.15)
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def load_candles(symbol, start_ms, end_ms, data_dir="data"):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{symbol}_1h_{start_ms}_{end_ms}.csv")
    if os.path.exists(path):
        with open(path) as f:
            rd = csv.reader(f); src = next(rd)[0]
            return [tuple([int(r[0])] + [float(x) for x in r[1:]]) for r in rd], src
    last_err = None
    for name, base in SOURCES:
        try:
            print(f"  ดึง {symbol} จาก {name} ...", flush=True)
            data = _download_from(base, symbol, "1h", start_ms, end_ms)
            if data and data[0][0] <= start_ms + 2 * DAY_MS:
                with open(path, "w", newline="") as f:
                    w = csv.writer(f); w.writerow([name]); w.writerows(data)
                return data, name
            have = datetime.fromtimestamp(data[0][0] / 1000, timezone.utc).date() if data else "ไม่มีข้อมูล"
            print(f"    ข้อมูลย้อนหลังไม่พอ (เริ่มที่ {have}) → ลองแหล่งถัดไป")
        except urllib.error.HTTPError as e:
            last_err = e
            body = e.read()[:200].decode(errors="replace")
            print(f"    ใช้ไม่ได้: HTTP {e.code} {body}")
        except Exception as e:  # noqa
            last_err = e
            print(f"    ใช้ไม่ได้: {e}")
    raise SystemExit(f"ดึงข้อมูล {symbol} ไม่ได้จากทุกแหล่ง: {last_err}")


def resample(candles, hours):
    """รวมแท่ง 1H เป็นแท่งใหญ่ (เช่น 4H) ตามเวลา UTC — ทิ้งแท่งที่ไม่ครบ"""
    step = hours * HOUR_MS; out = []; cur = None; n = 0
    for t, o, h, l, c, v in candles:
        b = t // step * step
        if cur and cur[0] == b:
            cur = [b, cur[1], max(cur[2], h), min(cur[3], l), c, cur[5] + v]; n += 1
        else:
            if cur and n == hours:
                out.append(tuple(cur))
            cur = [b, o, h, l, c, v]; n = 1
    if cur and n == hours:
        out.append(tuple(cur))
    return out


def synthetic_candles(start_ms, end_ms, seed, p0):
    """ข้อมูลสุ่มแบบมีช่วงขาขึ้น/ขาลง/ไซด์เวย์ — ใช้ทดสอบโค้ดเท่านั้น"""
    rnd = random.Random(seed); out = []; p = p0; t = start_ms; drift = 0
    while t < end_ms:
        if rnd.random() < 0.003:
            drift = rnd.choice([-0.0004, 0, 0.0004])
        o = p; c = o * math.exp(drift + rnd.gauss(0, 0.007))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.003))); l = min(o, c) * (1 - abs(rnd.gauss(0, 0.003)))
        out.append((t, o, h, l, c, 1.0)); p = c; t += HOUR_MS
    return out

# =====================================================================
# 2) Indicators
# =====================================================================
def ema(vals, n):
    out = [None] * len(vals); k = 2 / (n + 1); e = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        e = v if e is None else v * k + e * (1 - k)
        out[i] = e
    # ทิ้งช่วง warmup
    cnt = 0
    for i, v in enumerate(vals):
        if v is None:
            continue
        cnt += 1
        if cnt < n:
            out[i] = None
    return out


def rsi(close, n=14):
    out = [None] * len(close); ag = al = 0.0
    for i in range(1, len(close)):
        ch = close[i] - close[i - 1]; g, l = max(ch, 0), max(-ch, 0)
        if i <= n:
            ag += g / n; al += l / n
            if i == n:
                out[i] = 100 - 100 / (1 + ag / al) if al else 100
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + ag / al) if al else 100
    return out


def adx(high, low, close, n=14):
    L = len(close); out = [None] * L
    tr_s = pdm_s = ndm_s = 0.0; dx_list = []; adx_v = None
    for i in range(1, L):
        up, dn = high[i] - high[i - 1], low[i - 1] - low[i]
        pdm = up if up > dn and up > 0 else 0.0; ndm = dn if dn > up and dn > 0 else 0.0
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        if i <= n:
            tr_s += tr; pdm_s += pdm; ndm_s += ndm
            if i < n:
                continue
        else:
            tr_s = tr_s - tr_s / n + tr; pdm_s = pdm_s - pdm_s / n + pdm; ndm_s = ndm_s - ndm_s / n + ndm
        pdi = 100 * pdm_s / tr_s if tr_s else 0; ndi = 100 * ndm_s / tr_s if tr_s else 0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0
        if adx_v is None:
            dx_list.append(dx)
            if len(dx_list) == n:
                adx_v = sum(dx_list) / n
        else:
            adx_v = (adx_v * (n - 1) + dx) / n
        out[i] = adx_v
    return out


def fractals(high, low, left=3, right=3):
    """คืน list ของ (index_ของจุด swing, ราคา) — ใช้ได้ตั้งแต่ index + right เป็นต้นไป"""
    sh, sl = [], []
    for j in range(left, len(high) - right):
        win_h = high[j - left:j + right + 1]; win_l = low[j - left:j + right + 1]
        if high[j] == max(win_h) and win_h.count(high[j]) == 1:
            sh.append((j, high[j]))
        if low[j] == min(win_l) and win_l.count(low[j]) == 1:
            sl.append((j, low[j]))
    return sh, sl


class Swings:
    """ให้ swing ที่ยืนยันแล้ว ณ แท่ง i (ไม่มองอนาคต)"""
    def __init__(self, pts, right=3):
        self.pts, self.right, self.k = pts, right, 0

    def upto(self, i, n=None):
        """swing ที่ยืนยันแล้ว ณ แท่ง i — n = เอาแค่ n ตัวล่าสุด (เร็วกว่า)"""
        while self.k < len(self.pts) and self.pts[self.k][0] + self.right <= i:
            self.k += 1
        return self.pts[max(0, self.k - n):self.k] if n else self.pts[:self.k]


def daily_trend_ok(ts, close, ema_n=200):
    """True ถ้า Close(1D) ของวันก่อนหน้าที่ปิดแล้ว > EMA200(1D)"""
    days, dclose = [], []
    for t, c in zip(ts, close):
        d = t // DAY_MS
        if days and days[-1] == d:
            dclose[-1] = c
        else:
            days.append(d); dclose.append(c)
    de = ema(dclose, ema_n)
    ok_by_day = {}
    for k in range(1, len(days)):
        prev_c, prev_e = dclose[k - 1], de[k - 1]
        ok_by_day[days[k]] = prev_e is not None and prev_c > prev_e
    return [ok_by_day.get(t // DAY_MS, False) for t in ts]

# =====================================================================
# 3) Context ที่ทุกกลยุทธ์ใช้ร่วมกัน
# =====================================================================
class Ctx:
    def __init__(self, candles):
        self.t = [c[0] for c in candles]; self.o = [c[1] for c in candles]
        self.h = [c[2] for c in candles]; self.l = [c[3] for c in candles]; self.c = [c[4] for c in candles]
        self.e20, self.e50 = ema(self.c, 20), ema(self.c, 50)
        self.rsi = rsi(self.c, 14); self.rsig = ema(self.rsi, 9)
        self.adx = adx(self.h, self.l, self.c, 14)
        self.trend = daily_trend_ok(self.t, self.c, 200)
        self.sh_pts, self.sl_pts = fractals(self.h, self.l, 3, 3)
        # Swing High บน Daily (ใช้เป็น "แนวต้านถัดไปบน TF ใหญ่") — ใช้ได้หลังวันขวาที่ 3 ปิด
        dk, dh, dl = [], [], []
        for t, h, l in zip(self.t, self.h, self.l):
            d = t // DAY_MS
            if dk and dk[-1] == d:
                dh[-1] = max(dh[-1], h); dl[-1] = min(dl[-1], l)
            else:
                dk.append(d); dh.append(h); dl.append(l)
        dsh, _ = fractals(dh, dl, 3, 3)
        self.d_res = [((dk[j] + 4) * DAY_MS, v) for j, v in dsh]   # (เวลาที่ยืนยัน, ราคา)

    def daily_res_above(self, i, price):
        c = [v for ct, v in self.d_res if ct <= self.t[i] and v > price]
        return min(c) if c else None

    def bull(self, i):
        return self.c[i] > self.o[i]

    def pin_or_engulf(self, i):
        o, h, l, c = self.o[i], self.h[i], self.l[i], self.c[i]
        body = abs(c - o); rng = h - l or 1e-12
        pin = (min(o, c) - l) >= 2 * body and (c - l) / rng >= 0.66
        eng = c > o and self.c[i - 1] < self.o[i - 1] and c >= self.o[i - 1] and o <= self.c[i - 1]
        return pin or eng

# =====================================================================
# 4) กลยุทธ์ — signal(i) คืน dict หรือ None (ดูที่ "ปิดแท่ง i")
#    {"type": "market"|"limit", "price": (limit เท่านั้น), "sl": float,
#     "tp": float | None (None = ใช้ RR), "rr": 2.0, "tps": [(price, frac)] (ถ้ามีหลายจุด)}
# =====================================================================
class Strategy:
    name = "base"
    rr_filter = True        # กลยุทธ์ 1–5: ข้ามไม้ที่ R:R หลังหักต้นทุน < MIN_NET_RR

    def __init__(self, x: Ctx):
        self.x = x; self.SH = Swings(x.sh_pts); self.SL = Swings(x.sl_pts)

    def last_swing_low(self, i):
        p = self.SL.upto(i, 1); return p[-1][1] if p else None

    def swing_high_above(self, i, price):
        self.SH.upto(i, 1)
        for k in range(self.SH.k - 1, -1, -1):
            if self.SH.pts[k][1] > price:
                return self.SH.pts[k][1]
        return None


class StratA(Strategy):
    name = "A: EMA Cross 20/50 + SL Swing Low"
    use_trend = False
    rr_filter = False       # A/B ทดสอบตามที่สั่ง ไม่ใส่ตัวกรองเพิ่ม

    def signal(self, i):
        x = self.x
        if None in (x.e20[i], x.e50[i], x.e20[i - 1], x.e50[i - 1]):
            return None
        cross = x.e20[i - 1] <= x.e50[i - 1] and x.e20[i] > x.e50[i]
        if not cross or (self.use_trend and not x.trend[i]):
            return None
        sl = self.last_swing_low(i)
        if sl is None or sl >= x.c[i]:
            return None
        return {"type": "market", "sl": sl, "rr": 2.0}


class StratB(StratA):
    name = "B: A + กรอง EMA200 Daily"
    use_trend = True


class S1(Strategy):
    name = "1: EMA Trend Pullback"

    def signal(self, i):
        x = self.x
        if None in (x.e20[i], x.e50[i]) or not x.trend[i]:
            return None
        if not (x.e20[i] > x.e50[i] and x.l[i] <= x.e20[i] and x.bull(i) and x.c[i] > x.e20[i]):
            return None
        if x.c[i] > x.e20[i] * 1.02:
            return None
        sw = self.last_swing_low(i)
        base = min(x.e50[i], sw) if sw else x.e50[i]
        return {"type": "market", "sl": base * (1 - 0.005), "rr": 2.0}


class S2(Strategy):
    name = "2: S/R Flip"
    TOL, RETEST, FAIL, SLB, WAIT = 0.003, 0.003, 0.01, 0.008, 48

    def __init__(self, x):
        super().__init__(x); self.armed = None   # (level, bar_breakout)

    def key_levels(self, i):
        hs = [v for _, v in self.SH.upto(i, 30)]
        return sorted({round(v, 8) for v in hs if sum(abs(w - v) / v <= self.TOL for w in hs) >= 2})

    def signal(self, i):
        x = self.x
        if self.armed:
            lvl, b = self.armed
            if i - b > self.WAIT or x.c[i] < lvl * (1 - self.FAIL):
                self.armed = None
            elif i > b and abs(x.l[i] - lvl) / lvl <= self.RETEST and x.pin_or_engulf(i):
                self.armed = None
                # TP = แนวต้านถัดไปบน Daily (ตามสเปก) → ถ้าไม่มี ใช้ swing high บน TF นี้
                tp = x.daily_res_above(i, x.c[i] * 1.005) or self.swing_high_above(i, x.c[i] * 1.005)
                return {"type": "market", "sl": lvl * (1 - self.SLB), "tp": tp, "rr": 2.0}
            return None
        for lvl in self.key_levels(i):
            if x.c[i - 1] <= lvl < x.c[i]:
                self.armed = (lvl, i)
                break
        return None


class S3(Strategy):
    name = "3: Order Block (SMC)"
    SLB, EXPIRE = 0.003, 100

    def signal(self, i):
        x = self.x
        # FVG ขาขึ้นที่แท่ง i-1 (ยืนยันเมื่อแท่ง i ปิด): low[i] > high[i-2]
        if i < 8 or not (x.l[i] > x.h[i - 2] and x.bull(i - 1)):
            return None
        for k in range(i - 2, i - 7, -1):          # แท่งแดงสุดท้ายก่อนการพุ่ง
            if x.c[k] < x.o[k]:
                ob_h, ob_l = x.h[k], x.l[k]
                if x.l[i] <= ob_h:                  # ถูกแตะไปแล้วหลังเกิด FVG = mitigated
                    return None
                tp = self.swing_high_above(i, ob_h * 1.002)
                return {"type": "limit", "price": ob_h, "sl": ob_l * (1 - self.SLB),
                        "tp": tp, "rr": 2.0, "expire": i + self.EXPIRE}
        return None


class S4(Strategy):
    name = "4: RSI Divergence"
    WAIT = 24

    def __init__(self, x):
        super().__init__(x); self.armed = None; self.n_seen = 0

    def signal(self, i):
        x = self.x
        pts = self.SL.upto(i, 2)
        if self.SL.k > self.n_seen:
            self.n_seen = self.SL.k
            if len(pts) >= 2:
                (j0, p0), (j1, p1) = pts[-2], pts[-1]
                if p1 < p0 and x.rsi[j0] is not None and x.rsi[j1] is not None and x.rsi[j1] > x.rsi[j0]:
                    self.armed = (j1, p1, i)
        if not self.armed:
            return None
        j1, p1, armed_at = self.armed
        if i - armed_at > self.WAIT or x.l[i] < p1:
            self.armed = None; return None
        r0, r1, s0, s1 = x.rsi[i - 1], x.rsi[i], x.rsig[i - 1], x.rsig[i]
        if None in (r0, r1, s0, s1):
            return None
        trig = (r0 <= s0 and r1 > s1 and r1 < 40) or (r0 <= 30 < r1)
        if not (trig and x.bull(i)):
            return None
        self.armed = None
        sl = p1 * (1 - 0.005)
        tp2 = self.swing_high_above(i, x.c[i] * 1.002)
        tp1 = x.e20[i]
        if tp2 and tp1 and x.c[i] * 1.002 < tp1 < tp2:
            return {"type": "market", "sl": sl, "tps": [(tp1, 0.5), (tp2, 0.5)]}
        return {"type": "market", "sl": sl, "tp": tp2, "rr": 2.0}


class S5(Strategy):
    name = "5: Range Box (Long only)"
    EQ, ZONE = 0.005, 0.005

    def signal(self, i):
        x = self.x
        if x.adx[i] is None or x.adx[i] > 25 or x.rsi[i] is None or x.rsi[i] >= 40:
            return None
        hs, ls = self.SH.upto(i, 2), self.SL.upto(i, 2)
        if len(hs) < 2 or len(ls) < 2:
            return None
        (_, h0), (_, h1) = hs; (_, l0), (_, l1) = ls
        if abs(h0 - h1) / h1 > self.EQ or abs(l0 - l1) / l1 > self.EQ:
            return None
        R, S = max(h0, h1), min(l0, l1)
        if R <= S * 1.01:
            return None
        if not (S <= x.l[i] <= S * (1 + self.ZONE) and x.c[i] > S):
            return None
        return {"type": "market", "sl": S - 0.01 * (R - S), "tp": (R + S) / 2}

# =====================================================================
# 5) Engine
# =====================================================================
MIN_NET_RR = 1.5


def net_rr(entry, sl, tps, cost):
    k = (1 - cost) ** 2
    tp = sum(p * f for p, f in tps) / sum(f for _, f in tps)
    return (k * tp / entry - 1) / (1 - k * sl / entry)


def run(strategy_cls, candles, test_start_ms, capital, risk, cost, min_notional=5.0):
    x = Ctx(candles); st = strategy_cls(x); skipped = [0]
    cash, pos, pend = capital, None, None
    trades, equity_curve = [], []
    first = next(i for i, t in enumerate(x.t) if t >= test_start_ms)

    def open_pos(i, entry, sig):
        nonlocal cash
        sl = sig["sl"]
        if not (sl < entry):
            return None
        if sig.get("tps"):
            tps = [(p, f) for p, f in sig["tps"] if p > entry]
            if not tps:
                return None
        else:
            tp = sig.get("tp") or entry + sig.get("rr", 2.0) * (entry - sl)
            if tp <= entry:
                tp = entry + sig.get("rr", 2.0) * (entry - sl)
            tps = [(tp, 1.0)]
        if st.rr_filter and net_rr(entry, sl, tps, cost) < MIN_NET_RR:
            skipped[0] += 1
            return None
        eq = cash
        loss_per = 1 - (1 - cost) ** 2 * (sl / entry)
        notional = min(eq * risk / loss_per, eq)
        if notional < min_notional:
            return None
        qty = notional * (1 - cost) / entry
        cash -= notional
        return {"i": i, "entry": entry, "sl": sl, "tps": tps, "qty0": qty, "qty": qty,
                "notional": notional, "proceeds": 0.0}

    def close_part(p, price, q):
        nonlocal cash
        pr = q * price * (1 - cost); cash += pr; p["proceeds"] += pr; p["qty"] -= q

    for i in range(first, len(x.t)):
        o, h, l, c = x.o[i], x.h[i], x.l[i], x.c[i]
        # --- เติมคำสั่งที่รอ ---
        if pend and pos is None:
            if pend["type"] == "market":
                pos = open_pos(i, o, pend); pend = None
            elif i > pend["expire"]:
                pend = None
            elif l <= pend["price"]:
                pos = open_pos(i, min(o, pend["price"]), pend); pend = None
        # --- จัดการไม้ที่ถืออยู่ ---
        if pos:
            if l <= pos["sl"]:
                close_part(pos, min(o, pos["sl"]), pos["qty"])
            else:
                for k, (tp, frac) in enumerate(pos["tps"]):
                    if tp is not None and h >= tp and pos["qty"] > 1e-15:
                        q = pos["qty"] if k == len(pos["tps"]) - 1 else pos["qty0"] * frac
                        close_part(pos, max(o, tp), min(q, pos["qty"]))
                        pos["tps"][k] = (None, frac)
            if pos["qty"] <= 1e-12:
                pnl = pos["proceeds"] - pos["notional"]
                trades.append({"strategy": st.name, "entry_time": x.t[pos["i"]], "exit_time": x.t[i],
                               "entry": pos["entry"], "sl": pos["sl"], "notional": round(pos["notional"], 4),
                               "pnl": pnl})
                pos = None
        equity_curve.append((x.t[i], cash + (pos["qty"] * c if pos else 0)))
        # --- หาสัญญาณใหม่ที่ปิดแท่ง ---
        if pos is None and pend is None and i < len(x.t) - 1:
            sig = st.signal(i)
            if sig:
                pend = sig
    if pos:  # ปิดไม้ค้างที่ราคาสุดท้าย
        close_part(pos, x.c[-1], pos["qty"])
        trades.append({"strategy": st.name, "entry_time": x.t[pos["i"]], "exit_time": x.t[-1],
                       "entry": pos["entry"], "sl": pos["sl"], "notional": round(pos["notional"], 4),
                       "pnl": pos["proceeds"] - pos["notional"]})
        equity_curve[-1] = (equity_curve[-1][0], cash)
    run.skipped = skipped[0]
    return trades, equity_curve


def buy_and_hold(candles, test_start_ms, capital, cost):
    first = next(i for i, c in enumerate(candles) if c[0] >= test_start_ms)
    qty = capital * (1 - cost) / candles[first][1]
    curve = [(c[0], qty * c[4]) for c in candles[first:]]
    curve[-1] = (curve[-1][0], curve[-1][1] * (1 - cost))
    return curve

# =====================================================================
# 6) สถิติ
# =====================================================================
def metrics(trades, curve, capital):
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]; losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gp, gl = sum(wins), -sum(losses)
    peak, mdd = -1e18, 0.0
    for _, e in curve:
        peak = max(peak, e); mdd = max(mdd, (peak - e) / peak if peak > 0 else 0)
    daily = {}
    for t, e in curve:
        daily[t // DAY_MS] = e
    vals = [capital] + list(daily.values())
    rets = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals))]
    mu = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
    return {
        "trades": len(trades),
        "win_rate": 100 * len(wins) / len(trades) if trades else float("nan"),
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else float("nan")),
        "max_dd": 100 * mdd,
        "sharpe": (mu / sd * math.sqrt(365)) if sd > 0 else float("nan"),
        "total_return": 100 * (curve[-1][1] / capital - 1),
    }


def fmt(m):
    f = lambda v, d=2: "—" if v != v else ("∞" if v == float("inf") else f"{v:.{d}f}")
    return (f"{m['trades']:>6} | {f(m['win_rate'],1):>7}% | {f(m['profit_factor']):>6} | "
            f"{f(m['max_dd'],1):>6}% | {f(m['sharpe']):>6} | {f(m['total_return'],1):>8}%")

# =====================================================================
# 7) main
# =====================================================================
STRATS = [StratA, StratB, S1, S2, S3, S4, S5]
DESIGN_TF = {"StratA": "1h", "StratB": "1h", "S1": "4h", "S2": "4h", "S3": "4h", "S4": "4h", "S5": "4h"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--capital", type=float, default=200.0)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--costs", default="0.0008,0.0012", help="ค่าธรรมเนียม+slippage ต่อออเดอร์ (คั่นด้วย ,)")
    ap.add_argument("--cost", type=float, help="(แบบเก่า) ต้นทุนเดียว")
    ap.add_argument("--tf", default="auto", choices=["auto", "1h", "4h"],
                    help="auto = A/B บน 1H, กลยุทธ์ 1–5 บน 4H ตามที่ออกแบบ")
    ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args()
    costs = [a.cost] if a.cost is not None else [float(v) for v in a.costs.split(",")]

    now = (int(time.time() * 1000) // DAY_MS) * DAY_MS     # ตัดที่ต้นวัน (UTC) → ใช้ไฟล์แคชซ้ำได้ทั้งวัน
    test_start = now - int(a.years * 365 * DAY_MS)
    data_start = test_start - 230 * DAY_MS                 # warmup ให้ EMA200 Daily
    os.makedirs("results", exist_ok=True)

    header = ("| กลยุทธ์ | TF | Cost | เหรียญ | ไม้ | Win Rate | Profit Factor | Max DD | Sharpe | Total Return |\n"
              "|---|---|---:|---|---:|---:|---:|---:|---:|---:|")
    lines, notes = [], []
    print(f"\nทุน ${a.capital:.0f}/เหรียญ · risk {a.risk*100:.1f}%/ไม้ · ย้อนหลัง {a.years:g} ปี · "
          f"TF {a.tf} · กลยุทธ์ 1–5 ข้ามไม้ที่ R:R สุทธิ < {MIN_NET_RR}\n")
    for si, sym in enumerate(a.symbols.split(",")):
        if a.synthetic:
            base, src = synthetic_candles(data_start, now, 42 + si, 30000 / (si + 1)), "SYNTHETIC (สุ่ม)"
        else:
            base, src = load_candles(sym, data_start, now)
        by_tf = {"1h": base, "4h": resample(base, 4)}
        gaps = sum(1 for k in range(1, len(base)) if base[k][0] - base[k - 1][0] != HOUR_MS)
        notes.append(f"- {sym}: {len(base)} แท่ง 1H จาก {src} (ช่องว่างในข้อมูล {gaps} จุด)")
        for cost in costs:
            print(f"{sym} [{src}] · cost {cost*100:.2f}%/ออเดอร์")
            print(f"  {'กลยุทธ์':34s} TF     ไม้ | WinRate |   PF   | MaxDD  | Sharpe |  Return | ข้าม(R:R)")
            for S in STRATS:
                tf = DESIGN_TF[S.__name__] if a.tf == "auto" else a.tf
                tr, cv = run(S, by_tf[tf], test_start, a.capital, a.risk, cost)
                m = metrics(tr, cv, a.capital)
                print(f"  {S.name:34s} {tf:3s} {fmt(m)} | {run.skipped:>5}")
                cells = " | ".join(c.strip() for c in fmt(m).split("|"))
                lines.append(f"| {S.name} | {tf} | {cost*100:.2f}% | {sym} | {cells} |")
                with open(f"results/trades_{sym}_{S.__name__}_{tf}_c{cost*10000:.0f}.csv", "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=["strategy", "entry_time", "exit_time", "entry", "sl", "notional", "pnl"])
                    w.writeheader(); w.writerows(tr)
            bh = buy_and_hold(base, test_start, a.capital, cost)
            mb = metrics([], bh, a.capital); mb["trades"] = 1
            print(f"  {'(เทียบ) ซื้อแล้วถือ':34s} —   {fmt(mb)}\n")
            lines.append(f"| (เทียบ) ซื้อแล้วถือ | — | {cost*100:.2f}% | {sym} | "
                         + " | ".join(c.strip() for c in fmt(mb).split("|")) + " |")

    with open("results/summary.md", "w") as f:
        f.write(f"# ผล Backtest — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"ทุน ${a.capital:.0f}/เหรียญ · risk {a.risk*100:.1f}%/ไม้ · {a.years:g} ปี · TF {a.tf} · "
                f"กลยุทธ์ 1–5 กรอง R:R สุทธิ ≥ {MIN_NET_RR}"
                f"{' · ⚠️ ข้อมูลสุ่ม (ทดสอบโค้ด)' if a.synthetic else ''}\n\n")
        f.write(header + "\n" + "\n".join(lines) + "\n\n## ข้อมูลที่ใช้\n" + "\n".join(notes) + "\n")
    print("บันทึกแล้ว: results/summary.md และ results/trades_*.csv")


if __name__ == "__main__":
    main()
