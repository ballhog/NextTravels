#!/usr/bin/env python3
"""
Thailand Flight Price Tracker
Scrapes Google Flights daily for a multi-leg Thailand trip.
Emails a comparison table + trend to joeydanyriera@gmail.com
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
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
HOME_AIRPORTS   = ["DTW", "ORD", "YYZ"]
DEPARTURE_DATES = ["2026-10-24", "2026-11-20", "2026-12-26", "2027-01-24", "2027-02-26"]
WINDOW_LABELS   = {"2026-10-24": "Oct 24", "2026-11-20": "Nov 20", "2026-12-26": "Dec 26", "2027-01-24": "Jan 24", "2027-02-26": "Feb 26"}
EMAIL_TO        = "joeydanyriera@gmail.com"
PRICES_FILE     = Path("workflows/prices.json")

LEG_OFFSETS = [
    ("home", "BKK",  0,  "Home → Bangkok"),
    ("BKK",  "CNX",  5,  "Bangkok → Chiang Mai"),
    ("CNX",  "HKT",  8,  "Chiang Mai → Phuket"),
    ("HKT",  "home", 10, "Phuket → Home (Return)"),
]

# ── Helpers ──────────────────────────────────────────────────────────────────
def trip_dates(departure):
    d = datetime.strptime(departure, "%Y-%m-%d")
    results = []
    for from_code, to_code, offset, label in LEG_OFFSETS:
        date = (d + timedelta(days=offset)).strftime("%Y-%m-%d")
        results.append((from_code, to_code, date, label))
    return results


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
        best = None
        best_price = None
        for f in result.flights:
            price = parse_price(f.price)
            if price is None:
                continue
            if best_price is None or price < best_price:
                best_price = price
 # Debug: print all available fields
                print(f"      fields: {[a for a in dir(f) if not a.startswith('_')]}")
                best = {
                    "price": price,
                    "airline": (getattr(f, "name", None) or getattr(f, "airline", None) or
                                getattr(f, "airlines", None) or getattr(f, "carrier", None) or "–"),
                    "departure": (getattr(f, "departure", None) or getattr(f, "departure_time", None) or
                                  getattr(f, "depart", None) or getattr(f, "departs", None) or "–"),
                    "arrival": (getattr(f, "arrival", None) or getattr(f, "arrival_time", None) or
                                getattr(f, "arrive", None) or getattr(f, "arrives", None) or "–"),
                    "duration": (getattr(f, "duration", None) or getattr(f, "travel_time", None) or
                                 getattr(f, "flight_time", None) or "–"),
                    "stops": getattr(f, "stops", None) or getattr(f, "num_stops", None),
                }
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
    for departure in DEPARTURE_DATES:
        results[departure] = {}
        legs = trip_dates(departure)
        for home in HOME_AIRPORTS:
            print(f"\n🔍 {home} | {WINDOW_LABELS[departure]}")
            leg_data = []
            for from_code, to_code, date, label in legs:
                origin = home if from_code == "home" else from_code
                dest   = home if to_code  == "home" else to_code
                print(f"   {origin}→{dest} ({date})...", end=" ", flush=True)
                flight = search_leg(origin, dest, date)
                if flight:
                    print(f"${flight['price']} | {flight['airline']} | {flight['departure']}→{flight['arrival']} | {flight['duration']} | {stops_label(flight['stops'])}")
                else:
                    print("N/A")
                leg_data.append({"label": label.replace("Home", home), "date": date, "flight": flight})

            valid_prices = [l["flight"]["price"] for l in leg_data if l["flight"]]
            total = sum(valid_prices) if len(valid_prices) == 4 else None
            results[departure][home] = {"legs": leg_data, "total": total}
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
        origin = label.split("→")[0].strip().split(" ")[-1]
        dest = label.split("→")[1].strip().split(" ")[0]
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
    for departure in DEPARTURE_DATES:
        win_label = WINDOW_LABELS[departure]
        sections += f'<tr><td colspan="6" style="padding:24px 14px 8px;font-size:17px;font-weight:700;color:#111;border-top:3px solid #0055cc">📅 {win_label} window</td></tr>'

        for home in HOME_AIRPORTS:
            curr = today_data.get(departure, {}).get(home, {})
            prev = yesterday_data.get(departure, {}).get(home, {})
            curr_total = curr.get("total")
            prev_total = prev.get("total")
            arrow = trend_arrow(curr_total, prev_total)
            total_str = f"<strong style='font-size:16px;color:#111'>${curr_total:,}</strong> total" if curr_total else "<em style='color:#999'>N/A</em>"

            sections += f"""<tr style="background:#eef2ff">
          <td colspan="6" style="padding:10px 14px 6px">
            <span style="font-size:15px;font-weight:700;color:#0033aa">✈ From {home}</span>
            &nbsp;&nbsp;{total_str}&nbsp;&nbsp;
            <span style="font-size:13px">{arrow}</span>
            &nbsp;&nbsp;<span style="font-size:11px;color:#888;font-style:italic">4 flights incl. return</span>
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
    <h1 style="margin:0;font-size:22px">✈️ Thailand Trip — Daily Flight Prices</h1>
    <p style="margin:8px 0 0;opacity:.85;font-size:14px">Scraped {run_date} · Economy · 1 adult · Cheapest option per leg</p>
  </div>
  <div style="padding:16px 32px 8px;border-bottom:1px solid #eee">
    <p style="margin:0;color:#555;font-size:13px;line-height:1.7">
      <strong>Route:</strong> Home → Bangkok (Day 1) → Chiang Mai (Day 6) → Phuket (Day 9) → Home (Day 11)<br>
      <strong>Total</strong> = cheapest available flight on each of the 4 legs, including the <strong>return flight home</strong>.
    </p>
  </div>
  <div style="padding:8px 32px 28px">
    <table style="width:100%;border-collapse:collapse">{sections}</table>
    <p style="margin:20px 0 0;font-size:11px;color:#bbb">Prices from Google Flights · YYZ in CAD · All others USD · Best price at time of scrape</p>
  </div>
  <div style="background:#f8f9fa;padding:14px 32px;text-align:center;font-size:12px;color:#bbb">
    Thailand Flight Tracker · Auto-runs daily at 7am ET · github.com/ballhog/NextTravels
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
    msg["Subject"] = f"✈️ Thailand Flights — {run_date}"
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
    print(f"🦞 Thailand Flight Tracker — {run_date}\n{'─'*50}")
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
    for dep in DEPARTURE_DATES:
        print(f"  {WINDOW_LABELS[dep]}:")
        for home in HOME_AIRPORTS:
            d = today_data.get(dep, {}).get(home, {})
            total = d.get("total")
            print(f"    {home}: {'$'+str(total) if total else 'N/A'}")
            for leg in d.get("legs", []):
                f = leg.get("flight")
                if f:
                    print(f"      {leg['label']}: ${f['price']} | {f['airline']} | {f['departure']}→{f['arrival']} | {f['duration']} | {stops_label(f['stops'])}")
