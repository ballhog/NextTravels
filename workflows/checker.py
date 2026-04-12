#!/usr/bin/env python3
"""
Bangkok Flight Price Tracker - Full Featured Edition
Group (DTW/LAX/CLE/YYZ): 11-day windows
Maria (ABQ): 8-day windows, connects via LAX
"""

import json, os, smtplib, sys, urllib.request, urllib.parse, statistics
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

# ── Config ────────────────────────────────────────────────────────────────────
HOME_AIRPORTS = ["DTW", "LAX", "CLE", "YYZ"]

# ABQ travels separately with 8-day windows
ABQ_AIRPORTS = ["ABQ"]
CONNECTORS = {"ABQ": {"via": "LAX"}}

TRIP_WINDOWS = [
    {"label": "Dec 19 – Dec 30", "depart": "2026-12-19", "return": "2026-12-30", "peak": True},
    {"label": "Jan 22 – Feb 2",  "depart": "2027-01-22", "return": "2027-02-02", "peak": False},
    {"label": "Jan 29 – Feb 9",  "depart": "2027-01-29", "return": "2027-02-09", "peak": False},
    {"label": "Feb 3 – Feb 14",  "depart": "2027-02-03", "return": "2027-02-14", "peak": False},
]

ABQ_TRIP_WINDOWS = [
    {"label": "Dec 19 – Dec 28 (9d)", "depart": "2026-12-19", "return": "2026-12-28", "peak": True},
    {"label": "Jan 22 – Jan 31 (9d)", "depart": "2027-01-22", "return": "2027-01-31", "peak": False},
    {"label": "Jan 29 – Feb 7 (9d)",  "depart": "2027-01-29", "return": "2027-02-07", "peak": False},
    {"label": "Feb 3 – Feb 12 (9d)",  "depart": "2027-02-03", "return": "2027-02-12", "peak": False},
]

CAD_TO_USD         = 0.73
ALERT_THRESHOLD    = 1000
PRICE_DROP_ALERT   = 75
BOOK_NOW_THRESHOLD = 900
CHECK_PREMIUM_ECO  = True
CHECK_DMK          = True
CHECK_OPEN_JAW     = True

EMAIL_TO    = ["joeydanyriera@gmail.com", "maria.agoytia@gmail.com"]
PRICES_FILE = Path("prices.json")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DAYS_OF_WEEK = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_price(p):
    if isinstance(p, (int, float)) and p > 0: return int(p)
    elif isinstance(p, str):
        cleaned = p.replace("$","").replace(",","").replace("C","").strip()
        try: return int(float(cleaned))
        except: return None
    return None

def to_usd(price, airport):
    if price is None: return None
    return int(price * CAD_TO_USD) if airport == "YYZ" else price

def safe_stops(stops):
    try: return int(stops or 0)
    except: return 1

def stops_label(stops):
    if stops is None: return "–"
    if stops == 0: return "🟢 Nonstop"
    if stops == 1: return "1 stop"
    return f"{stops} stops"

def flight_score(price_usd, duration_str, stops):
    if price_usd is None: return 9999
    hours = 0
    if duration_str and duration_str != "–":
        parts = duration_str.replace("hr","h").replace("min","m").split()
        for p in parts:
            if "h" in p:
                try: hours += int(p.replace("h",""))
                except: pass
            elif "m" in p:
                try: hours += int(p.replace("m","")) / 60
                except: pass
    return int(price_usd + (hours * 15) + (safe_stops(stops) * 80))

def kayak_url(origin, dest, date, cabin="economy"):
    c = {"economy":"economy","premium-economy":"premiumeconomy"}.get(cabin,"economy")
    return f"https://www.kayak.com/flights/{origin}-{dest}/{date}/1adults/{c}"

def gflights_url(origin, dest, date):
    d = datetime.strptime(date, "%Y-%m-%d")
    pretty = d.strftime("%B %d %Y").replace(" 0"," ")
    q = f"flights from {origin} to {dest} on {pretty}"
    return "https://www.google.com/travel/flights?q=" + q.replace(" ","+")

