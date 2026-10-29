"""
quarters.py - calendar quarters the way EDGAR names them: (2026, 3) is "2026q3".
"""

from datetime import date, timedelta


def quarter_of(day):
    return day.year, (day.month - 1) // 3 + 1


def quarter_end(year, quarter):
    month = quarter * 3
    first_of_next = date(year + (month == 12), (month % 12) + 1, 1)
    return first_of_next - timedelta(days=1)


def quarter_name(year, quarter):
    return f"{year}q{quarter}"
