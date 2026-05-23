"""
WakeupStore: cola durable de "despertares" en SQLite.

Reemplaza al threading.Timer en-proceso del Harness. En vez de dormir un hilo,
el harness escribe una fila (thread_id, resume_at, payload) acá y devuelve el
control. Un worker externo (worker.py) lee las filas vencidas y reanuda el grafo
desde el checkpoint. Así el proceso que agendó puede morir: la cita sobrevive.

Es el equivalente single-host de ScheduleWakeup: "anotá esto, despertame después".
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Wakeup:
    id: int
    thread_id: str
    resume_at: float
    payload: dict
    created_at: float

    @property
    def is_due(self) -> bool:
        return self.resume_at <= time.time()


class WakeupStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wakeups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id  TEXT NOT NULL,
                resume_at  REAL NOT NULL,
                payload    TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wakeups_resume_at ON wakeups(resume_at)"
        )
        self._conn.commit()

    def schedule(self, thread_id: str, resume_at: float, payload: dict) -> int:
        cur = self._conn.execute(
            "INSERT INTO wakeups (thread_id, resume_at, payload, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, resume_at, json.dumps(payload), time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def due(self, now: float | None = None) -> list[Wakeup]:
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT * FROM wakeups WHERE resume_at <= ? ORDER BY resume_at", (now,)
        ).fetchall()
        return [self._row_to_wakeup(r) for r in rows]

    def pending(self) -> list[Wakeup]:
        rows = self._conn.execute(
            "SELECT * FROM wakeups ORDER BY resume_at"
        ).fetchall()
        return [self._row_to_wakeup(r) for r in rows]

    def delete(self, wakeup_id: int) -> None:
        self._conn.execute("DELETE FROM wakeups WHERE id = ?", (wakeup_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_wakeup(row: sqlite3.Row) -> Wakeup:
        return Wakeup(
            id=row["id"],
            thread_id=row["thread_id"],
            resume_at=row["resume_at"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
        )
