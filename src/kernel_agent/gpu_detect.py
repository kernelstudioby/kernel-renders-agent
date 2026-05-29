"""Detecta GPU disponibles ejecutando Blender headless con un mini script.

Output del detect:
  {
    "backend": "OPTIX" | "CUDA" | "HIP" | "ONEAPI" | "CPU",
    "devices": ["NVIDIA GeForce RTX 5060", ...],
    "vendor": "NVIDIA" | "AMD" | "Intel" | "CPU"
  }
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

DETECT_SCRIPT = r"""
import json, sys, bpy
prefs = bpy.context.preferences.addons['cycles'].preferences
result = {"backend": "CPU", "devices": [], "vendor": "CPU", "blender_version": bpy.app.version_string}
for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        gpus = [d.name for d in prefs.devices if d.type != "CPU"]
        if gpus:
            result["backend"] = backend
            result["devices"] = gpus
            if "NVIDIA" in gpus[0] or backend in ("OPTIX", "CUDA"):
                result["vendor"] = "NVIDIA"
            elif "AMD" in gpus[0] or backend == "HIP":
                result["vendor"] = "AMD"
            elif backend == "ONEAPI":
                result["vendor"] = "Intel"
            break
    except (TypeError, AttributeError):
        continue
print("###GPU_INFO###" + json.dumps(result) + "###END###")
"""


def detect_gpu(blender_bin: str, timeout: float = 60.0) -> dict:
    """Ejecuta Blender headless con el script de detección. Devuelve dict
    con backend, devices, vendor, blender_version. Si falla, devuelve CPU.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(DETECT_SCRIPT)
        script_path = tf.name

    try:
        proc = subprocess.run(
            [blender_bin, "--background", "--python", script_path, "--python-exit-code", "1"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"backend": "CPU", "devices": [], "vendor": "CPU", "error": str(e)}
    finally:
        try:
            Path(script_path).unlink()
        except OSError:
            pass

    # Parse output buscando el marcador ###GPU_INFO###
    for line in proc.stdout.splitlines():
        if "###GPU_INFO###" in line and "###END###" in line:
            try:
                payload = line.split("###GPU_INFO###")[1].split("###END###")[0]
                return json.loads(payload)
            except (IndexError, json.JSONDecodeError):
                continue
    return {"backend": "CPU", "devices": [], "vendor": "CPU", "stderr_tail": proc.stderr[-500:]}
