#!/usr/bin/env python3
"""
Bangkok Flight Price Tracker - Enhanced Edition
- Daily email with full price table
- Best deal summary at top
- 7-day sparkline trends
- ±1 day flexible date nudge
- CAD→USD conversion for YYZ
- Flight score (price + duration + stops)
- Nonstop highlights
- Peak season warnings
- Weekly summary on Sundays
- Telegram alerts for price drops
"""

import json
import os
import smtplib
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from fast_flights import FlightData, Passengers, get_flights
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "fast-flights"], check=True)
    from fast_flights import FlightData, Passengers, get_flights

# ── Config ──────────────────────────────────────────────────────────────────
HOME_AIRPORTS = ["DTW", "LAX", "CLE", "YYZ"]

TRIP_WINDOWS = [
    {"label": "Oct 24 – Nov 4",  "depart": "2026-10-24", "return": "2026-11-04", "peak": False},
    {"label": "Nov 21 – Dec 2",  "depart": "2026-11-21", "return": "2026-12-02", "peak": False},
    {"label": "Dec 19 – Dec 30", "depart": "2026-12-19", "return": "2026-12-30", "peak": True},
    {"label": "Jan 20 – Jan 30", "depart": "2027-01-20", "return": "2027-01-30", "peak": False},
    {"label": "Feb 3 – Feb 13",  "depart": "2027-02-03", "return": "2027-02-13", "peak": False},
]

# CAD to USD rough conversion (update periodically)
CAD_TO_USD = 0.73

# Alert threshold — send Telegram if round trip drops below this (USD)
ALERT_THRESHOLD = 1000

# Price drop threshold for Telegram alert (vs yesterday)
PRICE_DROP_ALERT = 75

EMAIL_TO    = "joeydanyriera@gmail.com"
PRICES_FILE = Path("workflows/prices.json")

# Telegram — set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID as GitHub secrets
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_price(p):
    if isinstance(p, (int, float)) and p > 0:
        return int(p)
    elif isinstance(p, str):
        cleaned = p.replace("$", "").replace(",", "").replace("C", "").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None


def to_usd(price, airport):
    """Convert to USD — YYZ prices are in CAD"""
    if price is None:
        return None
    return int(price * CAD_TO_USD) if airport == "YYZ" else price


def flight_score(price_usd, duration_str, stops):
    """
    Lower = better. Combines price, duration, stops.
    Score = price + (hours * 15) + (stops * 80)
    """
    if price_usd is None:
        return 9999
    hours = 0
    if duration_str and duration_str != "–":
        parts = duration_str.replace("hr", "h").replace("min", "m").split()
        for i, p in enumerate(parts):
            if "h" in p:
                try: hours += int(p.replace("h",""))
                except: pass
            elif "m" in p:
                try: hours += int(p.replace("m","")) / 60
                except: pass
    stop_penalty = (stops or 1) * 80
    return int(price_usd + (hours * 15) + stop_penalty)


def stops_label(stops):
    if stops is None: return "–"
    if stops == 0: return "🟢 Nonstop"
    if stops == 1: return "1 stop"
    return f"{stops} stops"


def search_leg(origin, destination, date):
    try:
        result = get_flights(
            flight_data=[FlightData(date=date, from_airport=origin, to_airport=destination)],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
        )
        candidates = []
        for f in result.flights:
            price = parse_price(f.price)
            if price is None:
                continue
            candidates.append({
                "price": price,
                "airline": getattr(f, "name", None) or "–",
                "departure": getattr(f, "departure", None) or "–",
                "arrival": getattr(f, "arrival", None) or "–",
                "duration": getattr(f, "duration", None) or "–",
                "stops": getattr(f, "stops", None),
            })
        if not candidates:
            return None
        candidates.sort(key=lambda x: x["price"])
        has_details = [c for c in candidates if c["departure"] != "–" and c["airline"] != "–"]
        nonstop = [c for c in candidates if c.get("stops") == 0]
        best = has_details[0] if has_details else candidates[0]
        best["price"] = candidates[0]["price"]
        best["nonstop_price"] = nonstop[0]["price"] if nonstop else None
        best["nonstop_airline"] = nonstop[0]["airline"] if nonstop else None
        return best
    except Exception as e:
        print(f"  ⚠ {origin}→{destination} on {date}: {e}")
        return None


