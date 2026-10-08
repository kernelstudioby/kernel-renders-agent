# Changelog — Kernel Renders Agent

> Archivo vivo y append-only: los más nuevos arriba. Una entrada por versión
> publicada (tag `agent-vX.Y.Z`). Reconstruido el 2026-10-07 a partir de `git log`,
> los tags y los PR de este repo. El estado actual está en [PROJECT.md](./PROJECT.md)
> y las decisiones en [DECISIONES.md](./DECISIONES.md).

---

## Cómo mantener este documento

**Cuándo agregar entrada:** cada versión publicada (bump en `pyproject.toml` + tag),
y cambios de documentación relevantes.

**Formato de entrada:**

```
## AAAA-MM-DD — X.Y.Z: título corto

**Tipo:** feat | fix | docs | chore
**PR / commits:** #NN, `hash`
**Tickets:** KER3-NN (si aplica)

Qué cambió y por qué, en una o dos frases.
```

Las referencias antiguas `KER-nnn` en commits corresponden a issues del equipo
Kernel Renders en Linear antes de la clave `KER3`.

---

## 2026-10-07 — Docs: estructura estándar de documentación

**Tipo:** docs

Se agregan `docs/PROJECT.md`, `docs/CHANGELOG.md` y `docs/DECISIONES.md`, enlazados
desde el README. Sin cambios de código ni de versión.

## 2026-10-05 — 0.7.0: catálogo de escenas por POST

**Tipo:** fix · **PR:** #32 · **Tickets:** KER3-46

El catálogo de escenas se manda a `POST /api/agent/library` (al cambiar o cada 10 min)
y sale de la query del poll; respaldo por query si el servidor no tiene la ruta.

## 2026-10-02 — 0.6.1: no perder los materiales al recortar el poll

**Tipo:** fix · **PR:** #31 · **Tickets:** KER3-46

El recorte por tamaño del poll va escena por escena (primero `frame_views`, al final
`components`) en vez de quitar `components` de todas a la vez.

## 2026-10-02 — 0.6.0: nombre de vista y ángulo por fotograma

**Tipo:** feat · **PR:** #30 · **Tickets:** KER3-46

El probe de escenas reporta `frame_views` (`frame`, nombre del marker, ángulo Z de
`NULL_ANIMATOR`).

## 2026-10-02 — 0.5.0: variantes de material por componente

**Tipo:** feat · **PR:** #29 · **Tickets:** KER3-45

El probe agrupa componentes por prefijo de material y la tool
`apply_material_overrides` asigna variantes solo durante el render. Antes, en #28, el
README se llevó al estándar Kernel (estructura, protocolo, pendientes).

## 2026-09-25 — 0.4.0: `PASS-opacity` define el alfa del resultado UV

**Tipo:** feat · **PR:** #27 · **Tickets:** KER3-36

## 2026-09-22 — 0.3.0: preview UV remoto en el carril UV

**Tipo:** feat · **PR:** #26 · **Tickets:** KER3-43

Canal remoto de respaldo cuando el navegador no alcanza el preview loopback.

## 2026-09-21 — 0.2.8 a 0.2.10: correcciones del render en scene fresh

**Tipo:** fix · **PR:** #23, #24, #25 · **Tickets:** KER3-42

`set_cap_color` respeta nodos conectados a Base Color (0.2.8); la scene fresh hereda
denoise/sampling (0.2.9) y la visibilidad por view layer (0.2.10).

## 2026-09-14 / 15 — 0.2.4 a 0.2.7: compositor y pases de líquido

**Tipo:** fix · **PR:** #18, #19, #20, #21, #22 · **Tickets:** KER3-40

Re-apuntar los nodos Render Layers del compositor al view layer activo (#21);
normalizar mayúsculas en variantes de líquido y track mattes (#19); Blender 5.2 LTS
obligatorio en la documentación (#20). El cambio de pintar en negro las zonas sin
cobertura del pase de líquido (#18, 0.2.4) se revirtió en #22 (0.2.7).

## 2026-09-07 — 0.2.3: costuras UV, render final y miniaturas

**Tipo:** feat / fix · **PR:** #15, #16, #17

Costuras UV en 0 por defecto, render final hasta 4000 px y miniaturas de productos UV
para el picker.

## 2026-08-20 — 0.2.2: texturas remotas de UV Lab más seguras

**Tipo:** fix · **Commit:** `becddb2`

Validación estricta de texturas descargadas (HTTPS 443, IPs públicas, límites de
tamaño y redirecciones).

## 2026-08-19 — 0.2.0 y 0.2.1: UV Lab V2 en el agente

**Tipo:** feat · **Commits:** `9e43e9b`, `cc6ed09`

Carril UV con catálogo, `uv_compose` y preview loopback (0.2.0); requisito de Python
compatible con OpenEXR (0.2.1).

## 2026-06-03 a 2026-07-23 — sin tag: renders, PSD y cancelación

**Tipo:** feat / fix · **PR:** #1 a #14

Descarga bajo demanda de `.blend` (#1), cancelación desde la UI (#2, #9), render por
fotogramas del turntable (#3, #5), URL por defecto del servidor corregida (#4), DPI
real en `export_psd` (#6), UV Lab V1 por pase UV (#7, #8), ajustes de visibilidad y
scene fresh (#10, #11, #12, #14) y `apply_postfx` (#13). Antes (junio), Export Pack
con `psd-tools`, metadatos de cámaras y view layers, thumbnails de `.blend` y render
EXR multilayer local.

## 2026-05-29 — 0.1.0: primera versión

**Tipo:** feat · **Commits:** `e99918b`, `0ff7de8`, `4f93cc3`, `2be2363`

Paquete instalable con CLI, poll a la web, escaneo de la library, descarga de assets y
subida de renders por URL firmada.