# ── Search ────────────────────────────────────────────────────────────────────
def search_leg(origin, destination, date, cabin="economy"):
    try:
        result = get_flights(
            flight_data=[FlightData(date=date, from_airport=origin, to_airport=destination)],
            trip="one-way", seat=cabin, passengers=Passengers(adults=1),
        )
        candidates = []
        for f in result.flights:
            price = parse_price(f.price)
            if price is None: continue
            candidates.append({
                "price": price,
                "airline": getattr(f,"name",None) or "–",
                "departure": getattr(f,"departure",None) or "–",
                "arrival": getattr(f,"arrival",None) or "–",
                "duration": getattr(f,"duration",None) or "–",
                "stops": getattr(f,"stops",None),
            })
        if not candidates: return None
        candidates.sort(key=lambda x: x["price"])
        has_details = [c for c in candidates if c["departure"]!="–" and c["airline"]!="–"]
        nonstop = [c for c in candidates if c.get("stops")==0]
        best = has_details[0] if has_details else candidates[0]
        best["price"] = candidates[0]["price"]
        best["nonstop_price"] = nonstop[0]["price"] if nonstop else None
        best["nonstop_airline"] = nonstop[0]["airline"] if nonstop else None
        best["kayak_url"] = kayak_url(origin, destination, date, cabin)
        best["gflights_url"] = gflights_url(origin, destination, date)
        return best
    except Exception as e:
        print(f"  ⚠ {origin}→{destination} on {date}: {e}")
        return None

# ── History & analytics ───────────────────────────────────────────────────────
def load_history():
    if PRICES_FILE.exists():
        with open(PRICES_FILE) as f: return json.load(f)
    return {}

def save_history(data):
    PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRICES_FILE, "w") as f: json.dump(data, f, indent=2)

def trend_arrow(current, previous):
    if current is None or previous is None: return "–"
    diff = current - previous
    if diff < -20:  return f"🟢 ▼${abs(diff)}"
    elif diff > 20: return f"🔴 ▲${diff}"
    else:           return f"⚪ ≈ ${diff:+d}"

def sparkline(prices_list):
    bars = "▁▂▃▄▅▆▇"
    if len(prices_list) < 2: return ""
    mn, mx = min(prices_list), max(prices_list)
    if mn == mx: return "▄" * len(prices_list)
    return "".join(bars[int((p-mn)/(mx-mn)*(len(bars)-1))] for p in prices_list)

def get_price_history(history, window_key, home, days=30):
    points = []
    for day_key in sorted(history.keys()):
        if day_key == "latest": continue
        p = history.get(day_key,{}).get(window_key,{}).get(home,{}).get("total_usd")
        if p: points.append((day_key, p))
    return points[-days:]

def predict_price(points):
    if len(points) < 5:
        return None, "Not enough data yet (need 5+ days)", "⏳ Collecting data"
    xs = list(range(len(points)))
    ys = [p for _, p in points]
    n = len(xs)
    mean_x = sum(xs)/n
    mean_y = sum(ys)/n
    slope = sum((x-mean_x)*(y-mean_y) for x,y in zip(xs,ys)) / sum((x-mean_x)**2 for x in xs)
    pred_14 = int(ys[-1] + slope * 14)
    if slope < -2:   rec = f"📉 Dropping ~${abs(int(slope))}/day — consider waiting"
    elif slope > 2:  rec = f"📈 Rising ~${int(slope)}/day — consider booking soon"
    else:            rec = "➡️ Prices stable — no urgency"
    return slope, f"{'📉' if slope<-2 else '📈' if slope>2 else '➡️'} Trend: {int(slope):+d}/day · Est. 14 days: ${pred_14:,}", rec

def best_day_to_buy(history, window_key, home):
    day_prices = {d: [] for d in DAYS_OF_WEEK}
    for day_key in sorted(history.keys()):
        if day_key == "latest": continue
        try:
            dow = datetime.strptime(day_key, "%Y-%m-%d").weekday()
            p = history.get(day_key,{}).get(window_key,{}).get(home,{}).get("total_usd")
            if p: day_prices[DAYS_OF_WEEK[dow]].append(p)
        except: pass
    avgs = {d: int(statistics.mean(v)) for d,v in day_prices.items() if v}
    if not avgs: return None
    best = min(avgs, key=avgs.get)
    return {"avgs": avgs, "best": best}

def all_time_low(history, window_key, home):
    points = get_price_history(history, window_key, home, days=365)
    if not points: return None
    return min(p for _,p in points)

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(url, data, timeout=10)
        print("📱 Telegram alert sent")
    except Exception as e:
        print(f"  ⚠ Telegram error: {e}")

