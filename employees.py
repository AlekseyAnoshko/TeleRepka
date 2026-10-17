"""Локальная база сотрудников лаборатории сетевых технологий ИРНИТУ."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "laboratory_employees.db"

SCHEMA = """
CREATE TABLE employees_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    istu_id INTEGER UNIQUE,
    schedule_url TEXT,
    profile_url TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT
)
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        existing = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'employees'
        """).fetchone()

        if not existing:
            conn.execute(SCHEMA.replace("employees_new", "employees"))
            return

        columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(employees)")
        }

        # Старые обязательные поля должны стать необязательными.
        needs_rebuild = (
            columns["istu_id"]["notnull"]
            or columns["schedule_url"]["notnull"]
        )

        if needs_rebuild:
            conn.execute(SCHEMA)

            conn.execute("""
                INSERT INTO employees_new
                    (id, name, istu_id, schedule_url, active)
                SELECT id, name, istu_id, schedule_url, active
                FROM employees
            """)

            conn.execute("DROP TABLE employees")
            conn.execute(
                "ALTER TABLE employees_new RENAME TO employees"
            )
        else:
            for name, definition in {
                "position": "TEXT NOT NULL DEFAULT ''",
                "profile_url": "TEXT",
                "last_seen_at": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE employees ADD COLUMN "
                        f"{name} {definition}"
                    )


def list_employees():
    with connect() as conn:
        rows = conn.execute("""
            SELECT id, name, position, istu_id,
                   schedule_url, profile_url, active,
                   last_seen_at
            FROM employees
            WHERE active = 1
            ORDER BY name COLLATE NOCASE
        """).fetchall()
    return [dict(row) for row in rows]


def get_employee(employee_id):
    with connect() as conn:
        row = conn.execute("""
            SELECT id, name, position, istu_id,
                   schedule_url, profile_url, active
            FROM employees
            WHERE id = ? AND active = 1
        """, (employee_id,)).fetchone()
    return dict(row) if row else None


def add_employee(name, istu_id):
    istu_id = int(istu_id)
    if istu_id <= 0 or not name.strip():
        raise ValueError("Некорректные данные преподавателя")

    url = (
        "https://www.istu.edu/raspisanie/"
        f"prepodavatel/{istu_id}/"
    )

    with connect() as conn:
        conn.execute("""
            INSERT INTO employees (name, istu_id, schedule_url)
            VALUES (?, ?, ?)
            ON CONFLICT(istu_id) DO UPDATE SET
                schedule_url = excluded.schedule_url,
                active = 1
        """, (name.strip(), istu_id, url))


if __name__ == "__main__":
    init_db()
    for employee in list_employees():
        print(employee)
