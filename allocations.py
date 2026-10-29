"""Atomic food reservations. No routing or delivery transitions in this step."""
import auth
import json
from datetime import datetime

import database
import beneficiary_needs as needs
from priority import rank_recipients


class ReservationConflict(ValueError):
    pass


def init_allocations_db():
    needs.init_needs_db()
    with database.get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_token TEXT NOT NULL UNIQUE,
                request_payload TEXT NOT NULL,
                donation_id INTEGER NOT NULL REFERENCES donations(id),
                need_id INTEGER NOT NULL REFERENCES beneficiary_needs(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','delivered','cancelled')),
                donor_name TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                food_type TEXT NOT NULL,
                food_category TEXT NOT NULL,
                pickup_address TEXT NOT NULL,
                recipient_address TEXT NOT NULL,
                snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS allocations_donation_idx ON allocations(donation_id)")


def list_allocations(donation_id=None):
    with database.get_connection() as con:
        user = auth.actor(con)
        clauses, params = [], []
        if user['role'] != 'admin':
            clauses.append('d.owner_user_id=?'); params.append(user['id'])
        if donation_id is not None:
            auth.require_donation(con, donation_id)
            clauses.append('a.donation_id=?'); params.append(donation_id)
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        rows = con.execute('SELECT a.* FROM allocations a JOIN donations d ON d.id=a.donation_id'+where+' ORDER BY a.id DESC', params).fetchall()
    return [dict(r) for r in rows]


def reserve(*, donation_id, need_id, quantity, expected_need_revision,
            expected_available, food_category, request_token, confirmed):
    """All checks, both quantity changes and receipt commit in one transaction.

    A repeated token with the same payload returns the prior receipt. A stale
    snapshot or different replay is rejected without changing either balance.
    """
    if confirmed is not True:
        raise ValueError("راجع قيود الأكل واتفق على الاستلام ثم فعّل التأكيد.")
    for value in (donation_id, need_id, quantity, expected_need_revision, expected_available):
        if type(value) is not int or value < 1:
            raise ValueError("الكمية وبيانات الحجز لازم تكون أعدادًا صحيحة موجبة.")
    if not isinstance(request_token, str) or not 16 <= len(request_token) <= 128:
        raise ValueError("طلب الحجز غير صحيح؛ حدّث الاقتراح.")
    if not isinstance(food_category, str) or food_category not in needs.FOOD_TYPES:
        raise ValueError("نوع الأكل غير صحيح.")
    payload = json.dumps([donation_id, need_id, quantity, expected_need_revision, expected_available, food_category])
    with database.get_connection() as con:
        con.execute("BEGIN IMMEDIATE")
        auth.require_donation(con, donation_id)
        previous = con.execute("SELECT * FROM allocations WHERE request_token=?", (request_token,)).fetchone()
        if previous is not None:
            if previous["request_payload"] != payload:
                raise ReservationConflict("طلب الحجز اتسجّل ببيانات مختلفة؛ حدّث الاقتراح.")
            return dict(previous)
        donor_row = con.execute("SELECT * FROM donations WHERE id=?", (donation_id,)).fetchone()
        need_row = con.execute("SELECT * FROM beneficiary_needs WHERE id=?", (need_id,)).fetchone()
        if donor_row is None or need_row is None:
            raise ReservationConflict("التبرع أو الاحتياج مش موجود؛ حدّث الاقتراح.")
        donation, need = dict(donor_row), dict(need_row)
        if donation["remaining_quantity"] != expected_available or need["revision"] != expected_need_revision:
            raise ReservationConflict("الكمية أو احتياج الجمعية اتغيّر. حدّث الاقتراح وراجع الحجز من جديد.")
        if donation.get("food_category") is not None and donation["food_category"] != food_category:
            raise ReservationConflict("تصنيف الأكل اتغيّر؛ حدّث الاقتراح.")
        donation["food_category"] = food_category
        need["food_types"] = json.loads(need["food_types"])
        now = datetime.now()
        candidates = rank_recipients(donation, [need], now)
        if not candidates:
            raise ReservationConflict("الاحتياج لم يعد نشطًا أو مطابقًا للتبرع.")
        if quantity > candidates[0]["proposed_meals"]:
            raise ReservationConflict("الكمية أكبر من التبرع المتاح أو من الاحتياج المتبقي.")
        changed = con.execute("""
            UPDATE donations SET remaining_quantity=remaining_quantity-?,
                status=CASE WHEN remaining_quantity=? THEN 'reserved' ELSE status END
            WHERE id=? AND status='available' AND remaining_quantity>=?
        """, (quantity, quantity, donation_id, quantity))
        if changed.rowcount != 1:
            raise ReservationConflict("التبرع لم يعد متاحًا بالكمية المطلوبة.")
        changed = con.execute("""
            UPDATE beneficiary_needs SET meals_reserved=meals_reserved+?,
                revision=revision+1, updated_at=?
            WHERE id=? AND revision=? AND meals_requested-meals_reserved>=?
                AND status='active'
        """, (quantity, now.strftime(database.DATETIME_FORMAT), need_id, expected_need_revision, quantity))
        if changed.rowcount != 1:
            raise ReservationConflict("الاحتياج اتغيّر؛ حدّث الاقتراح.")
        cursor = con.execute("""
            INSERT INTO allocations (
                request_token,request_payload,donation_id,need_id,quantity,
                donor_name,recipient_name,food_type,food_category,pickup_address,
                recipient_address,snapshot,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (request_token, payload, donation_id, need_id, quantity,
            donation.get("pickup_place_name") or donation["donor_name"], need["place_name"],
            donation["food_type"], food_category, donation["address"], need["address"],
            json.dumps({"donation": donation, "need": need}, ensure_ascii=False),
            now.strftime(database.DATETIME_FORMAT)))
        return dict(con.execute("SELECT * FROM allocations WHERE id=?", (cursor.lastrowid,)).fetchone())
