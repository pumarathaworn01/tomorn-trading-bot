#!/usr/bin/env python3
"""
runner.py — ตัวบอทของจริง: ดูกราฟ BTC/USDT บน Binance TH ตลอดเวลา แล้วแจ้งเตือน / เทรดจำลอง / เทรดจริง

คำสั่ง (รันในโฟลเดอร์ bot/):
    python3 runner.py --setup-telegram    # ครั้งแรก: ผูกบอท Telegram (ดู README_bot.md)
    python3 runner.py --test-telegram     # ลองส่งข้อความทดสอบ
    python3 runner.py --once              # เช็กสัญญาณ 1 รอบแล้วจบ (ไว้ทดสอบ)
    caffeinate -i python3 runner.py       # รันจริงต่อเนื่อง (caffeinate = กัน Mac หลับ)
    python3 runner.py --status            # ดูสถิติเทรดจำลอง + ความคืบหน้าด่านปลดล็อกเงินจริง
    python3 runner.py --check-live        # ตรวจ API key Binance TH แบบอ่านอย่างเดียว (ไม่เทรด)
    python3 runner.py --reset-halt        # ปลดล็อกหลัง kill switch หยุดบอท

ใช้ตรรกะกลยุทธ์ชุดเดียวกับ backtest.py ทุกบรรทัด (import มาตรง ๆ) ผลจริงจะได้เทียบกับ backtest ได้
"""
import argparse, hashlib, hmac, json, math, os, sys, time, traceback, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest as bt          # noqa: E402
import config as C             # noqa: E402

TH_TZ = timezone(timedelta(hours=7))
HOUR, DAY = bt.HOUR_MS, bt.DAY_MS
TF_MS = {"1h": HOUR, "4h": 4 * HOUR}
STRATS = {1: bt.S1, 2: bt.S2, 3: bt.S3, 4: bt.S4, 5: bt.S5}
HISTORY_DAYS = 260            # ต้องมีข้อมูลย้อนหลังพอให้ EMA200 รายวันคำนวณได้
COST = C.FEE_RATE + C.SLIPPAGE


def fmt_px(v):
    return f"{v:,.2f}"


def th_time(ms):
    return datetime.fromtimestamp(ms / 1000, TH_TZ).strftime("%d/%m %H:%M")

# =====================================================================
# .env (ความลับ)
# =====================================================================
ENV_PATH = os.path.join(HERE, ".env")


def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def save_env(key, value):
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    lines = [l for l in lines if not l.strip().startswith(key + "=")] + [f"{key}={value}"]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass

# =====================================================================
# Telegram
# =====================================================================
class Telegram:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, chat_id

    def _call(self, method, params):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            return json.loads(r.read().decode())

    def send(self, text):
        print(f"[{datetime.now(TH_TZ):%H:%M}] {text}\n", flush=True)
        if not (self.token and self.chat_id):
            return
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"})
        except Exception as e:  # noqa — แจ้งเตือนพังต้องไม่ทำให้บอทพัง
            print(f"  (ส่ง Telegram ไม่สำเร็จ: {e})")

    def setup(self):
        if not self.token:
            raise SystemExit("ยังไม่มี TELEGRAM_TOKEN ในไฟล์ .env — ทำตามขั้นที่ 1 ใน README_bot.md ก่อน")
        print("กำลังรอข้อความ... เปิด Telegram แล้วพิมพ์อะไรก็ได้ส่งให้บอทของคุณ (รอสูงสุด 2 นาที)")
        for _ in range(24):
            res = self._call("getUpdates", {"timeout": 5}).get("result", [])
            for u in reversed(res):
                chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
                if chat:
                    save_env("TELEGRAM_CHAT_ID", str(chat["id"]))
                    self.chat_id = str(chat["id"])
                    who = chat.get("first_name") or chat.get("title") or chat["id"]
                    print(f"เจอแล้ว! ผูกกับแชทของ {who} (chat id {chat['id']}) และบันทึกลง .env แล้ว")
                    self.send("✅ ผูกบอทสำเร็จ! จากนี้สัญญาณเทรดจะส่งมาที่นี่")
                    return
            time.sleep(5)
        raise SystemExit("ไม่เจอข้อความ — ตรวจว่าส่งข้อความหาบอทตัวที่ใช้ token นี้แล้ว แล้วลองใหม่")

