"""Tools de sistema: ejecución de comandos shell."""

from __future__ import annotations

import subprocess

from pydantic import BaseModel, Field

from .base import HarnessTool, Risk

_MAX_OUTPUT = 30_000


class BashInput(BaseModel):
    command: str = Field(
        description="Comando shell a ejecutar. Se corre con `bash -c`, así que soporta "
        "pipes, redirecciones y variables. Citá las rutas con espacios."
    )
    timeout: int = Field(
        default=120,
        ge=1,
        le=600,
        description="Timeout en segundos (máx 600). Si el comando lo excede, se mata.",
    )
    cwd: str | None = Field(
        default=None,
        description="Directorio de trabajo. Si es None, usa el directorio actual del proceso.",
    )


class BashTool(HarnessTool):
    name: str = "Bash"
    description: str = (
        "Ejecuta un comando shell con `bash -c` y devuelve stdout+stderr combinados más el "
        "exit code. Un exit code != 0 NO es una excepción: se devuelve la salida para que "
        "leas el error y reintentes. Para leer o editar archivos preferí Read/Edit; "
        "usá Bash para git, builds, tests, listados de directorios, etc."
    )
    args_schema: type[BaseModel] = BashInput
    risk: Risk = Risk.DESTRUCTIVE
    category: str = "system"

    def _run(self, command: str, timeout: int = 120, cwd: str | None = None) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return self.error(
                f"el comando excedió el timeout de {timeout}s.",
                "Acortá el comando, dividilo en pasos, o subí el timeout.",
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            return self.error(f"no se pudo ejecutar: {exc}.")

        output = (result.stdout + result.stderr).strip()
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n... [truncado — {len(output)} chars en total]"

        header = f"[exit {result.returncode}]"
        return f"{header}\n{output}" if output else header
