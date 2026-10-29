"""Local accounts with fail-closed service authorization and expiring sessions."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

import database

_session = ContextVar('savebite_session', default=None)
SESSION_SECONDS = 8 * 60 * 60


class AccessDenied(ValueError):
    pass


def init_auth_db():
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if not existing and con.execute('SELECT 1 FROM donations LIMIT 1').fetchone():
            folder = Path(database.DB_PATH).parent/'backups'
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder/('savebite_before_accounts_'+uuid.uuid4().hex+'.db')
            try:
                with sqlite3.connect(database.DB_PATH) as source, sqlite3.connect(backup) as destination:
                    source.backup(destination)
                    if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise sqlite3.DatabaseError('Backup integrity check failed')
            except Exception:
                backup.unlink(missing_ok=True)
                raise
        schema = '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','restaurant')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_attempts (
                bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL,
                window_start REAL NOT NULL
            );
        '''
        for statement in schema.split(';'):
            if statement.strip():
                con.execute(statement)
        columns = {r['name'] for r in con.execute('PRAGMA table_info(donations)')}
        if 'owner_user_id' not in columns:
            con.execute('ALTER TABLE donations ADD COLUMN owner_user_id INTEGER REFERENCES users(id)')
        con.execute('CREATE INDEX IF NOT EXISTS donations_owner_idx ON donations(owner_user_id)')


def username_key(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,49}', value):
        raise ValueError('اسم المستخدم من 3 إلى 50 حرفًا إنجليزيًا أو رقمًا أو . _ -')
    return value


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('كلمة المرور من 12 إلى 128 حرفًا.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64*1024*1024)
    return 'scrypt$32768$8$3$' + salt.hex() + '$' + digest.hex()


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        _, n, r, p, salt, expected = encoded.split('$')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64*1024*1024)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@contextmanager
def use_session(token):
    handle = _session.set(token)
    try:
        yield
    finally:
        _session.reset(handle)


def actor(con):
    token = _session.get()
    if not isinstance(token, str):
        raise AccessDenied('سجّل الدخول للمتابعة.')
    row = con.execute('''SELECT u.id,u.username,u.display_name,u.role
        FROM auth_sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',
        (_token_hash(token), time.time())).fetchone()
    if row is None:
        raise AccessDenied('انتهت الجلسة؛ سجّل الدخول مرة أخرى.')
    return dict(row)


def current_user():
    with database.get_connection() as con:
        return actor(con)


def require_admin(con):
    user = actor(con)
    if user['role'] != 'admin':
        raise AccessDenied('هذه العملية متاحة للإدارة فقط.')
    return user


def require_donation(con, donation_id):
    user = actor(con)
    row = con.execute('SELECT * FROM donations WHERE id=?', (donation_id,)).fetchone()
    if row is None or (user['role'] != 'admin' and row['owner_user_id'] != user['id']):
        raise AccessDenied('التبرع غير متاح لهذا الحساب.')
    return dict(row)


def require_allocation(con, allocation_id):
    row = con.execute('SELECT * FROM allocations WHERE id=?', (allocation_id,)).fetchone()
    if row is None:
        actor(con)
        raise AccessDenied('الحجز غير متاح لهذا الحساب.')
    require_donation(con, row['donation_id'])
    return dict(row)


def _throttle(con, bucket, limit=5, seconds=900):
    now = time.time()
    row = con.execute('SELECT * FROM auth_attempts WHERE bucket=?', (bucket,)).fetchone()
    if row and now-row['window_start'] < seconds:
        if row['failures'] >= limit:
            return False
        con.execute('UPDATE auth_attempts SET failures=failures+1 WHERE bucket=?', (bucket,))
    else:
        con.execute('INSERT OR REPLACE INTO auth_attempts VALUES(?,?,?)', (bucket, 1, now))
    return True


def login(username, password):
    key = str(username).strip().lower()[:200]
    bucket = 'login:' + hashlib.sha256(key.encode()).hexdigest()
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        allowed = _throttle(con, bucket)
        # Global budget also limits arbitrary-username CPU attacks.
        allowed = _throttle(con, 'login:global', 120, 60) and allowed
    if not allowed:
        raise ValueError('محاولات كثيرة؛ حاول لاحقًا.')
    with database.get_connection() as con:
        row = con.execute('SELECT * FROM users WHERE username=?', (key,)).fetchone()
    encoded = row['password_hash'] if row else 'scrypt$32768$8$3$'+'00'*16+'$'+'00'*32
    matched = password_matches(password, encoded)
    if not row or not matched or not row['active']:
        raise ValueError('اسم المستخدم أو كلمة المرور غير صحيحة.')
    token = secrets.token_urlsafe(32)
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        latest = con.execute('SELECT active,password_hash FROM users WHERE id=?', (row['id'],)).fetchone()
        if not latest['active'] or latest['password_hash'] != encoded:
            raise AccessDenied('انتهت الجلسة؛ سجّل الدخول مرة أخرى.')
        con.execute('DELETE FROM auth_attempts WHERE bucket=?', (bucket,))
        con.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (time.time(),))
        con.execute('INSERT INTO auth_sessions VALUES(?,?,?)', (_token_hash(token), row['id'], time.time()+SESSION_SECONDS))
    return token


def logout(token):
    if isinstance(token, str):
        with database.get_connection() as con:
            con.execute('DELETE FROM auth_sessions WHERE token_hash=?', (_token_hash(token),))


def register_restaurant(username, display_name, password):
    username = username_key(username)
    display_name = str(display_name).strip()
    if not 2 <= len(display_name) <= 100:
        raise ValueError('اكتب اسمًا من حرفين إلى 100 حرف.')
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        allowed = _throttle(con, 'register:global', 10, 60)
    if not allowed:
        raise ValueError('محاولات كثيرة؛ حاول لاحقًا.')
    encoded = password_hash(password)
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        if not con.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
            raise ValueError('حساب الإدارة لم يتم تجهيزه بعد.')
        if con.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise ValueError('اسم المستخدم مستخدم بالفعل.')
        return con.execute('''INSERT INTO users(username,display_name,password_hash,role,created_at)
            VALUES(?,?,?,'restaurant',?)''', (username, display_name, encoded, time.time())).lastrowid


def change_password(old, new):
    confirm_password(old)
    encoded = password_hash(new)
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        user = actor(con)
        row = con.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()
        if not password_matches(old, row['password_hash']):
            raise ValueError('كلمة المرور الحالية غير صحيحة.')
        con.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, user['id']))
        con.execute('DELETE FROM auth_sessions WHERE user_id=?', (user['id'],))


def confirm_password(password):
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        user = actor(con)
        allowed = _throttle(con, 'confirm:'+str(user['id']))
        row = con.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()
    if not allowed:
        raise ValueError('محاولات كثيرة؛ حاول لاحقًا.')
    if not password_matches(password, row['password_hash']):
        raise ValueError('كلمة المرور الحالية غير صحيحة.')
    with database.get_connection() as con:
        con.execute('DELETE FROM auth_attempts WHERE bucket=?', ('confirm:'+str(user['id']),))


def list_restaurants():
    with database.get_connection() as con:
        require_admin(con)
        return [dict(r) for r in con.execute("SELECT id,username,display_name,active FROM users WHERE role='restaurant' ORDER BY id DESC")]


def manage_restaurant(user_id, *, active, new_password=None):
    encoded = password_hash(new_password) if new_password else None
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        require_admin(con)
        row = con.execute("SELECT id FROM users WHERE id=? AND role='restaurant'", (user_id,)).fetchone()
        if row is None:
            raise ValueError('الحساب غير موجود.')
        con.execute('UPDATE users SET active=? WHERE id=?', (int(bool(active)), user_id))
        if encoded:
            con.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, user_id))
        con.execute('DELETE FROM auth_sessions WHERE user_id=?', (user_id,))
        if encoded:
            username = con.execute('SELECT username FROM users WHERE id=?', (user_id,)).fetchone()['username']
            con.execute('DELETE FROM auth_attempts WHERE bucket=?', ('login:'+hashlib.sha256(username.encode()).hexdigest(),))


def has_admin():
    with database.get_connection() as con:
        return bool(con.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone())


def local_admin_setup(username, display_name, password):
    """Local operator only: used by setup_admin.py, never exposed in web UI."""
    key = username_key(username)
    name = str(display_name).strip()
    if not 2 <= len(name) <= 100:
        raise ValueError('اكتب اسمًا من حرفين إلى 100 حرف.')
    encoded = password_hash(password)
    with database.get_connection() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT id,role FROM users WHERE username=?', (key,)).fetchone()
        if row:
            if row['role'] != 'admin':
                raise ValueError('اسم المستخدم تابع لحساب مطعم؛ اختار اسمًا آخر.')
            con.execute('UPDATE users SET password_hash=?,display_name=?,active=1 WHERE id=?', (encoded, name, row['id']))
            con.execute('DELETE FROM auth_sessions WHERE user_id=?', (row['id'],))
        elif con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            raise ValueError('حساب المدير موجود؛ استخدم نفس اسم المستخدم لإعادة تعيين كلمة مروره.')
        else:
            con.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,'admin',?)", (key, name, encoded, time.time()))
        con.execute('DELETE FROM auth_attempts WHERE bucket=?', ('login:'+hashlib.sha256(key.encode()).hexdigest(),))
