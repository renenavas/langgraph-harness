"""Tools de control del agente: ScheduleWakeup, Task."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from .base import ControlTool, Risk


class WaitInput(BaseModel):
    seconds: float = Field(
        description="Segundos a esperar. Máximo recomendado: 30. Para esperas largas preferí "
        "reintentos escalonados (1s, 2s, 4s) en vez de un sleep largo.",
        ge=0.1,
        le=60.0,
    )
    reason: str = Field(
        description="Por qué estás esperando. Aparece en el log para que el usuario entienda "
        "qué está pasando. Ej: 'rate limit alcanzado', 'esperando que el archivo sea escrito'."
    )


class WaitTool(ControlTool):
    name: str = "ScheduleWakeup"
    description: str = (
        "Agenda una reanudación: suspende el grafo y lo despierta dentro de N segundos. "
        "Con un wakeup_store la cita se persiste y sobrevive a que el proceso se apague. Usá esta tool cuando:\n"
        "  - Una operación necesita tiempo antes de poder reintentarse (rate limit, archivo en proceso de escritura)\n"
        "  - Querés hacer polling: intentá → fallá → esperá → reintentá\n"
        "Siempre incluí un reason descriptivo. Preferí esperas cortas con reintentos "
        "escalonados antes que un sleep largo único."
    )
    args_schema: type[BaseModel] = WaitInput
    risk: Risk = Risk.SAFE
    interrupts: bool = True

    def interrupt_payload(self, seconds: float, reason: str, **_) -> dict:
        return {"type": "wait", "wait_seconds": float(seconds), "reason": reason}

    def resume_message(self, payload: dict) -> str:
        return f"OK — esperé {payload['wait_seconds']}s ({payload['reason']}). Podés continuar."

    def _run(self, seconds: float, reason: str) -> str:
        # Fallback bloqueante para contextos sin soporte de interrupt
        # (p. ej. un AgentExecutor clásico). El Harness usa interrupt_payload().
        time.sleep(seconds)
        return self.resume_message({"wait_seconds": seconds, "reason": reason})


class TaskInput(BaseModel):
    description: str = Field(description="Resumen corto (3-5 palabras) de la subtarea.")
    prompt: str = Field(
        description="Instrucción completa y autocontenida para el sub-agente. NO ve esta "
        "conversación, así que incluí todo el contexto, rutas y criterios de éxito que necesite."
    )


class TaskTool(ControlTool):
    name: str = "Task"
    description: str = (
        "Lanza un sub-agente autónomo con su propio loop y contexto limpio para resolver una "
        "subtarea acotada, y devuelve solo su resultado final. Usalo para trabajo self-contained "
        "(buscar algo a fondo en el repo, una refactor puntual) sin llenar tu propio contexto con "
        "los pasos intermedios. El sub-agente tiene las tools de filesystem y Bash, pero NO puede "
        "lanzar otros sub-agentes ni agendar wakeups. Pasale TODO el contexto en `prompt`."
    )
    args_schema: type[BaseModel] = TaskInput
    risk: Risk = Risk.SAFE  # el spawn es inocuo; el Bash del sub-agente sigue pasando por la policy

    _harness: Any = PrivateAttr(default=None)

    def bind_harness(self, harness: Any) -> None:
        self._harness = harness

    def _run(self, description: str, prompt: str) -> str:
        if self._harness is None:
            return self.error(
                "Task no está conectada a un harness.",
                "Esta tool solo funciona dentro de un Harness, que la conecta al construirse.",
            )
        result = self._harness.spawn_subagent(prompt)
        return f"[sub-agente terminó: {description}]\n{result}"
