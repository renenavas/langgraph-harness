"""Registro de tools con filtros por categoría y nivel de riesgo."""

from __future__ import annotations

from collections.abc import Iterable

from .tools.base import HarnessTool, Risk


class ToolRegistry:
    def __init__(self, tools: Iterable[HarnessTool] | None = None) -> None:
        self._tools: dict[str, HarnessTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: HarnessTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Ya hay una tool registrada con el nombre '{tool.name}'")
        self._tools[tool.name] = tool

    def get(self, name: str) -> HarnessTool:
        if name not in self._tools:
            raise KeyError(f"No hay ninguna tool registrada con el nombre '{name}'")
        return self._tools[name]

    def all(self) -> list[HarnessTool]:
        return list(self._tools.values())

    def by_category(self, category: str) -> list[HarnessTool]:
        return [t for t in self._tools.values() if t.category == category]

    def by_risk(self, risk: Risk) -> list[HarnessTool]:
        return [t for t in self._tools.values() if t.risk == risk]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
