"""Metric Watcher v1.3.3 - bao Telegram moi khi cac vi swap tren Metric (Robinhood Chain).

Cach nhan dien lenh Metric (v1.3):
  - Giao dich gui toi router Metric da biet (cu 0x3c6c..., moi 0x0220...), HOAC
  - Gui toi hop dong co ten chua ca "Metric" va "Router" (tu bat router moi neu Metric doi lan nua)
  - Lenh them/rut thanh khoan (LP) va lenh qua KyberSwap... KHONG tinh.
"""
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CAU HINH: DANH SACH VI (them vi moi = them 1 dong) =====
# "rescan_from" (khong bat buoc): quet lai 1 lan tu so block nay (so nguyen)
#                hoac tu thoi diem nay (chuoi 'YYYY-MM-DD HH:MM', gio Han)
# "rescan_base"  (khong bat buoc): [so lenh, volume] dung ngay truoc block quet lai
#                -> dat lai tong truoc khi quet, khong bao gio bi cong trung
WALLETS = [
    {"address": "0x25CC275CFE3Cce1700E816e00d4CD1f60872038A", "name": "Ví 038A",
     "rescan_from": 108361076, "rescan_base": [40, 102969.49]},
    {"address": "0xfa68aC3Fd61396f478135d0018eb19c9b9ccd1dC", "name": "ví chính",
     "rescan_from": 110000000, "rescan_base": [0, 0.0]},
]
# Router Metric da biet (them dia chi vao day neu can)
KNOWN_METRIC = {
    "0x3c6c4fc94f1493b9d76c5e4c4cdedb444d3cc0a2",  # Metric router cu (den ~10/11/2026)
    "0x02201e6f13c41506c7bb635d7543f98826229ab7",  # MetricOmmSimpleRouter (tu ~10/11/2026)
}
CHAIN_ID = "4663"  # Robinhood Chain
EXPLORER_TX = "https://robin.etherscan.io/tx/"
STATE_FILE = "state.json"
KST = timezone(timedelta(hours=9))
ZERO = "0x0000000000000000000000000000000000000000"
# ==============================================================

API_KEY = os.environ.get("BLOCKSCOUT_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_BASES = [
    f"https://api.blockscout.com/{CHAIN_ID}/api",
    "https://api.blockscout.com/v2/api",
    "https://robinhoodchain.blockscout.com/api",  # du phong (khong can key)
]
CHUNK = 1_000_000      # quet lich su theo tung doan ~1 trieu block (~1 ngay)
SUMMARY_MIN = 5        # tu 5 lenh moi tro len trong 1 lan chay -> gop vao 1 tin


def http_get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "metric-watcher/1.3.3"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def api_raw(params):
    """Goi API Blockscout (kieu Etherscan). Tra ve truong 'result' (bat ky kieu)."""
    errors = []
    for base in API_BASES:
        p = dict(params)
        if "api.blockscout.com" in base:
            p["apikey"] = API_KEY
        if "/v2/" in base:
            p["chainid"] = CHAIN_ID
        url = base + "?" + urllib.parse.urlencode(p)
        try:
            data = http_get_json(url)
        except Exception as e:  # loi mang / het gio
            errors.append(f"{base.split('/')[2]}: {e}")
            continue
        result = data.get("result")
        if result not in (None, "") and not (isinstance(result, str) and "error" in result.lower()):
            return result
        if isinstance(result, list):
            return result
        errors.append(f"{base.split('/')[2]}: {data.get('message')} {str(result)[:100]}")
    raise RuntimeError("API loi: " + " | ".join(errors))


def api(params):
    """Nhu api_raw nhung luon tra ve list."""
    result = api_raw(params)
    if isinstance(result, list):
        return result
    if isinstance(result, str) and "no " in result.lower():
        return []  # "No transactions found"
    raise RuntimeError(f"API tra ve khong phai danh sach: {result}")


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        raise RuntimeError(f"Telegram loi: {res}")


def fmt_amount(raw, decimals):
    x = int(raw) / (10 ** int(decimals or 18))
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.6f}".rstrip("0").rstrip(".")


def money(x):
    return f"${x:,.2f}"


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def migrate(state):
    """Chuyen state.json kieu cu (1 vi) sang kieu moi (nhieu vi), giu nguyen so lieu."""
    if "wallets" in state:
        return state
    old_addr = WALLETS[0]["address"].lower()
    new = {"day": state.get("day", ""), "wallets": {}}
    if "last_block" in state:
        new["wallets"][old_addr] = {
            "last_block": state.get("last_block", 0),
            "total_count": state.get("total_count", 0),
            "total_volume": state.get("total_volume", 0.0),
            "since": state.get("since", ""),
            "day_count": state.get("day_count", 0),
            "day_volume": state.get("day_volume", 0.0),
            "started": state.get("started", False),
        }
    return new


