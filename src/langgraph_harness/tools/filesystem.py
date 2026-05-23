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
    name: str = "Read"
    description: str = (
        "Lee un archivo completo o un rango de líneas (start_line..end_line). "
        "Devuelve el contenido con números de línea prefijados para facilitar la búsqueda. "
        "Usá esta tool ANTES de Edit para obtener el texto exacto de old_string."
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


class GlobInput(BaseModel):
    pattern: str = Field(
        description="Patrón glob. Soporta `**` para recursión. Ej: '**/*.py', 'src/*.ts', 'README*'."
    )
    path: str = Field(
        default=".",
        description="Directorio base donde buscar. Default: directorio actual.",
    )


class GlobTool(FileSystemTool):
    name: str = "Glob"
    description: str = (
        "Encuentra archivos por patrón glob (soporta `**` recursivo) y los devuelve "
        "ordenados por fecha de modificación (más reciente primero). Usá esta tool para "
        "DESCUBRIR archivos antes de leerlos o editarlos. Para buscar CONTENIDO dentro de "
        "los archivos usá Grep."
    )
    args_schema: type[BaseModel] = GlobInput
    risk: Risk = Risk.SAFE

    _MAX_RESULTS = 200

    def _run(self, pattern: str, path: str = ".") -> str:
        base = Path(path)
        if not base.exists():
            return self.error(f"'{path}' no existe.")
        if not base.is_dir():
            return self.error(f"'{path}' no es un directorio.", "Pasá un directorio como base.")

        matches = sorted(
            (p for p in base.glob(pattern) if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            return f"No hay archivos que matcheen '{pattern}' en '{path}'."

        shown = matches[: self._MAX_RESULTS]
        listing = "\n".join(str(p) for p in shown)
        header = f"[{len(matches)} archivo(s) para '{pattern}' en {path}"
        if len(matches) > self._MAX_RESULTS:
            header += f"; mostrando los {self._MAX_RESULTS} más recientes"
        return f"{header}]\n{listing}"


class SearchInFileInput(BaseModel):
    pattern: str = Field(description="Texto literal o regex a buscar")
    path: str = Field(
        default=".",
        description="Archivo o directorio donde buscar. Si es un directorio, busca "
        "recursivamente. Default: directorio actual.",
    )
    context_lines: int = Field(
        default=3,
        description="Cuántas líneas de contexto mostrar antes y después de cada match",
    )
    use_regex: bool = Field(default=False, description="Si es True, pattern es una regex")
    glob: str | None = Field(
        default=None,
        description="Filtro de archivos cuando path es un directorio. Ej: '*.py'. "
        "Si es None, busca en todos los archivos.",
    )


class SearchInFileTool(FileSystemTool):
    name: str = "Grep"
    description: str = (
        "Busca un patrón en un archivo o, si path es un directorio, recursivamente en todo "
        "el árbol. Devuelve las ocurrencias con número de línea (y nombre de archivo cuando "
        "es recursivo) más líneas de contexto. Usá esta tool para:\n"
        "  - Resolver ambigüedad en Edit (ver dónde aparece cada ocurrencia)\n"
        "  - Construir un old_string único incluyendo contexto circundante\n"
        "  - Verificar si algo existe antes de editarlo\n"
        "Para encontrar archivos por nombre usá Glob."
    )
    args_schema: type[BaseModel] = SearchInFileInput
    risk: Risk = Risk.SAFE

    def _run(
        self,
        pattern: str,
        path: str = ".",
        context_lines: int = 3,
        use_regex: bool = False,
        glob: str | None = None,
    ) -> str:
        target = Path(path)
        if not target.exists():
            return self.error(f"'{path}' no existe.")

        flags = [] if use_regex else ["-F"]
        cmd = ["grep", "-n", f"--context={context_lines}", *flags]
        if target.is_dir():
            cmd.append("-r")
            if glob:
                cmd.append(f"--include={glob}")
        cmd += [pattern, str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 1:
            return (
                f"No se encontró '{pattern}' en '{path}'. "
                "Revisá el patrón, ampliá el path, o leé el archivo completo con Read."
            )
        if result.returncode > 1:
            return self.error(f"al buscar: {result.stderr.strip()}")

        count = result.stdout.count("\n")
        return f"[{count} líneas de resultado para '{pattern}' en {path}]\n{result.stdout}"


class WriteFileInput(BaseModel):
    file_path: str = Field(description="Ruta al archivo a crear o sobreescribir")
    content: str = Field(description="Contenido completo del archivo")


class WriteFileTool(FileSystemTool):
    name: str = "Write"
    description: str = (
        "Crea un archivo nuevo o sobreescribe uno existente con el contenido dado. "
        "Usá Edit para cambios puntuales. Usá Write solo para crear archivos nuevos "
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
        "Si falla por ambigüedad, usá Grep para ver las ocurrencias y ampliá "
        "old_string con más líneas de contexto hasta que sea único."
    )
    new_string: str = Field(description="Texto que reemplaza a old_string")
    replace_all: bool = Field(default=False, description="Reemplaza todas las ocurrencias")


class EditFileTool(FileSystemTool):
    name: str = "Edit"
    description: str = (
        "Edita un archivo reemplazando old_string por new_string. "
        "new_string puede ser más largo (inserción), más corto (borrado parcial), "
        "o vacío (borrado completo del fragmento). "
        "Para insertar líneas nuevas, incluí las líneas circundantes en old_string "
        "como ancla e intercalalas en new_string. "
        "Siempre leé el archivo con Read primero para obtener el texto exacto. "
        "Si old_string es ambiguo, usá Grep para resolverlo."
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
        if err := self._require_file(path, file_path, "Usá Write para crearlo, o verificá la ruta."):
            return err

        content = path.read_text(encoding="utf-8")
        count = content.count(old_string)

        if count == 0:
            return self.error(
                f"old_string no encontrado en '{file_path}'.",
                "Usá Read para ver el contenido actual y copiá el texto exacto, "
                "respetando espacios e indentación.",
            )

        if count > 1 and not replace_all:
            return self.error(
                f"old_string aparece {count} veces — es ambiguo.",
                "Usá Grep para ver dónde aparece cada ocurrencia, luego ampliá "
                "old_string con líneas de contexto únicas para identificar solo la que querés "
                "editar. O pasá replace_all=True para reemplazarlas todas.",
            )

        new_content = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(new_content, encoding="utf-8")
        replaced = count if replace_all else 1
        return f"OK — {replaced} reemplazo(s) en '{file_path}'."
