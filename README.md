# Kernel Renders Agent 0.7.0

Servicio Python que corre en las PCs con GPU de **Kernel Renders** (plataforma
interna de CGI de Beyond Design): ejecuta renders de Blender headless y
composiciones UV (UV Lab V2) que le manda la plataforma web, y reporta los
resultados. Se conecta **hacia afuera** (poll a la web y subida a Supabase
Storage); no abre puertos a internet.

> **Documentación del sistema completo** (arquitectura, API, modelo de datos,
> protocolo, estado y pendientes): `docs/PROJECT.md` del repo privado
> [`kernelstudioby/Kernel-Renders`](https://github.com/kernelstudioby/Kernel-Renders)
> (acceso solo para el equipo). Historial de cambios: `docs/CHANGELOG.md` de ese repo.

## Por qué este repo es público

Es **a propósito**. El agente se instala con `git clone` + `pip install -e .` en
PCs externas con GPU (Beyond, demos) que se conectan al servidor, y exigir
credenciales de un repo privado en cada máquina complicaría esas instalaciones.
Por eso aquí **no hay secretos ni lógica de negocio**: la API key de cada agente
vive solo en su `config.json` local (y es revocable desde la web), y el esquema
de datos, la API y los diagramas viven en el repo privado. Nunca subas tokens,
rutas privadas ni datos de clientes a este repo (decisión ADR-009 del repo web).

## Qué levanta `kernel-agent run`

Bajo una sola identidad (API key) corren estos componentes:

| Componente | Qué hace |
|---|---|
| **Carril Blender** (hilo principal) | Escanea `library_dir` (y `psds_dir`), hace poll, reclama jobs, ejecuta el plan en Blender headless y sube los resultados |
| **Heartbeat** | Mientras corre un job, hace heartbeat para que la web lo siga viendo online en renders largos |
| **Carril UV** (`UvWorker`, si hay carpeta de Productos) | Sincroniza el catálogo UV, ejecuta jobs `uv_compose` y atiende los previews remotos; **no usa Blender** |
| **Preview UV local** | Servidor HTTP solo-loopback en `http://127.0.0.1:8765` para que el navegador de esa misma PC refleje los sliders en vivo |

## Prerequisitos

- Python **3.13** recomendado en Windows (`3.10 <= Python < 3.14`). Python 3.14
  no se usa: OpenEXR no publica wheel de Windows para esa versión.
- **Blender 5.2 LTS (obligatorio, no solo "5.1+")** — KER3-40: escenas armadas
  con el addon Render Raw en Blender 5.2 pueden renderizar en NEGRO con
  `apply_postfx=true` si el agente corre una versión más vieja: Blender rompe
  en silencio los nodos de curva (`Float Curve`) del compositor al abrir un
  archivo de una versión más nueva en una más vieja. El wizard autodetecta
  Blender solo hasta 5.1 y `doctor` no valida la versión: escribe a mano la ruta
  de 5.2.
- GPU compatible con OptiX / CUDA / HIP / oneAPI (recomendado, no obligatorio).
- Para UV Lab: una carpeta local `Productos` con los pases pre-renderizados
  (convención más abajo).
- Acceso a internet de salida (HTTPS 443).

## Instalación

```powershell
git clone https://github.com/kernelstudioby/kernel-renders-agent.git
cd kernel-renders-agent
py -3.13 -m pip install -U pip
py -3.13 -m pip install -e .
```

Si ya lo tienes instalado (actualizar a una versión nueva):

```powershell
cd C:\ruta\a\kernel-renders-agent
git pull origin main
py -3.13 -m pip install -e .
```

Como la instalación es editable, `git pull` basta para el código Python;
`pip install -e .` solo es necesario si cambian las dependencias. No hay
auto-update: hay que reiniciar `kernel_agent run` después de actualizar.

> `psd-tools` **no** está en las dependencias: sin él, la tool `export_psd`
> (Export Pack) falla y los thumbnails de PSD se omiten. Instálalo aparte
> (`py -3.13 -m pip install psd-tools`) si esa PC procesa PSD.

## Configuración (una vez)

```powershell
py -3.13 -m kernel_agent setup
```

El wizard pregunta, en orden:

1. URL del servidor (default `https://kernel-renders-web.vercel.app`).
2. **API key** del agente (`kr_agent_…`). La crea un admin en la web
   (`/settings/agents` → nuevo agente) y el token **se muestra una sola vez**.
   Se valida con un poll de prueba y de ahí sale el nombre del agente. Ojo: la
   key se ve completa en pantalla; no compartas capturas.
3. Ruta a `blender.exe` (**5.2**).
4. Carpeta del library (`.blend` de Beyond).
5. Carpeta de output (renders).
6. Carpeta de productos UV (opcional; vacía desactiva el carril UV).
7. Puerto del preview UV (default `8765`; solo si hay carpeta UV).
8. Intervalo de polling en segundos (default `5`).
9. Detecta la GPU abriendo Blender headless (~30 s).

`psds_dir` **no** se pregunta en el wizard (solo con la variable `PSDS_DIR` o
editando `config.json`). Si `setup` se traba con caracteres raros (`←[36m`), usa
la consola clásica (`powershell.exe` o `cmd`), no `pwsh`.

**Dónde se guarda** (`config.json`; en Unix con permisos 600):

- Windows: `%LOCALAPPDATA%\KernelRendersAgent\config.json`
- macOS: `~/Library/Application Support/KernelRendersAgent/config.json`
- Linux: `~/.config/KernelRendersAgent/config.json`

**Variables de entorno** (solo nombres; sobrescriben la config **si están
exportadas en el entorno del proceso**; el agente **no lee archivos `.env`**
aunque `.env.example` lo sugiera): `KERNEL_RENDERS_SERVER_URL`,
`KERNEL_RENDERS_API_KEY`, `AGENT_NAME`, `BLENDER_BIN`, `LIBRARY_DIR`,
`OUTPUT_DIR`, `PSDS_DIR`, `UV_PRODUCTS_DIR`, `UV_CACHE_MAX_MB` (default 768),
`UV_PREVIEW_PORT` (default 8765), `POLL_INTERVAL_SECONDS` (default 5). Para
probar contra una web local: `KERNEL_RENDERS_SERVER_URL=http://localhost:3000`.

## Uso (CLI)

```powershell
py -3.13 -m kernel_agent status    # muestra la config actual
py -3.13 -m kernel_agent doctor    # Blender, GPU (~30 s) y conectividad
py -3.13 -m kernel_agent run       # arranca el agente; dejar la ventana abierta
py -3.13 -m kernel_agent version   # ⚠ hoy imprime 0.2.2 (ver "Pendientes")
```

Opción global `--log-level` (`DEBUG|INFO|WARNING|ERROR`, default `INFO`). El
agente vive mientras la ventana esté abierta (no hay servicio de Windows todavía).

En un arranque correcto aparecen `Render Agent online`, `UV lane online` y
`Preview UV local online · http://127.0.0.1:8765`, y el agente sale online en
`/settings/agents` con su versión de Blender.

## Cómo funciona

**Protocolo** (header `x-api-key` en todo; timeout 30 s; un `401` detiene el
agente): `GET /api/agent/poll` (heartbeat + siguiente job; envía `gpu`,
`blender`, `library`, `psds`, `capability` = `blender|uv|heartbeat` y
`uv_products`), `POST /api/agent/claim/:id` (`409` si otro agente lo tomó),
`POST /api/agent/progress/:id`, `POST /api/agent/upload-url/:id` (luego `PUT`
directo a Supabase Storage), `POST /api/agent/complete/:id`,
`GET /api/agent/jobs/:id/status` (detectar cancelación),
`POST /api/agent/catalog/uv/sync`, `POST /api/agent/library` (0.7.0), `POST /api/agent/thumbnail`,
`POST /api/agent/blend-download/:id/{upload-url,complete}` y
`GET /api/agent/uv-preview/pending` + `POST /api/agent/uv-preview/:id/complete`.
El agente **no envía su propia versión** al servidor (pendiente).

**Job Blender:** poll → claim → se baja al disco temporal lo que el plan
referencia por URL → se genera un runner `bpy` y se lanza
`blender --background --python …` → se parsea stdout para progreso, muestras y
ETA (máx. 1 reporte por segundo) → se suben solo los `.png/.jpg/.jpeg/.webp`
(los **EXR se quedan en disco local** para post-producción) → `complete`.
Cancelar desde la UI mata el proceso de Blender en segundos y el agente **no**
llama `complete` para no pisar el estado `cancelled`.

**Tools de Blender** (`kernel_scripts`): `swap_label`, `set_cap_color`,
`apply_material_overrides`, `set_active_view_layer`, `inspect_scene`, `render_one_view` (`frame`,
`apply_postfx`), `render_all_cameras`, `render_rotations` y `render_at_angle`.
Fuera de Blender: `export_psd` (psd-tools), `uv_retexture` (UV Lab V1) y
`uv_compose` (carril UV). Stubs no registrados: `render_seven_views`,
`export_pack`. Comportamientos que conviene conocer:

- `apply_postfx=true` respeta el compositor del `.blend` (equivale a
  "Composite"); en cada render se re-apuntan los nodos Render Layers al view
  layer activo (KER3-40).
- Si el `.blend` bloquea PNG (formato `OPEN_EXR_MULTILAYER`, muy común) se
  renderiza en una **scene fresh**: hereda motor, resolución, GPU, transparencia
  de vidrio, color management, world, cámara, frames, samples, ajustes de
  sampling/denoise (KER3-42) y la **visibilidad por view layer** (KER3-42).
- `set_cap_color`: con Base Color conectado a un nodo, actualiza el nodo Color
  o desconecta el link y fuerza el valor (KER3-42).
- `apply_material_overrides` (KER3-45): `{scene, overrides:[{object, slot,
  material}]}` asigna el material al slot solo durante el render (el `.blend`
  nunca se guarda) y falla listando las variantes disponibles si el objeto, el
  slot o el material no existen. Un solo paso cubre todos los renders del plan.
- `set_active_view_layer` nunca fuerza visibles los objetos que se ocultaron a
  mano.

**Componentes y variantes de material** (KER3-45): el mismo probe de Blender que
lee view layers, cámaras y fotogramas (`scene_metadata.py`) añade `collection` y
`components` a cada escena de `library`. Agrupa por el **prefijo del material**
(texto antes del primer `_`) los meshes de las colecciones del producto
(recursivo; salta `Drops` y las de luces); las variantes son todos los materiales
del `.blend` con ese prefijo (también los de Fake User sin asignar), sin
`Emissive_*`, `DropletMat*`, materiales con emisión ni el componente `Label`. El
color de muestra sigue el link del Base Color. Solo se reporta si algún
componente tiene 2+ variantes.

**Vistas por fotograma** (KER3-46): el mismo probe añade `frame_views`
(`[{frame, name, angle}]`; en el poll viaja compacto como `[frame, name, angle]`)
para los keyframes del turntable más los frames con
timeline marker (no usa `frame_start..frame_end`, que no refleja los keyframes).
`name` es el primer marker del frame, tal cual lo nombró el artista (`FRONT`,
`BACK`, `ESPECIAL`…) o `null`; `angle` es la rotación Z en grados de
`NULL_ANIMATOR` evaluada en ese frame (1 decimal) o `null` si el objeto no
existe. Sin markers ni `NULL_ANIMATOR` se reporta vacío; máximo 24 frames. Solo
sirve para mostrar y para la IA: el render sigue recibiendo el número de frame.

**Catálogo por POST** (0.7.0, KER3-46): el catálogo de escenas se manda a
`POST /api/agent/library` cuando cambia (hash) o cada 10 min, y el poll de Blender
omite `library`; el server enruta jobs con lo guardado. Si el server no tiene la
ruta (404/405) el agente lo recuerda y vuelve a mandar el catálogo en la query.

**Tamaño del poll (modo query):** la librería viaja en la query del GET (Vercel rechaza URLs
de ~14 KB). Si supera 12 000 caracteres, `api_client.poll` recorta escena por
escena, de la más pesada a la más ligera: primero `frame_views` y al final
`components` (que usa producción). En 0.6.0 se quitaba `components` de todas
las escenas a la vez y una PC con 11 escenas se quedó sin selector de
materiales (0.6.1).

### UV Lab (carril UV)

Cada **vista** es una subcarpeta de un **producto** dentro de `Productos/`. Los
pases se emparejan por **sufijo** del nombre, sin distinguir mayúsculas
(`.exr .png .jpg .jpeg .tif .tiff .bmp`):

| Pase | Rol |
|---|---|
| `PASS-uvpass` | Obligatorio: coordenadas UV |
| `PASS-all_white` (antes `base_label1`) | Pase "con arte" |
| `PASS-all_black` (antes `especular`) | Especular |
| `PASS-base_label0` | Fondo "sin etiqueta" (modo dual) |
| `PASS-track_matte-<Region>` | Regiones pintables (p. ej. Cap) |
| `PASS-liquid-<nombre>` / `PASS-liquid_<nombre>` | Variantes de color de líquido |
| `PASS-edges` | Opcional: antialiasing dirigido al exportar |
| `PASS-opacity` | Opcional: negro = transparente, blanco = visible; define el alfa final (KER3-36); sin él se usa el alfa de `all_white` |
| `sudado/` | Opcional: `PASS-shading_normal` (+ `all_black`) para el efecto de condensación |

Una vista es válida con `uvpass` + (`all_white` | `base_label1` | `base_label0` |
alguna variante de líquido). `Productos/generic.png` se usa para las
miniaturas. El agente escanea cada 60 s y sincroniza **solo metadatos** (nunca
EXR ni rutas absolutas). Las texturas de arte llegan por HTTPS con validación
estricta (solo puerto 443, IPs públicas, ≤4 redirects, ≤100 MB, MIME permitido).

**Preview en vivo:** el navegador llama `POST http://127.0.0.1:8765/v1/preview`
(solo funciona si el agente está en la **misma PC**; CORS con allowlist de
orígenes y `Access-Control-Allow-Private-Network`). Si eso falla por red, la web
cae a un **canal remoto** (KER3-43): el agente revisa `uv-preview/pending` en el
mismo loop del carril UV y sube el PNG (latencia ~5–8 s). **Guardar resultado
final** es un job `uv_compose` por la cola.

**Comprobación rápida de UV Lab:** con el agente abierto, entrar a
`/uv-lab`, elegir producto, vista y textura; mover `Offset X` u `Opacidad` y ver
que el preview cambia; pulsar "Guardar resultado final" y esperar `Completado`.
Si no hay preview, revisa que no haya otra aplicación en el puerto `8765` y que
Chrome/Brave haya aceptado el permiso de **Local Network Access** (si no, el
preview usa el canal remoto, más lento).

## Estructura

```
kernel-renders-agent/
├── pyproject.toml            versión (fuente real; ver "Pendientes")
├── README.md · .env.example · smoke_test.py
├── src/
│   ├── kernel_agent/         proceso del agente
│   │   ├── __main__.py · cli.py          CLI (click): setup, run, status, doctor, version
│   │   ├── config.py · setup_wizard.py   AgentConfig, config.json, overrides por entorno, wizard
│   │   ├── api_client.py                 cliente HTTP (x-api-key) de todos los endpoints
│   │   ├── daemon.py                     carril Blender, heartbeat, cancel watcher, blend downloads;
│   │   │                                 levanta UvWorker y UvPreviewServer
│   │   ├── executor.py                   genera y lanza el runner bpy, progreso/ETA, cancelación
│   │   ├── asset_materializer.py         baja URLs del plan a una carpeta temporal
│   │   ├── storage.py                    sube renders y .blend por signed URL
│   │   ├── library_scan.py               escanea .blend (view layers, cámaras, turntable, thumbnails)
│   │   ├── scene_metadata.py             lee metadatos abriendo Blender headless (con caché)
│   │   ├── blend_thumbnail.py            extrae el thumbnail embebido de un .blend
│   │   ├── gpu_detect.py                 backend GPU y versión de Blender
│   │   ├── psd_executor.py · psd_scan.py · psd_thumbnail.py   Export Pack y uv_retexture (sin bpy general)
│   │   ├── uv_catalog.py                 escaneo del catálogo UV y convención de pases
│   │   ├── uv_engine_core.py             motor 2D (cv2 + numpy) portado de UV Mapper
│   │   ├── uv_executor.py                run_uv_compose y render_uv_preview_png, cachés, seguridad de texturas
│   │   ├── uv_worker.py                  hilo del carril UV (catálogo, jobs, previews remotos)
│   │   ├── uv_preview_server.py          preview HTTP solo-loopback (127.0.0.1)
│   │   └── uv_thumbnails.py              miniaturas de producto para el picker
│   └── kernel_scripts/       tools que corren dentro de Blender (salvo indicación)
│       ├── swap_label.py · set_cap_color.py · set_view_layer.py · inspect_scene.py
│       ├── apply_material_overrides.py   variantes de material por componente (KER3-45)
│       ├── render_views.py               render_one_view / all_cameras / rotations / at_angle (+ stub seven_views)
│       ├── export_pack.py                stub de Fase 4
│       ├── psd_export.py                 export_psd (fuera de Blender: psd-tools + Pillow)
│       └── uv_retexture.py               UV Lab V1 (renderiza un EXR de pases y compone sin Blender)
└── tests/                    solo carril UV: test_uv_catalog, test_uv_preview_server, test_uv_state,
                              test_uv_texture_security
```

`kernel_scripts` es la copia que corre en producción; existe una copia hermana
en `packages/scripts` del repo privado (mantenerlas sincronizadas).

## Publicar una versión

Todo cambio funcional del agente lleva **bump de versión + tag**:

1. En el PR del cambio, sube `version` en `pyproject.toml` y el título de este README.
2. Tras el merge: `git tag -a agent-vX.Y.Z -m "descripción"` y `git push origin agent-vX.Y.Z`.
3. Avisa a quien opere agentes (Moy) que ejecute `git pull` + `pip install -e .` y reinicie.

Tags actuales: `agent-v0.2.0` … `agent-v0.6.1` (`agent-v0.7.0` tras el merge del catálogo por POST). No hay CI ni GitHub Releases
automatizados. Los cambios solo de docs no llevan bump.

## Revocar acceso

En `/settings/agents` (web), "Revocar". El próximo poll falla con 401 y el
agente se detiene solo.

## Logs

Salida a stdout con formato `HH:MM:SS [LEVEL] kernel-agent.X: …`. Para guardarlos:

```powershell
py -3.13 -m kernel_agent run 2>&1 | Tee-Object agent.log
```

## Pendientes conocidos

Detalle y contexto en `docs/PROJECT.md` §15 del repo privado.

- [ ] `kernel_agent.__version__` está en `0.2.2` (el comando `version` y `smoke_test.py` lo imprimen); la versión real es la de `pyproject.toml`. Falta sincronizarlas.
- [ ] Enviar la versión del agente al servidor en el poll (hoy la web no sabe qué versión corre cada PC).
- [ ] Declarar `psd-tools` en las dependencias y decidir qué hacer con `python-dotenv` (declarado, sin uso).
- [ ] Autodetectar y validar Blender ≥ 5.2 en `setup` y `doctor`.
- [ ] Preguntar `psds_dir` en el wizard; `.env.example` pide Blender 5.1 y sugiere un `.env` que no se lee.
- [ ] Tests para `executor`, `daemon`, `api_client` y `render_views`; CI.
- [ ] Servicio de Windows (`install-service`).
- [ ] Cancelación de las ramas PSD/`uv_retexture` y del carril UV (hoy no son cancelables).
- [ ] Versión GUI (Tauri/Electron, system tray) y auto-update.

Hecho (antes en el roadmap): CLI `setup/run/status/doctor`, autenticación con
API key, ejecución con Blender headless y GPU OptiX, progreso paso a paso con
muestras y ETA, subida real de renders a Supabase Storage, descarga bajo demanda
de `.blend`, cancelación desde la UI, UV Lab V2 (catálogo, preview local y
remoto, guardado final).

## Licencia

Proprietary · Kernel Studio · 2026
