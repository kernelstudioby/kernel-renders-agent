# Kernel Renders Agent 0.3.0

Servicio Python único que corre en la PC de producción. Mantiene el carril de
renders Blender y agrega el carril UV Lab V2 para composición 2D nativa, sin
abrir Blender. Hace polling a la plataforma web
(`https://kernel-renders-web.vercel.app`) y reporta los resultados al servidor.

Al ejecutar `kernel-agent run` se levantan, bajo la misma identidad del agente:

1. El worker de Blender para renders 3D.
2. El worker UV para guardados finales en cola.
3. El preview UV local en `http://127.0.0.1:8765`, usado por el navegador para
   reflejar sliders en vivo con CPU/RAM de esa PC.

Patrón de autenticación: **API key estática por agent** (estilo Kernel Pack
CEP). El admin crea el agent en la UI web (`/settings/agents`), copia el
token UNA SOLA VEZ y lo entrega a quien va a instalar el agent en su PC.

## Prerequisitos

- Python 3.13 recomendado en Windows (`3.10 <= Python < 3.14`)
- **Blender 5.2 LTS instalado (obligatorio, no solo "5.1+")** — KER3-40: escenas
  armadas con el addon Render Raw en Blender 5.2 pueden renderizar en NEGRO
  con `apply_postfx=true` si el agente corre en una versión más vieja (ej.
  5.1). Confirmado reproduciendo el bug en 5.1 y viendo el mismo .blend
  renderizar correctamente en 5.2 — Blender rompe silenciosamente datos de
  nodos de curva (`Float Curve`) del compositor al abrir un archivo de una
  versión más nueva en una más vieja.
- GPU compatible con OptiX / CUDA / HIP / ONEAPI (recomendado, no obligatorio)
- Carpeta local de UV Mapper que contenga `Productos`, con las escenas de prueba
- Acceso a internet de salida (HTTPS 443)

## Instalación

```powershell
git clone https://github.com/kernelstudioby/kernel-renders-agent.git
cd kernel-renders-agent
py -3.13 -m pip install -U pip
py -3.13 -m pip install -e .
```

> Python 3.14 no se usa todavía: OpenEXR no publica wheel de Windows para esa
> versión y `pip` intentaría compilarlo localmente con CMake/Visual Studio.

Si Moy ya lo tiene instalado:

```powershell
cd C:\ruta\a\kernel-renders-agent
git pull origin main
py -3.13 -m pip install -e .
```

## Configuración (una vez)

```powershell
py -3.13 -m kernel_agent setup
```

El wizard pregunta:
1. URL del servidor (default: `https://kernel-renders-web.vercel.app`)
2. API key del agent (cópiala del admin)
3. Ruta a `blender.exe` (autodetect en Windows)
4. Carpeta del library (donde están los `.blend`)
5. Carpeta de output (donde guardar los renders)
6. Carpeta UV (`Productos` o la raíz de UV Mapper que la contiene)
7. Puerto de preview UV (dejar `8765` salvo que esté ocupado)
8. Detecta GPU automáticamente

La config se guarda en (depende del SO):

- Windows: `%LOCALAPPDATA%\KernelRendersAgent\config.json`
- Mac: `~/Library/Application Support/KernelRendersAgent/config.json`
- Linux: `~/.config/KernelRendersAgent/config.json`

## Uso

```powershell
# Verificar config
py -3.13 -m kernel_agent status

# Diagnóstico (Blender, GPU, conectividad)
py -3.13 -m kernel_agent doctor

# Arrancar el daemon
py -3.13 -m kernel_agent run
```

El agente debe permanecer abierto mientras se use la plataforma. En el arranque
correcto deben aparecer mensajes equivalentes a `UV lane online`,
`Preview UV local online` y la conexión del carril Blender.

Cuando llega un job 3D:

1. Llama `POST /api/agent/claim/:id` para reclamarlo atómicamente
2. Ejecuta el plan con Blender headless (`blender --background --python`)
3. Reporta progreso después de cada step (`POST /api/agent/progress`)
4. Al terminar, reporta los renders (`POST /api/agent/complete`)

Cuando se usa UV Lab:

1. El agente sincroniza únicamente nombres y metadatos del catálogo local.
2. Los sliders llaman al preview loopback; el PNG temporal no pasa por Vercel.
3. Si el navegador no está en la misma PC que el agente (loopback no
   disponible), el preview cae a un canal remoto: el agente lo revisa en el
   mismo poll loop del UV lane y sube el PNG por Vercel (KER3-43).
4. `Guardar versión` crea un job persistente y sube solo el PNG final.
5. Los PSD, texturas fuente y archivos de escena UV permanecen en la PC.
6. Las texturas remotas se descargan únicamente por HTTPS, con límite de
   tamaño y validación de destino para impedir accesos a redes locales.

## Comprobación rápida de UV Lab

1. Dejar `kernel-agent run` abierto.
2. Abrir `https://kernel-renders-web.vercel.app/uv-lab` e iniciar sesión.
3. Confirmar que la tarjeta del agente diga `Conectado` y muestre productos.
4. Seleccionar un producto y mover `Gain` u `Opacidad`; la imagen debe cambiar
   mientras se arrastra.
5. Probar el campo numérico y sus botones `−`/`+`.
6. Pulsar `Guardar versión` y esperar el estado `Completado`.

Si no hay preview, comprobar que no exista otra aplicación usando el puerto
`8765`, que la terminal del agente siga abierta y que la URL configurada sea
exactamente la URL de la plataforma (sin una ruta adicional).

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
│   │   ├── uv_worker.py      # cola y catálogo UV
│   │   ├── uv_executor.py    # composición UV determinista
│   │   ├── uv_preview_server.py # preview HTTP solo-loopback
│   │   └── daemon.py         # coordina ambos carriles
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
- [x] UV Lab V2: catálogo local, preview interactivo y guardado final
- [ ] Upload real de outputs a Supabase Storage (placeholder ahora)
- [ ] Auto-start como servicio Windows (`kernel-agent install-service`)
- [ ] Versión GUI con Tauri o Electron (system tray)
- [ ] Auto-update del binario

## Licencia

Proprietary · Kernel Studio · 2026
