#!/usr/bin/env python3
"""
Thailand Flight Price Tracker
Scrapes Google Flights daily for round trips to Bangkok.
Emails a comparison table + trend to joeydanyriera@gmail.com
"""

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from fast_flights import FlightData, Passengers, get_flights
except ImportError:
    print("Installing fast-flights...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "fast-flights"], check=True)
    from fast_flights import FlightData, Passengers, get_flights

# ── Config ──────────────────────────────────────────────────────────────────
HOME_AIRPORTS = ["DTW", "LAX", "CLE", "YYZ"]

TRIP_WINDOWS = [
    {"label": "Oct 24 – Nov 4",   "depart": "2026-10-24", "return": "2026-11-04"},
    {"label": "Nov 21 – Dec 2",   "depart": "2026-11-21", "return": "2026-12-02"},
    {"label": "Dec 19 – Dec 30",  "depart": "2026-12-19", "return": "2026-12-30"},
    {"label": "Jan 20 – Jan 30",  "depart": "2027-01-20", "return": "2027-01-30"},
    {"label": "Feb 3 – Feb 13",   "depart": "2027-02-03", "return": "2027-02-13"},
]

EMAIL_TO   = "joeydanyriera@gmail.com"
PRICES_FILE = Path("workflows/prices.json")

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
        best = has_details[0] if has_details else candidates[0]
        best["price"] = candidates[0]["price"]
        return best
    except Exception as e:
        print(f"  ⚠ {origin}→{destination} on {date}: {e}")
        return None


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
    if diff < -20:
        return f"🟢 ▼${abs(diff)}"
    elif diff > 20:
        return f"🔴 ▲${diff}"
    else:
        return f"⚪ ≈ ${diff:+d}"


def stops_label(stops):
    if stops is None: return "–"
    if stops == 0: return "Nonstop"
    if stops == 1: return "1 stop"
    return f"{stops} stops"


# ── Main scrape ───────────────────────────────────────────────────────────────
def scrape_all():
    results = {}
    for window in TRIP_WINDOWS:
        key = window["depart"]
        results[key] = {}
        for home in HOME_AIRPORTS:
            print(f"\n🔍 {home} | {window['label']}")
            legs = []

            # Outbound: Home → Bangkok
            print(f"   {home}→BKK ({window['depart']})...", end=" ", flush=True)
            out = search_leg(home, "BKK", window["depart"])
            if out:
                print(f"${out['price']} | {out['airline']} | {out['departure']}→{out['arrival']} | {out['duration']} | {stops_label(out['stops'])}")
            else:
                print("N/A")
            legs.append({"label": f"{home} → Bangkok", "date": window["depart"], "flight": out})

            # Return: Bangkok → Home
            print(f"   BKK→{home} ({window['return']})...", end=" ", flush=True)
            ret = search_leg("BKK", home, window["return"])
            if ret:
                print(f"${ret['price']} | {ret['airline']} | {ret['departure']}→{ret['arrival']} | {ret['duration']} | {stops_label(ret['stops'])}")
            else:
                print("N/A")
            legs.append({"label": f"Bangkok → {home} (Return)", "date": window["return"], "flight": ret})

            out_price = out["price"] if out else None
            ret_price = ret["price"] if ret else None
            total = (out_price + ret_price) if (out_price and ret_price) else None
            results[key][home] = {"legs": legs, "total": total}
    return results


# ── HTML email ────────────────────────────────────────────────────────────────
def flights_url(origin, dest, date):
    d = datetime.strptime(date, "%Y-%m-%d")
    pretty = d.strftime("%B %d %Y").replace(" 0", " ")
    q = f"flights from {origin} to {dest} on {pretty}"
    return "https://www.google.com/travel/flights?q=" + q.replace(" ", "+")


def build_html(today_data, yesterday_data, run_date):

    def leg_row(leg):
        f = leg.get("flight")
        label = leg.get("label", "")
        date = leg.get("date", "")
        parts = label.split("→")
        origin = parts[0].strip().split(" ")[-1]
        dest = parts[1].strip().split(" ")[0] if len(parts) > 1 else "BKK"
        url = flights_url(origin, dest, date)
        is_return = "Return" in label

        if not f:
            return f'<tr style="background:#fafafa"><td colspan="6" style="padding:8px 14px 8px 28px;font-size:13px;color:#999;border-bottom:1px solid #f0f0f0">{label} · {date} — <a href="{url}">search Google Flights</a></td></tr>'

        stop_color = "#22863a" if f.get("stops") == 0 else "#555"
        return_badge = ' <span style="background:#e8f4fd;color:#0055cc;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700;letter-spacing:.3px">RETURN</span>' if is_return else ""
        book_link = f'<br><a href="{url}" style="font-size:11px;color:#0055cc;text-decoration:none">🔍 Search on Google Flights →</a>'

        return f"""<tr style="background:#fafafa">
          <td style="padding:9px 14px 9px 28px;font-size:13px;color:#444;border-bottom:1px solid #efefef">{label}{return_badge}<br><span style="color:#aaa;font-size:11px">{date}</span>{book_link}</td>
          <td style="padding:9px 14px;font-size:14px;font-weight:700;color:#0044bb;border-bottom:1px solid #efefef">${f['price']:,}</td>
          <td style="padding:9px 14px;font-size:13px;color:#333;border-bottom:1px solid #efefef">{f.get('airline','–')}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef;white-space:nowrap">{f.get('departure','–')} → {f.get('arrival','–')}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef">{f.get('duration','–')}</td>
          <td style="padding:9px 14px;font-size:13px;font-weight:600;color:{stop_color};border-bottom:1px solid #efefef">{stops_label(f.get('stops'))}</td>
        </tr>"""

    sections = ""
    for window in TRIP_WINDOWS:
        key = window["depart"]
        sections += f'<tr><td colspan="6" style="padding:24px 14px 8px;font-size:17px;font-weight:700;color:#111;border-top:3px solid #0055cc">📅 {window["label"]}</td></tr>'

        for home in HOME_AIRPORTS:
            curr = today_data.get(key, {}).get(home, {})
            prev = yesterday_data.get(key, {}).get(home, {})
            curr_total = curr.get("total")
            prev_total = prev.get("total")
            arrow = trend_arrow(curr_total, prev_total)
            total_str = f"<strong style='font-size:16px;color:#111'>${curr_total:,}</strong> total" if curr_total else "<em style='color:#999'>N/A</em>"

            sections += f"""<tr style="background:#eef2ff">
          <td colspan="6" style="padding:10px 14px 6px">
            <span style="font-size:15px;font-weight:700;color:#0033aa">✈ From {home}</span>
            &nbsp;&nbsp;{total_str}&nbsp;&nbsp;
            <span style="font-size:13px">{arrow}</span>
            &nbsp;&nbsp;<span style="font-size:11px;color:#888;font-style:italic">outbound + return</span>
          </td>
        </tr>
        <tr style="background:#dde4f8">
          <th style="padding:5px 14px 5px 28px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Leg</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Price</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Airline</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Times</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Duration</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:.5px">Stops</th>
        </tr>"""

            for leg in curr.get("legs", []):
                sections += leg_row(leg)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;margin:0;padding:20px">
<div style="max-width:800px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.10)">
  <div style="background:linear-gradient(135deg,#003faa,#0088ee);padding:28px 32px;color:#fff">
    <h1 style="margin:0;font-size:22px">✈️ Bangkok Trip — Daily Flight Prices</h1>
    <p style="margin:8px 0 0;opacity:.85;font-size:14px">Scraped {run_date} · Economy · 1 adult · Round trip to Bangkok</p>
  </div>
  <div style="padding:16px 32px 8px;border-bottom:1px solid #eee">
    <p style="margin:0;color:#555;font-size:13px;line-height:1.7">
      <strong>Route:</strong> Home → Bangkok (BKK) → Home<br>
      <strong>Total</strong> = outbound + return flight combined.
    </p>
  </div>
  <div style="padding:8px 32px 28px">
    <table style="width:100%;border-collapse:collapse">{sections}</table>
    <p style="margin:20px 0 0;font-size:11px;color:#bbb">Prices from Google Flights · YYZ in CAD · All others USD · Best price at time of scrape</p>
  </div>
  <div style="background:#f8f9fa;padding:14px 32px;text-align:center;font-size:12px;color:#bbb">
    Bangkok Flight Tracker · Auto-runs daily at 7am ET · github.com/ballhog/NextTravels
  </div>
</div>
</body></html>"""


def send_email(html, run_date):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        print("⚠ GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping email.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"✈️ Bangkok Flights — {run_date}"
    msg["From"]    = gmail_user
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, EMAIL_TO, msg.as_string())
    print(f"📧 Email sent to {EMAIL_TO}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_date = datetime.now().strftime("%B %d, %Y")
    today_key = datetime.now().strftime("%Y-%m-%d")
    print(f"🦞 Bangkok Flight Tracker — {run_date}\n{'─'*50}")
    history = load_history()
    yesterday_data = history.get("latest", {})
    print("\n📡 Scraping Google Flights...")
    today_data = scrape_all()
    history["latest"] = today_data
    history[today_key] = today_data
    save_history(history)
    print(f"\n💾 Prices saved to {PRICES_FILE}")
    html = build_html(today_data, yesterday_data, run_date)
    send_email(html, run_date)
    print(f"\n{'─'*50}\n📊 Summary:\n")
    for window in TRIP_WINDOWS:
        key = window["depart"]
        print(f"  {window['label']}:")
        for home in HOME_AIRPORTS:
            d = today_data.get(key, {}).get(home, {})
            total = d.get("total")
            print(f"    {home}: {'$'+str(total) if total else 'N/A'}")
            for leg in d.get("legs", []):
                f = leg.get("flight")
                if f:
                    print(f"      {leg['label']}: ${f['price']} | {f['airline']} | {f['departure']}→{f['arrival']} | {f['duration']} | {stops_label(f['stops'])}")