# ── Scrape helpers ────────────────────────────────────────────────────────────
def scrape_airport(home, window, results, key, is_abq=False):
    """Search one airport for one window. Modifies results[key][home] in place."""
    print(f"\n🔍 {home} | {window['label']}")

    print(f"   {home}→BKK ({window['depart']})...", end=" ", flush=True)
    out = search_leg(home, "BKK", window["depart"])
    print(f"${out['price']}" if out else "N/A")

    print(f"   BKK→{home} ({window['return']})...", end=" ", flush=True)
    ret = search_leg("BKK", home, window["return"])
    print(f"${ret['price']}" if ret else "N/A")

    legs = [
        {"label": f"{home} → Bangkok", "date": window["depart"], "flight": out, "cabin": "economy"},
        {"label": f"Bangkok → {home} (Return)", "date": window["return"], "flight": ret, "cabin": "economy"},
    ]

    # Premium economy (skip for ABQ to save time)
    prem_out = prem_ret = None
    if CHECK_PREMIUM_ECO and not is_abq:
        print(f"   [Prem] {home}→BKK...", end=" ", flush=True)
        prem_out = search_leg(home, "BKK", window["depart"], cabin="premium-economy")
        print(f"${prem_out['price']}" if prem_out else "N/A")
        print(f"   [Prem] BKK→{home}...", end=" ", flush=True)
        prem_ret = search_leg("BKK", home, window["return"], cabin="premium-economy")
        print(f"${prem_ret['price']}" if prem_ret else "N/A")

    # DMK (skip for ABQ)
    dmk_out = dmk_ret = None
    if CHECK_DMK and not is_abq:
        print(f"   {home}→DMK...", end=" ", flush=True)
        dmk_out = search_leg(home, "DMK", window["depart"])
        print(f"${dmk_out['price']}" if dmk_out else "N/A")
        print(f"   DMK→{home}...", end=" ", flush=True)
        dmk_ret = search_leg("DMK", home, window["return"])
        print(f"${dmk_ret['price']}" if dmk_ret else "N/A")

    # Open jaw (skip for ABQ)
    oj_ret = None
    if CHECK_OPEN_JAW and not is_abq:
        print(f"   [OpenJaw] HKT→{home}...", end=" ", flush=True)
        oj_ret = search_leg("HKT", home, window["return"])
        print(f"${oj_ret['price']}" if oj_ret else "N/A")

    # Totals
    out_usd = to_usd(out["price"] if out else None, home)
    ret_usd = to_usd(ret["price"] if ret else None, home)
    total_usd = (out_usd + ret_usd) if (out_usd and ret_usd) else None
    total_native = ((out["price"] + ret["price"]) if (out and ret) else None)

    # Connector for ABQ: add ABQ→LAX to LAX total
    connector_cost = None
    if home in CONNECTORS:
        via = CONNECTORS[home]["via"]
        print(f"   [Connector] {home}→{via}...", end=" ", flush=True)
        conn = search_leg(home, via, window["depart"])
        if conn:
            connector_cost = conn["price"]
            via_total = results[key].get(via, {}).get("total_usd")
            if via_total:
                total_usd = via_total + connector_cost
                total_native = total_usd
            print(f"${connector_cost} → all-in via {via}: ${total_usd}")
        else:
            print("N/A")

    prem_out_usd = to_usd(prem_out["price"] if prem_out else None, home)
    prem_ret_usd = to_usd(prem_ret["price"] if prem_ret else None, home)
    prem_total_usd = (prem_out_usd + prem_ret_usd) if (prem_out_usd and prem_ret_usd) else None
    upgrade_cost = (prem_total_usd - total_usd) if (prem_total_usd and total_usd) else None

    dmk_out_usd = to_usd(dmk_out["price"] if dmk_out else None, home)
    dmk_ret_usd = to_usd(dmk_ret["price"] if dmk_ret else None, home)
    dmk_total_usd = (dmk_out_usd + dmk_ret_usd) if (dmk_out_usd and dmk_ret_usd) else None

    oj_out_usd = to_usd(out["price"] if out else None, home)
    oj_ret_usd = to_usd(oj_ret["price"] if oj_ret else None, home)
    oj_total_usd = (oj_out_usd + oj_ret_usd) if (oj_out_usd and oj_ret_usd) else None

    total_stops = (safe_stops(out.get("stops")) + safe_stops(ret.get("stops"))) if (out and ret) else 2

    results[key][home] = {
        "legs": legs,
        "total": total_native,
        "total_usd": total_usd,
        "score": flight_score(total_usd, out["duration"] if out else "–", total_stops),
        "premium_total_usd": prem_total_usd,
        "upgrade_cost": upgrade_cost,
        "dmk_total_usd": dmk_total_usd,
        "openjaw_total_usd": oj_total_usd,
        "connector_cost": connector_cost,
    }

