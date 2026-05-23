"""
Permission layer: decide si una tool puede ejecutarse según su nivel de riesgo.

El harness consulta la política ANTES de ejecutar cada tool. Las tools no saben
nada de permisos — la decisión vive acá, separada de la lógica de la tool.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from .tools.base import HarnessTool, Risk


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


AskFn = Callable[[HarnessTool, dict], bool]


def _default_ask(tool: HarnessTool, args: dict) -> bool:
    answer = input(f"¿Permitir {tool.name}({args})? [y/N] ")
    return answer.strip().lower() in ("y", "yes", "s", "si", "sí")


class PermissionPolicy:
    DEFAULT_RULES = {
        Risk.SAFE: Decision.ALLOW,
        Risk.REVERSIBLE: Decision.ALLOW,
        Risk.DESTRUCTIVE: Decision.ASK,
    }

    def __init__(
        self,
        rules: dict[Risk, Decision] | None = None,
        ask_fn: AskFn | None = None,
    ) -> None:
        self.rules = {**self.DEFAULT_RULES, **(rules or {})}
        self.ask_fn = ask_fn or _default_ask

    def check(self, tool: HarnessTool, args: dict) -> bool:
        decision = self.rules.get(tool.risk, Decision.ASK)
        if decision is Decision.ALLOW:
            return True
        if decision is Decision.DENY:
            return False
        return self.ask_fn(tool, args)


def allow_all() -> PermissionPolicy:
    return PermissionPolicy({risk: Decision.ALLOW for risk in Risk})
