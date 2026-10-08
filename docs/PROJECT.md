# Kernel Renders Agent — PROJECT.md

> Referencia técnica breve del agente, verificada contra el código de este repo. El
> detalle operativo (wizard paso a paso, convención de pases UV, tools de Blender,
> logs) está en el [README](../README.md). La documentación del sistema completo
> (web, API, datos, estado y pendientes) vive en `docs/PROJECT.md` del repo privado
> `kernelstudioby/Kernel-Renders`.

| | |
|---|---|
| **Última revisión** | 2026-10-07 |
| **Commit verificado** | `8c51d1c` (rama `main`) |
| **Versión** | `0.7.0` (`pyproject.toml`; tag `agent-v0.7.0`) |
| **Historial de cambios** | [`docs/CHANGELOG.md`](./CHANGELOG.md) |
| **Decisiones** | [`docs/DECISIONES.md`](./DECISIONES.md) |

---

## 1. Qué hace

Servicio Python que corre en las PCs con GPU de Kernel Renders. Pregunta al servidor
web si hay trabajo (poll), reclama el job, lo ejecuta en local y reporta progreso y
resultados:

- **Carril Blender:** renders con Blender headless (`blender --background --python …`)
  a partir de un plan de tools (`kernel_scripts`), contra los `.blend` de una carpeta
  local (library).
- **Carril UV (UV Lab V2):** composición 2D de pases UV pre-renderizados con
  `cv2` + `numpy`, **sin Blender**; incluye un preview en vivo solo-loopback.
- **Export Pack:** exportación de PSD con `psd-tools` (dependencia opcional).
- **Catálogo:** escanea la library, los PSD y los productos UV y reporta solo
  metadatos y miniaturas al servidor.

Todas las conexiones son **salientes** (HTTPS al servidor y subidas por URL firmada).
El único puerto que abre es el del preview UV, ligado a `127.0.0.1`.

## 2. Requisitos

- Python **3.13** recomendado en Windows (`>=3.10,<3.14`; 3.14 no tiene wheel de
  OpenEXR para Windows).
- **Blender 5.2 LTS** (obligatorio; ver D-004 en [DECISIONES](./DECISIONES.md)).
- GPU compatible con OptiX / CUDA / HIP / oneAPI (recomendada).
- Salida a internet por HTTPS (443).
- Opcional: `psd-tools` para Export Pack; carpeta local `Productos` para UV Lab.

## 3. Instalación y uso

```powershell
git clone https://github.com/kernelstudioby/kernel-renders-agent.git
cd kernel-renders-agent
py -3.13 -m pip install -e .
py -3.13 -m kernel_agent setup     # wizard: URL, API key, Blender, carpetas, GPU
py -3.13 -m kernel_agent run       # arranca el agente (dejar la ventana abierta)
```

Otros comandos: `status`, `doctor` y `version` (CLI con `click`, opción global
`--log-level`). También existe el script `kernel-agent` declarado en
`pyproject.toml`. La instalación es editable: para actualizar basta `git pull` y
reiniciar (`pip install -e .` solo si cambian dependencias). No hay auto-update.

## 4. Configuración

El wizard guarda `config.json` en la carpeta de usuario (`platformdirs`):
`%LOCALAPPDATA%\KernelRendersAgent\` en Windows, `~/Library/Application Support/KernelRendersAgent/`
en macOS y `~/.config/KernelRendersAgent/` en Linux (permisos 600 en Unix).

**Variables de entorno** que sobrescriben la config si están exportadas en el proceso
(`config.py`; solo nombres):

| Variable | Campo de config |
|---|---|
| `KERNEL_RENDERS_SERVER_URL` | `server_url` |
| `KERNEL_RENDERS_API_KEY` | `api_key` |
| `AGENT_NAME` | `agent_name` |
| `BLENDER_BIN` | `blender_bin` |
| `LIBRARY_DIR` | `library_dir` |
| `OUTPUT_DIR` | `output_dir` |
| `PSDS_DIR` | `psds_dir` |
| `UV_PRODUCTS_DIR` | `uv_products_dir` |
| `UV_CACHE_MAX_MB` | `uv_cache_max_mb` |
| `UV_PREVIEW_PORT` | `uv_preview_port` |
| `POLL_INTERVAL_SECONDS` | `poll_interval_seconds` |

El agente **no lee archivos `.env`** aunque `.env.example` y el docstring de
`load_config` lo sugieran (`python-dotenv` está declarado pero sin uso). Además usa
`LOCALAPPDATA` para ubicar cachés locales.

## 5. Comunicación con la web

- **Una sola URL de servidor:** `server_url`, por defecto
  `https://kernel-renders-web.vercel.app` (configurable con
  `KERNEL_RENDERS_SERVER_URL`, p. ej. `http://localhost:3000` para desarrollo).
- **HTTP con `httpx`** y header `x-api-key` en todas las llamadas (timeout 30 s). La API
  key la crea un admin en la web y se puede revocar; un `401` detiene el agente.
- **Poll de jobs:** `GET /api/agent/poll` cada `poll_interval_seconds` (default 5)
  hace heartbeat y devuelve el siguiente job; luego `claim` → `progress` →
  `complete`. Mientras corre un render, un heartbeat aparte mantiene al agente online
  y un watcher consulta `/api/agent/jobs/:id/status` para detectar cancelaciones.
- **Archivos:** el servidor entrega URLs firmadas (`upload-url`) y el agente sube los
  resultados directo a Storage con `PUT`. El agente **no tiene llaves de Supabase**.
- Endpoints completos en el docstring de `src/kernel_agent/api_client.py` y en la
  sección "Cómo funciona" del [README](../README.md).

## 6. Estructura de carpetas

```
kernel-renders-agent/
├── README.md · pyproject.toml · .env.example · smoke_test.py
├── docs/
│   └── PROJECT.md · CHANGELOG.md · DECISIONES.md
├── src/
│   ├── kernel_agent/      proceso del agente: CLI, config, cliente HTTP, daemon (carril
│   │                      Blender + heartbeat), executor, storage, escaneos, carril UV y
│   │                      preview loopback
│   └── kernel_scripts/    tools que corren dentro de Blender (swap_label, set_cap_color,
│                          set_view_layer, inspect_scene, render_views,
│                          apply_material_overrides…) y export_psd / uv_retexture
└── tests/                 pruebas del carril UV (pytest)
```

Detalle archivo por archivo en la sección "Estructura" del [README](../README.md).

## 7. Versiones y pendientes

Todo cambio funcional lleva bump de `version` en `pyproject.toml` y tag anotado
`agent-vX.Y.Z` (ver D-008). Los pendientes conocidos están en la sección
"Pendientes conocidos" del [README](../README.md).
