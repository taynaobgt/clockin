#!/usr/bin/env python3
"""
watch_clockin.py  (ban single-run cho GitHub Actions - clockin.win)
--------------------------------------------------------------------------
Doi ten file nay thanh watch.py khi dua vao repo GitHub rieng cho $CLOCKIN,
de khop voi lenh "python watch.py" trong file workflow watch-clockin.yml.

Theo doi https://clockin.win/ va gui thong bao qua Telegram khi:
  1) Trang chuyen tu "Awaiting shift start" sang trang thai khac
  2) Phat hien chuoi giong CA (contract address) xuat hien lan dau
  3) Noi dung HTML thay doi (deploy moi, code moi)
  4) Cu moi STATUS_REPORT_INTERVAL_MINUTES phut, gui bao cao dinh ky

Ban CHAY MOT LAN, duoc GitHub Actions goi theo lich cron. State duoc luu
vao state.json va commit lai vao repo giua cac lan chay.
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = os.environ.get("WATCH_URL", "https://clockin.win/")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
REQUEST_TIMEOUT = 15
COMING_SOON_MARKERS = ["awaiting shift start"]

ETH_CA_REGEX = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOLANA_CA_REGEX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

STATUS_REPORT_INTERVAL_MINUTES = int(os.environ.get("STATUS_REPORT_INTERVAL_MINUTES", "60"))
STATUS_REPORT_INTERVAL_SECONDS = STATUS_REPORT_INTERVAL_MINUTES * 60


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log("Thieu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (kiem tra GitHub Secrets).")
        log(f"[Thong bao le ra se gui]: {text}")
        return
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(api_url, data=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            log(f"Gui Telegram that bai: {resp.status_code} {resp.text}")
        else:
            log("Da gui thong bao Telegram.")
    except requests.RequestException as e:
        log(f"Loi khi gui Telegram: {e}")


def fetch_page():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(URL, headers=headers, timeout=REQUEST_TIMEOUT)
        return resp.text, resp.status_code
    except requests.RequestException as e:
        log(f"Loi khi tai trang: {e}")
        return None, None


def normalize_html(html: str) -> str:
    return re.sub(r"\s+", " ", html).strip()


def compute_hash(html: str) -> str:
    return hashlib.sha256(normalize_html(html).encode("utf-8")).hexdigest()


def is_coming_soon(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in COMING_SOON_MARKERS)


def find_possible_ca(html: str):
    found = set()
    found.update(ETH_CA_REGEX.findall(html))
    for match in SOLANA_CA_REGEX.findall(html):
        if 32 <= len(match) <= 44:
            found.add(match)
    return found


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log("state.json loi, khoi tao lai tu dau.")
    return {
        "hash": None,
        "coming_soon": None,
        "last_report_ts": None,
        "last_changed_ts": None,
        "known_ca_candidates": [],
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def maybe_send_periodic_report(state: dict, current_coming_soon, status_code, changed_this_round: bool) -> None:
    now = time.time()
    last_report_ts = state.get("last_report_ts")

    should_report = (last_report_ts is None) or (now - last_report_ts >= STATUS_REPORT_INTERVAL_SECONDS)
    if not should_report:
        return

    status_text = "Van dang 'Awaiting shift start'" if current_coming_soon else "KHONG con 'Awaiting shift start' (co the da mo!)"
    change_text = "Co thay doi moi trong ky vua qua" if changed_this_round else "Khong co gi thay doi trong ky vua qua"
    last_changed_ts = state.get("last_changed_ts")
    last_changed_str = (
        datetime.fromtimestamp(last_changed_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if last_changed_ts else "chua ghi nhan thay doi nao"
    )

    send_telegram_message(
        "<b>Bao cao dinh ky - $CLOCKIN Watcher (GitHub Actions)</b>\n\n"
        f"URL: {URL}\n"
        f"Trang thai hien tai: {status_text}\n"
        f"Tinh hinh ky nay ({STATUS_REPORT_INTERVAL_MINUTES} phut qua): {change_text}\n"
        f"Lan thay doi gan nhat: {last_changed_str}\n"
        f"HTTP status lan check gan nhat: {status_code}\n\n"
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    state["last_report_ts"] = now


def main():
    state = load_state()
    html, status_code = fetch_page()

    if html is None:
        log("Bo qua lan nay do loi mang khi tai trang.")
        sys.exit(0)

    current_hash = compute_hash(html)
    current_coming_soon = is_coming_soon(html)
    current_ca_candidates = find_possible_ca(html)
    first_run = state.get("hash") is None
    changed_this_round = False

    known_ca = set(state.get("known_ca_candidates", []))
    new_ca_found = current_ca_candidates - known_ca

    if not first_run and state.get("coming_soon") is True and current_coming_soon is False:
        changed_this_round = True
        send_telegram_message(
            "<b>PHAT HIEN THAY DOI QUAN TRONG!</b>\n\n"
            f"Trang <a href='{URL}'>{URL}</a> co ve da <b>KHONG CON o trang thai 'Awaiting shift start'</b> nua!\n"
            "Rat co the site da mo / co the thao tac duoc. Kiem tra ngay!\n\n"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    elif not first_run and new_ca_found:
        changed_this_round = True
        ca_list_str = "\n".join(f"- <code>{ca}</code>" for ca in sorted(new_ca_found)[:5])
        send_telegram_message(
            "<b>PHAT HIEN CHUOI GIONG CONTRACT ADDRESS TREN TRANG!</b>\n\n"
            f"URL: {URL}\n"
            f"{ca_list_str}\n\n"
            "CANH BAO: day chi la phat hien mau tu dong, KHONG xac thuc tinh hop le. "
            "Luon tu kiem tra lai qua kenh X/Twitter chinh thuc cua du an truoc khi tin tuong bat ky CA nao.\n\n"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    elif not first_run and current_hash != state.get("hash"):
        changed_this_round = True
        send_telegram_message(
            "<b>Phat hien thay doi noi dung/code tren trang</b>\n\n"
            f"URL: {URL}\n"
            f"Trang thai 'Awaiting shift start': {'Co' if current_coming_soon else 'KHONG (da doi!)'}\n"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    elif first_run:
        log(f"Khoi tao state ban dau. Awaiting shift start = {current_coming_soon}, status_code={status_code}")
    else:
        log("Khong co thay doi.")

    if changed_this_round:
        state["last_changed_ts"] = time.time()

    state["hash"] = current_hash
    state["coming_soon"] = current_coming_soon
    state["known_ca_candidates"] = sorted(current_ca_candidates)
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    state["last_status_code"] = status_code

    maybe_send_periodic_report(state, current_coming_soon, status_code, changed_this_round)

    save_state(state)


if __name__ == "__main__":
    main()
