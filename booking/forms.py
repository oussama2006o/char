import re
from datetime import date

from django import forms

from .models import Service

PHONE = re.compile(r"\+213[567][0-9]{8}\Z")
WINDOW = re.compile(r"([0-9]{2}):([0-9]{2})\s*[-–]\s*([0-9]{2}):([0-9]{2})\Z")


def phone_number(value):
    phone = re.sub(r"[\s().-]", "", value)
    if phone.startswith("00213"):
        phone = "+" + phone[2:]
    elif phone.startswith("0"):
        phone = "+213" + phone[1:]
    if not PHONE.fullmatch(phone):
        raise ValueError
    return phone


def parse_date(value):
    return date.fromisoformat(value or "")


def parse_windows(value):
    windows = []
    for item in re.split(r"[,،\n]", value.strip()) if value.strip() else []:
        match = WINDOW.fullmatch(item.strip())
        if not match:
            raise ValueError
        opening_h, opening_m, closing_h, closing_m = map(int, match.groups())
        first, last = opening_h * 60 + opening_m, closing_h * 60 + closing_m
        if opening_m > 59 or closing_m > 59 or not 0 <= first < last <= 1440:
            raise ValueError
        windows.append([first, last])
    windows.sort()
    if any(left[1] > right[0] for left, right in zip(windows, windows[1:])):
        raise ValueError
    return windows


def display_windows(windows):
    def clock(minutes):
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return ", ".join(f"{clock(first)}–{clock(last)}" for first, last in windows)


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["price", "duration", "active"]