# ---------- Nhan dien router Metric (co nho tam ten hop dong trong state) ----------
def is_metric(addr, cache):
    addr = addr.lower()
    if addr in KNOWN_METRIC:
        return True
    if addr == ZERO:
        return False
    if addr not in cache:
        name = ""
        try:
            res = api_raw({"module": "contract", "action": "getsourcecode", "address": addr})
            if isinstance(res, list) and res:
                name = res[0].get("ContractName") or res[0].get("Name") or ""
            elif isinstance(res, dict):
                name = res.get("ContractName") or res.get("Name") or ""
            elif not isinstance(res, list):
                raise RuntimeError(str(res))
        except Exception as e:
            print("  khong tra duoc ten hop dong", addr, e)
            name = ""  # ghi nho luon de lan sau khong tra lai (tranh chay cham)
        cache[addr] = name
        print(f"  hop dong {addr}: '{name}'")
    n = cache[addr].lower()
    return "metric" in n and "router" in n


def block_at(time_str):
    """Lay so block tai 1 thoi diem (gio Han), dang 'YYYY-MM-DD HH:MM'."""
    ts = int(datetime.strptime(time_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST).timestamp())
    res = api_raw({"module": "block", "action": "getblocknobytime",
                   "timestamp": ts, "closest": "before"})
    if isinstance(res, dict):
        res = res.get("blockNumber")
    return int(res)


def head_block():
    """So block moi nhat cua chain (None neu khong lay duoc)."""
    try:
        res = api_raw({"module": "block", "action": "eth_block_number"})
        return int(res, 16) if isinstance(res, str) and res.startswith("0x") else int(res)
    except Exception as e:
        print("  khong lay duoc block moi nhat:", e)
        return None


def new_wallet_state(wallet, today_str):
    """Vi moi them vao: bat dau tu $0, bo qua lich su (lay block giao dich gan nhat lam moc)."""
    latest = api({"module": "account", "action": "txlist", "address": wallet,
                  "startblock": 0, "endblock": 999999999,
                  "sort": "desc", "page": 1, "offset": 1})
    last_block = max([int(t["blockNumber"]) for t in latest] or [0])
    return {"last_block": last_block, "total_count": 0, "total_volume": 0.0,
            "since": today_str, "day_count": 0, "day_volume": 0.0, "started": False}


def fetch_range(wallet, start_block):
    """Lay txlist/tokentx/internal tu start_block. Khoang dai -> chia doan.
    Tra ve (txs, toks, ints, end_ok, full): end_ok = block cuoi da lay xong."""
    head = head_block()
    if head is None or head - start_block <= CHUNK:
        segs = [(start_block, 999999999)]
    else:
        segs = [(a, min(a + CHUNK - 1, head)) for a in range(start_block, head + 1, CHUNK)]
        segs[-1] = (segs[-1][0], 999999999)
        print(f"  chia {len(segs)} doan tu block {start_block}")
    txs, toks, ints = [], [], []
    end_ok = start_block - 1
    for a, b in segs:
        rng = {"address": wallet, "startblock": a, "endblock": b,
               "sort": "asc", "page": 1, "offset": 1000}
        try:
            t1 = api(dict(rng, module="account", action="txlist"))
            t2 = api(dict(rng, module="account", action="tokentx"))
            try:
                t3 = api(dict(rng, module="account", action="txlistinternal"))
            except Exception:
                t3 = []
        except Exception as e:
            print(f"  LOI lay doan {a}-{b}:", e)
            return txs, toks, ints, end_ok, False
        txs += t1
        toks += t2
        ints += t3
        end_ok = b if b != 999999999 else max(
            [int(t["blockNumber"]) for t in t1 + t2 + t3] + [head or 0, a - 1])
    return txs, toks, ints, end_ok, True


