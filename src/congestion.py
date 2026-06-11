"""London Congestion Charge Zone detection and scheduling helpers.

Official source: https://tfl.gov.uk/modes/driving/congestion-charge/congestion-charge-zone
Last verified: 2026-06-10.

Charging hours (no charge outside these):
  - Mon-Fri: 07:00–18:00 (London local time)
  - Sat-Sun and bank holidays: 12:00–18:00
  - No charge between 25 Dec and 1 Jan (inclusive)

Daily charge (one payment per day, no matter how many zone entries):
  - £18 if paid same day / Auto Pay
  - £21 if paid within 3 days
  - £13.50 for electric cars on Auto Pay
  - £9 for electric vans / HGVs on Auto Pay

This module assumes a regular (non-electric) vehicle, default £18.
Override via env var CCZ_PRICE_GBP_DELIVERY_ETA.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from dataclasses import dataclass


# ---------- ZONE BOUNDARY ----------
# UK outcodes (first part of postcode like "W1S") fully inside the CCZ.
# Built from the TfL official boundary map: north of the Thames between
# Pentonville Rd / Marylebone Rd / Edgware Road in the north, Tower Bridge
# / Aldgate in the east, Park Lane / Vauxhall Bridge Rd in the west, plus
# Southbank between Waterloo and London Bridge.
CCZ_OUTCODES: frozenset[str] = frozenset({
    # City of London
    "EC1A", "EC1M", "EC1N", "EC1R", "EC1V", "EC1Y",
    "EC2A", "EC2M", "EC2N", "EC2R", "EC2V", "EC2Y",
    "EC3A", "EC3M", "EC3N", "EC3R", "EC3V",
    "EC4A", "EC4M", "EC4N", "EC4R", "EC4V", "EC4Y",
    # Holborn / Covent Garden / Bloomsbury
    "WC1A", "WC1B", "WC1E", "WC1H", "WC1N", "WC1R", "WC1V", "WC1X",
    "WC2A", "WC2B", "WC2E", "WC2H", "WC2N", "WC2R",
    # West End / Mayfair / Soho / Marylebone
    "W1A", "W1B", "W1C", "W1D", "W1F", "W1G", "W1H", "W1J", "W1K",
    "W1S", "W1T", "W1U", "W1W",
    # Westminster / Victoria / Pimlico
    "SW1A", "SW1E", "SW1H", "SW1P", "SW1V", "SW1W", "SW1X", "SW1Y",
})

# Some outcodes are partially in CCZ — use postcode SECTORS (outcode + first
# digit of incode like "SE1 6") to be precise. Sectors NOT listed here are
# treated as outside the zone.
CCZ_SECTORS: frozenset[str] = frozenset({
    # SE1 — Waterloo, Southbank, London Bridge are inside; Bermondsey (SE1 5)
    # and Borough's southern strip (SE1 2-4) are outside.
    "SE1 0", "SE1 1", "SE1 6", "SE1 7", "SE1 8", "SE1 9",
})


# ---------- TIMING ----------
WEEKDAY_CHARGE_START = time(7, 0)
WEEKDAY_CHARGE_END = time(18, 0)
WEEKEND_CHARGE_START = time(12, 0)
WEEKEND_CHARGE_END = time(18, 0)

# Safety buffer: if a stop's ETA is within `SAFETY_MIN_AFTER_18` minutes of 18:00,
# we still flag it as charging (because real-world traffic may push it earlier).
SAFETY_MIN_AFTER_18 = 5


def is_in_ccz(postcode: str) -> bool:
    """Return True if a UK postcode falls inside the Congestion Charge Zone."""
    if not postcode:
        return False
    pc = re.sub(r"\s+", " ", postcode.strip().upper())
    parts = pc.split(" ")
    if len(parts) != 2 or len(parts[1]) < 1:
        return False
    outcode, incode = parts[0], parts[1]
    if outcode in CCZ_OUTCODES:
        return True
    sector = f"{outcode} {incode[0]}"
    if sector in CCZ_SECTORS:
        return True
    return False


def is_christmas_break(when: datetime) -> bool:
    """No charge between 25 Dec and 1 Jan inclusive (TfL rule)."""
    if when.month == 12 and when.day >= 25:
        return True
    if when.month == 1 and when.day == 1:
        return True
    return False


def is_charging_at(when: datetime) -> bool:
    """True if Congestion Charge applies at the given London-local datetime."""
    if is_christmas_break(when):
        return False
    weekday = when.weekday()  # 0=Mon ... 6=Sun
    t = when.time()
    if weekday < 5:  # Mon-Fri
        if WEEKDAY_CHARGE_START <= t < WEEKDAY_CHARGE_END:
            return True
    else:  # Sat-Sun (bank holidays not detected — conservatively treat as weekend)
        if WEEKEND_CHARGE_START <= t < WEEKEND_CHARGE_END:
            return True
    return False


def is_charging_at_with_buffer(when: datetime) -> bool:
    """Same as is_charging_at but with a 5-min buffer before 18:00 — so a stop
    at 17:58 still counts as 'in charging hours' even if real arrival might
    slip to 18:01.
    """
    if is_christmas_break(when):
        return False
    weekday = when.weekday()
    t = when.time()
    end_with_buffer = time(
        WEEKDAY_CHARGE_END.hour,
        WEEKDAY_CHARGE_END.minute - SAFETY_MIN_AFTER_18 if WEEKDAY_CHARGE_END.minute >= SAFETY_MIN_AFTER_18
        else 0,
    )
    if weekday < 5:
        if WEEKDAY_CHARGE_START <= t < end_with_buffer:
            return True
    else:
        if WEEKEND_CHARGE_START <= t < end_with_buffer:
            return True
    return False


def hours_label(when: datetime) -> str:
    """'07:00–18:00 будни' / '12:00–18:00 вых' depending on day."""
    if when.weekday() < 5:
        return "07:00–18:00 будни"
    return "12:00–18:00 вых"


# ---------- SCHEDULING SUMMARY ----------
@dataclass
class StopSchedule:
    """One scheduled stop, with all info needed to decide CCZ status."""
    code: str          # postcode like "W1S 1JP"
    arrival: datetime  # London-local datetime when courier reaches this stop
    priority: bool = False


@dataclass
class CCZSummary:
    """Aggregated CCZ info for the whole route."""
    charge_gbp: int               # 0 or CCZ_PRICE_GBP
    in_zone_stops: list[str]      # postcodes that are inside the zone
    in_zone_during_charging: list[tuple[str, datetime]]  # (code, arrival) of stops that incur charge
    can_avoid_by_delay: bool      # True if shifting CCZ stops past 18:00 would zero the charge
    message_lines: list[str]      # ready-to-render lines for the Telegram message

    def text(self) -> str:
        return "\n".join(self.message_lines)


def summarize(
    schedule: list[StopSchedule],
    price_gbp: int = 18,
) -> CCZSummary:
    """Decide whether the route triggers the Congestion Charge and build a
    human-readable summary for the Telegram message."""
    in_zone = [s for s in schedule if is_in_ccz(s.code)]
    charging = [s for s in in_zone if is_charging_at_with_buffer(s.arrival)]

    in_zone_stops = [s.code for s in in_zone]
    in_zone_during_charging = [(s.code, s.arrival) for s in charging]

    charge_gbp = price_gbp if charging else 0

    # Could we avoid by delaying — i.e., if all the charging-time CCZ stops are
    # NOT priority AND their arrival is within an hour of 18:00, a delay would help.
    can_avoid = False
    if charging and not any(s.priority for s in charging):
        latest_charging = max(s.arrival for s in charging)
        end_today = latest_charging.replace(hour=18, minute=0, second=0, microsecond=0)
        if (end_today - latest_charging).total_seconds() / 60 <= 60:
            can_avoid = True

    lines = []
    if charge_gbp > 0:
        codes = ", ".join(c for c, _ in in_zone_during_charging)
        first_arrival = min(t for _, t in in_zone_during_charging)
        lines.append(
            f"💷 Congestion Charge: £{charge_gbp} "
            f"(въезд в зону в {first_arrival.strftime('%H:%M')}, стопы: {codes})"
        )
        if can_avoid:
            mins_to_18 = int(
                (latest_charging.replace(hour=18, minute=0, second=0, microsecond=0)
                 - latest_charging).total_seconds() / 60
            )
            lines.append(
                f"   💡 Подожди {mins_to_18} мин на последней не-CCZ точке — "
                f"приедешь в зону после 18:00 и сэкономишь £{price_gbp}."
            )
    elif in_zone:  # CCZ stops exist but outside charging hours
        codes = ", ".join(s.code for s in in_zone)
        lines.append(
            f"💷 Congestion Charge: £0 — {len(in_zone)} стоп(а) в CCZ ({codes}), "
            "но вне часов тарифа ✅"
        )
    else:
        lines.append("💷 Congestion Charge: £0 — маршрут не заходит в CCZ ✅")

    return CCZSummary(
        charge_gbp=charge_gbp,
        in_zone_stops=in_zone_stops,
        in_zone_during_charging=in_zone_during_charging,
        can_avoid_by_delay=can_avoid,
        message_lines=lines,
    )


def try_delay_ccz_to_after_18(
    ordered_stops: list,
    drive_seconds: list[int],
    parking_min: int,
    start: datetime,
    return_drive_sec: int,
) -> tuple[list, list[int], int]:
    """Re-order so non-CCZ stops go first, CCZ stops last (preserving priority).

    The aim: push CCZ stops past 18:00 to save £18, but ONLY if it doesn't
    break priority and the route is short enough to actually reach 18:00 with
    delays.

    Returns the (possibly reordered) (ordered_stops, drive_seconds, return_sec).
    For now: simple — split into priority / non-CCZ / CCZ buckets and rebuild.

    NOTE: For V1 we trust the matrix-based greedy to do most of the work and
    only swap order when the dispatch time + total drive crosses 18:00.
    """
    # Placeholder for future smarter reorder logic — returns input unchanged.
    return ordered_stops, drive_seconds, return_drive_sec
