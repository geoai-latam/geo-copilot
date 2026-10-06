"""La CLASIFICACIÓN de los valores en clases: rangos (Jenks, cuantiles, intervalos iguales,
desviación estándar), categorías, y los cortes manuales que pidió el usuario (saneados).

Salió de `SymbologyAgent` (F4 del plan de calidad: agent.py tenía 1.622 líneas), tal cual.
"""

import math
import statistics
from typing import Any

from geo_copilot.agents.symbology_agent.styles import (
    ClassBreak,
    ClassificationMethod,
    ColorScheme,
    get_color_palette,
)
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.symbology_agent.agent")


def _matriz_fisher(s: list[float], n: int, k: int) -> list[list[int]]:
    """Programación dinámica de Fisher para Jenks: para cada partición, dónde empieza la última clase."""
    # Matrices de costo (varianza acumulada) y enlace.
    # mat1[i][j] = clase asignada al elemento j en partición con i clases.
    # mat2[i][j] = "menor" varianza si j está en clase i.
    mat1 = [[0] * (n + 1) for _ in range(k + 1)]
    mat2 = [[float("inf")] * (n + 1) for _ in range(k + 1)]
    for i in range(1, k + 1):
        mat1[i][1] = 1
        mat2[i][1] = 0
        for j in range(2, n + 1):
            mat2[i][j] = float("inf")

    v_sum = 0.0
    for limit in range(2, n + 1):
        s1 = 0.0
        s2 = 0.0
        w = 0
        for m in range(1, limit + 1):
            i3 = limit - m + 1
            val = s[i3 - 1]
            s2 += val * val
            s1 += val
            w += 1
            v = s2 - (s1 * s1) / w
            i4 = i3 - 1
            if i4 != 0:
                for j in range(2, k + 1):
                    if mat2[j][limit] >= (v + mat2[j - 1][i4]):
                        mat1[j][limit] = i3
                        mat2[j][limit] = v + mat2[j - 1][i4]
        mat1[1][limit] = 1
        mat2[1][limit] = v
        v_sum = v
    return mat1


