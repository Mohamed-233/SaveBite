"""Reservation lifecycle: every transition and balance update is atomic."""
import auth
import json
from datetime import datetime

import database
import allocations

PHASES = ("pending", "in_transit", "delivered", "cancelled")


def init_workflow_db():
    allocations.init_allocations_db()
    with database.get_connection() as con:
        con.execute("BEGIN IMMEDIATE")
        columns={r["name"] for r in con.execute("PRAGMA table_info(allocations)")}
        for name,definition in {
            "delivery_phase":"TEXT NOT NULL DEFAULT 'pending' CHECK(delivery_phase IN ('pending','in_transit','delivered','cancelled'))",
            "revision":"INTEGER NOT NULL DEFAULT 1",
            "started_at":"TEXT", "delivered_at":"TEXT", "cancelled_at":"TEXT",
        }.items():
            if name not in columns:con.execute(f"ALTER TABLE allocations ADD COLUMN {name} {definition}")
        con.execute("UPDATE allocations SET delivery_phase=status WHERE status IN ('delivered','cancelled') AND delivery_phase<>status")
        con.execute("""CREATE TABLE IF NOT EXISTS allocation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            allocation_id INTEGER NOT NULL REFERENCES allocations(id),
            request_token TEXT NOT NULL UNIQUE,
            request_payload TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('start','deliver','cancel')),
            from_phase TEXT NOT NULL, to_phase TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        con.execute("CREATE TABLE IF NOT EXISTS app_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        con.execute("INSERT OR IGNORE INTO app_metadata VALUES('reset_epoch','0')")


def phase(row):
    if row["status"] in ("delivered","cancelled"):return row["status"]
    return row.get("delivery_phase","pending")


def get_allocation(key):
    with database.get_connection() as con:
        return auth.require_allocation(con, key)


def transition(*,allocation_id,action,expected_revision,request_token,confirmed):
    if action not in ("start","deliver","cancel"):
        raise ValueError("Invalid delivery action.")
    if confirmed is not True:
        raise ValueError("Confirm the action before submitting.")
    if type(allocation_id) is not int or allocation_id<1 or type(expected_revision) is not int or expected_revision<1:
        raise ValueError("Invalid reservation version.")
    if not isinstance(request_token,str) or not 16<=len(request_token)<=128:
        raise ValueError("Invalid request. Refresh the page.")
    payload=json.dumps([allocation_id,action,expected_revision])
    with database.get_connection() as con:
        con.execute("BEGIN IMMEDIATE")
        auth.require_allocation(con, allocation_id)
        previous=con.execute("SELECT * FROM allocation_events WHERE request_token=?",(request_token,)).fetchone()
        if previous:
            if previous["request_payload"]!=payload:
                raise allocations.ReservationConflict("Request already used for another action.")
            row=con.execute("SELECT * FROM allocations WHERE id=?",(allocation_id,)).fetchone()
            return dict(row)
        row=con.execute("SELECT * FROM allocations WHERE id=?",(allocation_id,)).fetchone()
        if row is None:raise allocations.ReservationConflict("Reservation no longer exists. Refresh the page.")
        row=dict(row);current=phase(row)
        if row["revision"]!=expected_revision:
            raise allocations.ReservationConflict("Reservation changed in another window. Refresh the page.")
        target={"start":"in_transit","deliver":"delivered","cancel":"cancelled"}[action]
        allowed={"start":{"pending"},"deliver":{"in_transit"},"cancel":{"pending","in_transit"}}
        if current not in allowed[action] or row["status"]!="reserved":
            raise allocations.ReservationConflict("This action is not allowed for the current reservation state.")
        now=datetime.now();stamp=now.strftime(database.DATETIME_FORMAT)
        if action=="start":
            donor=con.execute("SELECT pickup_deadline FROM donations WHERE id=?",(row["donation_id"],)).fetchone()
            need=con.execute("SELECT needed_by FROM beneficiary_needs WHERE id=?",(row["need_id"],)).fetchone()
            if (donor is None or need is None
                    or datetime.strptime(donor["pickup_deadline"],database.DATETIME_FORMAT)<=now
                    or datetime.strptime(need["needed_by"],database.DATETIME_FORMAT)<=now):
                raise allocations.ReservationConflict("Pickup or recipient deadline has expired. Review the reservation before dispatch.")
        if action=="cancel":
            donor=con.execute("SELECT * FROM donations WHERE id=?",(row["donation_id"],)).fetchone()
            need=con.execute("SELECT * FROM beneficiary_needs WHERE id=?",(row["need_id"],)).fetchone()
            quantity=row["quantity"]
            if (donor is None or need is None or donor["remaining_quantity"]+quantity>donor["original_quantity"]
                    or need["meals_reserved"]<quantity or donor["status"] not in ('available','reserved','expired')):
                raise allocations.ReservationConflict("Balances are inconsistent; cancellation was not applied.")
            deadline=datetime.strptime(donor["pickup_deadline"],database.DATETIME_FORMAT)
            status="available" if deadline>now else "expired"
            con.execute("UPDATE donations SET remaining_quantity=remaining_quantity+?,status=? WHERE id=?",(quantity,status,donor["id"]))
            con.execute("UPDATE beneficiary_needs SET meals_reserved=meals_reserved-?,revision=revision+1,updated_at=? WHERE id=?",(quantity,stamp,need["id"]))
        time_column={"start":"started_at","deliver":"delivered_at","cancel":"cancelled_at"}[action]
        status="reserved" if action=="start" else target
        con.execute(f"UPDATE allocations SET status=?,delivery_phase=?,{time_column}=?,revision=revision+1 WHERE id=? AND revision=?",
            (status,target,stamp,allocation_id,expected_revision))
        con.execute("""INSERT INTO allocation_events(allocation_id,request_token,request_payload,action,from_phase,to_phase,created_at)
            VALUES(?,?,?,?,?,?,?)""",(allocation_id,request_token,payload,action,current,target,stamp))
        return dict(con.execute("SELECT * FROM allocations WHERE id=?",(allocation_id,)).fetchone())
