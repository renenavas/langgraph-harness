"""
Constructor de un Harness durable: SqliteSaver (checkpoints) + WakeupStore (citas).

Tanto el proceso que agenda un wait como el worker que lo reanuda DEBEN construir
el harness igual (mismo modelo, mismas tools, mismo db_dir). Por eso ambos pasan
por acá: una sola fuente de verdad para "cómo es este agente".
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from .harness import Harness
from .permissions import PermissionPolicy, allow_all
from .registry import ToolRegistry
from .scheduler import WakeupStore
from .tools import DEFAULT_TOOLS


def default_db_dir() -> Path:
    return Path(os.environ.get("HARNESS_DB_DIR", Path.home() / ".langgraph-harness"))


def build_default_harness(
    *,
    db_dir: str | os.PathLike | None = None,
    model: str = "claude-sonnet-4-6",
    system_prompt: str | None = None,
    policy: PermissionPolicy | None = None,
    verbose: bool = True,
) -> Harness:
    path = Path(db_dir) if db_dir else default_db_dir()
    path.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path / "checkpoints.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    store = WakeupStore(str(path / "wakeups.db"))

    return Harness(
        registry=ToolRegistry(DEFAULT_TOOLS),
        policy=policy or allow_all(),
        system_prompt=system_prompt,
        model=model,
        checkpointer=checkpointer,
        wakeup_store=store,
        verbose=verbose,
    )
