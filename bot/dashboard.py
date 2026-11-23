#!/usr/bin/env python3
"""
dashboard.py — เว็บ Tomorn: ดูบอทเทรดจำลองแบบเห็นภาพ (อ่านอย่างเดียว ไม่สั่งเทรด)

    python3 dashboard.py            # เปิด http://localhost:8080 บน Mac เครื่องนี้ (เปิดเบราว์เซอร์ให้อัตโนมัติ)
    python3 dashboard.py --lan      # ให้มือถือที่ต่อ Wi-Fi เดียวกันเปิดดูได้ด้วย
    python3 dashboard.py --mode live  # ดูพอร์ตเงินจริง (ถ้าเปิดใช้แล้ว)

รันคู่กับ runner.py ได้ (คนละหน้าต่าง Terminal) — เว็บอ่านไฟล์ที่บอทบันทึกไว้ + ดึงราคาสดจาก Binance TH เอง
หน้าเว็บรีเฟรชทุก 5 วินาที (แท่ง 4H ล่าสุดที่ยังไม่ปิดจะขยับตามราคาจริง)
"""
import argparse, json, os, socket, sys, threading, time, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest as bt          # noqa: E402
import config as C             # noqa: E402
import runner as R             # noqa: E402

N_CANDLES = 180                # แสดงแท่ง 4H ล่าสุดกี่แท่ง (~30 วัน)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- ราคาสด
# บอทเก็บเฉพาะแท่ง 1H ที่ "ปิดแล้ว" (ไว้หาสัญญาณ) → เว็บดึงแท่งล่าสุดที่ยังวิ่งอยู่เองจาก Binance TH
# เก็บไว้ในหน่วยความจำ 4 วินาที กันยิง API ถี่เกิน (เปิดหลายแท็บ/มือถือพร้อมกันก็ยังดึงแค่ครั้งเดียว)
LIVE_TTL = 4
_live = {"t": 0.0, "start": None, "rows": [], "src": None, "at": None}
_live_lock = threading.Lock()