def find_swaps(wallet, start_block, cache):
    """Tim cac lenh Metric moi cua vi. Tra ve (danh sach lenh, block da xem xong, full)."""
    txs, toks, ints, end_ok, full = fetch_range(wallet, start_block)

    groups = {}
    max_block = start_block - 1

    def g(h, bn, ts):
        e = groups.setdefault(h.lower(), {"block": int(bn), "time": int(ts or 0), "tx": None,
                                          "toks": [], "ints": []})
        return e

    def txhash(t):
        # giao dich noi bo tren Blockscout dung "transactionHash" thay cho "hash"
        return t.get("hash") or t.get("transactionHash") or ""

    for kind, items in (("tx", txs), ("toks", toks), ("ints", ints)):
        for t in items:
            h = txhash(t)
            if not h or int(t.get("blockNumber") or 0) < start_block:
                continue
            e = g(h, t["blockNumber"], t.get("timeStamp"))
            if kind == "tx":
                e["tx"] = t
            else:
                e[kind].append(t)

    swaps = []
    for h, e in sorted(groups.items(), key=lambda kv: kv[1]["block"]):
        max_block = max(max_block, e["block"])
        tx = e["tx"]
        if tx is not None and str(tx.get("isError", "0")) != "0":
            continue  # lenh loi
        tx_to = (tx or {}).get("to", "").lower()
        eth_out = int((tx or {}).get("value") or 0) if (tx or {}).get("from", "").lower() == wallet else 0

        # Chi tinh lenh vi tu gui toi router Metric
        if not tx_to or (tx or {}).get("from", "").lower() != wallet:
            continue
        hit = is_metric(tx_to, cache)
        print(f"  tx {h[:10]}… to={tx_to[:10]} metric={hit}")
        if not hit:
            continue

        sold, got, usdg = [], [], 0.0
        if eth_out:
            sold.append(f"{fmt_amount(eth_out, 18)} ETH")
        for t in e["toks"]:
            sym = html.escape(t.get("tokenSymbol") or "?")
            amt = fmt_amount(t["value"], t.get("tokenDecimal"))
            if t.get("from", "").lower() == wallet:
                sold.append(f"{amt} {sym}")
            if t.get("to", "").lower() == wallet:
                got.append(f"{amt} {sym}")
            if "USDG" in sym.upper():
                usdg = max(usdg, int(t["value"]) / 10 ** int(t.get("tokenDecimal") or 6))
        for t in e["ints"]:
            if t.get("to", "").lower() == wallet and int(t.get("value") or 0) > 0:
                got.append(f"{fmt_amount(t['value'], 18)} ETH")
        if not sold and not got:
            continue  # khong co tien di chuyen -> khong phai swap
        swaps.append({"hash": h, "time": e["time"], "sold": sold, "got": got, "usdg": usdg})
    if not full:
        max_block = end_ok  # chi ghi nhan phan da lay xong, lan sau lay tiep
    else:
        max_block = max(max_block, end_ok)
    return swaps, max_block, full


