"""Tools de control del agente: wait."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

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
    name: str = "wait"
    description: str = (
        "Pausa la ejecución por N segundos. Usá esta tool cuando:\n"
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