# =====================================================================
# Exchange (Binance TH)
# =====================================================================
class Exchange:
    def __init__(self, key=None, secret=None, base=C.EXCHANGE_BASE_URL):
        self.key, self.secret, self.base = key, secret, base
        self.filters = None
        self.source = None

    # ---- สาธารณะ ----
    def _public(self, path, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return bt._get(self.base + path + q)

    def now_ms(self):
        try:
            return int(self._public("/api/v1/time")["serverTime"])
        except Exception:  # noqa
            return int(time.time() * 1000)

    def klines_1h(self, start_ms, end_ms):
        """ดึงแท่ง 1H — ใช้ Binance TH ก่อน ถ้าไม่ได้ใช้ข้อมูลสาธารณะของ Binance global"""
        last = None
        for name, url in bt.SOURCES:
            try:
                rows = bt._download_from(url, C.SYMBOL, "1h", start_ms, end_ms)
                self.source = name
                return rows
            except Exception as e:  # noqa
                last = e
        raise RuntimeError(f"ดึงราคาไม่ได้: {last}")

    def book(self):
        b = self._public("/api/v1/ticker/bookTicker", {"symbol": C.SYMBOL})
        return float(b["bidPrice"]), float(b["askPrice"])

    def symbol_filters(self):
        if self.filters is None:
            info = self._public("/api/v1/exchangeInfo", {"symbol": C.SYMBOL})
            sym = next(s for s in info["symbols"] if s["symbol"] == C.SYMBOL)
            f = {x["filterType"]: x for x in sym["filters"]}
            self.filters = {
                "step": float(f["LOT_SIZE"]["stepSize"]),
                "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "tick": float(f["PRICE_FILTER"]["tickSize"]),
                "min_notional": float(f.get("MIN_NOTIONAL", {}).get("minNotional", 5)),
            }
        return self.filters

    # ---- ต้องใช้ API key ----
    def _signed(self, method, path, params=None):
        if not (self.key and self.secret):
            raise RuntimeError("ไม่มี BINANCE_TH_API_KEY / BINANCE_TH_API_SECRET ใน .env")
        p = dict(params or {}); p["timestamp"] = self.now_ms(); p["recvWindow"] = 5000
        q = urllib.parse.urlencode(p)
        sig = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{self.base}{path}?{q}&signature={sig}", method=method,
                                     headers={"X-MBX-APIKEY": self.key, "User-Agent": "crypto-bot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Binance TH ตอบ HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")

    def account(self):
        return self._signed("GET", "/api/v1/accountV2")

    def balances(self):
        acc = self.account()
        bal = acc.get("balances") or acc.get("data", {}).get("balances", [])
        return {b["asset"]: float(b["free"]) for b in bal}

    def order(self, side, otype, qty, price=None, client_id=None):
        p = {"symbol": C.SYMBOL, "side": side, "type": otype, "quantity": self.fmt_qty(qty)}
        if otype == "LIMIT":
            p.update(price=self.fmt_price(price), timeInForce="GTC")
        if client_id:
            p["newClientOrderId"] = client_id
        return self._signed("POST", "/api/v1/order", p)

    def get_order(self, order_id):
        return self._signed("GET", "/api/v1/order", {"symbol": C.SYMBOL, "orderId": order_id})

    def cancel(self, order_id):
        return self._signed("DELETE", "/api/v1/order", {"symbol": C.SYMBOL, "orderId": order_id})

    def fmt_qty(self, q):
        step = self.symbol_filters()["step"]
        d = max(0, -int(math.floor(math.log10(step))))
        return f"{math.floor(q / step) * step:.{d}f}"

    def fmt_price(self, p):
        tick = self.symbol_filters()["tick"]
        d = max(0, -int(math.floor(math.log10(tick))))
        return f"{math.floor(p / tick) * tick:.{d}f}"

# =====================================================================
# ข้อมูลแท่งเทียน (เก็บในเครื่อง อัปเดตทีละนิด)
# =====================================================================
class CandleStore:
    def __init__(self, ex, path=None):
        self.ex = ex
        self.path = path or os.path.join(HERE, "data", f"live_{C.SYMBOL}_1h.json")
        self.c = json.load(open(self.path)) if os.path.exists(self.path) else []

    def update(self, now_ms):
        """ดึงแท่งใหม่ คืนเฉพาะแท่งที่ 'ปิดแล้ว' และยังไม่เคยเห็น"""
        need_from = now_ms - HISTORY_DAYS * DAY
        if not self.c or self.c[0][0] > need_from + DAY:
            start = need_from // HOUR * HOUR
            print(f"ดาวน์โหลดประวัติราคา {HISTORY_DAYS} วัน (ครั้งแรกนานหน่อย)...", flush=True)
            self.c = []
        else:
            start = self.c[-1][0] + HOUR
        if start + HOUR > now_ms:
            return []
        rows = self.ex.klines_1h(start, now_ms)
        closed = [list(r) for r in rows if r[0] + HOUR <= now_ms and (not self.c or r[0] > self.c[-1][0])]
        first_load = not self.c
        self.c.extend(closed)
        self.c = self.c[-(HISTORY_DAYS + 30) * 24:]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.c, f)
        return [] if first_load else closed

