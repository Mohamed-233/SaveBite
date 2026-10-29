import auth
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path


# ---------- Configuration ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "savebite.db"

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
STATUS_AVAILABLE = "available"


# ---------- Connection ----------
@contextmanager
def get_connection():
    """Open a connection and commit or roll back safely."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# ---------- Database setup ----------
def init_db() -> None:
    """Create or upgrade the table without replacing any existing rows."""
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_name TEXT NOT NULL,
                food_type TEXT NOT NULL,
                original_quantity INTEGER NOT NULL CHECK (original_quantity >= 1),
                remaining_quantity INTEGER NOT NULL
                    CHECK (remaining_quantity >= 0 AND remaining_quantity <= original_quantity),
                address TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                pickup_deadline TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL,
                pickup_place_id TEXT,
                pickup_place_name TEXT,
                location_source TEXT,
                pickup_confirmed INTEGER NOT NULL DEFAULT 0,
                food_category TEXT
            )
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(donations)")}
        additions = {
            "food_category": "TEXT",
            "latitude": "REAL",
            "longitude": "REAL",
            "pickup_place_id": "TEXT",
            "pickup_place_name": "TEXT",
            "location_source": "TEXT",
            "pickup_confirmed": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE donations ADD COLUMN {name} {declaration}")


# ---------- Save donations ----------
def insert_donation_record(
    donor_name: str,
    food_type: str,
    meals: int,
    address: str,
    deadline: datetime,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    pickup_place_id: str | None = None,
    pickup_place_name: str | None = None,
    location_source: str | None = None,
    pickup_confirmed: bool = False,
    food_category: str | None = None,
) -> int | None:
    """Save atomically and return its exact ID, or None for a recent duplicate."""
    donor_name = donor_name.strip()
    food_type = food_type.strip()
    address = address.strip()

    if not donor_name or not food_type or not address:
        raise ValueError("Required fields cannot be empty.")

    if isinstance(meals, bool) or not isinstance(meals, int) or meals < 1:
        raise ValueError("Meals must be a positive integer.")

    if (latitude is None) != (longitude is None):
        raise ValueError("Both pickup coordinates are required together.")
    if latitude is not None:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError("Invalid pickup coordinates.")
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Invalid pickup coordinates.") from None
        if (not math.isfinite(latitude) or not math.isfinite(longitude)
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            raise ValueError("Invalid pickup coordinates.")
    pickup_place_id = pickup_place_id.strip() if pickup_place_id else None
    pickup_place_name = pickup_place_name.strip() if pickup_place_name else None
    location_source = location_source.strip() if location_source else None
    if bool(pickup_place_id) != bool(pickup_place_name):
        raise ValueError("Pickup place ID and name must be supplied together.")
    if (pickup_place_id or pickup_confirmed) and latitude is None:
        raise ValueError("A selected pickup place needs coordinates.")
    if not isinstance(pickup_confirmed, bool):
        raise ValueError("Pickup confirmation must be boolean.")

    if food_category is not None and food_category not in {"cooked", "bakery", "produce", "packaged", "other"}:
        raise ValueError("Invalid food category.")

    now = datetime.now()

    if deadline <= now:
        raise ValueError("Pickup deadline must be in the future.")

    created_at = now.strftime(DATETIME_FORMAT)
    deadline_text = deadline.strftime(DATETIME_FORMAT)
    duplicate_cutoff = (
        now - timedelta(seconds=10)
    ).strftime(DATETIME_FORMAT)

    with get_connection() as connection:
        # Keep duplicate detection and insertion in one transaction.
        connection.execute("BEGIN IMMEDIATE")

        user = auth.actor(connection)
        duplicate = connection.execute(
            """
            SELECT id
            FROM donations
            WHERE owner_user_id = ?
              AND donor_name = ?
              AND food_type = ?
              AND original_quantity = ?
              AND address = ?
              AND pickup_deadline = ?
              AND latitude IS ?
              AND longitude IS ?
              AND pickup_place_id IS ?
              AND food_category IS ?
              AND created_at BETWEEN ? AND ?
            LIMIT 1
            """,
            (
                user['id'],
                donor_name,
                food_type,
                meals,
                address,
                deadline_text,
                latitude,
                longitude,
                pickup_place_id,
                food_category,
                duplicate_cutoff,
                created_at,
            ),
        ).fetchone()

        if duplicate is not None:
            return None

        cursor = connection.execute(
            """
            INSERT INTO donations (
                donor_name,
                food_type,
                original_quantity,
                remaining_quantity,
                address,
                latitude,
                longitude,
                pickup_place_id,
                pickup_place_name,
                location_source,
                pickup_confirmed,
                pickup_deadline,
                status,
                created_at,
                food_category, owner_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                donor_name,
                food_type,
                meals,
                meals,
                address,
                latitude,
                longitude,
                pickup_place_id,
                pickup_place_name,
                location_source,
                int(pickup_confirmed),
                deadline_text,
                STATUS_AVAILABLE,
                created_at,
                food_category,
                user["id"],
            ),
        )

    return cursor.lastrowid


# ---------- Read donations ----------
def list_donations() -> list[sqlite3.Row]:
    """All rows for an admin, only owned rows for a restaurant."""
    with get_connection() as connection:
        user = auth.actor(connection)
        if user['role'] == 'admin':
            return connection.execute('SELECT * FROM donations ORDER BY id DESC').fetchall()
        return connection.execute('SELECT * FROM donations WHERE owner_user_id=? ORDER BY id DESC', (user['id'],)).fetchall()

def insert_donation(*args, **kwargs) -> bool:
    """Backward-compatible boolean API for existing callers."""
    return insert_donation_record(*args, **kwargs) is not None


def get_donation(donation_id):
    with get_connection() as connection:
        return auth.require_donation(connection, donation_id)