def check_wallet(cfg, ws, cache):
    wallet = cfg["address"].lower()
    name = html.escape(cfg["name"])

    # Quet lai 1 lan (neu co cau hinh rescan_from)
    seen_before = ws["last_block"]
    rescanning = False
    rf = cfg.get("rescan_from")
    if rf and ws.get("rescan_done") != rf:
        rescanning = True
        try:
            b = int(rf) if isinstance(rf, int) else block_at(rf)
            ws["last_block"] = min(ws["last_block"], b)
            if cfg.get("rescan_base"):
                ws["total_count"], ws["total_volume"] = cfg["rescan_base"][0], float(cfg["rescan_base"][1])
            print(f"[{cfg['name']}] quet lai tu block {b} ({rf})")
        except Exception as e:
            print(f"[{cfg['name']}] KHONG quet lai duoc:", e)
        ws["rescan_done"] = rf

    since = ws["since"] or "lúc bật"
    if not ws["started"]:
        send_telegram(
            f"✅ <b>Đã bật theo dõi: {name}</b>\n"
            f"Ví <code>{short(wallet)}</code> trên Metric.\n"
            f"📈 Tổng từ {since}: {ws['total_count']} lệnh · {money(ws['total_volume'])}"
        )
        ws["started"] = True

    swaps, max_block, full = find_swaps(wallet, ws["last_block"] + 1, cache)
    print(f"[{cfg['name']}] {len(swaps)} lenh Metric moi")
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # Quet bu nhieu lenh -> gop vao 1 tin (tranh spam)
    if len(swaps) >= SUMMARY_MIN or (rescanning and len(swaps) > 1):
        lines = [f"🔁 <b>Quét bù lệnh Metric · {name}</b>"]
        for s in swaps:
            usdg = s["usdg"]
            ws["total_count"] += 1
            ws["total_volume"] = round(ws["total_volume"] + usdg, 2)
            dt = datetime.fromtimestamp(s["time"], KST)
            if dt.strftime("%Y-%m-%d") == today:
                ws["day_count"] += 1
                ws["day_volume"] = round(ws["day_volume"] + usdg, 2)
            lines.append(f"• {dt.strftime('%d/%m %H:%M')} · {', '.join(s['sold']) or '—'} → "
                         f"{', '.join(s['got']) or '—'}" + (f" · {money(usdg)}" if usdg else ""))
        lines.append(f"\nĐã cộng thêm: {len(swaps)} lệnh · {money(sum(x['usdg'] for x in swaps))}")
        lines.append(f"📈 Tổng từ {since}: {ws['total_count']} lệnh · {money(ws['total_volume'])}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = "\n".join(lines[:1] + lines[-2:])
        send_telegram(text)
        swaps = []

    for s in swaps:
        usdg = s["usdg"]
        ws["total_count"] += 1
        ws["total_volume"] = round(ws["total_volume"] + usdg, 2)
        dt = datetime.fromtimestamp(s["time"], KST)
        if dt.strftime("%Y-%m-%d") == today:
            ws["day_count"] += 1
            ws["day_volume"] = round(ws["day_volume"] + usdg, 2)
        lines = [f"🔔 <b>Swap mới trên Metric · {name}</b>"]
        lines.append("🔴 Bán: " + (", ".join(s["sold"]) or "—"))
        lines.append("🟢 Nhận: " + (", ".join(s["got"]) or "—"))
        if usdg:
            lines.append(f"💵 Volume lệnh: {money(usdg)}")
        lines.append(f"📊 Hôm nay: {ws['day_count']} lệnh · {money(ws['day_volume'])}")
        lines.append(f"📈 Tổng từ {since}: {ws['total_count']} lệnh · {money(ws['total_volume'])}")
        lines.append(f"⏰ {dt.strftime('%d/%m %H:%M')} (giờ Hàn)")
        lines.append(f'🔗 <a href="{EXPLORER_TX}{s["hash"]}">Xem giao dịch</a>')
        send_telegram("\n".join(lines))
        print("Da bao:", s["hash"])
    if full:
        ws["last_block"] = max(ws["last_block"], max_block, seen_before)
    else:
        ws["last_block"] = max(ws["last_block"], max_block)  # lay duoc toi dau ghi toi do


def main():
    for key, val in [("BLOCKSCOUT_API_KEY", API_KEY), ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
                     ("TELEGRAM_CHAT_ID", CHAT_ID)]:
        if not val:
            print(f"THIEU SECRET: {key}")
            return

    state = migrate(load_state())
    state.setdefault("day", "")
    state.setdefault("wallets", {})
    state.setdefault("contracts", {})
    before = json.dumps(state, sort_keys=True)

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    try:
        # 1) Them vi moi (neu co) - bat dau tu $0
        for cfg in WALLETS:
            addr = cfg["address"].lower()
            if addr not in state["wallets"]:
                try:
                    ws = new_wallet_state(addr, now.strftime("%d/%m/%Y"))
                    if cfg.get("rescan_from"):
                        ws["rescan_done"] = cfg["rescan_from"]  # vi moi: khong quet lai
                    state["wallets"][addr] = ws
                except Exception as e:
                    print(f"LOI khoi tao [{cfg['name']}]:", e)

        # 2) Sang ngay moi -> tong ket ngay hom truoc (tat ca vi trong 1 tin)
        if state["day"] != today:
            if state["day"]:
                d = datetime.strptime(state["day"], "%Y-%m-%d").strftime("%d/%m")
                lines = [f"🗓 <b>Tổng kết ngày {d}</b>"]
                sum_c, sum_v = 0, 0.0
                for cfg in WALLETS:
                    ws = state["wallets"].get(cfg["address"].lower())
                    if not ws:
                        continue
                    sum_c += ws["day_count"]
                    sum_v += ws["day_volume"]
                    lines.append(
                        f"\n👛 <b>{html.escape(cfg['name'])}</b>\n"
                        f"Hôm đó: {ws['day_count']} lệnh · {money(ws['day_volume'])}\n"
                        f"📈 Tổng từ {ws['since'] or 'lúc bật'}: {ws['total_count']} lệnh · "
                        f"{money(ws['total_volume'])}"
                    )
                if len(WALLETS) > 1:
                    lines.append(f"\n🧮 <b>Cộng tất cả ví hôm đó:</b> {sum_c} lệnh · {money(sum_v)}")
                send_telegram("\n".join(lines))
            state["day"] = today
            for ws in state["wallets"].values():
                ws["day_count"] = 0
                ws["day_volume"] = 0.0

        # 3) Kiem tra tung vi (loi o vi nay khong anh huong vi kia)
        for cfg in WALLETS:
            ws = state["wallets"].get(cfg["address"].lower())
            if not ws:
                continue
            try:
                check_wallet(cfg, ws, state["contracts"])
            except Exception as e:
                print(f"LOI [{cfg['name']}]:", e)
    except Exception as e:
        print("LOI:", e)
    finally:
        if json.dumps(state, sort_keys=True) != before:
            save_state(state)
            print("Da luu state.json")


if __name__ == "__main__":
    main()
    sys.exit(0)