# =====================================================================
# สัญญาณ
# =====================================================================
def find_signals(c1h):
    """คืน (เวลาแท่งสัญญาณ, ราคาปิด, [(เลขกลยุทธ์, ชื่อ, sig), ...] เรียงตาม priority)"""
    tf = TF_MS[C.SIGNAL_TF]
    candles = bt.resample([tuple(c) for c in c1h], tf // HOUR) if tf > HOUR else [tuple(c) for c in c1h]
    x = bt.Ctx(candles); n = len(candles); last = n - 1
    found = []
    for num in C.ENABLED_STRATEGIES:
        st = STRATS[num](x)
        for i in range(max(60, n - 400), n):        # เล่นย้อนให้กลยุทธ์ที่มี state (S2, S4) จำเหตุการณ์ได้
            s = st.signal(i)
            if i == last and s:
                if s.get("expire") is not None:
                    s["expire_ms"] = x.t[last] + (s["expire"] - last + 1) * tf
                found.append((num, st.name, s))
    found.sort(key=lambda f: C.STRATEGY_PRIORITY.index(f[0]) if f[0] in C.STRATEGY_PRIORITY else 99)
    return x.t[last], x.c[last], found


def make_plan(num, name, sig, entry, equity):
    """แปลงสัญญาณเป็นแผนเทรด: SL/TP/ขนาดไม้ (คิดค่าธรรมเนียม+slippage) — คืน (plan, เหตุผลถ้าไม่เข้า)"""
    if sig["type"] == "limit":
        entry = sig["price"]
    sl = sig["sl"]
    if not sl < entry:
        return None, "SL อยู่เหนือราคาเข้า"
    if sig.get("tps"):
        tps = [[p, f] for p, f in sig["tps"] if p > entry]
    else:
        tp = sig.get("tp") or entry + sig.get("rr", 2.0) * (entry - sl)
        if tp <= entry:
            tp = entry + sig.get("rr", 2.0) * (entry - sl)
        tps = [[tp, 1.0]]
    if not tps:
        return None, "ไม่มีจุดทำกำไรเหนือราคาเข้า"
    rr = bt.net_rr(entry, sl, tps, COST)
    if rr < C.MIN_NET_RR:
        return None, f"R:R หลังหักค่าธรรมเนียม 1:{rr:.2f} < 1:{C.MIN_NET_RR}"
    loss_per = 1 - (1 - COST) ** 2 * (sl / entry)
    notional = min(equity * C.RISK_PCT / loss_per, equity)
    if notional < 5:
        return None, "ขนาดไม้เล็กกว่าขั้นต่ำของกระดาน"
    return {"strategy": num, "name": name, "type": sig["type"], "entry": entry, "sl": sl, "tps": tps,
            "notional": notional, "rr": rr, "risk_usdt": notional * loss_per,
            "expire_ms": sig.get("expire_ms")}, None


def plan_text(p, header):
    tps = " / ".join(f"{fmt_px(t)} ({int(f*100)}%)" for t, f in p["tps"])
    kind = "Buy Limit ที่" if p["type"] == "limit" else "Buy Market ≈"
    return (f"{header}\n{p['name']} · {C.SYMBOL} · {C.SIGNAL_TF.upper()}\n"
            f"• {kind} {fmt_px(p['entry'])}\n• SL {fmt_px(p['sl'])} ({(p['sl']/p['entry']-1)*100:.2f}%)\n"
            f"• TP {tps}\n• ขนาดไม้ ${p['notional']:.2f} · เสี่ยง ${p['risk_usdt']:.2f}"
            f" · R:R สุทธิ 1:{p['rr']:.2f}")

# =====================================================================
# Broker: เก็บสถานะในไฟล์ state/ (ปิดเปิดบอทได้ ไม่ลืมไม้ที่ถืออยู่)
# =====================================================================
class Broker:
    def __init__(self, mode, tg):
        self.mode, self.tg = mode, tg
        self.path = os.path.join(HERE, "state", f"{mode}_{C.SYMBOL}.json")
        if os.path.exists(self.path):
            self.s = json.load(open(self.path))
            s = self.s
            # พอร์ตจำลองที่ยังว่าง (ไม่มีไม้เลย) แต่ทุนใน config เปลี่ยน → ใช้ทุนใหม่ได้เลย ไม่ต้องรีเซ็ต
            if (mode != "live" and not s["trades"] and not s["pos"] and not s["pend"]
                    and abs(s["start"] - C.CAPITAL_USDT) > 1e-9):
                print(f"[{mode}] เปลี่ยนทุนจำลอง {s['start']:g} → {C.CAPITAL_USDT:g} USDT ตาม config.py")
                s["cash"] = s["start"] = s["peak"] = C.CAPITAL_USDT
                s["created"] = int(time.time() * 1000)
                self.save()
        else:
            self.s = self.fresh()

    @staticmethod
    def fresh():
        return {"cash": C.CAPITAL_USDT, "start": C.CAPITAL_USDT, "peak": C.CAPITAL_USDT, "pos": None,
                "pend": None, "trades": [], "halted": False, "last_signal_bar": None,
                "last_summary_day": None, "created": int(time.time() * 1000)}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=1)
        os.replace(tmp, self.path)

    @property
    def busy(self):
        return bool(self.s["pos"] or self.s["pend"])

    def equity(self, price):
        p = self.s["pos"]
        return self.s["cash"] + (p["qty"] * price if p else 0.0)

    def check_kill(self, price):
        eq = self.equity(price)
        self.s["peak"] = max(self.s["peak"], eq)
        dd = 1 - eq / self.s["peak"]
        if dd >= C.KILL_SWITCH_DD and not self.s["halted"]:
            self.s["halted"] = True
            self.tg.send(f"🛑 KILL SWITCH: ขาดทุนจากจุดสูงสุด {dd*100:.1f}% (เกิน {C.KILL_SWITCH_DD*100:.0f}%)\n"
                         f"บอทหยุดเปิดไม้ใหม่แล้ว · ไม้ที่ถืออยู่ยังมี SL คุมตามเดิม\n"
                         f"ตรวจสอบแล้วปลดล็อกด้วย: python3 runner.py --reset-halt")

    def _record_close(self, t_ms, reason):
        p = self.s["pos"]
        pnl = p["proceeds"] - p["notional"]
        self.s["trades"].append({"strategy": p["strategy"], "name": p["name"], "entry_time": p["time"],
                                 "exit_time": t_ms, "entry": p["entry"], "sl": p["sl"],
                                 "notional": p["notional"], "pnl": pnl, "reason": reason})
        self.s["pos"] = None
        emoji = "✅" if pnl > 0 else "❌"
        self.tg.send(f"{emoji} ปิดไม้ [{self.mode}] {p['name']} · {reason}\nกำไร/ขาดทุน {pnl:+.2f} USDT "
                     f"({pnl / p['notional'] * 100:+.2f}% ของไม้) · เงินในพอร์ต {self.s['cash']:.2f} USDT")

    def stats(self, price=None):
        """price = ราคาปัจจุบัน (ไว้คิดมูลค่าไม้ที่ถืออยู่) — ไม่ใส่ = คิดที่ราคาเข้า"""
        tr = self.s["trades"]
        p = self.s["pos"]
        eq = self.equity(price if price else (p["entry"] if p else 0))
        gp = sum(t["pnl"] for t in tr if t["pnl"] > 0); gl = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
        wins = sum(1 for t in tr if t["pnl"] > 0)
        return {"trades": len(tr), "win_rate": 100 * wins / len(tr) if tr else 0.0, "pf": pf,
                "pnl": gp - gl, "equity": eq, "return": 100 * (eq / self.s["start"] - 1)}


