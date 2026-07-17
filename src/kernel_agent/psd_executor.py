"""Executor para jobs de PSD (no usan Blender).

Cuando el plan contiene únicamente el tool `export_psd`, el daemon usa este
executor en lugar del de Blender. Es más rápido porque no levanta Blender ni
abre escenas .blend pesadas.

Estructura del result idéntica a `executor.ExecuteResult` para que el daemon
no tenga que diferenciar el upload de outputs.
"""

from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class PsdExecuteResult:
    success: bool
    duration_seconds: float
    steps_total: int
    steps_executed: int
    outputs: list[Path] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    failed_step: dict | None = None
    step_results: list[dict] = field(default_factory=list)


def execute_psd_plan(
    plan: list[dict],
    on_step: Callable[[int, int, str], None] | None = None,
    blender_bin: str | None = None,
) -> PsdExecuteResult:
    """Ejecuta un plan "no-Blender-executor": export_psd (psd-tools + Pillow)
    y uv_retexture (UV Lab — este sí necesita Blender, pero lo spawnea él
    mismo como subprocess en vez de depender de que el executor genérico ya
    tenga la escena cargada; ver kernel_scripts/uv_retexture.py).
    """
    start = time.time()
    log_lines: list[str] = []
    outputs: list[Path] = []
    step_results: list[dict] = []
    failed: dict | None = None
    total = len(plan)

    def _log(msg: str) -> None:
        log_lines.append(msg)
        print(msg, flush=True)

    if total == 0:
        return PsdExecuteResult(
            success=False,
            duration_seconds=0.0,
            steps_total=0,
            steps_executed=0,
            stdout="",
            stderr="",
            failed_step={"index": -1, "tool": "_setup", "error": "plan vacío"},
        )

    # Importar los tools disponibles
    tools: dict[str, Callable[..., dict]] = {}
    try:
        from kernel_scripts.psd_export import run_export_psd
        tools["export_psd"] = run_export_psd
        from kernel_scripts.uv_retexture import run_uv_retexture
        tools["uv_retexture"] = run_uv_retexture
    except Exception as e:
        tb = traceback.format_exc()
        return PsdExecuteResult(
            success=False,
            duration_seconds=time.time() - start,
            steps_total=total,
            steps_executed=0,
            stdout="\n".join(log_lines),
            stderr=tb,
            failed_step={"index": -1, "tool": "_import", "error": str(e)},
        )

    for i, step in enumerate(plan):
        tool_name = step.get("tool")
        args = step.get("args", {})
        _log(f"[step {i+1}/{total}] {tool_name}")
        if on_step:
            try:
                on_step(i + 1, total, tool_name or "")
            except Exception as e:  # noqa: BLE001
                _log(f"  on_step warn: {e}")
        fn = tools.get(tool_name or "")
        if fn is None:
            failed = {
                "index": i,
                "tool": tool_name or "?",
                "error": f"tool desconocido (disponibles: {list(tools)})",
            }
            break
        # uv_retexture necesita blender_bin (spawnea su propio subprocess de
        # Blender) — es config local del agent, no algo que el plan traiga.
        call_args = dict(args)
        if tool_name == "uv_retexture":
            if not blender_bin:
                failed = {"index": i, "tool": tool_name, "error": "blender_bin no configurado en el agent"}
                break
            call_args["blender_bin"] = blender_bin
        try:
            result = fn(**call_args)
        except Exception as e:  # noqa: BLE001
            failed = {"index": i, "tool": tool_name, "error": f"{type(e).__name__}: {e}"}
            sys.stderr.write(traceback.format_exc())
            break
        step_results.append({"tool": tool_name, "result": result})
        # Recolectar paths absolutos generados (clave 'outputs' opcional)
        for out_spec in result.get("outputs", []) or []:
            path_str = (
                out_spec.get("path") if isinstance(out_spec, dict) else str(out_spec)
            )
            if path_str:
                p = Path(path_str)
                if p.exists():
                    outputs.append(p)

    duration = time.time() - start
    return PsdExecuteResult(
        success=failed is None,
        duration_seconds=duration,
        steps_total=total,
        steps_executed=total if failed is None else (failed["index"]),
        outputs=outputs,
        stdout="\n".join(log_lines),
        stderr="",
        failed_step=failed,
        step_results=step_results,
    )


def is_psd_plan(plan: list[dict]) -> bool:
    """Detecta si un plan debe ejecutarse con este executor (no el de Blender
    genérico) — export_psd y uv_retexture, que manejan su propio ciclo de
    vida de Blender (o no lo necesitan en absoluto)."""
    if not plan:
        return False
    psd_tools = {"export_psd", "uv_retexture"}
    return all(step.get("tool") in psd_tools for step in plan)
