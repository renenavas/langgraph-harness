"""Tools de filesystem: read, search, write, edit."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .base import FileSystemTool, Risk


class ReadFileInput(BaseModel):
    file_path: str = Field(description="Ruta al archivo")
    start_line: int = Field(default=1, description="Primera línea a leer (1-indexed, inclusive)")
    end_line: int | None = Field(
        default=None,
        description="Última línea a leer (inclusive). Si es None, lee hasta el final.",
    )


class ReadFileTool(FileSystemTool):
    name: str = "read_file"
    description: str = (
        "Lee un archivo completo o un rango de líneas (start_line..end_line). "
        "Devuelve el contenido con números de línea prefijados para facilitar la búsqueda. "
        "Usá esta tool ANTES de edit_file para obtener el texto exacto de old_string."
    )
    args_schema: type[BaseModel] = ReadFileInput
    risk: Risk = Risk.SAFE

    def _run(self, file_path: str, start_line: int = 1, end_line: int | None = None) -> str:
        path = Path(file_path)
        if err := self._require_file(path, file_path):
            return err

        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        total = len(lines)

        s = max(1, start_line)
        e = min(total, end_line) if end_line is not None else total

        if s > total:
            return self.error(
                f"start_line={s} supera el total de líneas ({total}).",
                f"El archivo tiene {total} líneas.",
            )

        chunk = lines[s - 1 : e]
        numbered = "".join(f"{s + i}\t{line}" for i, line in enumerate(chunk))
        return f"[{file_path} — líneas {s}-{e} de {total}]\n{numbered}"


class SearchInFileInput(BaseModel):
    file_path: str = Field(description="Ruta al archivo donde buscar")
    pattern: str = Field(description="Texto literal o regex a buscar")
    context_lines: int = Field(
        default=3,
        description="Cuántas líneas de contexto mostrar antes y después de cada match",
    )
    use_regex: bool = Field(default=False, description="Si es True, pattern es una regex")


class SearchInFileTool(FileSystemTool):
    name: str = "search_in_file"
    description: str = (
        "Busca un patrón en un archivo y devuelve las ocurrencias con número de línea "
        "y líneas de contexto. Usá esta tool para:\n"
        "  - Resolver ambigüedad en edit_file (ver dónde aparece cada ocurrencia)\n"
        "  - Construir un old_string único incluyendo contexto circundante\n"
        "  - Verificar si algo existe antes de editarlo"
    )
    args_schema: type[BaseModel] = SearchInFileInput
    risk: Risk = Risk.SAFE

    def _run(
        self,
        file_path: str,
        pattern: str,
        context_lines: int = 3,
        use_regex: bool = False,
    ) -> str:
        flag = [] if use_regex else ["-F"]
        cmd = ["grep", "-n", f"--context={context_lines}", *flag, pattern, file_path]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 1:
            return (
                f"No se encontró '{pattern}' en '{file_path}'. "
                "Revisá el patrón o leé el archivo completo con read_file."
            )
        if result.returncode > 1:
            return self.error(f"al buscar: {result.stderr.strip()}")

        count = result.stdout.count("\n")
        return f"[{count} líneas de resultado para '{pattern}' en {file_path}]\n{result.stdout}"


class WriteFileInput(BaseModel):
    file_path: str = Field(description="Ruta al archivo a crear o sobreescribir")
    content: str = Field(description="Contenido completo del archivo")


class WriteFileTool(FileSystemTool):
    name: str = "write_file"
    description: str = (
        "Crea un archivo nuevo o sobreescribe uno existente con el contenido dado. "
        "Usá edit_file para cambios puntuales. Usá write_file solo para crear archivos nuevos "
        "o cuando el cambio es tan grande que mandar el archivo entero es más limpio."
    )
    args_schema: type[BaseModel] = WriteFileInput
    risk: Risk = Risk.REVERSIBLE

    def _run(self, file_path: str, content: str) -> str:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content, encoding="utf-8")
        action = "sobreescrito" if existed else "creado"
        lines = content.count("\n") + 1
        return f"OK — archivo {action}: '{file_path}' ({lines} líneas)."


class EditFileInput(BaseModel):
    file_path: str = Field(description="Ruta al archivo a editar")
    old_string: str = Field(
        description="Texto exacto a usar como ancla. Debe aparecer exactamente una vez. "
        "Para insertar líneas nuevas, incluí las líneas vecinas como contexto y agregalas "
        "también en new_string. Para borrar, usá new_string vacío o más corto. "
        "Si falla por ambigüedad, usá search_in_file para ver las ocurrencias y ampliá "
        "old_string con más líneas de contexto hasta que sea único."
    )
    new_string: str = Field(description="Texto que reemplaza a old_string")
    replace_all: bool = Field(default=False, description="Reemplaza todas las ocurrencias")


class EditFileTool(FileSystemTool):
    name: str = "edit_file"
    description: str = (
        "Edita un archivo reemplazando old_string por new_string. "
        "new_string puede ser más largo (inserción), más corto (borrado parcial), "
        "o vacío (borrado completo del fragmento). "
        "Para insertar líneas nuevas, incluí las líneas circundantes en old_string "
        "como ancla e intercalalas en new_string. "
        "Siempre leé el archivo con read_file primero para obtener el texto exacto. "
        "Si old_string es ambiguo, usá search_in_file para resolverlo."
    )
    args_schema: type[BaseModel] = EditFileInput
    risk: Risk = Risk.REVERSIBLE

    def _run(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        path = Path(file_path)
        if err := self._require_file(path, file_path, "Usá write_file para crearlo, o verificá la ruta."):
            return err

        content = path.read_text(encoding="utf-8")
        count = content.count(old_string)

        if count == 0:
            return self.error(
                f"old_string no encontrado en '{file_path}'.",
                "Usá read_file para ver el contenido actual y copiá el texto exacto, "
                "respetando espacios e indentación.",
            )

        if count > 1 and not replace_all:
            return self.error(
                f"old_string aparece {count} veces — es ambiguo.",
                "Usá search_in_file para ver dónde aparece cada ocurrencia, luego ampliá "
                "old_string con líneas de contexto únicas para identificar solo la que querés "
                "editar. O pasá replace_all=True para reemplazarlas todas.",
            )

        new_content = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(new_content, encoding="utf-8")
        replaced = count if replace_all else 1
        return f"OK — {replaced} reemplazo(s) en '{file_path}'."