class PaperBroker(Broker):
    """จำลองการเทรดด้วยแท่ง 1H ที่ปิดแล้ว — กติกาเดียวกับ backtest (SL ก่อน TP, ต้นทุนทุกออเดอร์)"""

    def submit(self, plan, t_ms):
        plan["time"] = t_ms
        self.s["pend"] = plan
        self.tg.send(plan_text(plan, "🧪 [จำลอง] มีสัญญาณ — จะเข้าไม้ที่ราคาเปิดแท่งถัดไป"
                                     if plan["type"] == "market" else "🧪 [จำลอง] ตั้ง Buy Limit"))

    def _open(self, plan, entry, t_ms):
        # ใช้ราคาเข้าจริง (ราคาเปิดแท่งถัดไป) คำนวณขนาดไม้ใหม่ ให้เสี่ยง 1% พอดี
        p2, why = make_plan(plan["strategy"], plan["name"],
                            {"type": "market", "sl": plan["sl"], "tps": plan["tps"]}, entry, self.s["cash"])
        if not p2:
            self.tg.send(f"⏭️ [จำลอง] ยกเลิกไม้ {plan['name']}: {why} (ราคาเปิดจริง {fmt_px(entry)})")
            return
        qty = p2["notional"] * (1 - COST) / entry
        self.s["cash"] -= p2["notional"]
        self.s["pos"] = {**p2, "time": t_ms, "entry": entry, "qty0": qty, "qty": qty, "proceeds": 0.0}
        self.tg.send(f"🧪 [จำลอง] เข้าไม้แล้ว {p2['name']} ที่ {fmt_px(entry)} · ไม้ ${p2['notional']:.2f}"
                     f" · SL {fmt_px(p2['sl'])}")

    def _sell(self, price, q):
        p = self.s["pos"]
        pr = q * price * (1 - COST); self.s["cash"] += pr; p["proceeds"] += pr; p["qty"] -= q

    def on_candle(self, c):
        t, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
        pd = self.s["pend"]
        if pd and not self.s["pos"]:
            if pd["type"] == "market":
                self.s["pend"] = None; self._open(pd, o, t)
            elif pd.get("expire_ms") and t >= pd["expire_ms"]:
                self.s["pend"] = None
                self.tg.send(f"⌛ [จำลอง] คำสั่ง Limit ของ {pd['name']} หมดอายุ ไม่ได้เข้าไม้")
            elif l <= pd["entry"]:
                self.s["pend"] = None; self._open(pd, min(o, pd["entry"]), t)
        p = self.s["pos"]
        if p:
            if l <= p["sl"]:
                self._sell(min(o, p["sl"]), p["qty"]); self._record_close(t, "โดน SL")
            else:
                for k, (tp, frac) in enumerate(p["tps"]):
                    if tp is not None and h >= tp and p["qty"] > 1e-15:
                        last = k == len(p["tps"]) - 1
                        self._sell(max(o, tp), p["qty"] if last else min(p["qty0"] * frac, p["qty"]))
                        p["tps"][k] = [None, frac]
                        if not last:
                            self.tg.send(f"💰 [จำลอง] ปิดบางส่วนที่ TP{k+1} {fmt_px(tp)}")
                if p["qty"] <= 1e-12:
                    self._record_close(t, "ถึง TP")
        self.check_kill(cl)


