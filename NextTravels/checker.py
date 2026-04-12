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
DEPARTURE_DATES = ["2026-10-24", "2026-11-20", "2026-12-26"]
WINDOW_LABELS   = {"2026-10-24": "Oct 24", "2026-11-20": "Nov 20", "2026-12-26": "Dec 26"}
EMAIL_TO        = "joeydanyriera@gmail.com"
PRICES_FILE     = Path("prices.json")

# Trip structure (offsets from departure day)
# Day 1  → Home→BKK  (offset 0)
# Day 6  → BKK→CNX   (offset +5)
# Day 9  → CNX→HKT   (offset +8)
# Day 11 → HKT→Home  (offset +10)
LEG_OFFSETS = [
    ("home", "BKK",  0,  "Home → Bangkok"),
    ("BKK",  "CNX",  5,  "Bangkok → Chiang Mai"),
    ("CNX",  "HKT",  8,  "Chiang Mai → Phuket"),
    ("HKT",  "home", 10, "Phuket → Home"),
]

# ── Helpers ──────────────────────────────────────────────────────────────────
def trip_dates(departure: str) -> list[tuple]:
    """Return list of (from, to, date, label) for each leg given departure date."""
    d = datetime.strptime(departure, "%Y-%m-%d")
    results = []
    for from_code, to_code, offset, label in LEG_OFFSETS:
        date = (d + timedelta(days=offset)).strftime("%Y-%m-%d")
        results.append((from_code, to_code, date, label))
    return results


def search_leg(origin: str, destination: str, date: str) -> int | None:
    """Search one flight leg. Returns lowest price in native currency or None."""
    try:
        result = get_flights(
            flight_data=[FlightData(date=date, from_airport=origin, to_airport=destination)],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
        )
        prices = []
        for f in result.flights:
            p = f.price
            if isinstance(p, (int, float)) and p > 0:
                prices.append(int(p))
            elif isinstance(p, str):
                # strip currency symbols / commas
                cleaned = p.replace("$", "").replace(",", "").replace("C", "").strip()
                try:
                    prices.append(int(float(cleaned)))
                except ValueError:
                    pass
        return min(prices) if prices else None
    except Exception as e:
        print(f"  ⚠ {origin}→{destination} on {date}: {e}")
        return None


def load_history() -> dict:
    if PRICES_FILE.exists():
        with open(PRICES_FILE) as f:
            return json.load(f)
    return {}


def save_history(data: dict):
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def trend_arrow(current: int | None, previous: int | None) -> str:
    if current is None or previous is None:
        return "–"
    diff = current - previous
    if diff < -20:
        return f"🟢 ▼${abs(diff)}"
    elif diff > 20:
        return f"🔴 ▲${diff}"
    else:
        return f"⚪ ≈ ${diff:+d}"


# ── Main scrape ───────────────────────────────────────────────────────────────
def scrape_all() -> dict:
    """
    Returns nested dict:
      results[departure][home_airport] = {
        "legs": [(label, price), ...],
        "total": int | None
      }
    """
    results = {}
    for departure in DEPARTURE_DATES:
        results[departure] = {}
        legs = trip_dates(departure)
        for home in HOME_AIRPORTS:
            print(f"\n🔍 {home} | {WINDOW_LABELS[departure]}")
            leg_prices = []
            for from_code, to_code, date, label in legs:
                origin = home if from_code == "home" else from_code
                dest   = home if to_code  == "home" else to_code
                print(f"   {origin}→{dest} ({date})...", end=" ", flush=True)
                price = search_leg(origin, dest, date)
                print(f"${price}" if price else "N/A")
                leg_prices.append((label.replace("Home", home), price))

            valid = [p for _, p in leg_prices if p is not None]
            total = sum(valid) if len(valid) == 4 else None
            results[departure][home] = {"legs": leg_prices, "total": total}
    return results


