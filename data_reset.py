"""Reset registered donors, needs and reservations; keep the static map catalogue."""
import auth
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import database
import allocations


def _snapshot(con):
    tables={name:[dict(r) for r in con.execute(f"SELECT * FROM {name} ORDER BY id")]
        for name in ('donations','allocations','allocation_events','beneficiary_needs')}
    digest=hashlib.sha256(json.dumps(tables,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return tables,digest


def preview():
    with database.get_connection() as con:
        con.execute("BEGIN")
        auth.require_admin(con)
        tables,digest=_snapshot(con)
    return {"donations":len(tables['donations']),"allocations":len(tables['allocations']),
        "needs":len(tables['beneficiary_needs']),"digest":digest}


def reset_registered_data(*,expected_digest,confirmation):
    if confirmation!='RESET':raise ValueError("Type RESET to confirm deletion.")
    if not isinstance(expected_digest,str) or len(expected_digest)!=64:raise ValueError("Refresh the reset preview.")
    with database.get_connection() as con:
        con.execute("BEGIN IMMEDIATE")
        auth.require_admin(con)
        tables,digest=_snapshot(con)
        if digest!=expected_digest:
            raise allocations.ReservationConflict("Data changed after the preview. Review the updated counts before resetting.")
        if not any(tables.values()):
            return {"backup":None,"donations":0,"allocations":0,"needs":0}
        backup_dir=Path(database.DB_PATH).parent/'backups';backup_dir.mkdir(parents=True,exist_ok=True)
        backup=backup_dir/f"savebite_before_reset_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.db"
        # Separate reader sees the last committed state while BEGIN IMMEDIATE
        # prevents new writers. Back up before the first DELETE/UPDATE.
        try:
            with sqlite3.connect(database.DB_PATH) as source,sqlite3.connect(backup) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise sqlite3.DatabaseError('Backup integrity check failed')
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        con.execute("DELETE FROM allocation_events")
        con.execute("DELETE FROM allocations")
        con.execute("DELETE FROM donations")
        con.execute("DELETE FROM beneficiary_needs")
        con.execute("UPDATE app_metadata SET value=CAST(value AS INTEGER)+1 WHERE key='reset_epoch'")
        return {"backup":str(backup),"donations":len(tables['donations']),"allocations":len(tables['allocations']),
            "needs":len(tables['beneficiary_needs'])}
