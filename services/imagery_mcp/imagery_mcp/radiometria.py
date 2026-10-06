"""RADIOMETRÍA e índices: el factor de escala de cada escena (y de dónde sale), los índices de
diferencia normalizada, y cómo se le cuenta al consumidor qué se descartó y por qué.

Salió de `engine.py` (F4 del plan de calidad: tenía 1.303 líneas), tal cual.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from imagery_mcp.providers import (
    _SRC_ASSET,
    _SRC_BASELINE,
    _SRC_COLECCION,
    _SRC_IDENTIDAD,
    BOA_OFFSET_BASELINE,
    BOA_OFFSET_FECHA,
    BandScaling,
    Scene,
    baseline_value,
)


def _eng():
    """`engine` importa este módulo; lo que vive allí (y las pruebas sustituyen allí) se resuelve
    al usarlo."""
    from imagery_mcp import engine

    return engine


# Desde el baseline 04.00 (25 de enero de 2022) los productos L2A traen
# BOA_ADD_OFFSET = -1000. En un cociente normalizado ese offset se cancela en el
# numerador pero NO en el denominador, así que un NDVI calculado sobre el DN
# crudo sale ~0,20 bajo: con reflectancias reales 0,08/0,40 el NDVI verdadero es
# 0,6667 y el del DN post-04.00 es 0,4706. Todo lo que consuma el índice —stats,
# teselas, narrativa— hereda el sesgo sin ninguna señal.
#
# El factor y su origen los resuelve providers.py en tres escalones (asset STAC →
# colección → derivado de la especificación de ESA). Aquí solo se APLICA y se
# DECLARA. `baseline_value` y el corte se reexportan desde providers para que
# haya una sola definición del umbral en todo el servicio.
_BOA_OFFSET_BASELINE = BOA_OFFSET_BASELINE


_IDENTITY_SCALING = BandScaling()


# Cómo se le cuenta al consumidor de dónde salió el factor. La distinción
# importa: los dos primeros son un dato que publica el catálogo, el tercero es
# una inferencia nuestra a partir de la especificación de ESA.
_FUENTE_TEXTO = {
    _SRC_ASSET: "scale/offset publicados por el asset STAC (raster:bands)",
    _SRC_COLECCION: (
        "scale/offset publicados por la colección STAC (item_assets.raster:bands)"
    ),
    _SRC_BASELINE: (
        "DERIVADO de s2:processing_baseline con la fórmula de ESA "
        "(L2A_SR = (DN + BOA_ADD_OFFSET) / 10000); el proveedor no publica el factor"
    ),
    _SRC_IDENTIDAD: (
        "sin factor: el asset no publica scale/offset y el baseline no es legible, "
        "así que se usó el DN tal cual"
    ),
}


# T5.5: índices de diferencia normalizada (a - b) / (a + b) sobre bandas CANÓNICAS. El mismo
# `compute_ndvi_detallado` los calcula todos (con a = «nir», b = «red» es el NDVI de siempre).
INDICES: dict[str, dict] = {
    "ndvi": {"bandas": ("nir", "red"), "nombre": "NDVI", "colormap": "rdylgn",
             "lectura": "vigor de la vegetación: alto = vegetación densa y sana; ~0 = suelo o construido; <0 = agua"},
    "ndwi": {"bandas": ("green", "nir"), "nombre": "NDWI", "colormap": "rdbu",
             "lectura": "agua superficial (McFeeters): >0 = agua; <0 = suelo o vegetación"},
    "ndbi": {"bandas": ("swir16", "nir"), "nombre": "NDBI", "colormap": "rdylgn_r",
             "lectura": "superficie construida o suelo desnudo: alto = construido; <0 = vegetación o agua"},
    "ndmi": {"bandas": ("nir", "swir16"), "nombre": "NDMI", "colormap": "rdylgn",
             "lectura": "humedad de la vegetación: alto = húmeda; bajo = estrés hídrico o suelo seco"},
}


INDICE_DEFECTO = "ndvi"


def indice(nombre: str | None) -> dict:
    ind = INDICES.get((nombre or INDICE_DEFECTO).lower())
    if ind is None:
        raise _eng().ImageryError(f"índice desconocido: {nombre!r}; válidos: {', '.join(INDICES)}")
    return ind


def scene_scaling(scene: Any, band: str) -> BandScaling:
    """Factor de la banda en la escena, o la identidad.

    Con ``getattr`` porque el teselado también recibe escenas-doble de los tests
    y objetos viejos del caché en memoria tras un despliegue en caliente."""
    fn = getattr(scene, "scaling_for", None)
    return fn(band) if callable(fn) else _IDENTITY_SCALING


def apply_scaling(data: np.ndarray, scaling: BandScaling | None) -> np.ndarray:
    """DN crudo → reflectancia BOA: ``data * scale + offset`` (§1.3).

    Identidad ⇒ se devuelve el array TAL CUAL. Eso solo pasa cuando no hay factor
    publicado NI baseline del que derivarlo: ahí el DN crudo es lo único honesto
    y el payload lo advierte.

    El 0 del COG es el NODATA del recorte, no un DN: con un offset dejaría de
    valer 0 y ``compute_ndvi`` (que lo detecta comparando con 0) lo tomaría por
    dato válido, resucitando el NDVI espurio de ±1 que cerró el #13. Se convierte
    a NaN, que es el nodata que ya entienden ``compute_ndvi``, ``array_stats`` y
    la máscara de validez del teselado.
    """
    if scaling is None or scaling.is_identity:
        return data
    scaled = data * scaling.scale + scaling.offset
    return np.where(data == 0, np.nan, scaled).astype("float32")


def _origen(scene: Any, bandas: tuple[str, ...] = ("red", "nir")) -> str:
    """De dónde salió el factor de las bandas: uno de los tres escalones, 'mixto' si
    cada banda vino de un sitio distinto."""
    fuentes = {scene_scaling(scene, b).source for b in bandas}
    return fuentes.pop() if len(fuentes) == 1 else "mixto"


def radiometry(scene: Any) -> tuple:
    """Firma radiométrica de una escena: dos escenas con firmas distintas NO son
    restables (§1.3).

    En REFLECTANCIA basta con eso, sin mirar los números ni el escalón. Un NDVI
    es adimensional: dos escenas convertidas a reflectancia BOA son comparables
    aunque una traiga el factor publicado y la otra el derivado, y aunque sus
    offsets difieran (pre-04.00 tiene 0 y post-04.00 -0.1) — cada una está bien
    convertida. Comparar los números concretos rechazaría 2020 contra 2024.

    Sin factor queda DN crudo, y ahí sí decide el lado del corte 04.00: un DN pre
    y uno post están desplazados 1000 entre sí.

    REVISIÓN ADVERSA 2026-09-08 (bloqueante 2): antes, un baseline ilegible
    devolvía ``("dn-crudo", None)`` "porque desconocido solo casa con
    desconocido". Falso: la tupla es igual a sí misma, así que DOS escenas sin
    baseline casaban y la guarda no disparaba nunca — 2020-07 contra 2024-07 de
    un proveedor que no publique nada se restaban y salía una pérdida falsa de
    ~-0,20 en todo el AOI, o sea casi el 100 % del AOI marcado como deforestación
    sobre bosque intacto. Dos desconocidos NO son el mismo desconocido.

    Así que sin baseline se cae a la FECHA de la escena contra el despliegue de
    04.00, que es un dato que el item sí trae. Es un escalón de confianza menor
    (un archivo REPROCESADO tiene fecha vieja y baseline nuevo), y por eso va en
    su propio espacio de nombres: una escena fechada nunca casa en silencio con
    una que sí declaró baseline. Y sin baseline NI fecha, la firma es ÚNICA por
    escena para que no case ni consigo misma en otra escena.
    """
    red = scene_scaling(scene, "red")
    nir = scene_scaling(scene, "nir")
    if not (red.is_identity and nir.is_identity):
        return ("reflectancia",)
    b = baseline_value(getattr(scene, "processing_baseline", None))
    if b is not None:
        return ("dn-crudo/baseline", b >= BOA_OFFSET_BASELINE)
    fecha = str(getattr(scene, "datetime", "") or "")[:10]
    if len(fecha) == 10:
        return ("dn-crudo/fecha", fecha >= BOA_OFFSET_FECHA)
    return ("dn-crudo/indeterminado", getattr(scene, "id", None) or id(scene))


def mensaje_incompatibles(scene_a: Any, scene_b: Any) -> str:
    """Texto único del rechazo por radiometría, compartido por `run_change` y por
    el teselado de cambio — los dos restan las mismas dos escenas y tienen que
    rechazar con el mismo criterio y la misma explicación."""
    pa, pb = reflectance_payload(scene_a), reflectance_payload(scene_b)
    return (
        "Las dos escenas no son comparables radiométricamente: "
        f"{getattr(scene_a, 'id', '?')} (baseline "
        f"{pa['processing_baseline'] or 'desconocido'}, {pa['fuente']}) frente a "
        f"{getattr(scene_b, 'id', '?')} (baseline "
        f"{pb['processing_baseline'] or 'desconocido'}, {pb['fuente']}). Desde el "
        "baseline 04.00 el L2A trae BOA_ADD_OFFSET=-1000: restar un NDVI de DN "
        "crudo contra uno corregido da una pérdida falsa de ~0,20 en TODO el AOI. "
        "Usa dos fechas del mismo lado del corte (enero de 2022) o un proveedor "
        "que publique scale/offset en el asset."
    )


_MASCARA_TEXTO = {"scl": "SCL", "qa_pixel": "qa_pixel de Landsat"}


def motivo_sin_validos(win: Any, scene: Any = None) -> str:
    """POR QUÉ un AOI se quedó sin ningún píxel de NDVI: el hecho que el agente
    necesita para explicarlo y proponer otra fecha. «¿todo nodata?» no distinguía
    nube de agua/sombra de falta de dato (V5 F4, el NDVI de un punto marcado)."""
    total = int(np.asarray(win.data).size)
    partes = []
    nube = getattr(win, "cloud_pct_masked", None)
    if nube:
        mascara = _MASCARA_TEXTO.get(getattr(scene, "mask_kind", "scl"), "máscara de nubes")
        partes.append(f"{nube:g} % enmascarado como nube/sombra de nube ({mascara})")
    no_pos = int(getattr(win, "px_no_positivos", 0) or 0)
    if no_pos:
        partes.append(f"{no_pos} px con reflectancia ≤ 0 (agua o sombra)")
    fecha = getattr(scene, "datetime", None) if scene is not None else None
    en = f" en la escena del {str(fecha)[:10]}" if fecha else ""
    detalle = "; ".join(partes) if partes else "sin dato en la banda (nodata)"
    return f"Ningún píxel válido en el AOI ({total} px){en}: {detalle}. Prueba otra fecha."


def descartes_payload(win: Any, *wins: Any) -> dict:
    """Píxeles que el índice descartó por reflectancia no positiva.

    Se declara aparte del nodata y de la nube: son agua, sombra de nube y sombra
    de montaña, donde la corrección atmosférica deja reflectancia negativa. Sin
    este bloque desaparecerían en el mismo NaN que todo lo demás y el consumidor
    no podría distinguir "aquí no había dato" de "aquí el índice no aplica"
    (revisión adversa 2026-09-08, bloqueante 1)."""
    total = int(getattr(win, "px_no_positivos", 0) or 0)
    for w in wins:
        total += int(getattr(w, "px_no_positivos", 0) or 0)
    out = {"px_reflectancia_no_positiva": total}
    if total:
        out["nota"] = (
            "píxeles con reflectancia ≤ 0 en alguna banda del índice (agua, sombra de nube "
            "o de montaña tras la corrección atmosférica); el índice no está definido ahí y "
            "se excluyeron de las estadísticas"
        )
    return out


def reflectance_payload(scene: Any, bandas: tuple[str, ...] = ("red", "nir")) -> dict:
    """Declara QUÉ factor se aplicó y DE DÓNDE salió (§1.3).

    Se declara siempre, también cuando es la identidad: sin este bloque no hay
    forma de distinguir un NDVI de reflectancia de uno de DN crudo, y los dos
    salen igual de plausibles. ``origen`` separa el dato publicado de la
    inferencia — no valen lo mismo aunque den el mismo número."""
    factores = {b: scene_scaling(scene, b) for b in bandas}
    aplicado = not all(f.is_identity for f in factores.values())
    origen = _origen(scene, bandas) if aplicado else _SRC_IDENTIDAD
    out = {
        "aplicado": aplicado,
        **{b: f.as_dict() for b, f in factores.items()},
        "processing_baseline": getattr(scene, "processing_baseline", None),
        "origen": origen,
        "derivado": origen == _SRC_BASELINE,
        "fuente": _FUENTE_TEXTO.get(origen, f"orígenes distintos por banda: {origen}"),
    }
    if not aplicado:
        # Único caso irresoluble: ni factor publicado ni baseline legible. Si la
        # escena es posterior al despliegue de 04.00 el NDVI está subestimado
        # ~0,20 y no hay de dónde sacar el offset — se dice, que es lo que
        # separa un dato malo de un dato malo invisible.
        fecha = str(getattr(scene, "datetime", "") or "")[:10]
        sospecha = (
            f" La escena es del {fecha}, posterior al despliegue del baseline 04.00 "
            f"({BOA_OFFSET_FECHA}), así que casi con seguridad trae "
            "BOA_ADD_OFFSET=-1000 y el NDVI está subestimado (~0,20)."
            if fecha >= BOA_OFFSET_FECHA else ""
        )
        out["aviso"] = (
            "No se pudo determinar el factor de reflectancia: el asset no publica "
            "scale/offset y `s2:processing_baseline` falta o no es legible "
            f"({getattr(scene, 'processing_baseline', None)!r}). El índice se calculó "
            "sobre el DN crudo." + sospecha
        )
    return out


def check_unidades_coherentes(scene: Scene, bandas: tuple[str, str] = ("red", "nir")) -> None:
    """red y nir tienen que quedar en la MISMA unidad (revisión adversa
    2026-09-08, media 9).

    ``radiometry`` declara "reflectancia" si CUALQUIERA de las dos bandas tiene
    factor. Si red publica `raster:bands`, nir no, y el baseline no es legible,
    red queda en reflectancia (~0,08) y nir en DN (~4000): el cociente de esa
    ÚNICA escena ya es basura, y salía declarado como `origen: mixto` pero sin
    que nada lo parara. Error tipado y antes de leer nada."""
    a, b = bandas
    fa, fb = scene_scaling(scene, a), scene_scaling(scene, b)
    if fa.is_identity != fb.is_identity:
        con, sin = (a, b) if fb.is_identity else (b, a)
        raise _eng().ImageryError(
            f"La escena {scene.id} mezcla unidades entre bandas: {con} tiene "
            f"factor de reflectancia ({scene_scaling(scene, con).as_dict()}) y "
            f"{sin} no, así que el índice cruzaría reflectancia con DN crudo. "
            "Es un metadato incompleto del proveedor: prueba con otra escena o "
            "con el otro proveedor."
        )
