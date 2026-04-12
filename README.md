# ✈️ Thailand Flight Tracker

Daily Google Flights scraper for a multi-leg Thailand trip. Emails a price
comparison table every morning at 7am ET.

## Trip Structure

| Day | Leg | Notes |
|-----|-----|-------|
| 1   | Home → Bangkok (BKK) | Departure |
| 6   | Bangkok → Chiang Mai (CNX) | Internal flight |
| 9   | Chiang Mai → Phuket (HKT) | Internal flight |
| 11  | Phuket → Home | Return |

## Date Windows Tracked

| Window | Departure | Return |
|--------|-----------|--------|
| Oct 24 | Oct 24, 2026 | Nov 3, 2026 |
| Nov 20 | Nov 20, 2026 | Nov 30, 2026 |
| Dec 26 | Dec 26, 2026 | Jan 5, 2027 |

## Home Airports

- **DTW** — Detroit Metropolitan
- **ORD** — Chicago O'Hare
- **YYZ** — Toronto Pearson *(prices in CAD)*

---

## Setup (5 minutes)

### 1. Fork / clone this repo to your GitHub

### 2. Get a Gmail App Password

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Search for **"App passwords"** → create one named `FlightTracker`
4. Copy the 16-character password

### 3. Add GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|-------------|-------|
| `GMAIL_USER` | your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | the 16-char app password from step 2 |

### 4. Enable Actions

Go to the **Actions** tab in your repo and click **"I understand my workflows, go ahead and enable them"**.

That's it! The workflow runs every day at 7am ET and emails results to `joeydanyriera@gmail.com`.

---

## Manual Run

Trigger a run anytime from **Actions → Daily Flight Price Check → Run workflow**.

Or run locally:

```bash
pip install -r requirements.txt
GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxx python checker.py
```

## Files

| File | Purpose |
|------|---------|
| `checker.py` | Main scraper + email sender |
| `prices.json` | Price history (auto-committed daily) |
| `requirements.txt` | Python dependencies |
| `.github/workflows/daily.yml` | GitHub Actions cron |

---

## Email Format

Each daily email includes:
- **Total estimated trip cost** per window × departure airport
- **Trend vs yesterday** (🟢 dropped / 🔴 rose / ⚪ flat)
- **Per-leg breakdown** for each flight
