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
from .congestion import (
    CCZSummary,
    StopSchedule,
    is_charging_at_with_buffer,
    is_in_ccz,
    summarize as ccz_summarize,
)


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


def _greedy_walk(
    stops: list[Stop],
    matrix: list[list[float]],
    indices: list[int],
    start_idx: int,
) -> tuple[list[ScheduledStop], int] | str:
    """Walk `indices` (1-based stop indices into `stops`) greedily from `start_idx`.
    Returns (ordered, last_position) or error string."""
    ordered: list[ScheduledStop] = []
    cur = start_idx
    remaining = set(indices)
    while remaining:
        best, best_sec = -1, math.inf
        for idx in remaining:
            if matrix[cur][idx] < best_sec:
                best, best_sec = idx, matrix[cur][idx]
        if best == -1 or not math.isfinite(best_sec):
            return "Не удалось проложить маршрут к одному из стопов. Проверь корректность посткодов."
        ordered.append(ScheduledStop(stop=stops[best - 1], drive_sec=int(best_sec)))
        remaining.discard(best)
        cur = best
    return ordered, cur


def _walk_priority_then_greedy(
    stops: list[Stop],
    matrix: list[list[float]],
    indices: list[int],
    start_idx: int,
) -> tuple[list[ScheduledStop], int] | str:
    """Within `indices`: do priority stops first in declared order, then greedy nearest
    for the rest. Returns (ordered, last_position) or error string."""
    priority_idx = [i for i in indices if stops[i - 1].priority]
    regular_idx = [i for i in indices if not stops[i - 1].priority]

    ordered: list[ScheduledStop] = []
    cur = start_idx
    for pidx in priority_idx:
        sec = matrix[cur][pidx]
        if not math.isfinite(sec):
            return f"Не удалось проложить маршрут до {stops[pidx - 1].code}. Проверь корректность посткода."
        ordered.append(ScheduledStop(stop=stops[pidx - 1], drive_sec=int(sec)))
        cur = pidx

    if regular_idx:
        rest = _greedy_walk(stops, matrix, regular_idx, cur)
        if isinstance(rest, str):
            return rest
        more, cur = rest
        ordered.extend(more)
    return ordered, cur


def _build_schedule(
    stops: list[Stop], matrix: list[list[float]]
) -> tuple[list[ScheduledStop], float] | str:
    """Greedy schedule: priority in declared order, then nearest-neighbor for the rest.

    Returns (ordered, return_sec) or error string.
    Index 0 in `matrix` is the SHOP; stops are 1..N.
    """
    indices = list(range(1, len(stops) + 1))
    result = _walk_priority_then_greedy(stops, matrix, indices, start_idx=0)
    if isinstance(result, str):
        return result
    ordered, last = result
    return_sec = matrix[last][0]
    if not math.isfinite(return_sec):
        return "Не удалось рассчитать обратный путь до магазина."
    return ordered, float(return_sec)


def _build_schedule_ccz_last(
    stops: list[Stop], matrix: list[list[float]]
) -> tuple[list[ScheduledStop], float] | str:
    """Alternative ordering: visit ALL non-CCZ stops first (priority+greedy), then
    visit CCZ stops (priority+greedy). Used to try to push CCZ-zone arrivals past
    the 18:00 charging cutoff and save the £18 charge.

    Refuses to move priority stops that happen to be in CCZ — they stay early.
    """
    ccz_indices = [i + 1 for i, s in enumerate(stops) if is_in_ccz(s.code)]
    non_ccz_indices = [i + 1 for i, s in enumerate(stops) if not is_in_ccz(s.code)]

    # If a priority stop is in CCZ, we can't defer it — bail out.
    if any(stops[i - 1].priority for i in ccz_indices):
        return "Приоритетный стоп в CCZ — нельзя переносить на конец."

    # Phase A: non-CCZ first (priority + greedy)
    a = _walk_priority_then_greedy(stops, matrix, non_ccz_indices, start_idx=0)
    if isinstance(a, str):
        return a
    ordered_a, cur = a

    # Phase B: CCZ stops (greedy only — they have no priority by definition of bail-out above)
    if ccz_indices:
        b = _greedy_walk(stops, matrix, ccz_indices, cur)
        if isinstance(b, str):
            return b
        ordered_b, cur = b
        ordered = ordered_a + ordered_b
    else:
        ordered = ordered_a

    return_sec = matrix[cur][0]
    if not math.isfinite(return_sec):
        return "Не удалось рассчитать обратный путь до магазина."
    return ordered, float(return_sec)


