"""DEV-1103: instala en la librería un producto (.zip) subido desde Library.

El server entrega la tarea en el poll con una liga firmada de descarga. Aquí se
descarga el .zip, se revisa que su contenido no pueda escribir fuera de la
carpeta de librería, se descomprime en una carpeta temporal y se mueve como
carpeta del producto. Si ya existe una con ese nombre, se guarda como copia
("Nombre (2)"). Corre en un hilo aparte para no frenar los renders.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from .api_client import ApiClient, ApiError

log = logging.getLogger("kernel-agent.library-install")

# Defensas contra zips malformados o "bombas": tamaño total descomprimido,
# número de archivos y proporción de compresión.
MAX_UNCOMPRESSED_BYTES = 50 * 1024**3
MAX_FILES = 20_000
MAX_RATIO = 200
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
IGNORED = {"__MACOSX", ".DS_Store", "Thumbs.db"}


class InstallError(Exception):
    """Error con un mensaje que se muestra tal cual en la página."""


def _clean_name(name: str) -> str:
    cleaned = INVALID_CHARS.sub("_", name).strip(" .")
    return cleaned[:120] or "producto"


def _members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """Valida y normaliza las entradas del zip; rechaza rutas absolutas o con '..'."""
    out: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    total = 0
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        parts = [p for p in path.parts if p not in ("", ".")]
        if not parts:
            continue
        if raw.startswith("/") or ".." in parts or ":" in parts[0]:
            raise InstallError(f"El .zip trae una ruta no permitida: {info.filename}")
        if parts[0] in IGNORED or parts[-1] in IGNORED:
            continue
        if not info.is_dir():
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 50 * 1024**2:
                raise InstallError(f"El .zip parece dañado o inflado artificialmente ({info.filename}).")
        out.append((info, PurePosixPath(*parts)))
    if len(out) > MAX_FILES:
        raise InstallError(f"El .zip trae demasiados archivos ({len(out)}).")
    if total > MAX_UNCOMPRESSED_BYTES:
        raise InstallError("El contenido del .zip pasa de 50 GB descomprimido.")
    return out


def _layout(members: list[tuple[zipfile.ZipInfo, PurePosixPath]], zip_stem: str) -> tuple[str, int]:
    """Nombre del producto y cuántos niveles quitar de cada ruta.

    Si todo viene dentro de una sola carpeta, esa carpeta es el producto. Si
    los archivos vienen sueltos, el producto se llama como el .zip.
    """
    tops = {p.parts[0] for _, p in members}
    single_dir = len(tops) == 1 and all(len(p.parts) > 1 or info.is_dir() for info, p in members)
    if single_dir:
        return _clean_name(next(iter(tops))), 1
    return _clean_name(zip_stem), 0


def _unique_dir(library: Path, name: str) -> Path:
    candidate = library / name
    n = 2
    while candidate.exists():
        candidate = library / f"{name} ({n})"
        n += 1
    return candidate


def install_zip(zip_path: Path, file_name: str, library_dir: str) -> str:
    """Descomprime el producto en la librería y devuelve el nombre de su carpeta."""
    library = Path(library_dir)
    if not library_dir or not library.is_dir():
        raise InstallError("Este equipo no tiene configurada una carpeta de librería válida.")
    if not zipfile.is_zipfile(zip_path):
        raise InstallError("El archivo no es un .zip válido.")

    with zipfile.ZipFile(zip_path) as zf:
        members = _members(zf)
        if not members:
            raise InstallError("El .zip está vacío.")
        name, strip = _layout(members, Path(file_name).stem)
        files = [(info, PurePosixPath(*p.parts[strip:])) for info, p in members if len(p.parts) > strip]
        if not any(not info.is_dir() and rel.suffix.lower() == ".blend" and len(rel.parts) == 1 for info, rel in files):
            raise InstallError(
                "El .zip no trae ningún .blend en la carpeta del producto. "
                "Debe venir como Producto/archivo.blend (más sus texturas)."
            )

        tmp = library / f".kr_install_{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        try:
            for info, rel in files:
                target = tmp.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            final = _unique_dir(library, name)
            tmp.rename(final)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return final.name


def _download(url: str, dest: Path) -> None:
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
        if r.status_code != 200:
            raise InstallError(f"No se pudo descargar el .zip (HTTP {r.status_code}).")
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(4 * 1024 * 1024):
                f.write(chunk)


class LibraryInstaller:
    """Atiende una instalación a la vez en segundo plano."""

    def __init__(self, client: ApiClient, library_dir: str):
        self.client = client
        self.library_dir = library_dir
        self._current: str | None = None
        self._lock = threading.Lock()

    def submit(self, task: dict[str, Any]) -> None:
        install_id = str(task.get("id", ""))
        with self._lock:
            if not install_id or self._current is not None:
                return
            self._current = install_id
        threading.Thread(target=self._run, args=(task,), daemon=True, name="library-install").start()

    def _run(self, task: dict[str, Any]) -> None:
        install_id = str(task["id"])
        file_name = str(task.get("file_name") or "producto.zip")
        tmp_zip = Path(self.library_dir or ".") / f".kr_download_{install_id[:8]}.zip"
        try:
            log.info("Instalando producto %s (%s)", file_name, install_id)
            self.client.report_library_install(install_id, "downloading")
            _download(str(task["download_url"]), tmp_zip)
            self.client.report_library_install(install_id, "extracting")
            name = install_zip(tmp_zip, file_name, self.library_dir)
            self.client.report_library_install(install_id, "done", installed_name=name)
            log.info("Producto instalado en la librería: %s", name)
        except (InstallError, zipfile.BadZipFile) as e:
            log.error("Instalación %s falló: %s", install_id, e)
            self._report_error(install_id, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Instalación %s falló: %s", install_id, e)
            self._report_error(install_id, f"Error en el equipo: {e}")
        finally:
            tmp_zip.unlink(missing_ok=True)
            with self._lock:
                self._current = None

    def _report_error(self, install_id: str, message: str) -> None:
        try:
            self.client.report_library_install(install_id, "error", error=message)
        except ApiError:
            pass