# ── Main scrape ───────────────────────────────────────────────────────────────
def scrape_all():
    results = {}

    # Main group — 11-day windows
    for window in TRIP_WINDOWS:
        key = window["depart"]
        results[key] = {}
        for home in HOME_AIRPORTS:
            scrape_airport(home, window, results, key)

    # ABQ — 8-day windows
    for window in ABQ_TRIP_WINDOWS:
        key = "ABQ_" + window["depart"]
        results[key] = {}
        for home in ABQ_AIRPORTS:
            scrape_airport(home, window, results, key, is_abq=True)

    return results

# ── Alerts ────────────────────────────────────────────────────────────────────
def check_alerts(today_data, yesterday_data, history):
    alerts = []
    book_now = []
    all_windows = TRIP_WINDOWS + ABQ_TRIP_WINDOWS
    all_homes = HOME_AIRPORTS + ABQ_AIRPORTS
    for window in all_windows:
        is_abq = window in ABQ_TRIP_WINDOWS
        key = ("ABQ_" if is_abq else "") + window["depart"]
        for home in (ABQ_AIRPORTS if is_abq else HOME_AIRPORTS):
            curr = today_data.get(key,{}).get(home,{})
            prev = yesterday_data.get(key,{}).get(home,{})
            usd  = curr.get("total_usd")
            prev_usd = prev.get("total_usd")
            atl  = all_time_low(history, key, home)
            if usd and usd < ALERT_THRESHOLD:
                alerts.append(f"🚨 <b>{home}→BKK {window['label']}</b>: ${usd} (under ${ALERT_THRESHOLD}!)")
            if usd and prev_usd and (prev_usd - usd) >= PRICE_DROP_ALERT:
                alerts.append(f"💸 <b>{home}→BKK {window['label']}</b>: dropped ${prev_usd-usd} to ${usd}!")
            if usd and usd <= BOOK_NOW_THRESHOLD:
                book_now.append(f"🚨 BOOK NOW: {home}→BKK {window['label']} = ${usd} USD")
            if usd and atl and usd <= atl:
                alerts.append(f"🏆 <b>{home}→BKK {window['label']}</b>: NEW ALL-TIME LOW at ${usd}!")
    if alerts or book_now:
        send_telegram("✈️ <b>Bangkok Flight Alert</b>\n\n" + "\n".join(alerts + book_now))
        print(f"🚨 {len(alerts)+len(book_now)} alert(s) triggered")
    return alerts, book_now

# ── HTML ──────────────────────────────────────────────────────────────────────
def calendar_html(today_data):
    def price_color(p, mn, mx):
        if p is None: return "#f0f0f0", "#999"
        ratio = (p-mn)/(mx-mn) if mx!=mn else 0.5
        if ratio < 0.33:   return "#c8f7c5","#1a6b2a"
        elif ratio < 0.66: return "#fff3cd","#856404"
        else:              return "#ffd6d6","#a00"

    def make_table(windows, airports, key_prefix=""):
        all_prices = [today_data.get(key_prefix+w["depart"],{}).get(h,{}).get("total_usd") for w in windows for h in airports]
        valid = [p for p in all_prices if p]
        if not valid: return ""
        mn, mx = min(valid), max(valid)
        h = f"<table style='border-collapse:collapse;font-size:12px;width:100%;margin-bottom:16px'><tr style='background:#f0f4ff'><th style='padding:6px 10px;text-align:left'>Window</th>"
        for a in airports: h += f"<th style='padding:6px 10px;text-align:center'>{a}</th>"
        h += "</tr>"
        for w in windows:
            pk = "font-style:italic" if w["peak"] else ""
            h += f"<tr><td style='padding:7px 10px;font-weight:600;color:#333;{pk}'>{w['label']}{'⚠' if w['peak'] else ''}</td>"
            for a in airports:
                usd = today_data.get(key_prefix+w["depart"],{}).get(a,{}).get("total_usd")
                bg,fg = price_color(usd, mn, mx)
                kurl = f"https://www.kayak.com/flights/{a}-BKK/{w['depart']}/1adults/economy"
                h += f"<td style='padding:7px 10px;text-align:center;background:{bg};color:{fg};font-weight:700;border-radius:4px;cursor:pointer' onclick=\"window.open('{kurl}','_blank')\">${usd:,}{'🏆' if usd and usd==min(valid) else ''}" if usd else f"<td style='padding:7px 10px;text-align:center;color:#999'>–"
                h += "</td>"
            h += "</tr>"
        h += "</table>"
        return h

    out = "<div style='margin:20px 0'><div style='font-size:15px;font-weight:700;color:#111;margin-bottom:8px'>📅 Price Calendar</div>"
    out += "<div style='font-size:13px;font-weight:600;color:#333;margin-bottom:6px'>Group (11 days)</div>"
    out += make_table(TRIP_WINDOWS, HOME_AIRPORTS)
    out += "<div style='font-size:13px;font-weight:600;color:#333;margin-bottom:6px'>Maria — ABQ (8 days)</div>"
    out += make_table(ABQ_TRIP_WINDOWS, ABQ_AIRPORTS, key_prefix="ABQ_")
    out += "<div style='font-size:11px;color:#999'>Click cell to book on Kayak · 🟢 cheapest · 🔴 most expensive</div></div>"
    return out

