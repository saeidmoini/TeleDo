from __future__ import annotations
from datetime import datetime
import jdatetime


def gregorian_to_jalali(dt: datetime | None) -> str:
    """Return Jalali date string (YYYY-MM-DD) or fallback if missing."""
    if not dt:
        return "N/A"
    try:
        jd = jdatetime.datetime.fromgregorian(datetime=dt)
        return jd.strftime("%Y-%m-%d")
    except Exception:
        return "N/A"


def jalali_to_gregorian(date_str: str) -> datetime | None:
    """Parse a Jalali date string (YYYY-MM-DD) to Gregorian datetime."""
    if not date_str:
        return None
    try:
        jd = jdatetime.datetime.strptime(date_str, "%Y-%m-%d")
        return jd.togregorian()
    except Exception:
        return None
