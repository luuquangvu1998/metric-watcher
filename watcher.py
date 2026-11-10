"""Metric Watcher - bao Telegram moi khi cac vi swap tren Metric (Robinhood Chain)."""
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CAU HINH: DANH SACH VI (them vi moi = them 1 dong) =====
WALLETS = [
    {"address": "0x25CC275CFE3Cce1700E816e00d4CD1f60872038A", "name": "Ví 038A"},
    {"address": "0xfa68aC3Fd61396f478135d0018eb19c9b9ccd1dC", "name": "ví chính"},
]
ROUTER = "0x3c6c4fC94f1493b9d76C5e4C4CdEdB444D3CC0A2".lower()  # Metric router
CHAIN_ID = "4663"  # Robinhood Chain
EXPLORER_TX = "https://robin.etherscan.io/tx/"
STATE_FILE = "state.json"
KST = timezone(timedelta(hours=9))
# ==============================================================

API_KEY = os.environ.get("BLOCKSCOUT_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_BASES = [
    f"https://api.blockscout.com/{CHAIN_ID}/api",
    "https://api.blockscout.com/v2/api",
]


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "metric-watcher/1.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def api(params):
    """Goi API Blockscout (kieu Etherscan). Tra ve list ket qua."""
    errors = []
    for base in API_BASES:
        p = dict(params, apikey=API_KEY)
        if "/v2/" in base:
            p["chainid"] = CHAIN_ID
        url = base + "?" + urllib.parse.urlencode(p)
        try:
            data = http_get_json(url)
        except Exception as e:  # loi mang
            errors.append(f"{base}: {e}")
            continue
        result = data.get("result")
        if isinstance(result, list):
            return result
        errors.append(f"{base}: {data.get('message')} {result}")
    raise RuntimeError("API loi: " + " | ".join(errors))


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


def describe_swap(wallet, tx):
    """Doc chi tiet 1 giao dich: ban gi, nhan gi, volume bao nhieu."""
    bn = tx["blockNumber"]
    h = tx["hash"].lower()
    rng = {"address": wallet, "startblock": bn, "endblock": bn, "sort": "asc"}

    transfers = [t for t in api(dict(rng, module="account", action="tokentx"))
                 if t.get("hash", "").lower() == h]
    try:
        internals = [t for t in api(dict(rng, module="account", action="txlistinternal"))
                     if t.get("hash", "").lower() == h]
    except Exception:
        internals = []

    sold, got = [], []
    usdg = 0.0
    if int(tx.get("value") or 0) > 0:
        sold.append(f"{fmt_amount(tx['value'], 18)} ETH")
    for t in transfers:
        sym = html.escape(t.get("tokenSymbol") or "?")
        amt = fmt_amount(t["value"], t.get("tokenDecimal"))
        if t.get("from", "").lower() == wallet:
            sold.append(f"{amt} {sym}")
        if t.get("to", "").lower() == wallet:
            got.append(f"{amt} {sym}")
        if "USDG" in sym.upper():
            usdg = max(usdg, int(t["value"]) / 10 ** int(t.get("tokenDecimal") or 6))
    for t in internals:
        if t.get("to", "").lower() == wallet and int(t.get("value") or 0) > 0:
            got.append(f"{fmt_amount(t['value'], 18)} ETH")
    return sold, got, usdg


def new_wallet_state(wallet, today_str):
    """Vi moi them vao: bat dau tu $0, bo qua lich su (lay block giao dich gan nhat lam moc)."""
    latest = api({"module": "account", "action": "txlist", "address": wallet,
                  "startblock": 0, "endblock": 999999999,
                  "sort": "desc", "page": 1, "offset": 1})
    last_block = max([int(t["blockNumber"]) for t in latest] or [0])
    return {"last_block": last_block, "total_count": 0, "total_volume": 0.0,
            "since": today_str, "day_count": 0, "day_volume": 0.0, "started": False}


def check_wallet(cfg, ws):
    wallet = cfg["address"].lower()
    name = html.escape(cfg["name"])
    since = ws["since"] or "lúc bật"

    if not ws["started"]:
        send_telegram(
            f"✅ <b>Đã bật theo dõi: {name}</b>\n"
            f"Ví <code>{short(wallet)}</code> trên Metric.\n"
            f"📈 Tổng từ {since}: {ws['total_count']} lệnh · {money(ws['total_volume'])}"
        )
        ws["started"] = True

    txs = api({
        "module": "account", "action": "txlist", "address": wallet,
        "startblock": ws["last_block"] + 1, "endblock": 999999999,
        "sort": "asc", "page": 1, "offset": 100,
    })
    txs = [t for t in txs if int(t["blockNumber"]) > ws["last_block"]]  # chong bao trung
    txs.sort(key=lambda t: int(t["blockNumber"]))
    print(f"[{cfg['name']}] {len(txs)} giao dich moi")

    for tx in txs:
        is_metric = (tx.get("to", "").lower() == ROUTER
                     and str(tx.get("isError", "0")) == "0")
        if is_metric:
            sold, got, usdg = describe_swap(wallet, tx)
            ws["total_count"] += 1
            ws["total_volume"] = round(ws["total_volume"] + usdg, 2)
            ws["day_count"] += 1
            ws["day_volume"] = round(ws["day_volume"] + usdg, 2)
            t = datetime.fromtimestamp(int(tx["timeStamp"]), KST).strftime("%d/%m %H:%M")
            lines = [f"🔔 <b>Swap mới trên Metric · {name}</b>"]
            lines.append("🔴 Bán: " + (", ".join(sold) or "—"))
            lines.append("🟢 Nhận: " + (", ".join(got) or "—"))
            if usdg:
                lines.append(f"💵 Volume lệnh: {money(usdg)}")
            lines.append(f"📊 Hôm nay: {ws['day_count']} lệnh · {money(ws['day_volume'])}")
            lines.append(f"📈 Tổng từ {since}: {ws['total_count']} lệnh · {money(ws['total_volume'])}")
            lines.append(f"⏰ {t} (giờ Hàn)")
            lines.append(f'🔗 <a href="{EXPLORER_TX}{tx["hash"]}">Xem giao dịch</a>')
            send_telegram("\n".join(lines))
            print("Da bao:", tx["hash"])
        ws["last_block"] = max(ws["last_block"], int(tx["blockNumber"]))


def main():
    for key, val in [("BLOCKSCOUT_API_KEY", API_KEY), ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
                     ("TELEGRAM_CHAT_ID", CHAT_ID)]:
        if not val:
            print(f"THIEU SECRET: {key}")
            return

    state = migrate(load_state())
    state.setdefault("day", "")
    state.setdefault("wallets", {})
    before = json.dumps(state, sort_keys=True)

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    try:
        # 1) Them vi moi (neu co) - bat dau tu $0
        for cfg in WALLETS:
            addr = cfg["address"].lower()
            if addr not in state["wallets"]:
                try:
                    state["wallets"][addr] = new_wallet_state(addr, now.strftime("%d/%m/%Y"))
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
                check_wallet(cfg, ws)
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