class LiveBroker(Broker):
    """เทรดเงินจริง: เข้าด้วย Market/Limit แล้วบอทเฝ้าราคาเอง ถึง SL/TP สั่ง Market ขาย
    ⚠️ ถ้าบอทหยุด (Mac หลับ/เน็ตหลุด) จะไม่มีใครขายที่ SL — ใช้ caffeinate และเช็ก Telegram"""

    def __init__(self, mode, tg, ex):
        super().__init__(mode, tg); self.ex = ex

    def sync_cash(self):
        bal = self.ex.balances()
        self.s["cash"] = bal.get("USDT", 0.0)
        self.s["btc"] = bal.get(C.SYMBOL.replace("USDT", ""), 0.0)
        return bal

    def equity(self, price):
        # เงินจริง: USDT + BTC ทั้งหมดในบัญชี (รวมเศษ BTC ที่ขายไม่ได้เพราะเล็กกว่าขั้นต่ำ)
        return self.s["cash"] + self.s.get("btc", 0.0) * price

    def submit(self, plan, t_ms):
        f = self.ex.symbol_filters()
        bid, ask = self.ex.book()
        self.sync_cash()
        entry = plan["entry"] if plan["type"] == "limit" else ask
        p2, why = make_plan(plan["strategy"], plan["name"],
                            {"type": plan["type"], "price": plan["entry"], "sl": plan["sl"], "tps": plan["tps"],
                             "expire_ms": plan.get("expire_ms")}, entry, self.s["cash"])
        if not p2:
            self.tg.send(f"⏭️ [เงินจริง] ไม่เข้าไม้ {plan['name']}: {why}"); return
        qty = float(self.ex.fmt_qty(p2["notional"] / entry))
        if qty < f["min_qty"] or qty * entry < f["min_notional"]:
            self.tg.send(f"⏭️ [เงินจริง] ไม้เล็กกว่าขั้นต่ำของกระดาน ({qty} BTC)"); return
        cid = f"tomorn{int(time.time())}"
        if plan["type"] == "market":
            r = self.ex.order("BUY", "MARKET", qty, client_id=cid)
            self._filled(p2, r, t_ms)
        else:
            r = self.ex.order("BUY", "LIMIT", qty, price=entry, client_id=cid)
            self.s["pend"] = {**p2, "order_id": r["orderId"], "time": t_ms}
            self.tg.send(plan_text(p2, "💸 [เงินจริง] ตั้ง Buy Limit แล้ว"))

    def _filled(self, plan, r, t_ms):
        if r.get("status") != "FILLED":
            r = self.ex.get_order(r["orderId"])
        qty = float(r["executedQty"]); quote = float(r["cummulativeQuoteQty"])
        if qty <= 0:
            self.tg.send("⚠️ [เงินจริง] คำสั่งซื้อไม่ถูกจับคู่"); return
        qty_net = qty * (1 - C.FEE_RATE)                 # Binance TH หักค่าธรรมเนียมเป็น BTC
        self.s["pos"] = {**plan, "time": t_ms, "entry": quote / qty, "qty0": qty_net, "qty": qty_net,
                         "notional": quote, "proceeds": 0.0}
        self.tg.send(plan_text({**plan, "entry": quote / qty, "notional": quote}, "💸 [เงินจริง] เข้าไม้แล้ว"))

    def _sell(self, q, reason_tp=None):
        p = self.s["pos"]
        free_btc = self.ex.balances().get(C.SYMBOL.replace("USDT", ""), 0.0)
        q = min(q, free_btc)
        r = self.ex.order("SELL", "MARKET", q)
        if r.get("status") != "FILLED":
            r = self.ex.get_order(r["orderId"])
        sold = float(r["executedQty"]); got = float(r["cummulativeQuoteQty"]) * (1 - C.FEE_RATE)
        p["proceeds"] += got; p["qty"] = max(0.0, p["qty"] - sold)
        if p["qty"] < self.ex.symbol_filters()["step"]:        # เศษเล็กกว่าขั้นต่ำ ขายไม่ได้ → ยังอยู่ในบัญชีเป็น BTC
            bid, _ = self.ex.book()
            p["proceeds"] += p["qty"] * bid                     # คิดมูลค่าเศษไว้ในผลไม้นี้ ให้ PnL ตรงกับเงินจริง
            p["qty"] = 0.0

    def on_tick(self, t_ms):
        pd = self.s["pend"]
        if pd and not self.s["pos"]:
            o = self.ex.get_order(pd["order_id"])
            if o["status"] == "FILLED":
                self.s["pend"] = None; self._filled(pd, o, t_ms)
            elif pd.get("expire_ms") and t_ms >= pd["expire_ms"]:
                self.ex.cancel(pd["order_id"]); self.s["pend"] = None
                self.tg.send(f"⌛ [เงินจริง] ยกเลิก Limit ของ {pd['name']} (หมดอายุ)")
        p = self.s["pos"]
        bid, _ = self.ex.book()
        if p:
            if bid <= p["sl"]:
                self._sell(p["qty"]); self._record_close(t_ms, f"โดน SL ที่ ~{fmt_px(bid)}")
            else:
                for k, (tp, frac) in enumerate(p["tps"]):
                    if tp is not None and bid >= tp and p["qty"] > 0:
                        last = k == len(p["tps"]) - 1
                        self._sell(p["qty"] if last else p["qty0"] * frac)
                        p["tps"][k] = [None, frac]
                if p["qty"] <= 0:
                    self._record_close(t_ms, f"ถึง TP ที่ ~{fmt_px(bid)}")
        self.sync_cash()
        self.check_kill(bid)

