#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remap_core.py
-------------
Lógica pura (sin interfaz) del RE:Map UV Studio: carga de imágenes/EXR,
proyección UV, mipmapping anti-aliasing, mezcla de capas, detección de
productos/vistas y construcción de escenas. No depende de Tkinter -- lo usa
remap_app.py (la interfaz) y lo podrías reutilizar en un script de línea de
comandos o notebook si quisieras.

Estructura de carpetas esperada (ver remap_studio.py para el resumen
completo del flujo de productos/vistas/modos "dual" vs "single").
"""

import os
import re
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
import numpy as np
import cv2
from PIL import Image

# ==================== CONFIG ====================


# Carpeta donde vive este script (para que todo sea relativo, sin rutas
# hardcodeadas). Si mueves la carpeta V2_2 completa, esto se sigue
# resolviendo correctamente.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_DIRNAME = "Productos"
PRODUCTS_FOLDER = os.path.join(SCRIPT_DIR, PRODUCTS_DIRNAME)

# Textura genérica por defecto (vive en la raíz de Productos, aplica a
# cualquier producto que todavía no tenga su propia textura importada).
GENERIC_TEXTURE_NAME = "generic.png"

# Patrón de fondo para el viewport (reemplaza el negro fuera de la silueta
# del producto SOLO en la vista previa/miniaturas; el guardado/export no se
# ve afectado). Si este archivo existe en Productos/, se usa; si no, se
# genera un patrón de puntos por defecto.
BACKGROUND_PATTERN_NAME = "background_pattern.png"

# Nombre del archivo de textura importada POR PRODUCTO (se guarda dentro de
# la carpeta de cada producto).
IMPORTED_TEXTURE_NAME = "texture_imported.png"


# Extensiones de imagen soportadas para CUALQUIER pase (se pueden mezclar
# formatos dentro de una misma vista: ej. uvpass en .exr y base_label1 en
# .png, no tienen que ser todos el mismo formato).
PASS_EXTENSIONS = (".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")

# Patrón para detectar track mattes, con cualquiera de esas extensiones.
TRACK_MATTE_PATTERN = re.compile(
    r"-track_matte-([A-Za-z0-9_]+)\.(?:exr|png|jpe?g|tiff?|bmp)$", re.IGNORECASE)

# Patrón para detectar variantes de color de líquido del fondo "sin
# etiqueta" (modo dual): ej. PASS-liquid-mayonesa_light.exr,
# PASS-liquid_clear.exr. El separador después de "liquid" puede ser "-" o
# "_" (ambos se aceptan y simplemente se descartan) -- lo que importa es
# el nombre que queda después, donde cualquier "_" restante se muestra
# como espacio en la UI (ej. "mayonesa_light" -> "Mayonesa light"). El
# prefijo antiguo "base_label0-" también se sigue aceptando (opcional)
# para no romper vistas que todavía no se hayan renombrado. Cualquier
# cantidad de variantes es válida (2, 6, las que haya en la carpeta).
LIQUID_PATTERN = re.compile(
    r"(?:base_label0-)?liquid[-_]([A-Za-z0-9_]+)\.(?:exr|png|jpe?g|tiff?|bmp)$", re.IGNORECASE)
LEFTOVER_THRESHOLD = 1e-3

FLIP_V = True
WRAP_GLOBAL = False   # wrap global (se aplica a todas las vistas)
TEXTURE_IS_SRGB = True
DISPLAY_MAX_DIM = 900

BLEND_MODES = ["Normal", "Multiply", "Screen", "Overlay",
               "Hard Light", "Soft Light", "Linear Light", "Add (Linear Dodge)"]

NEUTRAL = dict(in_black=0.0, in_white=1.0, gamma=1.0,
               out_black=0.0, out_white=1.0, gain=1.0)

DEFAULT_BLEND = {"Base": "Multiply", "Especular": "Add (Linear Dodge)"}

# Umbral por defecto de limpieza de costuras UV (equivalente al "Edges
# Threshold %" de RE:Map). 0 = desactivado.
DEFAULT_EDGES_THRESHOLD = 0.0

# Nombre de la región usada en el modo "dual" (label0/label1)
LABEL_REGION_NAME = "Etiqueta"

# ---------- "Sudado" (gotas de condensación) ----------
#
# Carpeta OPCIONAL dentro de cada vista (ver SUDADO_DIRNAME) con una
# variante especular ("PASS-all_black", que en la convención nueva es el
# pase de especular) y un mapa de normales ("PASS-shading_normal"). Cuando
# se activa desde la UI: (1) reemplaza el especular normal de la vista por
# el de esta carpeta, y (2) usa el mapa de normales para distorsionar --
# por refracción, como la luz al doblarse al pasar por una gota de agua --
# SOLO la textura de arte proyectada. Nunca afecta a una región "pintada"
# (ej. la tapa, vía su propio track_matte-*): esas regiones ya se excluyen
# de la proyección de textura en render_single_frame/render_dual_frame, así
# que la distorsión automáticamente queda "por debajo" de ellas sin
# necesitar ningún caso especial.
SUDADO_DIRNAME = "sudado"

# Convención de canales del normal map (estándar tangent-space: R=X, G=Y,
# B=Z). Cambiar aquí si el render exporta los canales en otro orden.
SUDADO_NORMAL_CHANNEL_X = "R"
SUDADO_NORMAL_CHANNEL_Y = "G"
SUDADO_NORMAL_CHANNEL_Z = "B"
SUDADO_NORMAL_SIGN_X = 1.0
SUDADO_NORMAL_SIGN_Y = 1.0
# "auto" detecta solo si el normal map viene codificado como color 0..1
# (típico de PNG/JPG de 8 bit) o crudo en -1..1 (típico de EXR). Forzar
# "encoded_0_1" o "raw" si la detección automática se equivoca.
SUDADO_NORMAL_ENCODING = "auto"

# Fuerza/umbral por defecto de la distorsión. La fuerza también es
# ajustable desde la UI con el slider "Fuerza" de Sudado; el umbral (qué
# tanto debe inclinarse la normal para contar como "gota real" y no ruido
# de superficie) se deja fijo aquí -- se puede exponer como slider también
# si hace falta calibrarlo caso por caso.
SUDADO_DISTORTION_STRENGTH_DEFAULT = 0.06
SUDADO_NORMAL_THRESHOLD = 0.02

# ============================================================
#  NUCLEO (sin interfaz) -- funciones reutilizables
# ============================================================

def find_pass_file(files, suffix):
    """Busca, entre los archivos de una carpeta de vista, uno cuyo nombre
    termine en '{suffix}{extensión}' para CUALQUIER extensión de imagen
    soportada (ver PASS_EXTENSIONS). Esto permite mezclar formatos entre
    pases dentro de la misma vista: por ejemplo PASS-uvpass.exr junto con
    PASS-base_label1.png. Devuelve el primer nombre de archivo que
    coincida, o None."""
    suffix_lower = suffix.lower()
    for f in files:
        fl = f.lower()
        for ext in PASS_EXTENSIONS:
            if fl.endswith(suffix_lower + ext):
                return f
    return None

def find_pass_file_multi(files, suffixes):
    """Como find_pass_file(), pero prueba varios sufijos EN ORDEN y
    devuelve el primero que encuentre. Se usa para la transición entre la
    convención de nombres antigua y la nueva sin romper vistas que
    todavía no se hayan renombrado -- ej. buscar primero 'all_white' y,
    si no existe, 'base_label1'."""
    for suffix in suffixes:
        found = find_pass_file(files, suffix)
        if found:
            return found
    return None

def srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)

def load_image(path, is_color=False):
    """Carga cualquier formato soportado (EXR, PNG, JPG, TIFF, BMP).
    Los EXR (y en general cualquier archivo de punto flotante) se asumen
    YA en espacio lineal, como es estándar en pipelines de VFX -- no se
    tocan. Los formatos de 8 bits (PNG/JPG, típicamente usados para
    exportar de forma más liviana) casi siempre están en sRGB para verse
    bien a simple vista; si 'is_color=True' (pases de color como
    base_label0/1 o especular) se convierten a lineal para que la
    matemática de mezcla sea consistente sin importar el formato de
    origen. Los pases de DATOS (UV, track mattes) deben cargarse con
    is_color=False (default) -- sus valores no son color y no deben
    pasar por la curva sRGB."""
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if raw is None:
        raise FileNotFoundError(f"No pude leer el archivo: {path}")
    img = raw.astype(np.float32)
    if raw.dtype == np.uint8:
        img /= 255.0
        if is_color:
            img = srgb_to_linear(img).astype(np.float32)
    elif raw.dtype == np.uint16:
        img /= 65535.0
        if is_color:
            img = srgb_to_linear(img).astype(np.float32)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img, (img.shape[2] == 4)

def convert_texture_to_png(src_path, out_path):
    """Convierte cualquier textura a PNG, preservando el canal alfa si existe
    (el alfa se usa para el recorte de etiqueta en el modo 'dual')."""
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".exr":
        raw = cv2.imread(src_path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        if raw is None:
            raise ValueError(f"No pude leer el EXR: {src_path}")
        img = raw.astype(np.float32)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        rgb = img[:, :, :3][:, :, ::-1]
        rgb8 = (linear_to_srgb(rgb) * 255.0).clip(0, 255).astype(np.uint8)
        if img.ndim == 3 and img.shape[2] == 4:
            alpha8 = (np.clip(img[:, :, 3], 0.0, 1.0) * 255.0).astype(np.uint8)
            rgba8 = np.dstack([rgb8, alpha8])
            Image.fromarray(rgba8, "RGBA").save(out_path)
        else:
            Image.fromarray(rgb8, "RGB").save(out_path)
    else:
        im = Image.open(src_path)
        try:
            im.seek(0)
        except Exception:
            pass
        has_alpha = (im.mode in ("RGBA", "LA")) or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA") if has_alpha else im.convert("RGB")
        im.save(out_path, "PNG")
    return out_path

def apply_levels(x, p):
    x = (x - p["in_black"]) / max(p["in_white"] - p["in_black"], 1e-6)
    x = np.clip(x, 0.0, 1.0)
    if abs(p["gamma"] - 1.0) > 1e-6:
        x = np.power(x, 1.0 / p["gamma"])
    x = p["out_black"] + x * (p["out_white"] - p["out_black"])
    return x * p["gain"]

def blend(mode, b, s):
    if mode == "Normal":
        return s
    if mode == "Multiply":
        return b * s
    if mode == "Add (Linear Dodge)":
        return b + s
    if mode == "Linear Light":
        return b + 2.0 * s - 1.0
    bc = np.clip(b, 0.0, 1.0)
    sc = np.clip(s, 0.0, 1.0)
    if mode == "Screen":
        return 1.0 - (1.0 - bc) * (1.0 - sc)
    if mode == "Overlay":
        return np.where(bc <= 0.5, 2.0 * bc * sc, 1.0 - 2.0 * (1.0 - bc) * (1.0 - sc))
    if mode == "Hard Light":
        return np.where(sc <= 0.5, 2.0 * bc * sc, 1.0 - 2.0 * (1.0 - bc) * (1.0 - sc))
    if mode == "Soft Light":
        return (1.0 - 2.0 * sc) * bc * bc + 2.0 * sc * bc
    return s

def unpremultiply_uv(U, V, alpha, eps=1e-3):
    """Revierte la premultiplicación por alfa del pase de UV en los pixeles
    de borde con cobertura parcial (antialiasing de la silueta).

    Muchos renderers exportan los pases de utilidad (como el de UV) ya
    "premultiplicados": en un pixel de borde con, digamos, 78% de
    cobertura, el valor guardado es aproximadamente UV_real * 0.78 en vez
    de UV_real. Esa coordenada mezclada no corresponde a ningún punto real
    de la superficie -- al usarla para proyectar una textura de alto
    contraste, aparece un borde "sucio"/dentado justo en la silueta
    exterior, independientemente de qué tan grande sea la textura o el
    pase (no es un problema de resolución ni de aliasing por minificación,
    es una coordenada de origen incorrecta). Dividir por el alfa recupera
    la coordenada real. Donde alpha es prácticamente 0 (fondo, sin
    cobertura) no hay nada que recuperar y se deja el valor tal cual (no
    afecta el resultado final, esos pixeles no contribuyen al composite)."""
    safe_alpha = np.where(alpha > eps, alpha, 1.0)
    U_fixed = np.where(alpha > eps, U / safe_alpha, U)
    V_fixed = np.where(alpha > eps, V / safe_alpha, V)
    return U_fixed.astype(np.float32), V_fixed.astype(np.float32)

# ---------- Limpieza de costuras UV ("Edges Threshold %" de RE:Map) ----------
#
# unpremultiply_uv() arriba resuelve el caso de la silueta EXTERIOR (contra
# el fondo, alfa < 1). Pero hay un segundo caso, distinto, que no depende
# del alfa para nada: dos caras/parches de UV DIFERENTES que se encuentran
# en un pliegue o costura del modelo (ambas partes son "objeto" al 100%,
# alfa=1 en ambos lados). Ahí el antialiasing del renderer mezcla, en el
# pixel de transición, la UV de una cara con la de la otra -- el resultado
# es una coordenada que no corresponde a ninguna de las dos superficies
# reales. Al proyectar una textura con contraste (ej. un patrón moteado)
# sobre esa coordenada "basura", aparece el dentado/ruido justo en la
# costura, sin importar la resolución de muestreo (por eso el supersampling
# no lo arregla: el problema es el ORIGEN del dato, no cuántas veces se
# muestrea).
#
# La corrección: detectar esos pixeles de salto brusco de UV (su gradiente
# local es mucho mayor al de una superficie que varía suavemente) y
# reemplazar su valor por el de sus vecinos "limpios" más cercanos -- la
# misma idea detrás del parámetro "Edges Threshold %" de RE:Map. Aquí se
# implementa como un PERCENTIL del gradiente de UV (el threshold_pct% de
# pixeles con el salto más grande de toda la imagen se consideran costura),
# en vez de una magnitud fija, para que el mismo valor se comporte igual
# sin importar la resolución (900px de vista previa vs 4000px+ de
# exportación): 0 = desactivado, valores más ALTOS limpian más pixeles
# (más agresivo). No es necesario que el número coincida con la escala de
# RE:Map -- basta con encontrar, por prueba y error, el valor que se vea
# mejor en cada caso (suele bastar con menos del 2%).

def compute_uv_seam_mask(U, V, threshold_pct):
    """Devuelve una máscara (uint8, 255 = costura) marcando el
    'threshold_pct' por ciento de los pixeles con el salto de UV entre
    vecinos MÁS grande de toda la imagen (0-100; más alto = se limpian más
    pixeles = más agresivo). Usar un percentil -- en vez de un umbral de
    magnitud fija -- es lo que hace que el mismo valor se comporte igual
    sin importar la resolución de la vista (previa a 900px o exportación a
    4000px+): una costura real siempre está entre los saltos de UV más
    grandes de la imagen, sea cual sea la resolución, mientras que un
    umbral de magnitud fija variaría con cuántos pixeles cubren la misma
    superficie."""
    if threshold_pct is None or threshold_pct <= 0:
        return np.zeros(U.shape[:2], dtype=np.uint8)
    dUdx, dUdy, dVdx, dVdy = compute_uv_derivatives(U, V)
    grad = np.sqrt(dUdx**2 + dUdy**2 + dVdx**2 + dVdy**2)
    frac = max(0.0, min(100.0, float(threshold_pct))) / 100.0
    cutoff = np.percentile(grad, 100.0 * (1.0 - frac))
    if cutoff <= 0:
        return np.zeros(U.shape[:2], dtype=np.uint8)
    return (grad > cutoff).astype(np.uint8) * 255

def clean_uv_seams(U, V, threshold_pct, dilate_px=1):
    """Limpia las costuras de UV: detecta los pixeles de salto brusco (ver
    compute_uv_seam_mask) y les asigna, por inpainting, el valor que
    tendría la superficie continua alrededor -- en vez del valor mezclado
    (y por lo tanto incorrecto) que dejó el antialiasing del renderer justo
    ahí. threshold_pct <= 0 desactiva la limpieza por completo (se
    devuelven U, V sin tocar)."""
    if threshold_pct is None or threshold_pct <= 0:
        return U, V
    mask = compute_uv_seam_mask(U, V, threshold_pct)
    if dilate_px > 0:
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=int(dilate_px))
    if mask.max() == 0:
        return U, V
    U_clean = cv2.inpaint(U.astype(np.float32), mask, 3, cv2.INPAINT_NS)
    V_clean = cv2.inpaint(V.astype(np.float32), mask, 3, cv2.INPAINT_NS)
    return U_clean.astype(np.float32), V_clean.astype(np.float32)

def compute_projection(U, V, tex, flip_v, wrap, offset_x=0.0, offset_y=0.0):
    """Proyecta 'tex' (2D o 3D) usando coordenadas UV. Funciona tanto para
    texturas de color (H,W,3) como para un solo canal, ej. el alfa (H,W).
    Muestreo bilineal simple (un solo nivel/resolución) — usar
    compute_projection_mipmapped() cuando la textura pueda aparecer muy
    comprimida (minificada) en pantalla, para evitar aliasing.

    Con wrap=False, si el offset empuja el UV fuera de [0,1] se usa el
    borde de la textura estirado (BORDER_REPLICATE) en vez de negro sólido
    -- un negro sólido ahí se ve como un parche/cuña oscura al multiplicar
    contra la base, que es justo el artefacto que se busca evitar."""
    th, tw = tex.shape[:2]
    U2 = U + offset_x
    V2 = V + offset_y
    if wrap:
        U2 = np.mod(U2, 1.0)
        V2 = np.mod(V2, 1.0)
    Vv = (1.0 - V2) if flip_v else V2
    map_x = (U2 * (tw - 1)).astype(np.float32)
    map_y = (Vv * (th - 1)).astype(np.float32)
    border = cv2.BORDER_WRAP if wrap else cv2.BORDER_REPLICATE
    return cv2.remap(tex, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                     borderMode=border)

# ---------- Distorsión por normal map ("sudado" / gotas de agua) ----------

def _bgr_channel(img_bgr, name):
    idx = {"B": 0, "G": 1, "R": 2}[name]
    return img_bgr[:, :, idx]

def compute_normal_distortion_offset(normal_bgr, strength, threshold,
                                     ch_x=SUDADO_NORMAL_CHANNEL_X, ch_y=SUDADO_NORMAL_CHANNEL_Y,
                                     ch_z=SUDADO_NORMAL_CHANNEL_Z, sign_x=SUDADO_NORMAL_SIGN_X,
                                     sign_y=SUDADO_NORMAL_SIGN_Y, encoding=SUDADO_NORMAL_ENCODING):
    """Convierte un mapa de normales en un offset de UV (offset_x, offset_y,
    arrays del mismo tamaño que el normal map) que simula refracción: una
    superficie lisa tiene normal ~(0,0,1); donde una gota de agua inclina
    la normal en X/Y, la textura que se proyecta ahí se desplaza -- igual
    que la luz se dobla al atravesar la gota. 'threshold' descarta
    inclinaciones muy pequeñas (ruido de superficie) para que solo las
    gotas "reales" distorsionen."""
    raw_x = _bgr_channel(normal_bgr, ch_x)
    raw_y = _bgr_channel(normal_bgr, ch_y)
    raw_z = _bgr_channel(normal_bgr, ch_z)

    if encoding == "raw":
        encoded = False
    elif encoding == "encoded_0_1":
        encoded = True
    else:
        # heurística: si Z promedia alto y todo está en 0..1, casi seguro
        # está codificado como color (neutro = 0.5, 0.5, 1.0)
        encoded = (0.0 <= raw_z.min() and raw_z.max() <= 1.001 and raw_z.mean() > 0.3)

    if encoded:
        nx = (raw_x * 2.0 - 1.0) * sign_x
        ny = (raw_y * 2.0 - 1.0) * sign_y
    else:
        nx = raw_x * sign_x
        ny = raw_y * sign_y

    mag = np.sqrt(nx ** 2 + ny ** 2)
    mask = (mag > threshold).astype(np.float32)
    return (nx * mask * strength).astype(np.float32), (ny * mask * strength).astype(np.float32)


# ---------- Mipmapping (filtrado anti-aliasing por minificación) ----------
#
# Cuando el mapeo UV comprime mucho la textura en pantalla (ej. cerca del
# hombro de una botella, o en el borde entre dos regiones), un solo muestreo
# bilineal no alcanza a "ver" todos los texeles que caen dentro de ese pixel
# de pantalla, produciendo parpadeo/dentado (aliasing de minificación). Esto
# es lo mismo que resuelve el "MipMap smoothing" de RE:Map UV: se construye
# una pirámide de la textura a media resolución en cada nivel, y por cada
# pixel de pantalla se elige (y mezcla entre) el nivel cuyo texel ya
# representa aproximadamente el área que ese pixel cubre.

MIPMAP_MIN_SIZE = 2

def build_mip_pyramid(tex, min_size=MIPMAP_MIN_SIZE):
    """Genera una pirámide de mipmaps: nivel 0 = textura original, cada
    nivel siguiente es la mitad de resolución (filtro de área/caja), hasta
    llegar a min_size."""
    levels = [tex]
    h, w = tex.shape[:2]
    while max(h, w) > min_size:
        h2, w2 = max(1, h // 2), max(1, w // 2)
        tex = cv2.resize(tex, (w2, h2), interpolation=cv2.INTER_AREA)
        levels.append(tex)
        h, w = h2, w2
    return levels

def _ensure_mips(tex):
    """Construye (una sola vez, de forma perezosa) las pirámides de mipmap
    de un dict de textura. Con texturas grandes (ej. 5000x5000) construir
    la pirámide tiene un costo real, así que solo se hace si el filtrado
    mipmap está activado."""
    if tex.get("lin_mips") is None:
        tex["lin_mips"] = build_mip_pyramid(tex["lin"])
        tex["alpha_mips"] = build_mip_pyramid(tex["alpha"])
    return tex

def compute_uv_derivatives(U, V):
    """Derivadas por pixel de pantalla de las coordenadas UV (en unidades de
    'cambio de UV por pixel de pantalla'), usadas tanto para elegir el nivel
    de mip como para el muestreo anisotrópico."""
    dUdx = cv2.Sobel(U, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    dUdy = cv2.Sobel(U, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    dVdx = cv2.Sobel(V, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    dVdy = cv2.Sobel(V, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    return dUdx, dUdy, dVdx, dVdy

def compute_uv_footprint(U, V, tex_w, tex_h):
    """Estima, por pixel de pantalla, cuántos texeles cubre localmente ese
    pixel (el 'footprint' de minificación) a partir del gradiente de las
    coordenadas UV. Un footprint grande = la textura está muy comprimida ahí
    = se necesita un mip más borroso para evitar aliasing. (Versión
    isotrópica simple, ya no se usa para el muestreo -- se deja disponible
    por si se necesita en otro lado.)"""
    dUdx, dUdy, dVdx, dVdy = compute_uv_derivatives(U, V)
    ax = np.sqrt((dUdx * tex_w) ** 2 + (dVdx * tex_h) ** 2)
    ay = np.sqrt((dUdy * tex_w) ** 2 + (dVdy * tex_h) ** 2)
    return np.maximum(ax, ay)

def _sample_mip_trilinear(U, V, mip_levels, level_f, flip_v, wrap):
    """Muestrea la pirámide de mips en el nivel (fraccional) 'level_f' por
    pixel, mezclando entre los dos niveles enteros más cercanos."""
    max_level = len(mip_levels) - 1
    level_f = np.clip(level_f, 0.0, max_level)
    level_lo = np.floor(level_f).astype(np.int32)
    frac_full = level_f - level_lo

    result = None
    for lo in np.unique(level_lo):
        lo = int(lo)
        hi = min(lo + 1, max_level)
        mask = (level_lo == lo)
        proj_lo = compute_projection(U, V, mip_levels[lo], flip_v, wrap, 0.0, 0.0)
        if hi != lo:
            proj_hi = compute_projection(U, V, mip_levels[hi], flip_v, wrap, 0.0, 0.0)
            f = frac_full[:, :, None] if proj_lo.ndim == 3 else frac_full
            blended = proj_lo * (1.0 - f) + proj_hi * f
        else:
            blended = proj_lo
        if result is None:
            result = np.zeros_like(blended)
        m = mask[:, :, None] if blended.ndim == 3 else mask
        result = np.where(m, blended, result)
    return result

# Límites del filtro anisotrópico: más taps = mejor calidad en bordes muy
# comprimidos en una sola dirección (ej. vetas de madera vistas de canto),
# pero más lento. 8 taps cubre la mayoría de los casos reales sin disparar
# demasiado el tiempo de render.
ANISO_MAX_RATIO = 8.0
ANISO_TAPS = 8

def compute_projection_mipmapped(U, V, mip_levels, flip_v, wrap, offset_x=0.0, offset_y=0.0,
                                 taps=ANISO_TAPS, max_aniso=ANISO_MAX_RATIO):
    """Como compute_projection(), pero con mipmap + filtrado ANISOTRÓPICO.

    Un mip isotrópico (un solo nivel elegido por el peor de los dos ejes de
    compresión) evita el aliasing pero sobre-difumina el eje que NO está
    comprimido -- por ejemplo, vetas de madera vistas de canto: se comprimen
    mucho en una dirección pero casi nada en la otra. Isotrópico o bien deja
    ruido (si usa un nivel fino) o borra el detalle bueno (si usa un nivel
    grueso).

    En cambio, aquí: el nivel de mip se elige según el eje MENOS comprimido
    (footprint fino, poco blur), y se toman varias muestras (taps) repartidas
    a lo largo del eje MÁS comprimido, promediándolas. Esto reconstruye
    correctamente un pixel "elongado" sin perder detalle en la dirección que
    no lo necesita.
    """
    tex_h0, tex_w0 = mip_levels[0].shape[:2]
    U0 = U + offset_x
    V0 = V + offset_y
    dUdx, dUdy, dVdx, dVdy = compute_uv_derivatives(U0, V0)

    Lx = np.sqrt((dUdx * tex_w0) ** 2 + (dVdx * tex_h0) ** 2)
    Ly = np.sqrt((dUdy * tex_w0) ** 2 + (dVdy * tex_h0) ** 2)
    x_is_major = Lx >= Ly

    major_len = np.where(x_is_major, Lx, Ly)
    minor_len = np.where(x_is_major, Ly, Lx)
    major_dU = np.where(x_is_major, dUdx, dUdy)
    major_dV = np.where(x_is_major, dVdx, dVdy)

    minor_footprint = np.maximum(minor_len, 1.0)
    max_level = len(mip_levels) - 1
    level_f = np.clip(np.log2(minor_footprint), 0.0, max_level)

    aniso_ratio = np.clip(major_len / np.maximum(minor_len, 1e-6), 1.0, max_aniso)

    accum = None
    for k in range(taps):
        t = ((k + 0.5) / taps - 0.5) * aniso_ratio
        Uk = U0 + t * major_dU
        Vk = V0 + t * major_dV
        sample = _sample_mip_trilinear(Uk, Vk, mip_levels, level_f, flip_v, wrap)
        accum = sample if accum is None else accum + sample
    return accum / float(taps)



def load_texture_rgba(png_path):
    """Carga una textura y devuelve (rgb_lineal, alfa). Si la textura no
    tiene canal alfa (ej. un JPG), el alfa es 1.0 en toda la imagen (o sea,
    sin recorte alguno)."""
    img, has_alpha = load_image(png_path)
    rgb = img[:, :, :3].copy()
    if TEXTURE_IS_SRGB:
        rgb = srgb_to_linear(rgb).astype(np.float32)
    if has_alpha:
        alpha = np.clip(img[:, :, 3], 0.0, 1.0).astype(np.float32)
    else:
        alpha = np.ones(img.shape[:2], dtype=np.float32)
    return rgb, alpha

# ---------- track mattes / regiones (modo "single") ----------

def scan_track_mattes(folder):
    found = {}
    for fname in sorted(os.listdir(folder)):
        m = TRACK_MATTE_PATTERN.search(fname)
        if m:
            region_name = m.group(1).replace("_", " ").strip().capitalize()
            found[region_name] = os.path.join(folder, fname)
    return found

def scan_liquid_variants(folder):
    """Detecta variantes de color de líquido para el fondo 'sin etiqueta'
    del modo dual: archivos que terminan en 'base_label0-liquid_<nombre>.
    <ext>'. Devuelve un dict ORDENADO {nombre: ruta_completa}, ej.
    {'clear': '.../PASS-base_label0-liquid_clear.exr',
     'orange': '.../PASS-base_label0-liquid_orange.exr'}. 'nombre' se
    guarda en minúsculas tal cual aparece en el archivo -- es la clave
    interna (state['selected_liquid']); la UI se encarga de darle formato
    de exhibición (capitalizar, cambiar '_' por espacio, etc.)."""
    found = {}
    for fname in sorted(os.listdir(folder)):
        m = LIQUID_PATTERN.search(fname)
        if m:
            name = m.group(1).strip().lower()
            found[name] = os.path.join(folder, fname)
    return dict(sorted(found.items()))

def scan_sudado(view_folder):
    """Detecta la carpeta opcional 'sudado' dentro de una vista (ver
    SUDADO_DIRNAME). El mapa de normales (PASS-shading_normal) es
    obligatorio -- sin él no hay nada que distorsionar. El especular de
    reemplazo (PASS-all_black / PASS-especular) es opcional: si no está,
    'sudado' solo aplica la distorsión y sigue usando el especular normal
    de la vista. Devuelve None si la carpeta no existe o no tiene mapa de
    normales."""
    sudado_folder = os.path.join(view_folder, SUDADO_DIRNAME)
    if not os.path.isdir(sudado_folder):
        return None
    files = os.listdir(sudado_folder)
    normal_file = find_pass_file(files, "shading_normal")
    if normal_file is None:
        return None
    spec_file = find_pass_file_multi(files, ["all_black", "especular"])
    return {"folder": sudado_folder, "spec_file": spec_file, "normal_file": normal_file}

def liquid_swatch_rgb(bgr_linear):
    """Color representativo (RGB 0-255, sRGB) de una variante de líquido,
    para pintar el botón selector correspondiente en la UI. Se calcula
    directamente del promedio de los píxeles del propio pase -- así el
    botón siempre coincide con el color real de esa variante, sin
    depender de adivinar el color a partir del nombre del archivo (ej.
    'orange' podría no ser un naranja "de libro")."""
    srgb = linear_to_srgb(np.clip(bgr_linear, 0.0, 1.0))
    mean_bgr = srgb.reshape(-1, 3).mean(axis=0) * 255.0
    b, g, r = mean_bgr
    return (int(round(r)), int(round(g)), int(round(b)))

def load_matte_gray(path, target_shape):
    img, _ = load_image(path)
    gray = img[:, :, :3].mean(axis=2)
    if gray.shape != tuple(target_shape):
        gray = cv2.resize(gray, (target_shape[1], target_shape[0]),
                          interpolation=cv2.INTER_AREA)
    return np.clip(gray, 0.0, 1.0).astype(np.float32)

def build_region_weights(object_matte_2d, track_paths, eps=1e-6):
    h, w = object_matte_2d.shape
    if not track_paths:
        return {"Objeto": object_matte_2d[:, :, None].astype(np.float32)}

    raw = {name: load_matte_gray(path, (h, w)) for name, path in track_paths.items()}
    total = sum(raw.values())
    scale = np.where(total > object_matte_2d, object_matte_2d / np.maximum(total, eps), 1.0)
    scaled = {name: (m * scale) for name, m in raw.items()}
    covered = sum(scaled.values())
    remainder = np.clip(object_matte_2d - covered, 0.0, None)
    regions = dict(scaled)
    if remainder.max() > LEFTOVER_THRESHOLD:
        regions["Resto"] = remainder
    return {k: v[:, :, None].astype(np.float32) for k, v in regions.items()}

def color_layer_bgr(rgb_srgb_255, shape_hw):
    r, g, b = [c / 255.0 for c in rgb_srgb_255]
    r, g, b = srgb_to_linear(np.array(r)), srgb_to_linear(np.array(g)), srgb_to_linear(np.array(b))
    h, w = shape_hw
    out = np.empty((h, w, 3), np.float32)
    out[:, :, 0] = float(b); out[:, :, 1] = float(g); out[:, :, 2] = float(r)
    return out

def apply_color_layer(current, color_bgr, opacity, mode):
    if opacity <= 0.0:
        return current
    blended = blend(mode, current, color_bgr)
    return current * (1.0 - opacity) + blended * opacity

def render_composite_multi(base_bgr, spec_bgr, projected_dict, background_weight,
                           region_weights, region_levels, region_blend, region_color,
                           textured_regions=None):
    """textured_regions: nombres de región que SÍ deben recibir el multiply
    de la textura de etiqueta (típicamente solo 'Resto'/'Objeto'). Cualquier
    región que NO esté en este conjunto (ej. 'Cap', definida por su propio
    track_matte-*.exr) nunca recibe textura, sin importar qué UV haya
    quedado ahí en el pase -- solo se ve afectada por su propio especular y
    color personalizado. Si textured_regions es None, se aplica textura a
    todas las regiones (comportamiento antiguo)."""
    final = base_bgr * background_weight
    h, w = base_bgr.shape[:2]
    for name, wgt in region_weights.items():
        lv = region_levels[name]
        bl = region_blend[name]
        col = region_color[name]
        b = apply_levels(base_bgr, lv["Base"])
        sp = apply_levels(spec_bgr, lv["Especular"])
        if textured_regions is None or name in textured_regions:
            projected = projected_dict[name]
            tex_blend = blend(bl["Base"], b, projected)
        else:
            # Región "pintada" (ej. la tapa): sin textura de etiqueta.
            tex_blend = b
        if col["opacity"] > 0.0:
            color_bgr = color_layer_bgr(col["rgb"], (h, w))
            tex_blend = apply_color_layer(tex_blend, color_bgr, col["opacity"], bl["Color"])
        region_result = blend(bl["Especular"], tex_blend, sp)
        final = final + region_result * wgt
    return final

def apply_top_regions(base_image, spec_bgr, region_weights, region_levels, region_blend, region_color):
    """Pinta regiones con su propio track matte (ej. 'Cap') ENCIMA de
    'base_image' -- que aquí ya es un composite completo (ej. el resultado
    del modo 'dual', cuerpo+etiqueta), no un pase de color crudo. Usa
    exactamente la misma lógica de niveles/color/especular que
    render_composite_multi (Base leveled -> Shape-layer color opcional
    Multiply -> Especular Add), solo que 'base_image' hace las veces de
    pase de base (no hace falta un pase de color dedicado para la región).
    Estas regiones NUNCA reciben textura de etiqueta."""
    if not region_weights:
        return base_image
    h, w = base_image.shape[:2]
    contribution = np.zeros_like(base_image)
    total_weight = np.zeros((h, w, 1), dtype=np.float32)
    for name, wgt in region_weights.items():
        lv = region_levels[name]
        bl = region_blend[name]
        col = region_color[name]
        b = apply_levels(base_image, lv["Base"])
        sp = apply_levels(spec_bgr, lv["Especular"])
        tex_blend = b
        if col["opacity"] > 0.0:
            color_bgr = color_layer_bgr(col["rgb"], (h, w))
            tex_blend = apply_color_layer(tex_blend, color_bgr, col["opacity"], bl["Color"])
        region_result = blend(bl["Especular"], tex_blend, sp)
        contribution = contribution + region_result * wgt
        total_weight = total_weight + wgt
    total_weight = np.clip(total_weight, 0.0, 1.0)
    return base_image * (1.0 - total_weight) + contribution

def default_region_state(region_names):
    levels = {name: {"Base": dict(NEUTRAL), "Especular": dict(NEUTRAL)} for name in region_names}
    blends = {name: {"Base": DEFAULT_BLEND["Base"], "Especular": DEFAULT_BLEND["Especular"],
                     "Color": "Multiply"} for name in region_names}
    colors = {name: {"rgb": (128, 128, 128), "opacity": 0.0} for name in region_names}
    return levels, blends, colors

def build_neutral_state(region_names, offset_target, liquid_names=None):
    levels, blends, colors = default_region_state(region_names)
    return {
        "levels": levels, "blend": blends, "color": colors,
        "offset_x": 0.0, "offset_y": 0.0,
        "offset_target": offset_target,
        "region_names": region_names,
        "edges_threshold": DEFAULT_EDGES_THRESHOLD,
        # Variante de líquido activa (solo aplica si la vista tiene más de
        # un PASS-liquid-*.exr -- ver scan_liquid_variants). None si el
        # producto no tiene variantes de líquido.
        "selected_liquid": liquid_names[0] if liquid_names else None,
        # Opacidad del pase de arte (PASS-all_white) sobre el líquido de
        # fondo, SOLO en modo dual: 0 = respeta la transparencia propia de
        # la textura importada (comportamiento de siempre, el líquido se
        # asoma donde la textura tenga alfa=0); 1 = el arte cubre TODO el
        # área de etiqueta como envoltorio opaco, ignorando el alfa de la
        # textura -- útil en productos con líquido donde se quiere que la
        # etiqueta se vea sólida.
        "art_opacity": 0.0,
        # "Sudado" (gotas de condensación): activa el reemplazo del
        # especular (PASS-all_black) por la variante de 'sudado/' y la
        # distorsión por mapa de normales de la textura de arte. Solo
        # tiene efecto si la vista realmente tiene esa carpeta (ver
        # scan_sudado) -- si no, este flag simplemente no hace nada.
        "sudado_enabled": False,
        "sudado_strength": SUDADO_DISTORTION_STRENGTH_DEFAULT,
    }

def fit_display(arr, max_dim):
    h, w = arr.shape[:2]
    s = max_dim / float(max(h, w))
    if s < 1.0:
        return cv2.resize(arr, (int(round(w * s)), int(round(h * s))),
                          interpolation=cv2.INTER_AREA)
    return arr.copy()

# ---------- Patrón de fondo del viewport (solo vista previa) ----------

def generate_default_pattern(tile_size=48, spacing=16, bg_bgr=(26, 20, 18),
                             dot_bgr=(58, 48, 44)):
    """Genera un patrón de puntos sutil (BGR uint8) parecido al típico
    'fondo de transparencia' de apps de diseño, para cuando el usuario no
    haya puesto su propio 'background_pattern.png' en Productos/."""
    tile = np.full((tile_size, tile_size, 3), bg_bgr, dtype=np.uint8)
    r = max(1, spacing // 8)
    for y in range(spacing // 2, tile_size, spacing):
        for x in range(spacing // 2, tile_size, spacing):
            cv2.circle(tile, (x, y), r, dot_bgr, -1, lineType=cv2.LINE_AA)
    return tile

def tile_pattern_to_size(pattern_bgr, h, w):
    """Repite (tile) un patrón pequeño hasta cubrir un lienzo de h x w."""
    ph, pw = pattern_bgr.shape[:2]
    reps_y = int(np.ceil(h / ph))
    reps_x = int(np.ceil(w / pw))
    big = np.tile(pattern_bgr, (reps_y, reps_x, 1))
    return np.ascontiguousarray(big[:h, :w])

def composite_over_pattern(srgb_bgr_uint8, alpha, pattern_bgr_uint8):
    """Compone una imagen ya convertida a sRGB 8-bit sobre un patrón de
    fondo tileado, usando 'alpha' (0..1, HxW) como máscara de la silueta
    del producto. SOLO para vista previa -- el guardado/export nunca pasa
    por aquí."""
    if alpha is None:
        return srgb_bgr_uint8
    h, w = srgb_bgr_uint8.shape[:2]
    if alpha.shape[:2] != (h, w):
        alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA)
    tile = tile_pattern_to_size(pattern_bgr_uint8, h, w)
    a = alpha[:, :, None].astype(np.float32)
    out = srgb_bgr_uint8.astype(np.float32) * a + tile.astype(np.float32) * (1.0 - a)
    return out.clip(0, 255).astype(np.uint8)

def fit_display_2d(arr2d, max_dim):
    h, w = arr2d.shape[:2]
    s = max_dim / float(max(h, w))
    if s < 1.0:
        return cv2.resize(arr2d, (int(round(w * s)), int(round(h * s))),
                          interpolation=cv2.INTER_AREA)
    return arr2d.copy()

def fit_display_2d_coords(arr2d, max_dim):
    """Igual que fit_display_2d pero para datos DISCONTINUOS como coordenadas
    UV (donde 'fuera de área' = 0,0 no es un promedio válido con un valor UV
    real vecino). Usa vecino-más-cercano para no inventar coordenadas UV
    intermedias en los bordes, lo cual produciría un 'hormigueo'/ruido al
    proyectar la textura justo en los límites de la etiqueta o de una
    región (ej. borde de la tapa)."""
    h, w = arr2d.shape[:2]
    s = max_dim / float(max(h, w))
    if s < 1.0:
        return cv2.resize(arr2d, (int(round(w * s)), int(round(h * s))),
                          interpolation=cv2.INTER_NEAREST)
    return arr2d.copy()

# ---------- Composición modo "dual" (label0 / label1) ----------

def render_dual_frame(data, tex, state, wrap, flip_v, use_mipmap=True):
    """Recrea la cadena de After Effects:
       1. especular  // alpha matte de (2) // Add
       2. uvpass     // Multiply
       3. base_label1 // alpha matte de (2)
       4. base_label0 (fondo) -- o la variante de líquido seleccionada, si
          la vista tiene varias (ver scan_liquid_variants)
    donde el "alpha matte de (2)" = región válida de UV (no-negro) x alfa de
    la textura proyectada vía UV.

    Si además hay regiones con su propio track matte (ej. "Cap"), se
    pintan ENCIMA de todo lo anterior con su propio especular/color, sin
    recibir nunca la textura de etiqueta (ver apply_top_regions) --
    replica la cadena adicional:
       1. especular // luma matte de track_matte-cap // Add
       2. track_matte-cap
       3. Shape Layer (color) // luma matte de track_matte-cap // Multiply
    """
    U, V = data["U"], data["V"]
    ox, oy = state["offset_x"], state["offset_y"]

    # "Sudado": distorsiona SOLO la textura de arte proyectada (refracción
    # de gotas de agua) y, si hay un especular de reemplazo en la carpeta
    # 'sudado/', lo usa en vez del especular normal de la vista.
    sudado_on = bool(state.get("sudado_enabled")) and data.get("sudado") is not None
    if sudado_on:
        nx, ny = compute_normal_distortion_offset(
            data["sudado"]["normal"],
            state.get("sudado_strength", SUDADO_DISTORTION_STRENGTH_DEFAULT),
            SUDADO_NORMAL_THRESHOLD)
        spec_source = data["sudado"]["spec"] if data["sudado"].get("spec") is not None else data["spec"]
    else:
        nx, ny = 0.0, 0.0
        spec_source = data["spec"]
    ox_tex, oy_tex = ox + nx, oy + ny

    if use_mipmap:
        projected_tex = compute_projection_mipmapped(U, V, tex["lin_mips"], flip_v, wrap, ox_tex, oy_tex)
        projected_alpha = compute_projection_mipmapped(U, V, tex["alpha_mips"], flip_v, wrap, ox_tex, oy_tex)
    else:
        projected_tex = compute_projection(U, V, tex["lin"], flip_v, wrap, ox_tex, oy_tex)
        projected_alpha = compute_projection(U, V, tex["alpha"], flip_v, wrap, ox_tex, oy_tex)
    matte = (data["label_region_mask"] * projected_alpha)[:, :, None].astype(np.float32)

    # Opacidad de arte: empuja el matte hacia "cobertura completa" del área
    # de etiqueta, ignorando (parcial o totalmente) la transparencia propia
    # de la textura importada. 0 = comportamiento de siempre.
    art_opacity = float(state.get("art_opacity", 0.0))
    if art_opacity > 0.0:
        full_matte = data["label_region_mask"][:, :, None].astype(np.float32)
        matte = matte * (1.0 - art_opacity) + full_matte * art_opacity

    lv = state["levels"][LABEL_REGION_NAME]
    bl = state["blend"][LABEL_REGION_NAME]
    col = state["color"][LABEL_REGION_NAME]

    b = apply_levels(data["base_label1"], lv["Base"])
    sp = apply_levels(spec_source, lv["Especular"])

    tex_blend = blend(bl["Base"], b, projected_tex)  # normalmente Multiply
    if col["opacity"] > 0.0:
        h, w = data["base_label1"].shape[:2]
        color_bgr = color_layer_bgr(col["rgb"], (h, w))
        tex_blend = apply_color_layer(tex_blend, color_bgr, col["opacity"], bl["Color"])

    label_result = blend(bl["Especular"], tex_blend, sp)  # normalmente Add

    # Variante de líquido activa (si la vista tiene varias -- ej.
    # liquid_clear, liquid_orange). Si no hay variantes, se usa el único
    # base_label0 de siempre.
    base0 = data["base_label0"]
    variants = data.get("liquid_variants")
    if variants:
        base0 = variants.get(state.get("selected_liquid"), base0)

    # Donde matte=1 se ve la etiqueta (con textura+especular); donde matte=0
    # se revela base_label0/líquido (producto sin etiqueta), sin cambios.
    body = base0 * (1.0 - matte) + label_result * matte

    region_weights = data.get("region_weights")
    if region_weights:
        body = apply_top_regions(body, spec_source, region_weights,
                                 state["levels"], state["blend"], state["color"])
    return body

def render_single_frame(data, tex, state, wrap, flip_v, use_mipmap=True):
    """Modo original: regiones / track mattes sobre una sola base (label1).
    Solo la región "textured" (el cuerpo/etiqueta, ej. 'Resto') recibe la
    textura proyectada. Regiones definidas por su propio track matte (ej.
    'Cap') NUNCA reciben textura -- solo su propio especular y color
    personalizado -- sin importar qué UV haya quedado ahí en el pase."""
    U, V = data["U"], data["V"]
    ox, oy = state["offset_x"], state["offset_y"]
    target = state["offset_target"]
    textured_regions = data.get("textured_regions")

    # "Sudado": igual que en render_dual_frame -- distorsiona SOLO la
    # textura de arte proyectada (nunca una región pintada como Cap, que ya
    # queda excluida más abajo) y, si hay especular de reemplazo, lo usa en
    # vez del especular normal de la vista.
    sudado_on = bool(state.get("sudado_enabled")) and data.get("sudado") is not None
    if sudado_on:
        nx, ny = compute_normal_distortion_offset(
            data["sudado"]["normal"],
            state.get("sudado_strength", SUDADO_DISTORTION_STRENGTH_DEFAULT),
            SUDADO_NORMAL_THRESHOLD)
        spec_source = data["sudado"]["spec"] if data["sudado"].get("spec") is not None else data["spec"]
    else:
        nx, ny = 0.0, 0.0
        spec_source = data["spec"]

    proj_per_region = {}
    for name in data["region_names"]:
        if textured_regions is not None and name not in textured_regions:
            continue  # región pintada (ej. Cap): no necesita textura proyectada
        ox_r, oy_r = (ox, oy) if name == target else (0.0, 0.0)
        ox_r, oy_r = ox_r + nx, oy_r + ny
        if use_mipmap:
            proj = compute_projection_mipmapped(U, V, tex["lin_mips"], flip_v, wrap, ox_r, oy_r)
        else:
            proj = compute_projection(U, V, tex["lin"], flip_v, wrap, ox_r, oy_r)
        proj_per_region[name] = proj
    return render_composite_multi(data["base"], spec_source, proj_per_region,
                                  data["background_weight"], data["region_weights"],
                                  state["levels"], state["blend"], state["color"],
                                  textured_regions=textured_regions)

def render_frame(data, tex, state, wrap, flip_v, use_mipmap=True):
    """Despachador genérico. 'data' puede ser una escena a resolución
    completa o una versión reducida para display; 'tex' es un dict con
    claves 'lin', 'alpha', 'lin_mips' y 'alpha_mips' (pirámides de mipmap,
    solo se usan si use_mipmap=True). 'use_mipmap' permite desactivar el
    filtrado anti-aliasing por mipmap para respuesta más fluida en tiempo
    real (sacrificando calidad en zonas muy minificadas)."""
    if use_mipmap:
        _ensure_mips(tex)
    if data["mode"] == "dual":
        return render_dual_frame(data, tex, state, wrap, flip_v, use_mipmap)
    return render_single_frame(data, tex, state, wrap, flip_v, use_mipmap)

# ---------- Supersampling (SSAA) -- antialiasing de bordes geométricos ----------
#
# El mipmap de arriba corrige el aliasing por MINIFICACIÓN de la textura
# (cuando se ve muy comprimida en pantalla). Pero no toca el aliasing
# geométrico de borde -- el clásico "escalón"/dentado de la silueta del
# producto, el límite entre regiones (ej. tapa vs cuerpo), o los bordes de
# alto contraste de la propia etiqueta (texto/logos) -- porque esos bordes
# se siguen evaluando con UN solo muestreo por pixel de salida.
#
# La solución clásica es supersampling: renderizar el composite completo a
# N veces la resolución final y reducirlo con un filtro de área (el mismo
# tipo de box-filter que ya usa fit_display con INTER_AREA), promediando
# así varios sub-muestreos por cada pixel final. Esto suaviza CUALQUIER
# borde con aliasing -- mattes, silueta, límites de región, y el aliasing
# residual de la textura que el mipmap por sí solo no elimina del todo --
# sin necesitar ningún pase adicional (ej. un AOV de "edges"). El costo es
# cuadrático con el factor (2x = 4 veces más trabajo), así que, igual que
# el mipmap, solo tiene sentido activarlo al exportar/guardar, nunca para
# la vista previa en tiempo real.

def build_supersampled_scene(scene, factor):
    """Devuelve una copia de 'scene' con todos sus arrays de datos (U, V,
    base(s), especular, mattes/pesos de región) escalados a 'factor' veces
    la resolución original. U/V se interpolan con INTER_LINEAR: al
    AUMENTAR resolución (a diferencia de fit_display_2d_coords, que usa
    NEAREST al REDUCIR para no inventar UVs falsas en los bordes) es
    seguro y deseable interpolar suavemente entre muestras vecinas ya
    válidas -- cualquier valor "intermedio" que caiga justo en la
    silueta exterior queda automáticamente atenuado después, porque su
    peso de matte (background_weight/region_weights, también
    supersampleado) será pequeño ahí."""
    if factor is None or factor <= 1:
        return scene

    def up(arr, interp=cv2.INTER_LINEAR):
        h, w = arr.shape[:2]
        nh, nw = max(1, int(round(h * factor))), max(1, int(round(w * factor)))
        return cv2.resize(arr, (nw, nh), interpolation=interp)

    out = dict(scene)
    out["U"] = up(scene["U"])
    out["V"] = up(scene["V"])
    out["spec"] = up(scene["spec"])

    if scene.get("sudado"):
        sud = scene["sudado"]
        out["sudado"] = {
            "normal": up(sud["normal"]),
            "spec": up(sud["spec"]) if sud.get("spec") is not None else None,
        }

    if scene["mode"] == "dual":
        out["base_label0"] = up(scene["base_label0"])
        out["base_label1"] = up(scene["base_label1"])
        out["label_region_mask"] = up(scene["label_region_mask"])
        if scene.get("liquid_variants"):
            out["liquid_variants"] = {name: up(v) for name, v in scene["liquid_variants"].items()}
        if scene.get("region_weights"):
            rw = {}
            for k, v in scene["region_weights"].items():
                r = up(v)
                rw[k] = r[:, :, None] if r.ndim == 2 else r
            out["region_weights"] = rw
    else:
        out["base"] = up(scene["base"])
        bg = up(scene["background_weight"])
        out["background_weight"] = bg[:, :, None] if bg.ndim == 2 else bg
        rw = {}
        for k, v in scene["region_weights"].items():
            r = up(v)
            rw[k] = r[:, :, None] if r.ndim == 2 else r
        out["region_weights"] = rw
    return out

def downsample_result(comp, factor, target_hw=None):
    """Reduce el resultado final con un filtro de área (promedia los
    sub-muestreos), que es exactamente lo que hace que el supersampling
    suavice los bordes. 'target_hw' permite forzar el tamaño exacto
    (h, w) de la escena original, evitando desajustes de +-1 pixel por
    redondeo al escalar de ida y vuelta."""
    if factor is None or factor <= 1:
        return comp
    if target_hw is not None:
        h, w = target_hw
    else:
        h0, w0 = comp.shape[:2]
        h, w = max(1, int(round(h0 / factor))), max(1, int(round(w0 / factor)))
    return cv2.resize(comp, (w, h), interpolation=cv2.INTER_AREA)

# ---------- Construcción de escena para una vista ----------

def build_scene(view_folder):
    """Carga uv/base(s)/especular/track-mattes desde la carpeta de una vista
    y detecta automáticamente si el modo es 'dual' (label0+label1) o
    'single' (solo label1, con regiones/track mattes). Cada pase puede
    venir en un formato de archivo distinto (ej. uvpass.exr junto con
    base_label1.png) -- ver find_pass_file / PASS_EXTENSIONS."""
    files = os.listdir(view_folder)
    uv_file = find_pass_file(files, "uvpass")
    base0_file = find_pass_file(files, "base_label0")
    # Convención nueva: PASS-all_white = pase "con arte" (antes
    # base_label1). Se prueba primero la nueva, con fallback a la vieja
    # para vistas que todavía no se hayan renombrado.
    base1_file = find_pass_file_multi(files, ["all_white", "base_label1"])
    # Convención nueva: PASS-all_black = especular (antes PASS-especular.exr).
    spec_file = find_pass_file_multi(files, ["all_black", "especular"])
    # Pase OPCIONAL de bordes (ej. PASS-edges.exr): si el usuario lo pone en
    # la carpeta de la vista, se usa para un antialiasing DIRIGIDO solo en
    # esas zonas al exportar (ver build_export_composite en remap_app.py).
    # Si no existe, el export sigue funcionando exactamente como antes
    # (supersampling de imagen completa según la opción del editor, o sin
    # antialiasing extra si esa opción está en "Desactivado").
    edges_file = find_pass_file(files, "edges")
    # Variantes de color de líquido (ej. PASS-liquid-mayonesa_light.exr,
    # PASS-liquid_clear.exr): cuentan como "base_label0" para efectos de
    # validación/modo dual, aunque no exista un PASS-base_label0.exr plano.
    liquid_files = scan_liquid_variants(view_folder)

    if uv_file is None:
        raise ValueError(f"Falta el pase de UV (uvpass) en {view_folder}")
    if base0_file is None and base1_file is None and not liquid_files:
        raise ValueError(
            f"Faltan archivos base (PASS-all_white / PASS-base_label1 / "
            f"PASS-base_label0 / PASS-liquid-*) en {view_folder}")

    # El pase de UV es un pase de DATOS (coordenadas, no color): is_color=False.
    uv, uv_has_alpha = load_image(os.path.join(view_folder, uv_file), is_color=False)
    U = uv[:, :, 2].copy()
    V = uv[:, :, 1].copy()

    base1_bgr = None
    base1_has_alpha = False
    if base1_file:
        # Pase de COLOR: si viene en un formato de 8 bits (PNG/JPG) se
        # convierte de sRGB a lineal para que la mezcla sea consistente
        # sin importar si el resto de los pases son EXR.
        base1_img, base1_has_alpha = load_image(os.path.join(view_folder, base1_file), is_color=True)
        base1_bgr = base1_img[:, :, :3].astype(np.float32)

    # Corrección de borde de silueta: muchos renderers (Modo incluido)
    # exportan el pase de UV con el antialiasing de la silueta "premultiplicado"
    # -- en los pixeles de cobertura parcial del borde, el valor UV real se
    # mezcla proporcionalmente con 0, dejando una coordenada UV que no
    # corresponde a ningún punto real de la superficie. Al proyectar una
    # textura de alto contraste con esa coordenada corrupta, aparece un
    # borde "sucio"/dentado justo en la silueta exterior -- el mismo
    # síntoma reportado para el nodo Map UV de Blender y otros compositores
    # basados en pases de UV (no está relacionado con la resolución del
    # pase ni con el filtrado de minificación). Revertir la premultiplicación
    # (dividir por el alfa) recupera la coordenada UV real.
    if base1_has_alpha:
        U, V = unpremultiply_uv(U, V, base1_img[:, :, 3])

    # Región válida del pase de UV (usada para el recorte de etiqueta en modo
    # "dual"): se ignora el alfa del propio uvpass y en su lugar se usa el
    # negro puro (R0,G0,B0) como transparencia/fuera-de-área.
    label_region_mask = ((np.abs(U) + np.abs(V)) > 1e-6).astype(np.float32)

    dual_mode = base0_file is not None or bool(liquid_files)

    # Canal alfa "de exportación": el alfa de PASS-base_label1 (la
    # silueta con etiqueta), que se reaplica a la imagen final al guardar,
    # tal como en el archivo de origen.
    output_alpha = None
    if base1_file and base1_has_alpha:
        output_alpha = np.clip(base1_img[:, :, 3], 0.0, 1.0).astype(np.float32)

    # Máscara de bordes (opcional): valores continuos 0..1 (promedio de
    # RGB, como cualquier otro matte de datos) que indican dónde el
    # composite final debe suavizarse -- típicamente una línea blanca sobre
    # negro trazando la silueta/las aristas del producto.
    edges_mask = None
    if edges_file:
        edges_mask = load_matte_gray(os.path.join(view_folder, edges_file), U.shape)

    scene = {"U": U, "V": V, "label_region_mask": label_region_mask,
             "output_alpha": output_alpha, "edges_mask": edges_mask}

    # "Sudado" (gotas de condensación): carpeta opcional dentro de la vista
    # (ver SUDADO_DIRNAME/scan_sudado). None si no existe -- en ese caso el
    # toggle de la UI simplemente no aparece para esta vista.
    sudado_info = scan_sudado(view_folder)
    sudado_data = None
    if sudado_info is not None:
        sudado_spec_bgr = None
        if sudado_info["spec_file"]:
            s_img, _ = load_image(os.path.join(sudado_info["folder"], sudado_info["spec_file"]),
                                  is_color=True)
            sudado_spec_bgr = s_img[:, :, :3].astype(np.float32)
        # El mapa de normales es un pase de DATOS (vectores, no color):
        # is_color=False, igual que el pase de UV.
        n_img, _ = load_image(os.path.join(sudado_info["folder"], sudado_info["normal_file"]),
                              is_color=False)
        sudado_normal = n_img[:, :, :3].astype(np.float32)
        sudado_data = {"spec": sudado_spec_bgr, "normal": sudado_normal}
    scene["sudado"] = sudado_data

    if dual_mode:
        liquid_variants = None
        liquid_names = None
        liquid_swatches = None
        if liquid_files:
            liquid_variants, liquid_swatches = {}, {}
            for name, fpath in liquid_files.items():
                v_img, _ = load_image(fpath, is_color=True)
                v_bgr = v_img[:, :, :3].astype(np.float32)
                liquid_variants[name] = v_bgr
                liquid_swatches[name] = liquid_swatch_rgb(v_bgr)
            liquid_names = sorted(liquid_variants.keys())
            base0_bgr = liquid_variants[liquid_names[0]]
        else:
            base0_img, _ = load_image(os.path.join(view_folder, base0_file), is_color=True)
            base0_bgr = base0_img[:, :, :3].astype(np.float32)

        if base1_bgr is None:
            base1_bgr = base0_bgr.copy()
        if spec_file:
            spec_img, _ = load_image(os.path.join(view_folder, spec_file), is_color=True)
            spec_bgr = spec_img[:, :, :3].astype(np.float32)
        else:
            spec_bgr = np.zeros_like(base0_bgr)

        # Regiones adicionales con su propio track matte (ej. "Cap", en
        # botellas con líquido): se pintan ENCIMA de todo el composite del
        # cuerpo/etiqueta, con su propio color/especular -- igual que en
        # modo "single", pero usando el composite ya armado como su propio
        # "pase de base" (no hace falta uno dedicado). El "Resto" que
        # build_region_weights generaría para el área NO cubierta por
        # ningún track matte se descarta A PROPÓSITO: esa área ya es
        # exactamente el cuerpo/etiqueta que maneja el resto de esta
        # función -- pintarla de nuevo aquí duplicaría el especular sobre
        # toda la botella.
        track_paths = scan_track_mattes(view_folder)
        extra_region_weights = {}
        if track_paths:
            if base1_has_alpha:
                full_matte = base1_img[:, :, 3].copy()
            elif uv_has_alpha:
                full_matte = uv[:, :, 3].copy()
            else:
                full_matte = label_region_mask
            full_matte = np.clip(full_matte, 0.0, 1.0).astype(np.float32)
            all_weights = build_region_weights(full_matte, track_paths)
            extra_region_weights = {k: v for k, v in all_weights.items() if k in track_paths}

        region_names = [LABEL_REGION_NAME] + sorted(extra_region_weights.keys())
        scene.update({
            "mode": "dual",
            "base_label0": base0_bgr,
            "base_label1": base1_bgr,
            "spec": spec_bgr,
            "region_names": region_names,
            "liquid_variants": liquid_variants,
            "liquid_names": liquid_names,
            "liquid_swatches": liquid_swatches,
            "region_weights": extra_region_weights if extra_region_weights else None,
        })
    else:
        if spec_file:
            spec_img, _ = load_image(os.path.join(view_folder, spec_file), is_color=True)
            spec_bgr = spec_img[:, :, :3].astype(np.float32)
        else:
            spec_bgr = np.zeros_like(base1_bgr)

        # Silueta COMPLETA del producto (botella + tapa), usada para repartir
        # las regiones/track mattes (ej. Cap vs Resto). A diferencia del
        # recorte de etiqueta del modo "dual", aquí SÍ se usa el alfa
        # disponible, porque la silueta completa suele exceder el área de
        # UV válida del label (la tapa normalmente no tiene UV de etiqueta).
        if base1_has_alpha:
            object_matte = base1_img[:, :, 3].copy()
        elif uv_has_alpha:
            object_matte = uv[:, :, 3].copy()
        else:
            object_matte = label_region_mask
        object_matte = np.clip(object_matte, 0.0, 1.0).astype(np.float32)

        track_paths = scan_track_mattes(view_folder)
        region_weights = build_region_weights(object_matte, track_paths)
        background_weight = (1.0 - object_matte)[:, :, None].astype(np.float32)
        # Solo la región "restante" (el cuerpo con la etiqueta, no cubierto
        # por ningún track matte nombrado) recibe la textura proyectada.
        # Cualquier región definida por su propio track_matte-*.exr (ej.
        # "Cap") es una región "pintada": nunca recibe textura, sin importar
        # qué UV haya quedado ahí en el pase.
        textured_regions = set(region_weights.keys()) - set(track_paths.keys())
        scene.update({
            "mode": "single",
            "base": base1_bgr,
            "spec": spec_bgr,
            "region_weights": region_weights,
            "background_weight": background_weight,
            "region_names": list(region_weights.keys()),
            "textured_regions": textured_regions,
        })

    return scene

# ---------- Detección de productos y vistas ----------

def scan_views(product_folder):
    """Devuelve la lista de subcarpetas (vistas) que contienen escenas
    válidas dentro de la carpeta de un producto. Cada pase puede estar en
    cualquier formato soportado (ver PASS_EXTENSIONS)."""
    views = []
    if not os.path.isdir(product_folder):
        return views
    for item in sorted(os.listdir(product_folder)):
        subpath = os.path.join(product_folder, item)
        if os.path.isdir(subpath):
            try:
                files = os.listdir(subpath)
            except OSError:
                continue
            has_uv = find_pass_file(files, "uvpass") is not None
            has_base = (find_pass_file_multi(
                            files, ["all_white", "base_label1", "base_label0"]) is not None
                       or bool(scan_liquid_variants(subpath)))
            if has_uv and has_base:
                views.append(item)
    return sorted(views)

def scan_products(products_folder):
    """Devuelve la lista de subcarpetas (productos) dentro de Productos/ que
    contienen al menos una vista válida. Totalmente dinámico: no hay
    nombres de producto hardcodeados."""
    products = []
    if not os.path.isdir(products_folder):
        return products
    for item in sorted(os.listdir(products_folder)):
        subpath = os.path.join(products_folder, item)
        if os.path.isdir(subpath) and scan_views(subpath):
            products.append(item)
    return products

# ============================================================
#  Exportación (composite final, escritura de archivos, y trabajo
#  ejecutable en un proceso separado para exportar varias vistas en
#  paralelo aprovechando varios núcleos del CPU)
# ============================================================
#
# Estas funciones son deliberadamente "puras": no dependen de Tkinter ni de
# ningún estado de la app (RemapStudioApp), solo de datos simples (arrays,
# rutas, dicts de números/strings). Eso es lo que permite mandarlas a un
# ProcessPoolExecutor -- un proceso hijo puede importar remap_core y llamar
# a export_view_job(job) sin necesitar nada más que esté en el proceso
# principal.

def render_export_composite(scene, tex, state, wrap, flip_v, mipmap_enabled, supersample_factor):
    """Renderiza el composite final para Guardar/Exportar, con antialiasing
    de borde:

    - Si la vista tiene un pase de bordes (PASS-edges.exr o cualquier otra
      extensión soportada, en la carpeta de la vista): se usa como máscara
      para MEZCLAR el render normal con una versión supersampleada SOLO
      donde el pase marca un borde. El resto de la imagen queda
      exactamente igual al render normal, sin pasar por ningún escalado de
      ida y vuelta.
    - Si no hay pase de bordes: el factor de supersampling se aplica a la
      imagen COMPLETA (o ninguno, si supersample_factor <= 1)."""
    edges_mask = scene.get("edges_mask")
    target_hw = scene["U"].shape[:2]

    if edges_mask is not None:
        # Modo dirigido: si el factor pedido es <=1 igual se aplica un 2x
        # mínimo en los bordes marcados -- detectar el pase ya es la señal
        # explícita de que se quiere antialiasing ahí.
        factor = supersample_factor if supersample_factor > 1 else 2
        sharp = render_frame(scene, tex, state, wrap, flip_v, use_mipmap=mipmap_enabled)
        ss_scene = build_supersampled_scene(scene, factor)
        smoothed = render_frame(ss_scene, tex, state, wrap, flip_v, use_mipmap=mipmap_enabled)
        smoothed = downsample_result(smoothed, factor, target_hw=target_hw)

        m = edges_mask
        if m.shape[:2] != sharp.shape[:2]:
            m = cv2.resize(m, (sharp.shape[1], sharp.shape[0]), interpolation=cv2.INTER_AREA)
        m = np.clip(m, 0.0, 1.0).astype(np.float32)[:, :, None]
        return sharp * (1.0 - m) + smoothed * m

    if supersample_factor > 1:
        render_scene = build_supersampled_scene(scene, supersample_factor)
        comp = render_frame(render_scene, tex, state, wrap, flip_v, use_mipmap=mipmap_enabled)
        return downsample_result(comp, supersample_factor, target_hw=target_hw)

    return render_frame(scene, tex, state, wrap, flip_v, use_mipmap=mipmap_enabled)

def write_render_output(comp, scene, stem):
    """Escribe {stem}.exr + {stem}.png (con el canal alfa de salida
    reaplicado si estaba disponible). Devuelve (exr_path, png_path)."""
    exr_path = stem + ".exr"
    png_path = stem + ".png"

    output_alpha = scene.get("output_alpha")
    if output_alpha is not None:
        if output_alpha.shape[:2] != comp.shape[:2]:
            output_alpha = cv2.resize(output_alpha, (comp.shape[1], comp.shape[0]),
                                      interpolation=cv2.INTER_AREA)
        exr_out = np.dstack([comp.astype(np.float32), output_alpha.astype(np.float32)])
        prev_rgb = (linear_to_srgb(comp) * 255.0).clip(0, 255).astype(np.uint8)
        alpha_8u = (np.clip(output_alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
        png_out = np.dstack([prev_rgb, alpha_8u])
        cv2.imwrite(exr_path, exr_out)
        cv2.imwrite(png_path, png_out)
    else:
        cv2.imwrite(exr_path, comp.astype(np.float32))
        prev = (linear_to_srgb(comp) * 255.0).clip(0, 255).astype(np.uint8)
        cv2.imwrite(png_path, prev)
    return exr_path, png_path

def _load_export_texture(tex_path):
    """Reconstruye el dict de textura DESDE DISCO para un proceso hijo (en
    vez de recibir los arrays ya cargados por IPC, lo cual sería mucho más
    pesado de serializar -- el PNG convertido en session_tex_dir es un
    archivo normal en disco, cualquier proceso puede leerlo)."""
    if tex_path and os.path.exists(tex_path):
        lin, alpha = load_texture_rgba(tex_path)
    else:
        gray = np.full((512, 512, 3), 0.5, dtype=np.float32)
        if TEXTURE_IS_SRGB:
            gray = srgb_to_linear(gray)
        lin, alpha = gray.astype(np.float32), np.ones((512, 512), dtype=np.float32)
    return {"lin": lin, "alpha": alpha, "lin_mips": None, "alpha_mips": None}

def export_view_job(job):
    """Trabajo de exportación de UNA vista, pensado para correr en un
    proceso separado (ver export_all_views/save_dialog en remap_app.py, que
    reparten estos jobs en un ProcessPoolExecutor). 'job' es un dict de
    solo tipos simples (rutas, números, el 'state' de esa vista -- nunca
    objetos de Tkinter ni arrays pesados) para que sea 'picklable' y se
    pueda enviar a un proceso hijo. Reconstruye la escena y la textura
    DESDE DISCO en ese proceso -- evita tener que serializar por IPC los
    arrays grandes de la escena/textura, que sería más lento que
    simplemente releerlos."""
    view = job.get("view")
    try:
        view_folder = os.path.join(job["products_folder"], job["product"], view)
        scene = build_scene(view_folder)
        tex = _load_export_texture(job.get("tex_path"))
        state = job["state"]

        threshold = state.get("edges_threshold", DEFAULT_EDGES_THRESHOLD)
        if threshold and threshold > 0:
            U_c, V_c = clean_uv_seams(scene["U"], scene["V"], threshold)
            scene = dict(scene)
            scene["U"] = U_c
            scene["V"] = V_c

        comp = render_export_composite(scene, tex, state, job["wrap"], FLIP_V,
                                       job["mipmap_enabled"], job["export_supersample"])
        exr_path, png_path = write_render_output(comp, scene, job["stem"])
        return {"view": view, "ok": True, "exr": exr_path, "png": png_path, "error": None}
    except Exception as e:
        return {"view": view, "ok": False, "exr": None, "png": None, "error": str(e)}
