"""Beneficiary catalogue and current reported needs; no invented demand data."""
import auth
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import database
from locations import CARE_PLACES, is_inside_service_area

FORMAT = "%Y-%m-%d %H:%M:%S"
FOOD_TYPES = {
    "cooked": "وجبات مطهية", "bakery": "مخبوزات",
    "produce": "خضار وفاكهة", "packaged": "مواد غذائية معبأة",
    "other": "أخرى (وضحها في الملاحظات)",
}
URGENCY = {1: "عادي", 2: "مرتفع", 3: "عاجل جدًا"}
CATEGORIES = ("جمعية خيرية", "دار أيتام", "دار مسنين", "جهة رعاية أخرى")
STATUSES = {"active": "نشط", "paused": "متوقف مؤقتًا", "closed": "مغلق"}


class ConflictError(ValueError):
    """The record changed after the user opened the form."""


def get_beneficiaries():
    result = {}
    for place in CARE_PLACES:
        lat, lon = place.get("lat"), place.get("lon")
        name = str(place.get("name") or "").strip()
        if not name or not is_inside_service_area(lat, lon):
            continue
        if place.get("directory_id"):
            key = "YP-" + str(place["directory_id"])
        elif place.get("osm_id"):
            key = "OSM-" + str(place["osm_id"])
        else:
            raw = f"{name}|{float(lat):.7f}|{float(lon):.7f}"
            key = "PLACE-" + hashlib.sha256(raw.encode()).hexdigest()[:20]
        result.setdefault(key, {
            "place_id": key, "name": name,
            "address": str(place.get("address") or ""),
            "latitude": float(lat), "longitude": float(lon),
            "source": str(place.get("source") or "مصدر منشور"),
        })
    return sorted(result.values(), key=lambda p: p["name"])