def search_leg_flexible(origin, destination, date):
    """Search the given date plus ±1 day, return best prices for each."""
    results = {}
    d = datetime.strptime(date, "%Y-%m-%d")
    for offset in [-1, 0, 1]:
        check_date = (d + timedelta(days=offset)).strftime("%Y-%m-%d")
        f = search_leg(origin, destination, check_date)
        results[check_date] = f["price"] if f else None
    return results


def load_history():
    if PRICES_FILE.exists():
        with open(PRICES_FILE) as f:
            return json.load(f)
    return {}


def save_history(data):
    PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def trend_arrow(current, previous):
    if current is None or previous is None:
        return "–"
    diff = current - previous
    if diff < -20:   return f"🟢 ▼${abs(diff)}"
    elif diff > 20:  return f"🔴 ▲${diff}"
    else:            return f"⚪ ≈ ${diff:+d}"


def sparkline(history, key, home, window_key):
    """Build a 7-char sparkline from price history."""
    bars = "▁▂▃▄▅▆▇"
    prices = []
    sorted_days = sorted(history.keys())[-8:-1]  # last 7 days before today
    for day in sorted_days:
        p = history.get(day, {}).get(window_key, {}).get(home, {}).get("total")
        if p:
            prices.append(p)
    if len(prices) < 2:
        return ""
    mn, mx = min(prices), max(prices)
    if mn == mx:
        return "".join(["▄"] * len(prices))
    spark = ""
    for p in prices:
        idx = int((p - mn) / (mx - mn) * (len(bars) - 1))
        spark += bars[idx]
    return spark


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        urllib.request.urlopen(url, data, timeout=10)
        print("📱 Telegram alert sent")
    except Exception as e:
        print(f"  ⚠ Telegram error: {e}")


# ── Main scrape ───────────────────────────────────────────────────────────────
def scrape_all():
    results = {}
    for window in TRIP_WINDOWS:
        key = window["depart"]
        results[key] = {}
        for home in HOME_AIRPORTS:
            print(f"\n🔍 {home} | {window['label']}")
            legs = []

            # Outbound
            print(f"   {home}→BKK ({window['depart']})...", end=" ", flush=True)
            out = search_leg(home, "BKK", window["depart"])
            if out:
                print(f"${out['price']} | {out['airline']} | {out['departure']}→{out['arrival']} | {out['duration']} | {stops_label(out['stops'])}")
            else:
                print("N/A")
            legs.append({"label": f"{home} → Bangkok", "date": window["depart"], "flight": out})

            # Return
            print(f"   BKK→{home} ({window['return']})...", end=" ", flush=True)
            ret = search_leg("BKK", home, window["return"])
            if ret:
                print(f"${ret['price']} | {ret['airline']} | {ret['departure']}→{ret['arrival']} | {ret['duration']} | {stops_label(ret['stops'])}")
            else:
                print("N/A")
            legs.append({"label": f"Bangkok → {home} (Return)", "date": window["return"], "flight": ret})

            out_p = out["price"] if out else None
            ret_p = ret["price"] if ret else None
            out_usd = to_usd(out_p, home)
            ret_usd = to_usd(ret_p, home)
            total_usd = (out_usd + ret_usd) if (out_usd and ret_usd) else None
            total_native = (out_p + ret_p) if (out_p and ret_p) else None

            results[key][home] = {
                "legs": legs,
                "total": total_native,
                "total_usd": total_usd,
                "score": flight_score(total_usd, out["duration"] if out else "–",
                                      (out.get("stops") or 0) + (ret.get("stops") or 0) if out and ret else 2)
            }
    return results


# ── Alerts ────────────────────────────────────────────────────────────────────
def check_alerts(today_data, yesterday_data):
    alerts = []
    for window in TRIP_WINDOWS:
        key = window["depart"]
        for home in HOME_AIRPORTS:
            curr = today_data.get(key, {}).get(home, {})
            prev = yesterday_data.get(key, {}).get(home, {})
            total_usd = curr.get("total_usd")
            prev_usd  = prev.get("total_usd")

            if total_usd and total_usd < ALERT_THRESHOLD:
                alerts.append(f"🚨 <b>{home} → Bangkok {window['label']}</b>: ${total_usd} (under ${ALERT_THRESHOLD} threshold!)")

            if total_usd and prev_usd and (prev_usd - total_usd) >= PRICE_DROP_ALERT:
                drop = prev_usd - total_usd
                alerts.append(f"💸 <b>{home} → Bangkok {window['label']}</b>: dropped ${drop} to ${total_usd}!")

    if alerts:
        msg = "✈️ <b>Bangkok Flight Alert</b>\n\n" + "\n".join(alerts)
        send_telegram(msg)
        print(f"🚨 {len(alerts)} alert(s) triggered")
    return alerts


