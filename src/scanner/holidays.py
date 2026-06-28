"""
NSE Market Holiday Checker.
Priority:
  1. Hardcoded known holidays (fastest, no API call)
  2. Fetches from NSE API as fallback for unknown years

is_market_holiday() → True if today is a holiday or weekend.
"""
import requests
from datetime import date, datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

# ── Hardcoded NSE holidays (CM segment) ──────────────────────
# Format: "YYYY-MM-DD"
_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr (Ramzan Eid)
    "2025-04-10",  # Shri Ram Navami
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti / Dussehra
    "2025-10-20",  # Diwali Laxmi Pujan (Muhurat trading only)
    "2025-10-21",  # Diwali Balipratipada
    "2025-11-05",  # Prakash Gurpurb Sri Guru Nanak Dev Ji
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Mahashivratri
    "2026-03-20",  # Holi
    "2026-03-20",  # Holi
    "2026-04-02",  # Shri Ram Navami
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-04-20",  # Id-Ul-Fitr (tentative)
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-09-15",  # Ganesh Chaturthi (tentative)
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-11-09",  # Diwali (tentative)
    "2026-12-25",  # Christmas
}

_nse_cache: dict = {}   # year → set of date strings fetched from NSE


def _fetch_nse_holidays(year: int) -> set:
    """Fetch holiday list from NSE website for a given year. Returns set of YYYY-MM-DD strings."""
    if year in _nse_cache:
        return _nse_cache[year]
    try:
        url  = "https://www.nseindia.com/api/holiday-master?type=trading"
        hdrs = {
            "User-Agent": "Mozilla/5.0",
            "Accept":     "application/json",
            "Referer":    "https://www.nseindia.com/",
        }
        r    = requests.get(url, headers=hdrs, timeout=8)
        data = r.json()
        days = set()
        for item in data.get("CM", []):     # CM = Capital Markets segment
            try:
                d = datetime.strptime(item["tradingDate"], "%d-%b-%Y").strftime("%Y-%m-%d")
                if d.startswith(str(year)):
                    days.add(d)
            except Exception:
                continue
        _nse_cache[year] = days
        return days
    except Exception as e:
        print(f"[holiday] NSE API fetch failed: {e}")
        return set()


def is_market_holiday(check_date: date | None = None) -> bool:
    """
    Returns True if the given date (default: today IST) is:
    - A Saturday or Sunday, OR
    - A known NSE holiday
    """
    d = check_date or datetime.now(IST).date()

    # Weekend
    if d.weekday() >= 5:
        return True

    ds = d.strftime("%Y-%m-%d")

    # Hardcoded list first (fast path)
    if ds in _HOLIDAYS:
        return True

    # Fallback: try NSE API for this year
    nse_days = _fetch_nse_holidays(d.year)
    return ds in nse_days


def get_holiday_name(check_date: date | None = None) -> str:
    """Returns a human-readable reason string."""
    d = check_date or datetime.now(IST).date()
    if d.weekday() == 5:
        return "Saturday"
    if d.weekday() == 6:
        return "Sunday"
    return "NSE Holiday"
