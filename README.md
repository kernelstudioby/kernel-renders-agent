# Kernel Renders Agent

Daemon Python que corre en una PC con GPU. Hace polling a la plataforma web
(`https://kernel-renders.vercel.app`) y ejecuta los jobs de Blender contra
escenas locales. Los renders se reportan de vuelta al server.

Patrón de autenticación: **API key estática por agent** (estilo Kernel Pack
CEP). El admin crea el agent en la UI web (`/settings/agents`), copia el
token UNA SOLA VEZ y lo entrega a quien va a instalar el agent en su PC.

## Prerequisitos

- Python 3.10+
- Blender 5.1+ instalado
- GPU compatible con OptiX / CUDA / HIP / ONEAPI (recomendado, no obligatorio)
- Acceso a internet de salida (HTTPS 443)

## Instalación

```bash
git clone https://github.com/kernelstudioby/kernel-renders-agent.git
cd kernel-renders-agent
pip install -e .
```

## Configuración (una vez)

```bash
kernel-agent setup
```

El wizard pregunta:
1. URL del servidor (default: `https://kernel-renders.vercel.app`)
2. API key del agent (cópiala del admin)
3. Ruta a `blender.exe` (autodetect en Windows)
4. Carpeta del library (donde están los `.blend`)
5. Carpeta de output (donde guardar los renders)
6. Detecta GPU automáticamente

La config se guarda en (depende del SO):

- Windows: `%LOCALAPPDATA%\KernelRendersAgent\config.json`
- Mac: `~/Library/Application Support/KernelRendersAgent/config.json`
- Linux: `~/.config/KernelRendersAgent/config.json`

## Uso

```bash
# Verificar config
kernel-agent status

# Diagnóstico (Blender, GPU, conectividad)
kernel-agent doctor

# Arrancar el daemon
kernel-agent run
```

Cuando esté corriendo, el daemon hace polling cada N segundos (default 5s).
Cuando llega un job:

1. Llama `POST /api/agent/claim/:id` para reclamarlo atómicamente
2. Ejecuta el plan con Blender headless (`blender --background --python`)
3. Reporta progreso después de cada step (`POST /api/agent/progress`)
4. Al terminar, reporta los renders (`POST /api/agent/complete`)

## Estructura

```
kernel-renders-agent/
├── pyproject.toml
├── README.md
├── src/
│   ├── kernel_agent/        # paquete principal
│   │   ├── cli.py            # CLI con click
│   │   ├── config.py         # carga/guarda config.json
│   │   ├── setup_wizard.py   # wizard interactivo
│   │   ├── api_client.py     # HTTP contra el server
│   │   ├── gpu_detect.py     # detecta GPU via Blender
│   │   ├── executor.py       # ejecuta plan via Blender headless
│   │   ├── storage.py        # metadata de renders (upload viene)
│   │   └── daemon.py         # main loop poll+execute
│   └── kernel_scripts/       # (copia del paquete del monorepo)
│       ├── swap_label.py
│       ├── set_cap_color.py
│       ├── set_view_layer.py
│       ├── inspect_scene.py
│       └── render_views.py
└── tests/
```

## Revocar acceso

Si el admin quiere desconectar este agent: va a `/settings/agents` en la UI
web y le da click a "Revocar". El próximo poll del agent falla con 401 y el
daemon se detiene automáticamente.

## Logs

El daemon imprime a stdout con formato `HH:MM:SS [LEVEL] kernel-agent.X: ...`.
Para guardarlos a archivo:

```bash
kernel-agent run 2>&1 | tee agent.log
```

## Roadmap

- [x] CLI con setup, run, status, doctor
- [x] Autenticación con API key estática
- [x] Ejecución de plan via Blender headless con GPU OptiX
- [x] Reporte de progreso paso a paso
- [ ] Upload real de outputs a Supabase Storage (placeholder ahora)
- [ ] Auto-start como servicio Windows (`kernel-agent install-service`)
- [ ] Versión GUI con Tauri o Electron (system tray)
- [ ] Auto-update del binario

## Licencia

Proprietary · Kernel Studio · 2026