# =====================================================================
# ด่านปลดล็อกเงินจริง
# =====================================================================
def live_gate(env):
    reasons = []
    if not C.LIVE_ENABLED:
        reasons.append("config.py: LIVE_ENABLED ยังเป็น False")
    if not (env.get("BINANCE_TH_API_KEY") and env.get("BINANCE_TH_API_SECRET")):
        reasons.append(".env: ยังไม่มี BINANCE_TH_API_KEY / BINANCE_TH_API_SECRET")
    paper = os.path.join(HERE, "state", f"paper_{C.SYMBOL}.json")
    if not os.path.exists(paper):
        reasons.append("ยังไม่เคยรันโหมด paper")
    else:
        b = Broker.__new__(Broker); b.s = json.load(open(paper)); st = b.stats()
        g = C.LIVE_GATE
        if st["trades"] < g["min_paper_trades"]:
            reasons.append(f"เทรดจำลองได้ {st['trades']}/{g['min_paper_trades']} ไม้")
        if st["pf"] < g["min_profit_factor"]:
            reasons.append(f"Profit Factor จำลอง {st['pf']:.2f} < {g['min_profit_factor']}")
    return reasons

# =====================================================================
# Bot
# =====================================================================
class Bot:
    def __init__(self, mode, ex, tg, store=None):
        self.mode, self.ex, self.tg = mode, ex, tg
        self.store = store or CandleStore(ex)
        if mode == "live":
            self.broker = LiveBroker(mode, tg, ex)
        else:
            self.broker = PaperBroker(mode, tg)          # alert ใช้ไฟล์ state แยก (ไว้กันสัญญาณซ้ำ)
        self.last_err = 0

    def step(self, now_ms):
        new = self.store.update(now_ms)
        b = self.broker
        if self.mode == "paper":
            for c in new:
                b.on_candle(c)
        elif self.mode == "live":
            b.on_tick(now_ms)
        tf = TF_MS[C.SIGNAL_TF]
        if any((c[0] + HOUR) % tf == 0 for c in new):        # มีแท่ง 4H ปิดใหม่
            self.check_signals()
        self.daily_summary(now_ms)
        b.save()

    def check_signals(self, dry=False):
        b = self.broker
        bar_t, close, found = find_signals(self.store.c)
        if dry:
            print(f"แท่ง {C.SIGNAL_TF} ล่าสุดที่ปิดแล้ว: {th_time(bar_t)} · ปิดที่ {fmt_px(close)}")
            for num, name, sig in found:
                plan, why = make_plan(num, name, sig, close, b.equity(close))
                print(plan_text(plan, "  สัญญาณ:") if plan else f"  สัญญาณ {name} แต่ไม่เข้า: {why}")
            if not found:
                print("  ไม่มีสัญญาณที่แท่งนี้ (ปกติ — กลยุทธ์บน 4H มีสัญญาณไม่บ่อย)")
            return
        if b.s["last_signal_bar"] == bar_t:
            return
        b.s["last_signal_bar"] = bar_t
        if not found:
            print(f"[{th_time(bar_t + TF_MS[C.SIGNAL_TF])}] แท่ง {C.SIGNAL_TF} ปิด — ไม่มีสัญญาณ", flush=True)
            return
        num, name, sig = found[0]
        equity = b.equity(close)
        plan, why = make_plan(num, name, sig, close, equity)
        if not plan:
            print(f"สัญญาณ {name} แต่ไม่เข้า: {why}", flush=True)
            return
        if self.mode == "alert":
            self.tg.send(plan_text(plan, "📣 สัญญาณใหม่ (แจ้งเตือนอย่างเดียว ยังไม่เทรด)"))
        elif b.s["halted"]:
            self.tg.send(plan_text(plan, "🛑 มีสัญญาณ แต่บอทถูก kill switch หยุดไว้"))
        elif b.busy:
            print(f"สัญญาณ {name} แต่มีไม้/คำสั่งค้างอยู่แล้ว — ข้าม", flush=True)
        else:
            b.submit(plan, bar_t + TF_MS[C.SIGNAL_TF])

    def daily_summary(self, now_ms):
        d = datetime.fromtimestamp(now_ms / 1000, TH_TZ)
        b = self.broker
        if d.hour < C.DAILY_SUMMARY_HOUR or b.s["last_summary_day"] == d.strftime("%Y-%m-%d"):
            return
        b.s["last_summary_day"] = d.strftime("%Y-%m-%d")
        px = self.store.c[-1][4] if self.store.c else 0; st = b.stats(px)
        pos = b.s["pos"]
        self.tg.send(f"☀️ สรุปประจำวัน [{self.mode}] · BTC {fmt_px(px)}\n"
                     f"พอร์ต {st['equity']:.2f} USDT ({st['return']:+.2f}%) · เทรดแล้ว {st['trades']} ไม้"
                     f" · ชนะ {st['win_rate']:.0f}% · PF {st['pf']:.2f}\n"
                     f"ไม้ที่ถือ: {pos['name'] + ' @ ' + fmt_px(pos['entry']) if pos else 'ไม่มี'}"
                     f"{' · 🛑 หยุดอยู่ (kill switch)' if b.s['halted'] else ''}")

    def run_forever(self):
        self.tg.send(f"🤖 บอทเริ่มทำงาน · โหมด {self.mode} · {C.SYMBOL} · กลยุทธ์ {C.ENABLED_STRATEGIES}")
        while True:
            try:
                self.step(self.ex.now_ms())
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa — บอทต้องไม่ตายเพราะเน็ตหลุดครั้งเดียว
                traceback.print_exc()
                if time.time() - self.last_err > 3600:
                    self.last_err = time.time()
                    self.tg.send(f"⚠️ บอทเจอปัญหา (จะลองใหม่อัตโนมัติ): {str(e)[:300]}")
            time.sleep(C.POLL_SECONDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["alert", "paper", "live"], default=C.MODE)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--setup-telegram", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--check-live", action="store_true")
    ap.add_argument("--reset-halt", action="store_true")
    ap.add_argument("--reset-paper", action="store_true",
                    help="เริ่มพอร์ตจำลองใหม่ด้วยทุน CAPITAL_USDT (ไฟล์เดิมถูกเก็บสำรองไว้ ไม่ลบ)")
    a = ap.parse_args()
    env = load_env()
    tg = Telegram(env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    ex = Exchange(env.get("BINANCE_TH_API_KEY"), env.get("BINANCE_TH_API_SECRET"))

    if a.setup_telegram:
        return tg.setup()
    if a.test_telegram:
        if not tg.chat_id:
            raise SystemExit("ยังไม่ได้ผูก Telegram — รัน --setup-telegram ก่อน")
        return tg.send("👋 สวัสดีจากบอทเทรดของคุณ! ทดสอบการแจ้งเตือนสำเร็จ")
    if a.check_live:
        f = ex.symbol_filters()
        print(f"กติกา {C.SYMBOL}: ขั้นจำนวน {f['step']}, ขั้นราคา {f['tick']}, ขั้นต่ำต่อออเดอร์ {f['min_notional']} USDT")
        bal = ex.balances()
        print("API key ใช้ได้ ✅  ยอดคงเหลือ:", {k: v for k, v in bal.items() if v > 0} or "ว่าง")
        print("⚠️ อย่าลืม: ตอนสร้าง API key ให้เปิดแค่ 'Spot Trading' ห้ามเปิด 'Withdraw'")
        r = live_gate(env)
        print("ด่านปลดล็อกเงินจริง:", "ผ่านครบ ✅" if not r else "ยังไม่ผ่าน → " + " · ".join(r))
        return
    if a.reset_paper:
        p = os.path.join(HERE, "state", f"paper_{C.SYMBOL}.json")
        if os.path.exists(p):
            bak = p.replace(".json", time.strftime("_old_%Y%m%d_%H%M%S.json"))
            os.replace(p, bak)
            print(f"เก็บพอร์ตจำลองเดิมไว้ที่ state/{os.path.basename(bak)}")
        b = Broker("paper", tg); b.save()
        print(f"เริ่มพอร์ตจำลองใหม่ ทุน {C.CAPITAL_USDT:g} USDT ✅ (ถ้าบอทเปิดอยู่ ให้ปิดแล้วเปิดใหม่)")
        return
    if a.status or a.reset_halt:
        for m in ("paper", "live"):
            p = os.path.join(HERE, "state", f"{m}_{C.SYMBOL}.json")
            if not os.path.exists(p):
                continue
            b = Broker(m, tg)
            if a.reset_halt:
                b.s["halted"] = False; b.s["peak"] = b.equity(b.s["pos"]["entry"] if b.s["pos"] else 0) or b.s["cash"]
                b.save(); print(f"[{m}] ปลดล็อก kill switch แล้ว")
                continue
            st = b.stats()
            print(f"[{m}] พอร์ต {st['equity']:.2f} USDT ({st['return']:+.2f}%) · {st['trades']} ไม้ · "
                  f"ชนะ {st['win_rate']:.0f}% · PF {st['pf']:.2f} · ถือ: {b.s['pos']['name'] if b.s['pos'] else '-'}"
                  f"{' · 🛑 HALTED' if b.s['halted'] else ''}")
        if a.status:
            r = live_gate(env)
            print("ด่านปลดล็อกเงินจริง:", "ผ่านครบ ✅" if not r else "ยังไม่ผ่าน → " + " · ".join(r))
        return

    if a.mode == "live":
        r = live_gate(env)
        if r:
            raise SystemExit("ยังเปิดโหมดเงินจริงไม่ได้:\n  - " + "\n  - ".join(r) +
                             "\n(ตั้งใจให้เป็นแบบนี้ — ต้องพิสูจน์ด้วยเทรดจำลองก่อน)")
    bot = Bot(a.mode, ex, tg)
    if a.once:
        bot.step(ex.now_ms()); bot.check_signals(dry=True)
        b = bot.broker; st = b.stats()
        print(f"รอบเดียวเสร็จ · ข้อมูลจาก {ex.source} · แท่งล่าสุด {th_time(bot.store.c[-1][0])} · "
              f"BTC {fmt_px(bot.store.c[-1][4])} · โหมด {a.mode} · ไม้จำลองสะสม {st['trades']}")
        return
    bot.run_forever()


if __name__ == "__main__":
    main()