def _fetch_1h(base, start_ms):
    url = f"{base}?symbol={C.SYMBOL}&interval=1h&startTime={start_ms}&limit=1000"
    req = urllib.request.Request(url, headers={"User-Agent": "tomorn-web/1.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        rows = json.loads(r.read().decode())
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in rows]


def live_rows(start_ms):
    """แท่ง 1H ตั้งแต่ start_ms ถึงตอนนี้ (รวมแท่งที่ยังไม่ปิด) — คืน (rows, แหล่งข้อมูล, เวลาที่ดึง)"""
    start_ms = max(start_ms, int(time.time() * 1000) - 6 * 24 * R.HOUR)   # Binance TH ขอได้ไม่เกิน 7 วันต่อครั้ง
    with _live_lock:
        if time.time() - _live["t"] < LIVE_TTL and _live["start"] == start_ms:
            return _live["rows"], _live["src"], _live["at"]
        for name, base in bt.SOURCES:
            try:
                rows = _fetch_1h(base, start_ms)
                if rows:
                    _live.update(t=time.time(), start=start_ms, rows=rows, src=name, at=int(time.time() * 1000))
                    return rows, name, _live["at"]
            except Exception:  # noqa — เน็ตหลุด/โดนจำกัด → ลองแหล่งถัดไป หรือใช้ข้อมูลในเครื่องไปก่อน
                continue
        _live["t"] = time.time() - LIVE_TTL + 2       # พลาด → รอ 2 วินาทีค่อยลองใหม่
        return [], None, None


def resample_open(candles, hours):
    """เหมือน bt.resample แต่เก็บแท่งสุดท้ายที่ยังไม่ครบไว้ด้วย (แท่งที่กำลังวิ่ง)"""
    step = hours * R.HOUR; out = []; cur = None; n = 0
    for t, o, h, l, c, v in candles:
        b = t // step * step
        if cur and cur[0] == b:
            cur = [b, cur[1], max(cur[2], h), min(cur[3], l), c, cur[5] + v]; n += 1
        else:
            if cur and n == hours:
                out.append(tuple(cur))
            cur = [b, o, h, l, c, v]; n = 1
    forming = False
    if cur:
        forming = cur[0] + step > time.time() * 1000      # แท่งนี้ยังไม่ถึงเวลาปิด
        if n == hours or forming:
            out.append(tuple(cur))
    return out, forming


def build_payload(mode):
    data_path = os.path.join(R.HERE, "data", f"live_{C.SYMBOL}_1h.json")
    state_path = os.path.join(R.HERE, "state", f"{mode}_{C.SYMBOL}.json")
    c1h = load_json(data_path, [])
    st = load_json(state_path, None)
    now = int(time.time() * 1000)

    # ---- ต่อแท่งสดจาก Binance TH เข้ากับข้อมูลที่บอทเก็บไว้ ----
    rows = [tuple(c) for c in c1h]
    start = rows[-1][0] + R.HOUR if rows else now - 6 * 24 * R.HOUR
    fresh, src, fetched_at = live_rows(start)
    if fresh:
        have = rows[-1][0] if rows else -1
        rows += [r for r in fresh if r[0] > have]

    # ---- ราคา 4H + EMA ----
    candles, ema20, ema50, forming = [], [], [], False
    if rows:
        tf = R.TF_MS[C.SIGNAL_TF]
        c4, forming = resample_open(rows, tf // R.HOUR)
        closes = [c[4] for c in c4]
        e20, e50 = bt.ema(closes, 20), bt.ema(closes, 50)
        k = max(0, len(c4) - N_CANDLES)
        candles = [[c[0], c[1], c[2], c[3], c[4]] for c in c4[k:]]
        ema20, ema50 = e20[k:], e50[k:]
    last_px = rows[-1][4] if rows else None

    # ---- พอร์ต ----
    port = None
    if st is not None:
        b = R.Broker.__new__(R.Broker); b.s = st; b.mode = mode
        stats = b.stats(last_px)
        pos = st.get("pos")
        upnl = None
        if pos and last_px:
            upnl = pos["qty"] * last_px * (1 - R.COST) + pos.get("proceeds", 0) - pos["notional"]
        curve = [[st.get("created", now), st["start"], None, None]]
        eq = st["start"]
        for t in st["trades"]:
            eq += t["pnl"]
            curve.append([t["exit_time"], eq, t["name"], t["pnl"]])
        curve.append([now, stats["equity"], None, None])
        port = {"start": st["start"], "cash": st["cash"], "equity": stats["equity"], "return": stats["return"],
                "pnl": stats["pnl"], "trades": stats["trades"], "win_rate": stats["win_rate"],
                "pf": None if stats["pf"] == float("inf") else stats["pf"], "pf_inf": stats["pf"] == float("inf"),
                "peak": st.get("peak"), "halted": st.get("halted", False), "pos": pos, "pend": st.get("pend"),
                "upnl": upnl, "history": st["trades"][-200:], "curve": curve, "created": st.get("created")}

    # ---- บอทยังทำงานไหม (ดูจากเวลาที่บอทเขียนไฟล์ล่าสุด) ----
    mtimes = [os.path.getmtime(p) for p in (state_path, data_path) if os.path.exists(p)]
    last_write = int(max(mtimes) * 1000) if mtimes else None

    gate = R.live_gate(R.load_env())
    paper = load_json(os.path.join(R.HERE, "state", f"paper_{C.SYMBOL}.json"), None)
    pb = None
    if paper:
        b = R.Broker.__new__(R.Broker); b.s = paper; s2 = b.stats()
        pb = {"trades": s2["trades"], "pf": None if s2["pf"] == float("inf") else s2["pf"]}
    return {
        "now": now, "mode": mode, "symbol": C.SYMBOL, "tf": C.SIGNAL_TF, "last_price": last_px,
        "last_candle": c1h[-1][0] if c1h else None, "last_write": last_write,
        "live": bool(fresh), "price_src": src, "price_at": fetched_at, "forming": forming,
        "candles": candles, "ema20": ema20, "ema50": ema50, "portfolio": port,
        "config": {"risk_pct": C.RISK_PCT, "kill_dd": C.KILL_SWITCH_DD, "strategies": C.ENABLED_STRATEGIES,
                   "priority": C.STRATEGY_PRIORITY, "min_rr": C.MIN_NET_RR, "fee": C.FEE_RATE,
                   "slippage": C.SLIPPAGE, "capital": C.CAPITAL_USDT, "live_enabled": C.LIVE_ENABLED},
        "gate": {"reasons": gate, "need": C.LIVE_GATE, "paper": pb},
    }


# ---------------------------------------------------------------- FT Tomorn (delta neutral)
FT_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "ft-tomorn", "bot"))
_ft_live = {}
_ft_cfg = {"mtime": None, "mod": None}


def ft_config():
    """อ่าน ft_config.py ของบอท FT (โหลดใหม่เมื่อไฟล์ถูกแก้)"""
    path = os.path.join(FT_DIR, "ft_config.py")
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    if _ft_cfg["mtime"] != mt:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ft_config_view", path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        _ft_cfg.update(mtime=mt, mod=mod)
    return _ft_cfg["mod"]