# ── HTML email ────────────────────────────────────────────────────────────────
def build_html(today_data: dict, yesterday_data: dict, run_date: str) -> str:
    rows = ""
    for departure in DEPARTURE_DATES:
        label = WINDOW_LABELS[departure]
        d = datetime.strptime(departure, "%Y-%m-%d")

        for home in HOME_AIRPORTS:
            curr = today_data.get(departure, {}).get(home, {})
            prev = yesterday_data.get(departure, {}).get(home, {})

            curr_total = curr.get("total")
            prev_total = prev.get("total")
            arrow = trend_arrow(curr_total, prev_total)

            total_str = f"<strong>${curr_total:,}</strong>" if curr_total else "<em>N/A</em>"

            # Build leg breakdown tooltip
            legs_html = ""
            for leg_label, price in curr.get("legs", []):
                p_str = f"${price:,}" if price else "N/A"
                legs_html += f"<span style='display:block;font-size:12px;color:#666'>{leg_label}: {p_str}</span>"

            rows += f"""
            <tr>
              <td style="padding:10px 14px;border-bottom:1px solid #eee;font-weight:600">{label}</td>
              <td style="padding:10px 14px;border-bottom:1px solid #eee">{home}</td>
              <td style="padding:10px 14px;border-bottom:1px solid #eee">{total_str}<br>{legs_html}</td>
              <td style="padding:10px 14px;border-bottom:1px solid #eee">{arrow}</td>
            </tr>"""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;margin:0;padding:20px">
  <div style="max-width:680px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">

    <div style="background:linear-gradient(135deg,#0066cc,#00aaff);padding:28px 32px;color:#fff">
      <h1 style="margin:0;font-size:22px">✈️ Thailand Trip — Daily Flight Prices</h1>
      <p style="margin:8px 0 0;opacity:.85;font-size:14px">Scraped {run_date} · Economy · 1 adult · 11-night windows</p>
    </div>

    <div style="padding:24px 32px">
      <p style="margin:0 0 16px;color:#444;font-size:14px">
        <strong>Itinerary:</strong> Home → Bangkok (Day 1) → Chiang Mai (Day 6) → Phuket (Day 9) → Home (Day 11)
      </p>

      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#f8f9fa">
            <th style="padding:10px 14px;text-align:left;color:#555;font-weight:600;border-bottom:2px solid #e0e0e0">Window</th>
            <th style="padding:10px 14px;text-align:left;color:#555;font-weight:600;border-bottom:2px solid #e0e0e0">Departure</th>
            <th style="padding:10px 14px;text-align:left;color:#555;font-weight:600;border-bottom:2px solid #e0e0e0">Total Est. Cost</th>
            <th style="padding:10px 14px;text-align:left;color:#555;font-weight:600;border-bottom:2px solid #e0e0e0">vs Yesterday</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <p style="margin:20px 0 0;font-size:12px;color:#999">
        Prices sourced from Google Flights via fast-flights · YYZ prices in CAD · All others USD<br>
        Total = sum of cheapest available flight on each leg · N/A = no results found
      </p>
    </div>

    <div style="background:#f8f9fa;padding:16px 32px;text-align:center;font-size:12px;color:#aaa">
      Thailand Flight Tracker · Runs daily at 7am ET via GitHub Actions
    </div>
  </div>
</body>
</html>"""


def send_email(html: str, run_date: str):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_pass:
        print("⚠ GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping email.")
        print("  Set these as GitHub Actions secrets (see README).")
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

    # Update history
    history["latest"] = today_data
    history[today_key] = today_data
    save_history(history)
    print(f"\n💾 Prices saved to {PRICES_FILE}")

    # Build & send email
    html = build_html(today_data, yesterday_data, run_date)
    send_email(html, run_date)

    # Print summary to console
    print(f"\n{'─'*50}\n📊 Summary:\n")
    for dep in DEPARTURE_DATES:
        print(f"  {WINDOW_LABELS[dep]}:")
        for home in HOME_AIRPORTS:
            total = today_data.get(dep, {}).get(home, {}).get("total")
            print(f"    {home}: {'$'+str(total) if total else 'N/A'}")
