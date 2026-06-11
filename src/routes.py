"""Google Routes API integration + greedy nearest-neighbor scheduling.

Strategy:
  1. Single call to computeRouteMatrix → NxN drive-time matrix
  2. Priority stops first in declared order
  3. Then greedy nearest-neighbor for the rest (closest stop next)
  4. Add PARKING_MIN minutes per stop (parking + walk-to-door)
  5. Return formatted route message in Europe/London timezone
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .classifier import Stop
from .congestion import StopSchedule, summarize as ccz_summarize


ROUTES_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,status,condition"
LONDON_TZ = ZoneInfo("Europe/London")
HTTP_TIMEOUT = 30.0


@dataclass
class ScheduledStop:
    stop: Stop
    drive_sec: int


async def _compute_matrix(api_key: str, addresses: list[str]) -> list[list[float]] | str:
    """Return NxN drive-time matrix in seconds (math.inf where no route), or error string."""
    waypoints = [{"waypoint": {"address": a}} for a in addresses]
    body = {
        "origins": waypoints,
        "destinations": waypoints,
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": (datetime.now(timezone.utc) + timedelta(minutes=1))
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "languageCode": "en-GB",
        "regionCode": "GB",
        "units": "METRIC",
    }
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.post(ROUTES_MATRIX_URL, json=body, headers=headers)
    except httpx.HTTPError as e:
        return f"Network error: {e}"

    if r.status_code >= 400:
        return f"Google Routes Matrix HTTP {r.status_code} — {r.text[:400]}"

    try:
        elements = r.json()
    except Exception as e:
        return f"Unparseable response: {e}"

    if not isinstance(elements, list) or not elements:
        return f"Empty / unexpected response: {str(elements)[:300]}"

    n = len(addresses)
    matrix: list[list[float]] = [[math.inf] * n for _ in range(n)]
    for e in elements:
        if e.get("condition") != "ROUTE_EXISTS":
            continue
        status_code = (e.get("status") or {}).get("code")
        if status_code:
            continue
        d = e.get("duration")
        sec: int | None = None
        if isinstance(d, str):
            try:
                sec = int(d.rstrip("s"))
            except ValueError:
                pass
        elif isinstance(d, dict) and "seconds" in d:
            try:
                sec = int(d["seconds"])
            except (TypeError, ValueError):
                pass
        if sec is None:
            continue
        oi, di = e.get("originIndex"), e.get("destinationIndex")
        if oi is None or di is None:
            continue
        matrix[oi][di] = max(60, sec)  # min 1 minute per leg
    return matrix


def _build_schedule(
    stops: list[Stop], matrix: list[list[float]]
) -> tuple[list[ScheduledStop], float] | str:
    """Greedy schedule: priority in declared order, then nearest-neighbor.

    Returns (ordered, return_sec) or error string.
    Index 0 in `matrix` is the SHOP; stops are 1..N.
    """
    priority_idx = [i + 1 for i, s in enumerate(stops) if s.priority]
    regular_idx = [i + 1 for i, s in enumerate(stops) if not s.priority]

    ordered: list[ScheduledStop] = []
    cur = 0  # SHOP

    for pidx in priority_idx:
        sec = matrix[cur][pidx]
        if not math.isfinite(sec):
            return f"Не удалось проложить маршрут до {stops[pidx - 1].code}. Проверь корректность посткода."
        ordered.append(ScheduledStop(stop=stops[pidx - 1], drive_sec=int(sec)))
        cur = pidx

    remaining = set(regular_idx)
    while remaining:
        best, best_sec = -1, math.inf
        for idx in remaining:
            if matrix[cur][idx] < best_sec:
                best, best_sec = idx, matrix[cur][idx]
        if best == -1 or not math.isfinite(best_sec):
            return "Не удалось проложить маршрут к одному из оставшихся посткодов. Проверь их корректность."
        ordered.append(ScheduledStop(stop=stops[best - 1], drive_sec=int(best_sec)))
        remaining.discard(best)
        cur = best

    return_sec = matrix[cur][0]
    if not math.isfinite(return_sec):
        return "Не удалось рассчитать обратный путь до магазина."

    return ordered, float(return_sec)


def _format_time(d: datetime) -> str:
    return d.strftime("%H:%M")


async def build_route_text(
    api_key: str,
    shop_address: str,
    stops: list[Stop],
    parking_min: int = 7,
) -> str:
    """Returns ready-to-send Telegram message text with the route + ETAs."""
    if not stops:
        return "⚠️ Нет посткодов для расчёта."
    if not api_key or len(api_key) < 20:
        return (
            "⚠️ GOOGLE_API_KEY_DELIVERY_ETA не задан.\n"
            "Открой Railway → Variables → добавь переменную GOOGLE_API_KEY_DELIVERY_ETA "
            "с ключом Google Routes API. Бот рестартует и будет готов."
        )

    addresses = [shop_address] + [f"{s.code}, London, UK" for s in stops]
    matrix_or_err = await _compute_matrix(api_key, addresses)
    if isinstance(matrix_or_err, str):
        return f"⚠️ {matrix_or_err}"

    schedule_or_err = _build_schedule(stops, matrix_or_err)
    if isinstance(schedule_or_err, str):
        return f"⚠️ {schedule_or_err}"

    ordered, return_sec = schedule_or_err

    now = datetime.now(LONDON_TZ)
    lines: list[str] = [
        f"🚚 Маршрут (старт {_format_time(now)}, трафик учтён, "
        f"+{parking_min} мин паркинг/донести):"
    ]

    cum_min = 0
    schedule_for_ccz: list[StopSchedule] = []
    for i, ss in enumerate(ordered, start=1):
        drive_min = max(1, round(ss.drive_sec / 60))
        cum_min += drive_min + parking_min
        eta = now + timedelta(minutes=cum_min)
        flag = " ⭐" if ss.stop.priority else ""
        note = f" ({ss.stop.note})" if ss.stop.note else ""
        lines.append(
            f"{i}. {ss.stop.code}{flag}{note} — ETA {_format_time(eta)}  "
            f"({drive_min}+{parking_min} мин)"
        )
        schedule_for_ccz.append(
            StopSchedule(code=ss.stop.code, arrival=eta, priority=ss.stop.priority)
        )

    return_min = max(1, round(return_sec / 60))
    cum_min += return_min
    back = now + timedelta(minutes=cum_min)
    lines.append(f"🏠 Возврат в магазин — ETA {_format_time(back)}  (+{return_min} мин)")
    lines.append("")
    lines.append(f"Всего: {cum_min} мин (с паркингом).")

    # Congestion Charge Zone summary (London-specific)
    ccz = ccz_summarize(schedule_for_ccz)
    lines.append("")
    lines.extend(ccz.message_lines)

    return "\n".join(lines)