# ── HTML email ────────────────────────────────────────────────────────────────
def flights_url(origin, dest, date):
    d = datetime.strptime(date, "%Y-%m-%d")
    pretty = d.strftime("%B %d %Y").replace(" 0", " ")
    q = f"flights from {origin} to {dest} on {pretty}"
    return "https://www.google.com/travel/flights?q=" + q.replace(" ", "+")


def build_html(today_data, yesterday_data, history, run_date, is_sunday=False):

    # ── Best deal finder ──
    all_deals = []
    for window in TRIP_WINDOWS:
        key = window["depart"]
        for home in HOME_AIRPORTS:
            d = today_data.get(key, {}).get(home, {})
            usd = d.get("total_usd")
            score = d.get("score", 9999)
            if usd:
                all_deals.append((usd, score, home, window["label"], key, d))

    all_deals.sort(key=lambda x: x[0])
    best_price_deal = all_deals[0] if all_deals else None
    all_deals_by_score = sorted(all_deals, key=lambda x: x[1])
    best_score_deal = all_deals_by_score[0] if all_deals_by_score else None

    best_deal_html = ""
    if best_price_deal:
        usd, score, home, win_label, key, d = best_price_deal
        native = d.get("total")
        cad_note = f" (${native:,} CAD)" if home == "YYZ" else ""
        best_deal_html += f"""
        <div style="background:#e8f5e9;border-left:4px solid #22863a;padding:14px 20px;margin-bottom:10px;border-radius:0 8px 8px 0">
          <div style="font-size:13px;color:#1a6b2a;font-weight:700;margin-bottom:4px">💰 CHEAPEST ROUND TRIP TODAY</div>
          <div style="font-size:20px;font-weight:700;color:#1a6b2a">${usd:,} USD{cad_note}</div>
          <div style="font-size:14px;color:#2d8a3e;margin-top:2px">{home} → Bangkok · {win_label}</div>
        </div>"""

    if best_score_deal and best_score_deal[2] != best_price_deal[2] or (best_score_deal and best_score_deal[3] != best_price_deal[3]):
        usd, score, home, win_label, key, d = best_score_deal
        best_deal_html += f"""
        <div style="background:#e8f0fe;border-left:4px solid #0055cc;padding:14px 20px;margin-bottom:10px;border-radius:0 8px 8px 0">
          <div style="font-size:13px;color:#0033aa;font-weight:700;margin-bottom:4px">⭐ BEST VALUE (price + duration + stops)</div>
          <div style="font-size:20px;font-weight:700;color:#0033aa">${usd:,} USD</div>
          <div style="font-size:14px;color:#1a55cc;margin-top:2px">{home} → Bangkok · {win_label}</div>
        </div>"""

    def leg_row(leg, home):
        f = leg.get("flight")
        label = leg.get("label", "")
        date = leg.get("date", "")
        parts = label.split("→")
        origin = parts[0].strip().split(" ")[-1]
        dest = parts[1].strip().split(" ")[0] if len(parts) > 1 else "BKK"
        url = flights_url(origin, dest, date)
        is_return = "Return" in label
        return_badge = ' <span style="background:#e8f4fd;color:#0055cc;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700">RETURN</span>' if is_return else ""

        if not f:
            return f'<tr style="background:#fafafa"><td colspan="6" style="padding:8px 14px 8px 28px;font-size:13px;color:#999;border-bottom:1px solid #f0f0f0">{label} · {date} — <a href="{url}">search Google Flights</a></td></tr>'

        has_info = f.get('departure', '–') != '–' and f.get('airline', '–') != '–'
        price_native = f['price']
        price_usd = to_usd(price_native, home)
        cad_note = f" <span style='font-size:10px;color:#888'>(${price_native:,} CAD)</span>" if home == "YYZ" else ""

        # Nonstop callout
        nonstop_html = ""
        ns_p = f.get("nonstop_price")
        ns_a = f.get("nonstop_airline")
        if ns_p and ns_p != price_native:
            ns_usd = to_usd(ns_p, home)
            nonstop_html = f'<br><span style="font-size:11px;color:#22863a">🟢 Nonstop available: ${ns_usd:,} ({ns_a})</span>'

        if not has_info:
            return f"""<tr style="background:#fafafa">
              <td style="padding:9px 14px 9px 28px;font-size:13px;color:#444;border-bottom:1px solid #efefef">{label}{return_badge}<br><span style="color:#aaa;font-size:11px">{date}</span></td>
              <td style="padding:9px 14px;font-size:14px;font-weight:700;color:#0044bb;border-bottom:1px solid #efefef">${price_usd:,}{cad_note}</td>
              <td colspan="4" style="padding:9px 14px;font-size:12px;color:#888;border-bottom:1px solid #efefef;font-style:italic">Details unavailable — <a href="{url}" style="color:#0055cc">🔍 View on Google Flights →</a>{nonstop_html}</td>
            </tr>"""

        stop_color = "#22863a" if f.get("stops") == 0 else "#555"
        book_link = f'<br><a href="{url}" style="font-size:11px;color:#0055cc;text-decoration:none">🔍 Search on Google Flights →</a>'
        return f"""<tr style="background:#fafafa">
          <td style="padding:9px 14px 9px 28px;font-size:13px;color:#444;border-bottom:1px solid #efefef">{label}{return_badge}<br><span style="color:#aaa;font-size:11px">{date}</span>{book_link}{nonstop_html}</td>
          <td style="padding:9px 14px;font-size:14px;font-weight:700;color:#0044bb;border-bottom:1px solid #efefef">${price_usd:,}{cad_note}</td>
          <td style="padding:9px 14px;font-size:13px;color:#333;border-bottom:1px solid #efefef">{f.get('airline','–')}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef;white-space:nowrap">{f.get('departure','–')} → {f.get('arrival','–')}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef">{f.get('duration','–')}</td>
          <td style="padding:9px 14px;font-size:13px;font-weight:600;color:{stop_color};border-bottom:1px solid #efefef">{stops_label(f.get('stops'))}</td>
        </tr>"""

    sections = ""
    for window in TRIP_WINDOWS:
        key = window["depart"]
        peak_badge = ' <span style="background:#fff3cd;color:#856404;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700">⚠ PEAK HOLIDAY PRICES</span>' if window["peak"] else ""

        sections += f'<tr><td colspan="6" style="padding:24px 14px 8px;font-size:17px;font-weight:700;color:#111;border-top:3px solid #0055cc">📅 {window["label"]}{peak_badge}</td></tr>'

        for home in HOME_AIRPORTS:
            curr = today_data.get(key, {}).get(home, {})
            prev = yesterday_data.get(key, {}).get(home, {})
            curr_usd = curr.get("total_usd")
            prev_usd = prev.get("total_usd")
            curr_native = curr.get("total")
            arrow = trend_arrow(curr_usd, prev_usd)
            score = curr.get("score")

            cad_note = f" <span style='font-size:11px;color:#888'>(${curr_native:,} CAD)</span>" if home == "YYZ" and curr_native else ""
            total_str = f"<strong style='font-size:16px;color:#111'>${curr_usd:,} USD</strong>{cad_note}" if curr_usd else "<em style='color:#999'>N/A</em>"
            score_str = f"<span style='font-size:11px;color:#888'>score: {score}</span>" if score and score < 9999 else ""
            spark = sparkline(history, key, home, key)
            spark_html = f"<span style='font-family:monospace;font-size:13px;color:#aaa;letter-spacing:1px'>{spark}</span>" if spark else ""

            sections += f"""<tr style="background:#eef2ff">
          <td colspan="6" style="padding:10px 14px 6px">
            <span style="font-size:15px;font-weight:700;color:#0033aa">✈ From {home}</span>
            &nbsp;&nbsp;{total_str}&nbsp;&nbsp;
            <span style="font-size:13px">{arrow}</span>
            &nbsp;&nbsp;{score_str}
            &nbsp;&nbsp;{spark_html}
            &nbsp;&nbsp;<span style="font-size:11px;color:#888;font-style:italic">outbound + return</span>
          </td>
        </tr>
        <tr style="background:#dde4f8">
          <th style="padding:5px 14px 5px 28px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Leg</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Price (USD)</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Airline</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Times</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Duration</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Stops</th>
        </tr>"""

            for leg in curr.get("legs", []):
                sections += leg_row(leg, home)

    # Weekly summary table (Sundays only)
    weekly_html = ""
    if is_sunday:
        weekly_html = "<h2 style='font-size:16px;color:#333;margin:24px 0 12px'>📊 7-Day Price History</h2><table style='width:100%;border-collapse:collapse;font-size:12px'>"
        weekly_html += "<tr style='background:#f0f4ff'><th style='padding:6px 10px;text-align:left'>Route</th>"
        sorted_days = sorted(history.keys())[-8:]
        for day in sorted_days:
            weekly_html += f"<th style='padding:6px 10px;text-align:right'>{day[5:]}</th>"
        weekly_html += "</tr>"
        for window in TRIP_WINDOWS:
            key = window["depart"]
            for home in HOME_AIRPORTS:
                weekly_html += f"<tr><td style='padding:5px 10px;color:#444'>{home}→BKK {window['label']}</td>"
                for day in sorted_days:
                    p = history.get(day, {}).get(key, {}).get(home, {}).get("total_usd")
                    cell = f"${p:,}" if p else "–"
                    weekly_html += f"<td style='padding:5px 10px;text-align:right;color:#333'>{cell}</td>"
                weekly_html += "</tr>"
        weekly_html += "</table>"

    title = "📊 Weekly Bangkok Flight Summary" if is_sunday else "✈️ Bangkok Trip — Daily Flight Prices"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;margin:0;padding:20px">