class ClasificacionMixin:
    """Clasificación de los valores en clases (rangos, categorías, cortes manuales)."""

    @staticmethod
    def _coerce_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Nombres de color (es/en) → hex, para cuando el LLM escribe "rojo" en vez
    # de "#e41a1c" en un manual break. Paleta ColorBrewer Set1 (alto contraste).
    _COLOR_NAME_TO_HEX = {
        "rojo": "#e41a1c", "red": "#e41a1c",
        "azul": "#377eb8", "blue": "#377eb8",
        "verde": "#4daf4a", "green": "#4daf4a",
        "amarillo": "#ffb300", "yellow": "#ffb300",
        "naranja": "#ff7f00", "orange": "#ff7f00",
        "morado": "#984ea3", "purpura": "#984ea3", "púrpura": "#984ea3", "purple": "#984ea3",
        "gris": "#999999", "gray": "#999999", "grey": "#999999",
        "negro": "#000000", "black": "#000000",
        "blanco": "#ffffff", "white": "#ffffff",
        "rosa": "#f781bf", "pink": "#f781bf",
        "cafe": "#a65628", "café": "#a65628", "marron": "#a65628", "marrón": "#a65628", "brown": "#a65628",
    }

    def _sanitize_manual_breaks(
        self, raw: Any
    ) -> list[dict[str, Any]] | None:
        """Normaliza ``manual_class_breaks`` del LLM a dicts válidos o None.

        Cada entrada válida: ``{min: float|None, max: float|None, color: '#hex',
        label: str}``. ``min``/``max`` pueden ser None (rango abierto — se cierra
        luego con el rango real del campo). Descarta entradas sin color hex
        resoluble o sin ningún umbral. Devuelve None si no queda ninguna.
        """
        import re as _re

        if not isinstance(raw, list) or not raw:
            return None
        cleaned: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            mn = self._coerce_float(item.get("min"))
            mx = self._coerce_float(item.get("max"))
            if mn is None and mx is None:
                continue  # un break sin ningún umbral no es clasificable
            color = item.get("color")
            c = color.strip().lower() if isinstance(color, str) else ""
            if c and not c.startswith("#"):
                c = self._COLOR_NAME_TO_HEX.get(c, "")
            if not _re.fullmatch(r"#[0-9a-fA-F]{6}", c or ""):
                continue  # sin color válido, el break no aporta
            label = item.get("label")
            if not isinstance(label, str) or not label.strip():
                lo_txt = "" if mn is None else f"≥{mn:g}"
                hi_txt = "" if mx is None else f"<{mx:g}"
                label = " ".join(t for t in (lo_txt, hi_txt) if t) or "clase"
            cleaned.append({"min": mn, "max": mx, "color": c, "label": label})
        return cleaned or None

    def _sanitize_category_colors(self, raw: Any) -> dict[str, str] | None:
        """{valor: '#hex'} válidos del LLM, o None. Nombres de color → hex."""
        import re as _re

        if not isinstance(raw, dict) or not raw:
            return None
        limpio: dict[str, str] = {}
        for valor, color in raw.items():
            c = color.strip().lower() if isinstance(color, str) else ""
            if c and not c.startswith("#"):
                c = self._COLOR_NAME_TO_HEX.get(c, "")
            if _re.fullmatch(r"#[0-9a-fA-F]{6}", c or ""):
                limpio[str(valor)] = c
        return limpio or None

    @staticmethod
    def _numeric_field_values(geojson: dict, field: str) -> list[float]:
        """Extrae los valores numéricos de ``field`` en el GeoJSON (ignora
        no-numéricos)."""
        out: list[float] = []
        for feat in (geojson or {}).get("features", []):
            val: Any = (feat.get("properties") or {}).get(field)
            try:
                out.append(float(val))
            except (TypeError, ValueError):
                continue
        return out

    def _build_manual_breaks(
        self, spec: list[dict[str, Any]], values: list[float]
    ) -> list[ClassBreak]:
        """Construye ``ClassBreak``s MANUALES desde el spec del LLM.

        CRÍTICO para el render: ``el render del mapa`` SALTA breaks con
        ``min_value``/``max_value`` None y matchea por ORDEN de array con
        semántica ``[lo, hi)`` salvo el último que es ``[lo, hi]``. Por eso:
        (1) cerramos los rangos abiertos con el min/max REAL del campo, y
        (2) ORDENAMOS ascendente para que el rango más alto quede de último
        (su ``<= hi`` inclusivo captura el valor máximo del dato). Un rango con
        ``min == max`` es IGUALDAD (``_clase_de``).
        Los features que no caen en ningún break usan el color base (gris).
        """
        if not spec:
            return []
        nums = list(values or [])
        data_min = min(nums) if nums else 0.0
        data_max = max(nums) if nums else 0.0
        built: list[ClassBreak] = []
        for b in spec:
            lo = b.get("min")
            hi = b.get("max")
            hi_pedido = hi is not None
            hi = data_max if hi is None else float(hi)
            if lo is not None:
                lo = float(lo)
            elif not hi_pedido or data_min < hi:
                lo = data_min
            else:
                # F7 (auditoría): «todo lo menor que max» sin datos por debajo es una clase VACÍA. Cerrarla
                # con data_min daría [max, max] (igualdad, atraparía el max) y un límite inventado sería
                # un dato falso: se deja sin `min`, que el render y la leyenda ya tratan como sin elementos.
                lo = None
            built.append(ClassBreak(
                min_value=lo,
                max_value=hi,
                label=str(b.get("label") or "clase"),
                color=str(b.get("color") or "#9e9e9e"),
            ))
        # Las clases vacías (sin `min`) van primero: nunca deben quedar de última (la inclusiva).
        built.sort(key=lambda cb: (-math.inf if cb.min_value is None else cb.min_value,
                                   cb.max_value))
        # Cuántos caen en cada clase, con la MISMA regla del render (maplibreSymbology.ts) y de la
        # leyenda (leyenda.ts). Antes no se contaban: el agente leía «(0)» en las 3 clases y seguía
        # re-simbolizando.
        for cb in built:
            cb.count = 0
        for v in nums:
            i = self._clase_de(built, v)
            if i is not None:
                built[i].count += 1
        return built

    @staticmethod
    def _clase_de(breaks: list[ClassBreak], v: float) -> int | None:
        """La clase que pinta el mapa para `v`: la PRIMERA que lo contiene, con `[lo, hi)` salvo la
        última, `[lo, hi]`. F7 (auditoría): un rango degenerado (min == max, «Cluster 0» = [0, 0],
        «0 incidentes») significa IGUALDAD; antes se ensanchaba hasta la clase siguiente y pintaba con
        su color y su etiqueta valores que el LLM dejó fuera a propósito (el hueco queda en gris)."""
        for i, cb in enumerate(breaks):
            lo, hi = cb.min_value, cb.max_value
            if lo is None or hi is None:
                continue  # el render también los salta
            if lo == hi:
                dentro = v == lo
            else:
                dentro = lo <= v and (v <= hi if i == len(breaks) - 1 else v < hi)
            if dentro:
                return i
        return None

    def _extract_field_values(self, geojson: dict, field: str) -> list[float]:
        """Extraer valores numéricos de un campo en el geojson."""
        out: list[float] = []
        for f in geojson.get("features", []):
            v = f.get("properties", {}).get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
        return out

    def _calculate_numeric_breaks(
        self,
        geojson: dict,
        field: str,
        method: ClassificationMethod,
        color_scheme: ColorScheme,
        num_classes: int = 5,
    ) -> list[ClassBreak]:
        """Calcular breaks numéricos por método.

        Despacha a la implementación específica: quantile, equal_interval,
        natural_breaks (Jenks), o std_deviation.
        """
        values = self._extract_field_values(geojson, field)
        if not values:
            return []
        if method == ClassificationMethod.QUANTILE:
            edges = self._edges_quantile(values, num_classes)
        elif method == ClassificationMethod.EQUAL_INTERVAL:
            edges = self._edges_equal_interval(values, num_classes)
        elif method == ClassificationMethod.NATURAL_BREAKS:
            edges = self._edges_jenks(values, num_classes)
        elif method == ClassificationMethod.STANDARD_DEVIATION:
            edges = self._edges_std_dev(values, num_classes)
        else:
            # Fallback técnico (no semántico): quantile es el método más
            # estable cuando el LLM elige uno que no sabemos calcular.
            logger.warning(
                f"[SymbologyAgent] método {method} no soportado, uso quantile"
            )
            edges = self._edges_quantile(values, num_classes)

        return self._edges_to_breaks(values, edges, color_scheme)

    def _edges_quantile(self, values: list[float], k: int) -> list[float]:
        """Bordes por quantiles (mismo nº de items por clase)."""
        s = sorted(values)
        n = len(s)
        edges = [s[0]]
        for i in range(1, k):
            edges.append(s[int(i * n / k)])
        edges.append(s[-1])
        return edges

    def _edges_equal_interval(self, values: list[float], k: int) -> list[float]:
        """Bordes uniformes en el rango [min, max]."""
        lo, hi = min(values), max(values)
        if hi == lo:
            return [lo, hi]
        step = (hi - lo) / k
        return [lo + i * step for i in range(k + 1)]

    def _edges_std_dev(self, values: list[float], k: int) -> list[float]:
        """Bordes basados en media ± n·σ. k clases → desviaciones centradas
        en la media. Ej. k=5 → [-2σ, -1σ, 0, +1σ, +2σ] alrededor de mean.
        """
        if len(values) < 2:
            return [min(values), max(values)]
        m = statistics.mean(values)
        sd = statistics.stdev(values)
        if sd == 0:
            return [min(values), max(values)]
        # k clases → k-1 bordes interiores simétricos. Step = 2·sd / k.
        half = k / 2
        step = (2 * sd) / k
        interior = [m + (i - half) * step for i in range(1, k)]
        return [min(values)] + interior + [max(values)]

    def _edges_jenks(self, values: list[float], k: int) -> list[float]:
        """Natural Breaks (Jenks). Minimiza la varianza intra-clase.

        Implementación clásica de Fisher (programación dinámica). O(n²·k).
        Para datasets grandes (>1000 valores) hacemos un sub-muestreo
        determinístico para mantener performance razonable.
        """
        s = sorted(values)
        n = len(s)
        distintos = sorted(set(s))
        if len(distintos) <= k:
            # V5 (imagery): 2 lotes con NDVI 0.38 y 0.68 en «5 clases» daban bordes [0.38, 0.68, 0.68]
            # → una sola clase y los dos del mismo color (el agente narró colores distintos). Con
            # tantos valores distintos como clases o menos, cada valor es su clase: bordes en los
            # puntos medios (la misma regla de borde que Jenks, ver abajo).
            if len(distintos) == 1:
                return [distintos[0], distintos[0]]
            return ([distintos[0]] + [(a + b) / 2 for a, b in zip(distintos, distintos[1:], strict=False)]
                    + [distintos[-1]])
        # Subsample para datasets grandes (Fisher es O(n²·k)).
        if n > 1000:
            step = n / 1000
            s = [s[int(i * step)] for i in range(1000)]
            n = len(s)

        mat1 = _matriz_fisher(s, n, k)

        # Reconstruir bordes. El borde entre dos clases es el punto medio entre el último valor
        # de una y el primero de la siguiente: con el primero tal cual (antes), una clase de UN
        # valor en el máximo («{2166}») daba el borde 2166 repetido, se deduplicaba y 3 clases
        # pedidas salían 2 (V5 auditoría pre-producción: 4 lotes en «3 clases» → 2).
        k_idx = n
        edges = [s[-1]]
        for j in range(k, 1, -1):
            id_ = int(mat1[j][k_idx]) - 1
            edges.append((s[id_ - 1] + s[id_]) / 2 if id_ > 0 else s[id_])
            k_idx = id_
        edges.append(s[0])
        edges.reverse()
        return edges

    def _edges_to_breaks(
        self,
        values: list[float],
        edges: list[float],
        color_scheme: ColorScheme,
    ) -> list[ClassBreak]:
        """Convertir lista de bordes a ClassBreaks con colores y conteos."""
        # V5 EH.7: bordes repetidos (pocos valores distintos, o todos iguales) daban clases
        # vacías o duplicadas en la leyenda («1–1 (0), 1–1 (0), 1–1 (3)», «163–163»).
        unicos = [e for i, e in enumerate(edges) if i == 0 or e != edges[i - 1]]
        edges = unicos if len(unicos) >= 2 else unicos * 2
        if len(edges) < 2:
            return []
        num_classes = len(edges) - 1
        colors = get_color_palette(color_scheme, num_classes)
        s = sorted(values)
        breaks: list[ClassBreak] = []
        for i in range(num_classes):
            lo, hi = edges[i], edges[i + 1]
            # Contar valores en [lo, hi] (inclusivo en último, semiabierto antes).
            if i == num_classes - 1:
                count = sum(1 for v in s if lo <= v <= hi)
            else:
                count = sum(1 for v in s if lo <= v < hi)
            breaks.append(ClassBreak(
                min_value=lo,
                max_value=hi,
                label=f"{lo:.2f} – {hi:.2f}",
                color=colors[i],
                count=count,
            ))
        return breaks

    def _calculate_categorical_breaks(
        self,
        value_counts: dict,
        color_scheme: ColorScheme,
        others_count: int = 0,
    ) -> list[ClassBreak]:
        """Calcular breaks para datos categóricos (UNIQUE_VALUES).

        R4.5: si hay categorías fuera del top perfilado, se añade una clase
        "Otros" gris con su conteo — la leyenda declara lo que antes se perdía
        en silencio (el frontend trata "Otros" como color de fallback).
        """
        if not value_counts:
            return []
        colors = get_color_palette(color_scheme, len(value_counts))
        breaks = [
            ClassBreak(
                label=str(value),
                color=colors[i % len(colors)],
                count=count,
            )
            for i, (value, count) in enumerate(value_counts.items())
        ]
        if others_count > 0:
            breaks.append(
                ClassBreak(label="Otros", color="#999999", count=others_count)
            )
        return breaks

    def _darken_color(self, hex_color: str, factor: float = 0.7) -> str:
        """Oscurecer un color hex."""
        hex_color = hex_color.lstrip("#")
        r = int(int(hex_color[0:2], 16) * factor)
        g = int(int(hex_color[2:4], 16) * factor)
        b = int(int(hex_color[4:6], 16) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"