def build_html(today_data, yesterday_data, history, run_date, book_now_alerts, is_sunday=False):

    # Best deal across all
    all_deals = []
    for window in TRIP_WINDOWS:
        for home in HOME_AIRPORTS:
            d = today_data.get(window["depart"],{}).get(home,{})
            usd = d.get("total_usd")
            if usd: all_deals.append((usd, d.get("score",9999), home, window["label"], window["depart"], d))
    for window in ABQ_TRIP_WINDOWS:
        d = today_data.get("ABQ_"+window["depart"],{}).get("ABQ",{})
        usd = d.get("total_usd")
        if usd: all_deals.append((usd, d.get("score",9999), "ABQ", window["label"], "ABQ_"+window["depart"], d))

    all_deals.sort(key=lambda x: x[0])
    best_price = all_deals[0] if all_deals else None
    best_score = sorted(all_deals, key=lambda x: x[1])[0] if all_deals else None

    best_html = ""
    if book_now_alerts:
        for alert in book_now_alerts:
            best_html += f'<div style="background:#fff0f0;border-left:4px solid #c00;padding:14px 20px;margin-bottom:10px;border-radius:0 8px 8px 0"><div style="font-size:16px;font-weight:700;color:#c00">🚨 BOOK NOW</div><div style="font-size:14px;color:#800">{alert}</div></div>'
    if best_price:
        usd,score,home,win_label,key,d = best_price
        native = d.get("total")
        cad = f" (${native:,} CAD)" if home=="YYZ" else ""
        best_html += f'<div style="background:#e8f5e9;border-left:4px solid #22863a;padding:14px 20px;margin-bottom:10px;border-radius:0 8px 8px 0"><div style="font-size:13px;color:#1a6b2a;font-weight:700">💰 CHEAPEST TODAY</div><div style="font-size:22px;font-weight:700;color:#1a6b2a">${usd:,} USD{cad}</div><div style="font-size:14px;color:#2d8a3e">{home} → Bangkok · {win_label}</div></div>'
    if best_score and (best_score[2]!=best_price[2] or best_score[3]!=best_price[3]):
        usd,score,home,win_label,key,d = best_score
        best_html += f'<div style="background:#e8f0fe;border-left:4px solid #0055cc;padding:14px 20px;margin-bottom:10px;border-radius:0 8px 8px 0"><div style="font-size:13px;color:#0033aa;font-weight:700">⭐ BEST VALUE</div><div style="font-size:22px;font-weight:700;color:#0033aa">${usd:,} USD</div><div style="font-size:14px;color:#1a55cc">{home} → Bangkok · {win_label}</div></div>'

    def leg_row(leg, home):
        f = leg.get("flight")
        label = leg.get("label","")
        date  = leg.get("date","")
        cabin = leg.get("cabin","economy")
        parts = label.split("→")
        origin = parts[0].strip().split(" ")[-1]
        dest   = parts[1].strip().split(" ")[0] if len(parts)>1 else "BKK"
        kurl = kayak_url(origin, dest, date, cabin)
        gurl = gflights_url(origin, dest, date)
        is_return = "Return" in label
        ret_badge = ' <span style="background:#e8f4fd;color:#0055cc;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700">RETURN</span>' if is_return else ""
        if not f:
            return f'<tr style="background:#fafafa"><td colspan="6" style="padding:8px 14px 8px 28px;font-size:13px;color:#999;border-bottom:1px solid #f0f0f0">{label}{ret_badge} · {date} — <a href="{gurl}">Google Flights</a> · <a href="{kurl}">Kayak</a></td></tr>'
        has_info = f.get("departure","–")!="–" and f.get("airline","–")!="–"
        price_usd = to_usd(f["price"], home)
        cad_note = f" <span style='font-size:10px;color:#888'>(${f['price']:,} CAD)</span>" if home=="YYZ" else ""
        ns_html = ""
        if f.get("nonstop_price") and f["nonstop_price"] != f["price"]:
            ns_html = f'<br><span style="font-size:11px;color:#22863a">🟢 Nonstop: ${to_usd(f["nonstop_price"],home):,} ({f["nonstop_airline"]})</span>'
        book_links = f'<br><a href="{kurl}" style="font-size:11px;color:#0055cc;font-weight:600">🎫 Book Kayak →</a>&nbsp;&nbsp;<a href="{gurl}" style="font-size:11px;color:#888">Google Flights</a>'
        if not has_info:
            return f'<tr style="background:#fafafa"><td style="padding:9px 14px 9px 28px;font-size:13px;color:#444;border-bottom:1px solid #efefef">{label}{ret_badge}<br><span style="color:#aaa;font-size:11px">{date}</span>{book_links}{ns_html}</td><td style="padding:9px 14px;font-size:14px;font-weight:700;color:#0044bb;border-bottom:1px solid #efefef">${price_usd:,}{cad_note}</td><td colspan="4" style="padding:9px 14px;font-size:12px;color:#888;border-bottom:1px solid #efefef;font-style:italic">Details unavailable</td></tr>'
        stop_color = "#22863a" if f.get("stops")==0 else "#555"
        return f"""<tr style="background:#fafafa">
          <td style="padding:9px 14px 9px 28px;font-size:13px;color:#444;border-bottom:1px solid #efefef">{label}{ret_badge}<br><span style="color:#aaa;font-size:11px">{date}</span>{book_links}{ns_html}</td>
          <td style="padding:9px 14px;font-size:14px;font-weight:700;color:#0044bb;border-bottom:1px solid #efefef">${price_usd:,}{cad_note}</td>
          <td style="padding:9px 14px;font-size:13px;color:#333;border-bottom:1px solid #efefef">{f.get("airline","–")}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef;white-space:nowrap">{f.get("departure","–")} → {f.get("arrival","–")}</td>
          <td style="padding:9px 14px;font-size:13px;color:#555;border-bottom:1px solid #efefef">{f.get("duration","–")}</td>
          <td style="padding:9px 14px;font-size:13px;font-weight:600;color:{stop_color};border-bottom:1px solid #efefef">{stops_label(f.get("stops"))}</td>
        </tr>"""

    def build_sections(windows, airports, key_prefix="", group_label=""):
        sections = ""
        if group_label:
            sections += f'<tr><td colspan="6" style="padding:20px 14px 4px;font-size:18px;font-weight:700;color:#003faa;border-top:4px solid #003faa">{group_label}</td></tr>'
        for window in windows:
            key = key_prefix + window["depart"]
            peak_badge = ' <span style="background:#fff3cd;color:#856404;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700">⚠ PEAK</span>' if window["peak"] else ""
            sections += f'<tr><td colspan="6" style="padding:20px 14px 8px;font-size:16px;font-weight:700;color:#111;border-top:2px solid #dde4f8">📅 {window["label"]}{peak_badge}</td></tr>'
            for home in airports:
                curr = today_data.get(key,{}).get(home,{})
                prev = yesterday_data.get(key,{}).get(home,{})
                usd  = curr.get("total_usd")
                prev_usd = prev.get("total_usd")
                native = curr.get("total")
                score  = curr.get("score")
                prem   = curr.get("premium_total_usd")
                upgrade = curr.get("upgrade_cost")
                dmk    = curr.get("dmk_total_usd")
                oj     = curr.get("openjaw_total_usd")
                conn   = curr.get("connector_cost")
                atl    = all_time_low(history, key, home)
                pts    = get_price_history(history, key, home)
                _, pred_text, pred_rec = predict_price(pts)
                bday   = best_day_to_buy(history, key, home)
                bday_html = f"<br><span style='font-size:11px;color:#666'>📆 Cheapest day: <strong>{bday['best']}</strong> (avg ${bday['avgs'][bday['best']]:,})</span>" if bday else ""
                spark  = sparkline([p for _,p in pts[-7:]])
                spark_html = f"<span style='font-family:monospace;font-size:14px;color:#aaa'>{spark}</span>" if spark else ""
                atl_html = ' <span style="background:#fff0b3;color:#8a6200;font-size:10px;padding:2px 6px;border-radius:8px;font-weight:700">🏆 ATL</span>' if (atl and usd and usd<=atl) else (f" <span style='font-size:11px;color:#888'>ATL:${atl:,}</span>" if atl else "")
                cad_note = f" <span style='font-size:11px;color:#888'>(${native:,} CAD)</span>" if home=="YYZ" and native else ""
                total_str = f"<strong style='font-size:16px;color:#111'>${usd:,} USD</strong>{cad_note}{atl_html}" if usd else "<em style='color:#999'>N/A</em>"
                score_str = f"<span style='font-size:11px;color:#888'>score:{score}</span>" if score and score<9999 else ""
                alts = []
                if prem and upgrade: alts.append(f"✈ Premium: ${prem:,} (+${upgrade:,})")
                if dmk and usd and dmk<usd: alts.append(f"🛬 DMK: ${dmk:,} (saves ${usd-dmk:,})")
                if oj and usd:
                    diff = oj-usd
                    alts.append(f"↗ Open jaw: ${oj:,} ({'saves $'+str(abs(diff)) if diff<0 else '+$'+str(diff)})")
                if conn: alts.append(f"🔗 ABQ→LAX connector: ${conn:,} included")
                alts_html = "<br>" + " &nbsp;·&nbsp; ".join(f"<span style='font-size:11px;color:#555'>{a}</span>" for a in alts) if alts else ""
                sections += f"""<tr style="background:#eef2ff">
          <td colspan="6" style="padding:10px 14px 6px">
            <div>{total_str} &nbsp;<span style="font-size:13px">{trend_arrow(usd,prev_usd)}</span> &nbsp;{score_str} &nbsp;{spark_html} &nbsp;<span style="font-size:15px;font-weight:700;color:#0033aa">✈ {home}</span></div>
            <div style="font-size:11px;color:#666;margin-top:2px">{pred_text}</div>
            <div style="font-size:11px;color:#444;margin-top:2px">{pred_rec}{bday_html}</div>
            {alts_html}
          </td></tr>
        <tr style="background:#dde4f8">
          <th style="padding:5px 14px 5px 28px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Leg</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Price (USD)</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Airline</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Times</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Duration</th>
          <th style="padding:5px 14px;font-size:11px;color:#555;text-align:left;font-weight:600;text-transform:uppercase">Stops</th>
        </tr>"""
                for leg in curr.get("legs",[]): sections += leg_row(leg, home)
        return sections

    sections  = build_sections(TRIP_WINDOWS, HOME_AIRPORTS, group_label="✈ Group (11-day trips)")
    sections += build_sections(ABQ_TRIP_WINDOWS, ABQ_AIRPORTS, key_prefix="ABQ_", group_label="✈ Maria — ABQ (8-day trips)")

    weekly_html = ""
    if is_sunday:
        weekly_html = "<h2 style='font-size:16px;color:#333;margin:24px 0 8px'>📊 7-Day Price History</h2><table style='width:100%;border-collapse:collapse;font-size:12px'><tr style='background:#f0f4ff'><th style='padding:6px 10px;text-align:left'>Route</th>"
        days = [k for k in sorted(history.keys()) if k!="latest"][-8:]
        for d in days: weekly_html += f"<th style='padding:6px 10px;text-align:right'>{d[5:]}</th>"
        weekly_html += "</tr>"
        for window in TRIP_WINDOWS:
            for home in HOME_AIRPORTS:
                weekly_html += f"<tr><td style='padding:5px 10px;color:#444'>{home}→BKK {window['label']}</td>"
                for d in days:
                    p = history.get(d,{}).get(window["depart"],{}).get(home,{}).get("total_usd")
                    weekly_html += f"<td style='padding:5px 10px;text-align:right'>${p:,}</td>" if p else "<td style='padding:5px 10px;text-align:right;color:#ccc'>–</td>"
                weekly_html += "</tr>"
        for window in ABQ_TRIP_WINDOWS:
            weekly_html += f"<tr><td style='padding:5px 10px;color:#444'>ABQ→BKK {window['label']}</td>"
            for d in days:
                p = history.get(d,{}).get("ABQ_"+window["depart"],{}).get("ABQ",{}).get("total_usd")
                weekly_html += f"<td style='padding:5px 10px;text-align:right'>${p:,}</td>" if p else "<td style='padding:5px 10px;text-align:right;color:#ccc'>–</td>"
            weekly_html += "</tr>"
        weekly_html += "</table>"

    title = "📊 Weekly Bangkok Flight Summary" if is_sunday else "✈️ Bangkok Trip — Daily Flight Prices"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;margin:0;padding:20px">
