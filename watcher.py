"""Metric Watcher - bao Telegram moi khi vi swap tren Metric (Robinhood Chain)."""
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CAU HINH (chi sua o day neu muon doi vi / router) =====
WALLET = "0x25CC275CFE3Cce1700E816e00d4CD1f60872038A".lower()
ROUTER = "0x3c6c4fC94f1493b9d76C5e4C4CdEdB444D3CC0A2".lower()  # Metric router
CHAIN_ID = "4663"  # Robinhood Chain
EXPLORER_TX = "https://robin.etherscan.io/tx/"
STATE_FILE = "state.json"
KST = timezone(timedelta(hours=9))
# ============================================================

API_KEY = os.environ.get("BLOCKSCOUT_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_BASES = [
    f"https://api.blockscout.com/{CHAIN_ID}/api",
    "https://api.blockscout.com/v2/api",
]


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "metric-watcher/1.0"})
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


def describe_swap(tx):
    """Doc chi tiet 1 giao dich: ban gi, nhan gi, volume bao nhieu."""
    bn = tx["blockNumber"]
    h = tx["hash"].lower()
    rng = {"address": WALLET, "startblock": bn, "endblock": bn, "sort": "asc"}

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
        if t.get("from", "").lower() == WALLET:
            sold.append(f"{amt} {sym}")
        if t.get("to", "").lower() == WALLET:
            got.append(f"{amt} {sym}")
        if "USDG" in sym.upper():
            usdg = max(usdg, int(t["value"]) / 10 ** int(t.get("tokenDecimal") or 6))
    for t in internals:
        if t.get("to", "").lower() == WALLET and int(t.get("value") or 0) > 0:
            got.append(f"{fmt_amount(t['value'], 18)} ETH")
    return sold, got, usdg


def main():
    for name, val in [("BLOCKSCOUT_API_KEY", API_KEY), ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
                      ("TELEGRAM_CHAT_ID", CHAT_ID)]:
        if not val:
            print(f"THIEU SECRET: {name}")
            return

    state = load_state()
    state.setdefault("last_block", 0)
    state.setdefault("total_count", 0)
    state.setdefault("total_volume", 0.0)
    state.setdefault("since", "")
    state.setdefault("day", "")
    state.setdefault("day_count", 0)
    state.setdefault("day_volume", 0.0)
    state.setdefault("started", False)
    before = json.dumps(state, sort_keys=True)

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    try:
        # 1) Tin nhan bat dau (chi gui 1 lan)
        if not state["started"]:
            send_telegram(
                "✅ <b>Metric Watcher đã bật!</b>\n"
                f"Đang theo dõi ví <code>{WALLET[:6]}…{WALLET[-4:]}</code> trên Metric.\n"
                f"📈 Tổng từ {state['since'] or 'lúc bật'}: {state['total_count']} lệnh · "
                f"{money(state['total_volume'])}"
            )
            state["started"] = True

        # 2) Sang ngay moi -> gui tong ket ngay hom truoc
        if state["day"] != today:
            if state["day"]:
                d = datetime.strptime(state["day"], "%Y-%m-%d").strftime("%d/%m")
                send_telegram(
                    f"🗓 <b>Tổng kết ngày {d}</b>\n"
                    f"Số lệnh Metric: {state['day_count']}\n"
                    f"Volume: {money(state['day_volume'])}\n"
                    f"📈 Tổng từ {state['since'] or 'lúc bật'}: {state['total_count']} lệnh · "
                    f"{money(state['total_volume'])}"
                )
            state["day"] = today
            state["day_count"] = 0
            state["day_volume"] = 0.0

        # 3) Lay giao dich moi cua vi
        txs = api({
            "module": "account", "action": "txlist", "address": WALLET,
            "startblock": state["last_block"] + 1, "endblock": 999999999,
            "sort": "asc", "page": 1, "offset": 100,
        })
        txs = [t for t in txs if int(t["blockNumber"]) > state["last_block"]]  # chong bao trung
        txs.sort(key=lambda t: int(t["blockNumber"]))
        print(f"Tim thay {len(txs)} giao dich moi")

        for tx in txs:
            is_metric = (tx.get("to", "").lower() == ROUTER
                         and str(tx.get("isError", "0")) == "0")
            if is_metric:
                sold, got, usdg = describe_swap(tx)
                state["total_count"] += 1
                state["total_volume"] = round(state["total_volume"] + usdg, 2)
                state["day_count"] += 1
                state["day_volume"] = round(state["day_volume"] + usdg, 2)
                t = datetime.fromtimestamp(int(tx["timeStamp"]), KST).strftime("%d/%m %H:%M")
                lines = ["🔔 <b>Swap mới trên Metric</b>"]
                lines.append("🔴 Bán: " + (", ".join(sold) or "—"))
                lines.append("🟢 Nhận: " + (", ".join(got) or "—"))
                if usdg:
                    lines.append(f"💵 Volume lệnh: {money(usdg)}")
                lines.append(f"📊 Hôm nay: {state['day_count']} lệnh · {money(state['day_volume'])}")
                lines.append(f"📈 Tổng từ {state['since'] or 'lúc bật'}: {state['total_count']} lệnh · "
                             f"{money(state['total_volume'])}")
                lines.append(f"⏰ {t} (giờ Hàn)")
                lines.append(f'🔗 <a href="{EXPLORER_TX}{tx["hash"]}">Xem giao dịch</a>')
                send_telegram("\n".join(lines))
                print("Da bao:", tx["hash"])
            state["last_block"] = max(state["last_block"], int(tx["blockNumber"]))
    except Exception as e:
        print("LOI:", e)
    finally:
        if json.dumps(state, sort_keys=True) != before:
            save_state(state)
            print("Da luu state.json")


if __name__ == "__main__":
    main()
    sys.exit(0)
