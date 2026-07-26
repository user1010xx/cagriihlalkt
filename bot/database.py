from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Iterator

from bot.models import Department, DepartmentResponsible, DepartmentRules, Personnel
from bot.time_utils import format_time, parse_hhmm


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS departments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    api_key TEXT,
                    telegram_chat_id TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS department_rules (
                    department_id INTEGER PRIMARY KEY,
                    work_start_time TEXT,
                    pre_break_leave_time TEXT,
                    break_start_time TEXT,
                    break_end_time TEXT,
                    post_break_start_time TEXT,
                    work_end_time TEXT,
                    max_call_gap_minutes INTEGER,
                    is_configured INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS personnel (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (department_id, name),
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS personnel_leave_periods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    personnel_name TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS department_weekly_leaves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    weekday INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (department_id, weekday),
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS department_weekly_leave_cancellations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    leave_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (department_id, leave_date),
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS department_responsibles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (department_id, username),
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notified_violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    report_date TEXT NOT NULL,
                    personnel_name TEXT NOT NULL,
                    violation_key TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (department_id, report_date, personnel_name, violation_key),
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notified_dept_date ON notified_violations (department_id, report_date)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS personnel_meeting_periods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL,
                    personnel_name TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_leave_periods_dept ON personnel_leave_periods (department_id, start_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_meeting_periods_dept ON personnel_meeting_periods (department_id, start_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_personnel_dept ON personnel (department_id, is_active)"
            )

    def add_department(self, name: str, telegram_chat_id: str, api_key: str | None = None) -> Department:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO departments (name, api_key, telegram_chat_id)
                VALUES (?, ?, ?)
                """,
                (name.strip(), _clean_api_key(api_key), str(telegram_chat_id).strip()),
            )
            department_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO department_rules (department_id, is_configured) VALUES (?, 0)",
                (department_id,),
            )
        department = self.get_department(name)
        if department is None:
            raise RuntimeError("Departman kaydedildi ancak okunamadi.")
        return department

    def get_department(self, identifier: str | int) -> Department | None:
        query = (
            "SELECT * FROM departments WHERE id = ?"
            if isinstance(identifier, int)
            else "SELECT * FROM departments WHERE lower(name) = lower(?)"
        )
        with self.connect() as connection:
            row = connection.execute(query, (identifier,)).fetchone()
        return self._department_from_row(row) if row else None

    def list_departments(self, only_active: bool = False, chat_id: str | int | None = None) -> list[Department]:
        query = "SELECT * FROM departments WHERE 1=1"
        params: list[object] = []
        if only_active:
            query += " AND is_active = 1"
        if chat_id is not None:
            query += " AND telegram_chat_id = ?"
            params.append(str(chat_id))
        query += " ORDER BY name"
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._department_from_row(row) for row in rows]

    def set_department_active(self, identifier: str | int, active: bool) -> bool:
        department = self.get_department(identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                "UPDATE departments SET is_active = ? WHERE id = ?",
                (1 if active else 0, department.id),
            )
        return True

    def delete_department(self, identifier: str | int) -> bool:
        department = self.get_department(identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute("DELETE FROM departments WHERE id = ?", (department.id,))
        return True

    def update_department_api_key(self, identifier: str | int, api_key: str) -> bool:
        department = self.get_department(identifier)
        if department is None:
            return False
        cleaned = _clean_api_key(api_key)
        if not cleaned:
            return False
        with self.connect() as connection:
            connection.execute(
                "UPDATE departments SET api_key = ? WHERE id = ?",
                (cleaned, department.id),
            )
        return True

    def update_department_chat(self, identifier: str | int, telegram_chat_id: str) -> bool:
        department = self.get_department(identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                "UPDATE departments SET telegram_chat_id = ? WHERE id = ?",
                (str(telegram_chat_id).strip(), department.id),
            )
        return True

    def get_rules(self, department_id: int) -> DepartmentRules:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM department_rules WHERE department_id = ?",
                (department_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Departman kural kaydi bulunamadi.")
        return DepartmentRules(
            department_id=int(row["department_id"]),
            work_start_time=_parse_optional_time(row["work_start_time"]),
            pre_break_leave_time=_parse_optional_time(row["pre_break_leave_time"]),
            break_start_time=_parse_optional_time(row["break_start_time"]),
            break_end_time=_parse_optional_time(row["break_end_time"]),
            post_break_start_time=_parse_optional_time(row["post_break_start_time"]),
            work_end_time=_parse_optional_time(row["work_end_time"]),
            max_call_gap_minutes=int(row["max_call_gap_minutes"]) if row["max_call_gap_minutes"] is not None else None,
            is_configured=bool(row["is_configured"]),
        )

    def update_rules(
        self,
        identifier: str | int,
        work_start_time: str | None,
        pre_break_leave_time: str | None,
        break_start_time: str | None,
        break_end_time: str | None,
        post_break_start_time: str | None,
        work_end_time: str | None,
        max_call_gap_minutes: int | None,
    ) -> bool:
        department = self.get_department(identifier)
        if department is None:
            return False
        values = (
            _format_optional_time(work_start_time),
            _format_optional_time(pre_break_leave_time),
            _format_optional_time(break_start_time),
            _format_optional_time(break_end_time),
            _format_optional_time(post_break_start_time),
            _format_optional_time(work_end_time),
            int(max_call_gap_minutes) if max_call_gap_minutes is not None else None,
            department.id,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE department_rules
                SET work_start_time = ?,
                    pre_break_leave_time = ?,
                    break_start_time = ?,
                    break_end_time = ?,
                    post_break_start_time = ?,
                    work_end_time = ?,
                    max_call_gap_minutes = ?,
                    is_configured = 1
                WHERE department_id = ?
                """,
                values,
            )
            if cursor.rowcount == 0:
                connection.execute(
                    """
                    INSERT INTO department_rules (
                        work_start_time, pre_break_leave_time, break_start_time,
                        break_end_time, post_break_start_time, work_end_time,
                        max_call_gap_minutes, department_id, is_configured
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    values,
                )
        return True

    def add_personnel(self, department_identifier: str | int, name: str, extension: str | None) -> Personnel | None:
        department = self.get_department(department_identifier)
        if department is None:
            return None
        with self.connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO personnel (department_id, name, extension)
                    VALUES (?, ?, ?)
                    """,
                    (department.id, name.strip(), extension.strip() if extension else None),
                )
                personnel_id = int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None
        return self.get_personnel(personnel_id)

    def upsert_personnel(
        self,
        department_identifier: str | int,
        name: str,
        extension: str | None,
    ) -> tuple[Personnel | None, str]:
        """Personel yoksa ekler, varsa dahili/ad bilgisini gunceller.

        Returns: (personnel, action) action in added|updated|unchanged|error
        """
        department = self.get_department(department_identifier)
        if department is None:
            return None, "error"
        existing = self.find_personnel_by_name(department.id, name)
        clean_ext = extension.strip() if extension else None
        if existing is None:
            person = self.add_personnel(department.id, name, clean_ext)
            return person, "added" if person else "error"
        # Ayni kayit: dahili farkliysa guncelle
        if (existing.extension or None) == (clean_ext or None):
            return existing, "unchanged"
        with self.connect() as connection:
            connection.execute(
                "UPDATE personnel SET extension = ? WHERE id = ?",
                (clean_ext, existing.id),
            )
        return self.get_personnel(existing.id), "updated"

    def get_personnel(self, personnel_id: int) -> Personnel | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM personnel WHERE id = ?", (personnel_id,)).fetchone()
        return self._personnel_from_row(row) if row else None

    def find_personnel_by_name(self, department_id: int, name: str) -> Personnel | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM personnel
                WHERE department_id = ? AND lower(name) = lower(?)
                """,
                (department_id, name.strip()),
            ).fetchone()
        return self._personnel_from_row(row) if row else None

    def list_personnel(self, department_id: int, only_active: bool = True) -> list[Personnel]:
        query = "SELECT * FROM personnel WHERE department_id = ?"
        params: tuple[object, ...] = (department_id,)
        if only_active:
            query += " AND is_active = 1"
        query += " ORDER BY name"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._personnel_from_row(row) for row in rows]

    def delete_personnel(self, personnel_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM personnel WHERE id = ?", (personnel_id,))
        return cursor.rowcount > 0

    def set_personnel_active(self, personnel_id: int, active: bool) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE personnel SET is_active = ? WHERE id = ?",
                (1 if active else 0, personnel_id),
            )
        return cursor.rowcount > 0

    def has_active_leave(self, department_id: int, personnel_name: str, current_at: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM personnel_leave_periods
                WHERE department_id = ?
                  AND lower(personnel_name) = lower(?)
                  AND datetime(start_at) <= datetime(?)
                  AND end_at IS NULL
                LIMIT 1
                """,
                (department_id, personnel_name.strip(), current_at),
            ).fetchone()
        return row is not None

    def start_leave(self, department_identifier: str | int, personnel_name: str, start_at: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO personnel_leave_periods (department_id, personnel_name, start_at)
                VALUES (?, ?, ?)
                """,
                (department.id, personnel_name.strip(), start_at),
            )
        return True

    def end_leave(self, department_identifier: str | int, personnel_name: str, end_at: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE personnel_leave_periods
                SET end_at = ?
                WHERE department_id = ?
                  AND lower(personnel_name) = lower(?)
                  AND end_at IS NULL
                """,
                (end_at, department.id, personnel_name.strip()),
            )
        return cursor.rowcount > 0

    def list_leave_periods(self, department_id: int, report_date: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personnel_leave_periods
                WHERE department_id = ?
                  AND date(start_at) <= date(?)
                  AND (end_at IS NULL OR date(end_at) >= date(?))
                ORDER BY start_at
                """,
                (department_id, report_date, report_date),
            ).fetchall()
        return rows

    def list_active_leave_periods(self, department_id: int, current_at: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personnel_leave_periods
                WHERE department_id = ?
                  AND datetime(start_at) <= datetime(?)
                  AND end_at IS NULL
                ORDER BY personnel_name, start_at
                """,
                (department_id, current_at),
            ).fetchall()
        return rows

    def has_active_meeting(self, department_id: int, personnel_name: str, current_at: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM personnel_meeting_periods
                WHERE department_id = ?
                  AND lower(personnel_name) = lower(?)
                  AND datetime(start_at) <= datetime(?)
                  AND end_at IS NULL
                LIMIT 1
                """,
                (department_id, personnel_name.strip(), current_at),
            ).fetchone()
        return row is not None

    def start_meeting(self, department_identifier: str | int, personnel_name: str, start_at: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO personnel_meeting_periods (department_id, personnel_name, start_at)
                VALUES (?, ?, ?)
                """,
                (department.id, personnel_name.strip(), start_at),
            )
        return True

    def end_meeting(self, department_identifier: str | int, personnel_name: str, end_at: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE personnel_meeting_periods
                SET end_at = ?
                WHERE department_id = ?
                  AND lower(personnel_name) = lower(?)
                  AND end_at IS NULL
                """,
                (end_at, department.id, personnel_name.strip()),
            )
        return cursor.rowcount > 0

    def list_meeting_periods(self, department_id: int, report_date: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personnel_meeting_periods
                WHERE department_id = ?
                  AND date(start_at) <= date(?)
                  AND (end_at IS NULL OR date(end_at) >= date(?))
                ORDER BY start_at
                """,
                (department_id, report_date, report_date),
            ).fetchall()
        return rows

    def list_active_meeting_periods(self, department_id: int, current_at: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personnel_meeting_periods
                WHERE department_id = ?
                  AND datetime(start_at) <= datetime(?)
                  AND end_at IS NULL
                ORDER BY personnel_name, start_at
                """,
                (department_id, current_at),
            ).fetchall()
        return rows

    def add_department_weekly_leave(self, department_identifier: str | int, weekday: int) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO department_weekly_leaves (department_id, weekday)
                VALUES (?, ?)
                """,
                (department.id, int(weekday)),
            )
            connection.execute(
                "DELETE FROM department_weekly_leave_cancellations WHERE department_id = ?",
                (department.id,),
            )
        return True

    def delete_department_weekly_leave(self, department_identifier: str | int, weekday: int | None = None) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        query = "DELETE FROM department_weekly_leaves WHERE department_id = ?"
        params: tuple[object, ...] = (department.id,)
        if weekday is not None:
            query += " AND weekday = ?"
            params = (department.id, int(weekday))
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            connection.execute(
                "DELETE FROM department_weekly_leave_cancellations WHERE department_id = ?",
                (department.id,),
            )
        return cursor.rowcount > 0

    def list_department_weekly_leaves(self, department_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM department_weekly_leaves
                WHERE department_id = ?
                ORDER BY weekday
                """,
                (department_id,),
            ).fetchall()
        return rows

    def cancel_department_weekly_leave(self, department_identifier: str | int, leave_date: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO department_weekly_leave_cancellations (department_id, leave_date)
                VALUES (?, ?)
                """,
                (department.id, leave_date),
            )
        return True

    def is_department_weekly_leave(self, department_id: int, weekday: int, report_date: str | None = None) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM department_weekly_leaves
                WHERE department_id = ? AND weekday = ?
                """,
                (department_id, int(weekday)),
            ).fetchone()
            if row is None:
                return False
            if report_date is None:
                return True
            cancellation = connection.execute(
                """
                SELECT 1 FROM department_weekly_leave_cancellations
                WHERE department_id = ? AND leave_date = ?
                """,
                (department_id, report_date),
            ).fetchone()
        return cancellation is None

    def add_responsible(self, department_identifier: str | int, username: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO department_responsibles (department_id, username)
                VALUES (?, ?)
                """,
                (department.id, _normalize_username(username)),
            )
        return True

    def delete_responsible(self, department_identifier: str | int, username: str) -> bool:
        department = self.get_department(department_identifier)
        if department is None:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM department_responsibles
                WHERE department_id = ? AND lower(username) = lower(?)
                """,
                (department.id, _normalize_username(username)),
            )
        return cursor.rowcount > 0

    def list_responsibles(self, department_id: int) -> list[DepartmentResponsible]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM department_responsibles WHERE department_id = ? ORDER BY username",
                (department_id,),
            ).fetchall()
        return [
            DepartmentResponsible(
                id=int(row["id"]),
                department_id=int(row["department_id"]),
                username=str(row["username"]),
            )
            for row in rows
        ]

    def list_notified_violations(self, department_id: int, report_date: str) -> set[tuple[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT personnel_name, violation_key
                FROM notified_violations
                WHERE department_id = ? AND report_date = ?
                """,
                (department_id, report_date),
            ).fetchall()
        return {(str(row["personnel_name"]).casefold(), str(row["violation_key"])) for row in rows}

    def mark_notified_violations(
        self,
        department_id: int,
        report_date: str,
        violations: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    ) -> None:
        if not violations:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO notified_violations (
                    department_id, report_date, personnel_name, violation_key
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (department_id, report_date, personnel_name.casefold(), violation_key)
                    for personnel_name, violation_key in violations
                ],
            )

    def cleanup_old_notified_violations(self, before_date: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM notified_violations WHERE report_date < ?",
                (before_date,),
            )
            return cursor.rowcount

    @staticmethod
    def _department_from_row(row: sqlite3.Row) -> Department:
        api_key = row["api_key"]
        return Department(
            id=int(row["id"]),
            name=str(row["name"]),
            api_key=str(api_key) if api_key else None,
            telegram_chat_id=str(row["telegram_chat_id"]),
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _personnel_from_row(row: sqlite3.Row) -> Personnel:
        return Personnel(
            id=int(row["id"]),
            department_id=int(row["department_id"]),
            name=str(row["name"]),
            extension=str(row["extension"]) if row["extension"] else None,
            is_active=bool(row["is_active"]),
        )


def _parse_optional_time(value: str | None):
    if value is None:
        return None
    return parse_hhmm(value)


def _format_optional_time(value: str | None) -> str | None:
    if value is None:
        return None
    return format_time(parse_hhmm(value))


def _normalize_username(value: str) -> str:
    username = value.strip().lstrip("@")
    if not username:
        raise ValueError("Telegram kullanici adi bos olamaz.")
    return username


def _clean_api_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text or None