<div style="max-width:840px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.1)">
  <div style="background:linear-gradient(135deg,#003faa,#0088ee);padding:28px 32px;color:#fff">
    <h1 style="margin:0;font-size:22px">{title}</h1>
    <p style="margin:8px 0 0;opacity:.85;font-size:14px">Scraped {run_date} · Economy + Premium · All prices USD · Group: 11 days · Maria (ABQ): 8 days</p>
  </div>
  <div style="padding:20px 32px 8px">{best_html}</div>
  <div style="padding:0 32px 8px">{calendar_html(today_data)}</div>
  <div style="padding:4px 32px 8px;border-bottom:1px solid #eee">
    <p style="margin:0;color:#555;font-size:12px;line-height:1.7">
      <strong>Score</strong> = price+(hrs×$15)+(stops×$80) · <strong>Sparkline</strong> = 7-day trend ·
      <strong>ATL</strong> = all-time low · <strong>ABQ</strong> price includes ABQ→LAX connector
    </p>
  </div>
  <div style="padding:8px 32px 28px">
    <table style="width:100%;border-collapse:collapse">{sections}</table>
    {weekly_html}
    <p style="margin:20px 0 0;font-size:11px;color:#bbb">YYZ in USD (CAD×{CAD_TO_USD}) · Kayak = direct booking · Alerts: drop>${PRICE_DROP_ALERT} or under ${ALERT_THRESHOLD}</p>
  </div>
  <div style="background:#f8f9fa;padding:14px 32px;text-align:center;font-size:12px;color:#bbb">
    Bangkok Flight Tracker · 7am ET daily · <a href="https://ballhog.github.io/NextTravels" style="color:#aaa">Live Dashboard →</a>
  </div>