<div style="max-width:820px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.10)">
  <div style="background:linear-gradient(135deg,#003faa,#0088ee);padding:28px 32px;color:#fff">
    <h1 style="margin:0;font-size:22px">{title}</h1>
    <p style="margin:8px 0 0;opacity:.85;font-size:14px">Scraped {run_date} · Economy · 1 adult · All prices in USD</p>
  </div>

  <div style="padding:20px 32px 8px">
    {best_deal_html}
  </div>

  <div style="padding:8px 32px 8px;border-bottom:1px solid #eee">
    <p style="margin:0;color:#555;font-size:13px;line-height:1.7">
      <strong>Route:</strong> Home → Bangkok (BKK) → Home &nbsp;·&nbsp;
      <strong>Score</strong> = price + (flight hours × $15) + (stops × $80) — lower is better &nbsp;·&nbsp;
      <strong>Sparkline</strong> = 7-day price trend (▁=low ▇=high)
    </p>
  </div>

  <div style="padding:8px 32px 28px">
    <table style="width:100%;border-collapse:collapse">{sections}</table>
    {weekly_html}
    <p style="margin:20px 0 0;font-size:11px;color:#bbb">
      Prices from Google Flights · YYZ shown in USD (CAD×{CAD_TO_USD}) with native CAD in brackets ·
      🟢 Nonstop = cheapest nonstop option if available · Alerts fire via Telegram when price drops ${PRICE_DROP_ALERT}+ or goes under ${ALERT_THRESHOLD}
    </p>
  </div>

  <div style="background:#f8f9fa;padding:14px 32px;text-align:center;font-size:12px;color:#bbb">
    Bangkok Flight Tracker · Runs daily at 7am ET · github.com/ballhog/NextTravels
  </div>