def _jget(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tomorn-web/1.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def ft_live(sym):
    """ราคาตลาด + funding รอบนี้ (ข้อมูลสาธารณะ Binance) · เก็บไว้ 4 วิ"""
    c = _ft_live.get(sym)
    if c and time.time() - c[0] < 4:
        return c[1]
    out = {}
    try:
        pi = _jget(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
        out.update(mark=float(pi["markPrice"]), rate=float(pi["lastFundingRate"]), next=int(pi["nextFundingTime"]))
    except Exception:  # noqa
        pass
    for base in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            bt_ = _jget(f"{base}/api/v3/ticker/bookTicker?symbol={sym}")
            out.update(bid=float(bt_["bidPrice"]), ask=float(bt_["askPrice"])); break
        except Exception:  # noqa
            continue
    _ft_live[sym] = (time.time(), out)
    return out


def build_ft():
    cfg = ft_config()
    now = int(time.time() * 1000)
    mode = getattr(cfg, "MODE", "demo") if cfg else "demo"
    path = os.path.join(FT_DIR, "state", f"ft_{mode}.json")
    st = load_json(path, None)
    syms = list(getattr(cfg, "SYMBOLS", ["BTCUSDT"])) if cfg else ["BTCUSDT"]
    sym = (st or {}).get("pos", None) and st["pos"]["sym"] or syms[0]
    live = ft_live(sym)
    out = {"now": now, "symbol": sym, "live": live, "state": st,
           "config": {"strategy": getattr(cfg, "STRATEGY", "?"), "enter": getattr(cfg, "ENTER_APR", 0), "exit": getattr(cfg, "EXIT_APR", 0),
                      "leverage": getattr(cfg, "LEVERAGE", 2), "spot_fee": getattr(cfg, "SPOT_FEE", 0.001), "fut_fee": getattr(cfg, "FUT_FEE", 0.0005)}}
    if os.path.exists(path):
        out["last_write"] = int(os.path.getmtime(path) * 1000)
        out["alive"] = now - out["last_write"] < 5 * 60000
    else:
        out["alive"] = False
    if st:
        p = st.get("pos")
        eq = st["cash"]
        if p:
            bid, mark = live.get("bid"), live.get("mark")
            if not (bid and mark):        # ดึงราคาไม่ได้ → ใช้ราคาตอนเปิด (ประมาณ)
                bid = mark = p["fut_entry"]
            short = p["fq"] * (p["fut_entry"] - mark)
            sfee = out["config"]["spot_fee"]
            spot_fee_est = p["spot_cost"] * sfee
            out["legs"] = {"spot_pnl": p["q"] / (1 - sfee) * bid - p["spot_cost"], "short_pnl": short,
                           "fees": p["fut_fee"] + spot_fee_est}
            eq = st["cash"] + p["q"] * bid + short + p["funding"] - p["fut_fee"]
            vm = st["cash"] + short + p["funding"] - p["fut_fee"]
            out["margin_ratio"] = vm / (p["fq"] * mark / out["config"]["leverage"]) if p["fq"] else 0
            out["legs_match"] = p["fq"] > 0 and abs(p["q"] - p["fq"]) / p["fq"] < 0.03
        out["equity"] = eq
    return out


class Handler(BaseHTTPRequestHandler):
    mode = "paper"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/ft_view.js":
            with open(os.path.join(HERE, "ft_view.js"), "rb") as f:
                return self._send(200, f.read(), "text/javascript; charset=utf-8")
        if path == "/api/ft":
            try:
                body = json.dumps(build_ft(), ensure_ascii=False).encode()
                return self._send(200, body, "application/json; charset=utf-8")
            except Exception as e:  # noqa
                return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        if path == "/api/data":
            try:
                body = json.dumps(build_payload(self.mode), ensure_ascii=False).encode()
                return self._send(200, body, "application/json; charset=utf-8")
            except Exception as e:  # noqa
                return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--lan", action="store_true", help="ให้มือถือใน Wi-Fi เดียวกันเปิดดูได้")
    ap.add_argument("--mode", choices=["paper", "live", "alert"], default="paper")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    Handler.mode = a.mode
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    url = f"http://localhost:{a.port}"
    print(f"🌐 เว็บ Tomorn เปิดแล้ว: {url}")
    if a.lan and lan_ip():
        print(f"📱 เปิดบนมือถือ (Wi-Fi เดียวกัน): http://{lan_ip()}:{a.port}")
    print("กด Ctrl + C เพื่อปิดเว็บ (บอทยังทำงานต่อตามปกติ)")
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nปิดเว็บแล้ว")


if __name__ == "__main__":
    main()