</div></body></html>"""

def send_email(html, run_date, is_sunday=False):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        print("⚠ Email secrets not set — skipping.")
        return
    subj = f"📊 Bangkok Weekly — {run_date}" if is_sunday else f"✈️ Bangkok Flights — {run_date}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subj
    msg["From"] = gmail_user
    msg["To"] = ", ".join(EMAIL_TO) if isinstance(EMAIL_TO, list) else EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gmail_user, gmail_pass)
        s.sendmail(gmail_user, EMAIL_TO if isinstance(EMAIL_TO, list) else [EMAIL_TO], msg.as_string())
    print(f"📧 Email sent to {', '.join(EMAIL_TO) if isinstance(EMAIL_TO, list) else EMAIL_TO}")

if __name__ == "__main__":
    run_date  = datetime.now().strftime("%B %d, %Y")
    today_key = datetime.now().strftime("%Y-%m-%d")
    is_sunday = datetime.now().weekday() == 6

    print(f"🦞 Bangkok Flight Tracker — {run_date}\n{'─'*50}")
    if is_sunday: print("📊 Sunday — weekly summary included")

    history = load_history()
    yesterday_data = history.get("latest", {})

    print("\n📡 Scraping Google Flights...")
    today_data = scrape_all()

    history["latest"] = today_data
    history[today_key] = today_data
    save_history(history)
    print(f"\n💾 Saved to {PRICES_FILE}")

    alerts, book_now = check_alerts(today_data, yesterday_data, history)
    html = build_html(today_data, yesterday_data, history, run_date, book_now, is_sunday)
    send_email(html, run_date, is_sunday)

    print(f"\n{'─'*50}\n📊 Summary:\n")
    print("  GROUP (11-day):")
    for window in TRIP_WINDOWS:
        print(f"  {window['label']}:")
        for home in HOME_AIRPORTS:
            d = today_data.get(window["depart"],{}).get(home,{})
            print(f"    {home}: {'$'+str(d.get('total_usd')) if d.get('total_usd') else 'N/A'}")
    print("\n  MARIA — ABQ (8-day):")
    for window in ABQ_TRIP_WINDOWS:
        d = today_data.get("ABQ_"+window["depart"],{}).get("ABQ",{})
        print(f"  {window['label']}: {'$'+str(d.get('total_usd')) if d.get('total_usd') else 'N/A'}")
