# Kernel Renders Agent — Decisiones

> Registro de las decisiones técnicas del agente, con su contexto y sus
> consecuencias. Las decisiones de la plataforma completa (web, API, datos) son ADR
> del repo privado `kernelstudioby/Kernel-Renders`; aquí se citan por número cuando
> aplican, sin copiar su contenido.
>
> **Cómo se usa:** una decisión cerrada no se edita. Si se revierte, se agrega una
> nueva que diga a cuál reemplaza. Las más nuevas van arriba. Formato de cada
> entrada: **Fecha · Contexto · Decisión · Consecuencias · Referencias**.

**Última decisión:** D-009

---

## D-009 — Catálogo de escenas por POST, fuera de la query del poll

**Fecha:** 2026-10-05 (0.7.0) · **Ref.:** KER3-46, PR #32

**Contexto:** el catálogo de escenas viajaba en la query del `GET /api/agent/poll`.
Con metadatos nuevos (componentes, vistas por fotograma) la URL crecía hasta rozar el
límite del hosting (~14 KB) y había que recortar datos útiles (PR #31).

**Decisión:** el catálogo se manda a `POST /api/agent/library` cuando cambia (hash) o
cada 10 minutos, y el poll de Blender lo omite. Si el servidor no tiene esa ruta
(404/405), el agente lo recuerda y vuelve a mandar el catálogo en la query.

**Consecuencias:** el poll queda ligero; el agente sigue funcionando contra servidores
anteriores. El recorte por tamaño solo aplica en el modo de respaldo por query.

---

## D-008 — Versión y tag anotado por cada cambio funcional

**Fecha:** 2026-09-07 en adelante · **Ref.:** README, sección "Publicar una versión"

**Contexto:** el agente se instala en varias PCs con `pip install -e .` y no tiene
auto-update; hace falta saber qué versión corre cada máquina y a cuál volver.

**Decisión:** todo cambio funcional sube `version` en `pyproject.toml` (y el título del
README) y, tras el merge, se crea el tag anotado `agent-vX.Y.Z`. Los cambios solo de
documentación no llevan bump.

**Consecuencias:** actualizar una PC es `git pull` + reiniciar. Pendiente:
`kernel_agent.__version__` no está sincronizado con `pyproject.toml` y el agente aún no
reporta su versión al servidor.

---

## D-007 — Preview UV solo-loopback con canal remoto de respaldo

**Fecha:** 2026-08-19 (0.2.0), respaldo 2026-09-22 (0.3.0) · **Ref.:** KER3-43, PR #26, ADR-012 del repo web

**Contexto:** UV Lab necesita reflejar los sliders casi en tiempo real; pasar cada
preview por la cola en la nube sería lento.

**Decisión:** el agente abre un servidor HTTP ligado a `127.0.0.1` (puerto 8765 por
defecto, configurable) con CORS por lista de orígenes, para el navegador de la misma
PC. Cuando el navegador no llega por red, la web usa un canal remoto que el carril UV
atiende (`uv-preview/pending` → `complete`). El resultado final siempre es un job
`uv_compose` por la cola.

**Consecuencias:** no se expone ningún puerto a la red; el preview remoto tiene más
latencia (segundos).

---

## D-006 — Los EXR se quedan en disco local; solo viajan imágenes ligeras

**Fecha:** 2026-06-10 · **Ref.:** `daemon.py`, `storage.py`

**Contexto:** los renders `OPEN_EXR_MULTILAYER` son pesados y se usan en
post-producción local.

**Decisión:** el agente sube solo `.png/.jpg/.jpeg/.webp`; los EXR se quedan en la
carpeta de output de la PC. Del carril UV y de los catálogos solo se sincronizan
metadatos y miniaturas, nunca EXR ni rutas absolutas.

**Consecuencias:** menos tráfico y almacenamiento; el EXR solo existe en la PC que lo
generó.

---

## D-005 — Un solo agente con dos carriles (Blender y UV)

**Fecha:** 2026-08-19 (0.2.0) · **Ref.:** ADR-006 y ADR-008 del repo web

**Contexto:** UV Lab V2 compone pases en 2D sin Blender y corre en las mismas PCs que
renderizan.

**Decisión:** `kernel_agent run` levanta, bajo una sola API key, el carril Blender
(hilo principal), el heartbeat y, si hay carpeta de productos, el carril UV
(`UvWorker`) y el preview loopback. El carril UV no usa Blender.

**Consecuencias:** una sola instalación e identidad por PC; un render largo no bloquea
los jobs ni los previews UV.

---

## D-004 — Blender 5.2 LTS obligatorio

**Fecha:** 2026-09-14 · **Ref.:** KER3-40, PR #20, ADR-010 del repo web

**Contexto:** escenas armadas en Blender 5.2 con el compositor (addon Render Raw)
salían en negro con `apply_postfx=true` al abrirse en versiones anteriores: Blender
rompe en silencio los nodos de curva al abrir un archivo de una versión más nueva.

**Decisión:** todos los agentes usan Blender 5.2 LTS, no "5.1 o superior".

**Consecuencias:** cada PC nueva debe instalar 5.2. Pendiente: el wizard solo
autodetecta hasta 5.1 y `doctor` no valida la versión.

---

## D-003 — Una sola URL de servidor configurable

**Fecha:** 2026-07-16 · **Ref.:** PR #4, `config.py`

**Contexto:** el agente necesita un único punto de contacto con la plataforma, y el
default inicial tenía un error en el nombre del host.

**Decisión:** toda la comunicación va a `server_url`, con default
`https://kernel-renders-web.vercel.app`. Se cambia en el wizard o con
`KERNEL_RENDERS_SERVER_URL` (p. ej. una web local para desarrollo). No hay otras URLs
de servicios configuradas en el agente.

**Consecuencias:** apuntar un agente a otro entorno es cambiar un solo valor.

---

## D-002 — El agente solo habla HTTP con API key; no usa llaves de Supabase

**Fecha:** 2026-05-29 (0.1.0) · **Ref.:** `api_client.py`, `storage.py`

**Contexto:** el agente corre en PCs fuera del control directo del equipo; una llave
de base de datos ahí daría acceso a todo.

**Decisión:** todas las llamadas son HTTP al servidor con header `x-api-key`. Para
subir archivos, el servidor emite URLs firmadas y el agente hace `PUT`
directo a Storage. La API key vive solo en el `config.json` local y se revoca desde la
web (el siguiente poll recibe `401` y el agente se detiene).

**Consecuencias:** revocar una PC no afecta a las demás; toda la autorización y la
lógica de negocio quedan del lado del servidor.

---

## D-001 — Repositorio público a propósito

**Fecha:** vigente desde julio de 2026 (formalizada el 2026-09-26) · **Ref.:** README, ADR-009 del repo web

**Contexto:** el agente se instala con `git clone` + `pip install -e .` en PCs
externas con GPU; exigir credenciales de un repo privado en cada máquina complicaría
la instalación y las actualizaciones.

**Decisión:** este repo es público. No contiene secretos ni lógica de negocio: el
esquema de datos, la API y la lógica sensible viven en el repo privado de la web.

**Consecuencias:** nunca se suben tokens, rutas privadas ni datos de clientes. Las
API keys solo existen en la configuración local de cada PC.
