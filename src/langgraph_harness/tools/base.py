"""
Jerarquía de clases base para las tools del harness.

    BaseTool (LangChain)
    └── HarnessTool        ← metadata (risk, category) + error helper
            ├── FileSystemTool   ← _require_file compartido
            └── ControlTool      ← tools que controlan el agente (interrupt/resume)

Filosofía: los errores se devuelven como strings-instrucción para el LLM,
nunca como excepciones.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool


class Risk(str, Enum):
    SAFE = "safe"               # solo lectura, sin efectos secundarios
    REVERSIBLE = "reversible"   # escribe, pero se puede deshacer (git, backup)
    DESTRUCTIVE = "destructive" # difícil de revertir


class HarnessTool(BaseTool):
    risk: Risk = Risk.SAFE
    category: str = "general"

    @staticmethod
    def error(msg: str, hint: str = "") -> str:
        return f"ERROR: {msg} {hint}".rstrip()

    def bind_harness(self, harness: Any) -> None:
        """
        Hook que el Harness llama sobre cada tool al construirse, pasándose a sí mismo.
        La mayoría de las tools lo ignoran; las que necesitan al harness (p. ej. Task,
        que lanza sub-agentes) lo sobreescriben para capturar la referencia.
        """


class FileSystemTool(HarnessTool):
    category: str = "filesystem"

    def _require_file(self, path: Path, file_path: str, hint: str = "") -> str | None:
        if not path.exists():
            return self.error(f"'{file_path}' no existe.", hint)
        return None


class ControlTool(HarnessTool):
    """
    Tool que controla el flujo del agente en vez de tocar el filesystem.

    Si `interrupts` es True, el harness la trata especialmente: en vez de
    ejecutar `_run`, llama `interrupt_payload()` para suspender el grafo
    (wait no-bloqueante) y `resume_message()` al reanudar.
    """

    category: str = "control"
    interrupts: bool = False

    def interrupt_payload(self, **args: Any) -> dict:
        raise NotImplementedError("Las tools con interrupts=True deben implementar interrupt_payload()")

    def resume_message(self, payload: dict) -> str:
        raise NotImplementedError("Las tools con interrupts=True deben implementar resume_message()")
