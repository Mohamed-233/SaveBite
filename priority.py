"""Explainable weighted ranking, not a trained model or a routing algorithm.

Eligibility is checked before scoring. Each result is an alternative proposal,
never a reservation; neither quantities nor needs are modified here.
"""
import math
from datetime import datetime

from locations import is_inside_service_area

FORMAT = "%Y-%m-%d %H:%M:%S"
MATCHABLE_FOOD = {"cooked", "bakery", "produce", "packaged"}
WEIGHTS = {"urgency": 50, "deadline": 20, "people": 15, "meals": 10, "distance": 5}


def _date(value):
    try:
        return datetime.strptime(value, FORMAT)
    except (ValueError, TypeError):
        return None


def _positive_integer(value):
    return type(value) is int and value > 0


def straight_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance; this is NOT road length or an arrival estimate."""
    a, b = math.radians(float(lat1)), math.radians(float(lat2))
    da = b - a
    db = math.radians(float(lon2) - float(lon1))
    value = math.sin(da/2)**2 + math.cos(a)*math.cos(b)*math.sin(db/2)**2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def rank_recipients(donation, needs, now=None):
    """Return eligible alternatives, stable order, with unrounded score factors.

    Inputs are saved record snapshots, supplied by the caller. Dietary notes
    require human review; category matching alone is not food-safety clearance.
    """
    now = now or datetime.now()
    if not isinstance(now, datetime) or now.tzinfo is not None:
        raise ValueError("التوقيت لازم يكون التوقيت المحلي للجهاز.")
    deadline = _date(donation.get("pickup_deadline"))
    if (donation.get("status") != "available" or deadline is None or deadline <= now
            or not _positive_integer(donation.get("remaining_quantity"))):
        raise ValueError("التبرع ده غير متاح حاليًا أو انتهى موعد استلامه.")
    if (donation.get("pickup_confirmed") != 1
            or not is_inside_service_area(donation.get("latitude"), donation.get("longitude"))):
        raise ValueError("موقع الاستلام غير مكتمل أو غير مؤكد؛ لازم يتراجع قبل اقتراح جهة.")
    category = donation.get("food_category")
    if category not in MATCHABLE_FOOD:
        raise ValueError("نوع الأكل غير مصنّف للمطابقة. فئة «أخرى» تحتاج مراجعة يدوية ولا نرشّح لها جهة تلقائيًا.")

    results = []
    seen = set()
    # The DB stores one current record per place. Prefer its highest revision
    # if the pure function receives duplicate snapshots from another caller.
    ordered = sorted(needs, key=lambda row: row.get("revision", 0), reverse=True)
    for need in ordered:
        place_id = need.get("place_id")
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        needed_by = _date(need.get("needed_by"))
        foods = need.get("food_types")
        urgency = need.get("urgency")
        total = need.get("meals_requested")
        reserved = need.get("meals_reserved", 0)
        if (not _positive_integer(total) or type(reserved) is not int or not 0 <= reserved <= total):
            continue
        requested = total - reserved
        if (need.get("status") != "active" or need.get("confirmed") != 1
                or needed_by is None or needed_by <= now
                or not isinstance(foods, (list, tuple)) or category not in foods
                or type(urgency) is not int or urgency not in (1, 2, 3)
                or not _positive_integer(need.get("people"))
                or not _positive_integer(requested)
                or not is_inside_service_area(need.get("latitude"), need.get("longitude"))):
            continue
        distance = straight_distance_km(donation["latitude"], donation["longitude"], need["latitude"], need["longitude"])
        # An institution whose need is sooner than pickup's last deadline is
        # still eligible: pickup is allowed now, not only at that deadline.
        hours_left = (needed_by-now).total_seconds()/3600
        factors = {
            "urgency": WEIGHTS["urgency"] * (urgency-1)/2,
            "deadline": WEIGHTS["deadline"] * max(0.0, 1-hours_left/24),
            "people": WEIGHTS["people"] * min(need["people"]/100, 1.0),
            "meals": WEIGHTS["meals"] * min(requested/100, 1.0),
            "distance": WEIGHTS["distance"]/(1+distance/3),
        }
        results.append({
            "need_id": need.get("id"), "need_revision": need.get("revision"),
            "place_id": place_id, "name": need["place_name"], "address": need["address"],
            "urgency": urgency, "people": need["people"],
            "requested": requested,
            "proposed_meals": min(donation["remaining_quantity"], requested),
            "needed_by": need["needed_by"], "distance_km": distance,
            "dietary_notes": need.get("dietary_notes") or "",
            "score": sum(factors.values()), "factors": factors,
        })
    return sorted(results, key=lambda r: (-r["score"], r["needed_by"], r["place_id"]))
