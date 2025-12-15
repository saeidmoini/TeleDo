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


def is_future_date(dt: datetime | None) -> bool:
    """Return True if the given datetime is strictly in the future (by date)."""
    if not dt:
        return False
    return dt.date() > datetime.now().date()


def parse_flexible_date(date_str: str) -> datetime | None:
    """
    Try to parse a date string in several common formats.
    Supports Jalali (YYYY-MM-DD) and a handful of Gregorian layouts.
    Returns a datetime on success, otherwise None.
    """
    if not date_str:
        return None

    cleaned = date_str.strip()

    # Try Jalali first
    jalali_dt = jalali_to_gregorian(cleaned)
    if jalali_dt:
        return jalali_dt

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except Exception:
            continue

    return None