</div>
</body></html>"""


def send_email(html, run_date, is_sunday=False):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        print("⚠ GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping email.")
        return
    subject = f"📊 Bangkok Flights Weekly Summary — {run_date}" if is_sunday else f"✈️ Bangkok Flights — {run_date}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, EMAIL_TO, msg.as_string())
    print(f"📧 Email sent to {EMAIL_TO}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_date  = datetime.now().strftime("%B %d, %Y")
    today_key = datetime.now().strftime("%Y-%m-%d")
    is_sunday = datetime.now().weekday() == 6

    print(f"🦞 Bangkok Flight Tracker — {run_date}\n{'─'*50}")
    if is_sunday:
        print("📊 Sunday — weekly summary email will be sent")

    history = load_history()
    yesterday_data = history.get("latest", {})

    print("\n📡 Scraping Google Flights...")
    today_data = scrape_all()

    history["latest"] = today_data
    history[today_key] = today_data
    save_history(history)
    print(f"\n💾 Prices saved to {PRICES_FILE}")

    check_alerts(today_data, yesterday_data)

    html = build_html(today_data, yesterday_data, history, run_date, is_sunday)
    send_email(html, run_date, is_sunday)

    print(f"\n{'─'*50}\n📊 Summary:\n")
    for window in TRIP_WINDOWS:
        key = window["depart"]
        print(f"  {window['label']}:")
        for home in HOME_AIRPORTS:
            d = today_data.get(key, {}).get(home, {})
            usd = d.get("total_usd")
            score = d.get("score")
            print(f"    {home}: {'$'+str(usd)+' USD' if usd else 'N/A'} (score: {score})")