@contextmanager
def connection():
    path = Path(database.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_needs_db():
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("""
            CREATE TABLE IF NOT EXISTS beneficiary_needs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id TEXT NOT NULL UNIQUE,
                place_name TEXT NOT NULL,
                address TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                location_source TEXT NOT NULL,
                category TEXT NOT NULL,
                people INTEGER NOT NULL CHECK(people > 0),
                meals_requested INTEGER NOT NULL CHECK(meals_requested > 0),
                urgency INTEGER NOT NULL CHECK(urgency BETWEEN 1 AND 3),
                food_types TEXT NOT NULL,
                dietary_notes TEXT NOT NULL,
                needed_by TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','paused','closed')),
                confirmed INTEGER NOT NULL CHECK(confirmed = 1),
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                meals_reserved INTEGER NOT NULL DEFAULT 0 CHECK(meals_reserved >= 0 AND meals_reserved <= meals_requested)
            )
        """)

        columns = {row["name"] for row in con.execute("PRAGMA table_info(beneficiary_needs)")}
        if "meals_reserved" not in columns:
            con.execute("ALTER TABLE beneficiary_needs ADD COLUMN meals_reserved INTEGER NOT NULL DEFAULT 0 CHECK(meals_reserved >= 0 AND meals_reserved <= meals_requested)")


def remaining_need(row):
    return max(0, row["meals_requested"] - row.get("meals_reserved", 0))


def list_needs():
    with connection() as con:
        user = auth.actor(con)
        where = " WHERE status='active' AND confirmed=1" if user['role'] != 'admin' else ''
        rows = con.execute("SELECT * FROM beneficiary_needs"+where+" ORDER BY updated_at DESC, id DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["food_types"] = json.loads(item["food_types"])
        result.append(item)
    return result


def effective_status(row, now=None):
    now = now or datetime.now()
    if row["status"] == "active" and remaining_need(row) == 0:
        return "fulfilled"
    if row["status"] == "active" and datetime.strptime(row["needed_by"], FORMAT) <= now:
        return "expired"
    return row["status"]


def list_active_needs(now=None):
    """Future allocation should consume this, not every stored row."""
    return [r for r in list_needs() if effective_status(r, now) == "active"]


def save_need(*, place_id, address, category, people, meals_requested, urgency,
              food_types, dietary_notes, needed_by, confirmed, status="active",
              expected_revision=None):
    catalogue = {p["place_id"]: p for p in get_beneficiaries()}
    if not isinstance(place_id, str) or place_id not in catalogue:
        raise ValueError("اختار جهة صحيحة من القائمة.")
    place = catalogue[place_id]
    if not isinstance(address, str) or not address.strip() or len(address.strip()) > 300:
        raise ValueError("اكتب عنوانًا صحيحًا لا يتجاوز 300 حرف.")
    if category not in CATEGORIES:
        raise ValueError("حدد نوع الجهة.")
    for value in (people, meals_requested):
        if type(value) is not int or not 1 <= value <= 100000:
            raise ValueError("عدد الأفراد والوجبات لازم يكون عددًا صحيحًا من 1 إلى 100000.")
    if type(urgency) is not int or urgency not in URGENCY:
        raise ValueError("حدد درجة الاستعجال.")
    if (not isinstance(food_types, (list, tuple)) or not food_types
            or any(not isinstance(f, str) or f not in FOOD_TYPES for f in food_types)):
        raise ValueError("اختار نوع أكل مناسب واحدًا على الأقل.")
    if not isinstance(dietary_notes, str) or len(dietary_notes) > 1000:
        raise ValueError("الملاحظات لا تتجاوز 1000 حرف.")
    if "other" in food_types and not dietary_notes.strip():
        raise ValueError("وضح نوع الأكل الآخر في الملاحظات.")
    if confirmed is not True:
        raise ValueError("راجع بيانات الاحتياج وموقع الجهة ثم فعّل التأكيد.")
    if status not in STATUSES:
        raise ValueError("حالة الاحتياج غير صحيحة.")
    if not isinstance(needed_by, datetime) or needed_by.tzinfo is not None:
        raise ValueError("حدد موعدًا صحيحًا بالتوقيت المحلي للجهاز.")
    now = datetime.now().replace(microsecond=0)
    needed_by = needed_by.replace(microsecond=0)
    if status == "active" and needed_by <= now:
        raise ValueError("موعد الاحتياج النشط لازم يكون في المستقبل.")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("نسخة السجل غير صحيحة؛ أعد فتح الصفحة.")
    values = (
        place["name"], address.strip(), place["latitude"], place["longitude"],
        place["source"], category, people, meals_requested, urgency,
        json.dumps(sorted(set(food_types)), ensure_ascii=False), dietary_notes.strip(),
        needed_by.strftime(FORMAT), status, 1, now.strftime(FORMAT),
    )
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        auth.require_admin(con)
        current = con.execute("SELECT id, revision, meals_reserved FROM beneficiary_needs WHERE place_id=?", (place_id,)).fetchone()
        if current is None:
            if expected_revision is not None:
                raise ConflictError("السجل اتغير؛ أعد تحميل الصفحة.")
            cursor = con.execute("""
                INSERT INTO beneficiary_needs (
                    place_name,address,latitude,longitude,location_source,category,people,
                    meals_requested,urgency,food_types,dietary_notes,needed_by,status,
                    confirmed,updated_at,place_id,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, values + (place_id, now.strftime(FORMAT)))
            return cursor.lastrowid
        if expected_revision != current["revision"]:
            raise ConflictError("الجهة دي ليها احتياج محفوظ أو اتعدّل من نافذة تانية. أعد تحميل الصفحة وافتح سجلها للتعديل.")
        if meals_requested < current["meals_reserved"]:
            raise ValueError("إجمالي الاحتياج لا يمكن يكون أقل من الوجبات المحجوزة بالفعل.")
        con.execute("""
            UPDATE beneficiary_needs SET
                place_name=?,address=?,latitude=?,longitude=?,location_source=?,category=?,
                people=?,meals_requested=?,urgency=?,food_types=?,dietary_notes=?,needed_by=?,
                status=?,confirmed=?,updated_at=?,revision=revision+1
            WHERE id=? AND revision=?
        """, values + (current["id"], expected_revision))
        return current["id"]