def _eta_schedule(
    ordered: list[ScheduledStop], start: datetime, parking_min: int
) -> list[StopSchedule]:
    """Turn drive-times into datetime ETAs."""
    out: list[StopSchedule] = []
    cum_min = 0
    for ss in ordered:
        drive_min = max(1, round(ss.drive_sec / 60))
        cum_min += drive_min + parking_min
        out.append(
            StopSchedule(
                code=ss.stop.code,
                arrival=start + timedelta(minutes=cum_min),
                priority=ss.stop.priority,
            )
        )
    return out


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
    matrix = matrix_or_err

    primary = _build_schedule(stops, matrix)
    if isinstance(primary, str):
        return f"⚠️ {primary}"
    primary_ordered, primary_return_sec = primary

    now = datetime.now(LONDON_TZ)
    primary_eta = _eta_schedule(primary_ordered, now, parking_min)
    primary_ccz = ccz_summarize(primary_eta)

    # Try CCZ-last alternate ONLY if primary triggers a charge AND it's plausibly
    # avoidable (mix of CCZ + non-CCZ stops, no priority inside CCZ).
    chosen_ordered = primary_ordered
    chosen_return_sec = primary_return_sec
    chosen_eta = primary_eta
    chosen_ccz = primary_ccz
    optimization_note: str | None = None

    if primary_ccz.charge_gbp > 0:
        has_ccz = any(is_in_ccz(s.code) for s in stops)
        has_non_ccz = any(not is_in_ccz(s.code) for s in stops)
        priority_in_ccz = any(s.priority and is_in_ccz(s.code) for s in stops)

        if has_ccz and has_non_ccz and not priority_in_ccz:
            alt = _build_schedule_ccz_last(stops, matrix)
            if not isinstance(alt, str):
                alt_ordered, alt_return_sec = alt
                alt_eta = _eta_schedule(alt_ordered, now, parking_min)
                alt_ccz = ccz_summarize(alt_eta)
                if alt_ccz.charge_gbp < primary_ccz.charge_gbp:
                    chosen_ordered = alt_ordered
                    chosen_return_sec = alt_return_sec
                    chosen_eta = alt_eta
                    chosen_ccz = alt_ccz
                    saving = primary_ccz.charge_gbp - alt_ccz.charge_gbp
                    optimization_note = (
                        f"💡 Маршрут оптимизирован: CCZ-стопы перенесены в конец, "
                        f"экономия £{saving}. ✅"
                    )

        if chosen_ccz.charge_gbp > 0 and optimization_note is None:
            if priority_in_ccz:
                reason = "приоритетный стоп внутри зоны — нельзя двигать"
            elif not has_non_ccz:
                reason = "все стопы внутри зоны"
            else:
                reason = "маршрут не успевает дотянуть до 18:00"
            optimization_note = (
                f"⚠️ Избежать £{chosen_ccz.charge_gbp} не получилось: {reason}."
            )

    # Render the chosen schedule into Telegram text
    lines: list[str] = [
        f"🚚 Маршрут (старт {_format_time(now)}, трафик учтён, "
        f"+{parking_min} мин паркинг/донести):"
    ]
    cum_min = 0
    for i, ss in enumerate(chosen_ordered, start=1):
        drive_min = max(1, round(ss.drive_sec / 60))
        cum_min += drive_min + parking_min
        eta = now + timedelta(minutes=cum_min)
        flag = " ⭐" if ss.stop.priority else ""
        ccz_mark = ""
        if is_in_ccz(ss.stop.code):
            ccz_mark = " 💷£18" if is_charging_at_with_buffer(eta) else " 💷✓бесплатно"
        note = f" ({ss.stop.note})" if ss.stop.note else ""
        lines.append(
            f"{i}. {ss.stop.code}{flag}{ccz_mark}{note} — ETA {_format_time(eta)}  "
            f"({drive_min}+{parking_min} мин)"
        )

    return_min = max(1, round(chosen_return_sec / 60))
    cum_min += return_min
    back = now + timedelta(minutes=cum_min)
    lines.append(f"🏠 Возврат в магазин — ETA {_format_time(back)}  (+{return_min} мин)")
    lines.append("")
    lines.append(f"Всего: {cum_min} мин (с паркингом).")

    # CCZ summary bottom line + optional optimization note
    lines.append("")
    if chosen_ccz.charge_gbp > 0:
        lines.append(f"💷 Congestion Charge: £{chosen_ccz.charge_gbp}")
    elif any(is_in_ccz(s.code) for s in stops):
        lines.append("💷 Congestion Charge: £0 — CCZ-стопы вне часов тарифа ✅")
    else:
        lines.append("💷 Congestion Charge: £0 — маршрут не заходит в CCZ ✅")
    if optimization_note:
        lines.append(optimization_note)

    return "\n".join(lines)
