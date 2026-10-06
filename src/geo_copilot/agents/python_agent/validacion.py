"""La VALIDACIÓN de una operación espacial antes de ejecutarla (A2A con el planner): si es viable
para la geometría, el número de elementos y los parámetros.

Reglas deterministas (sin LLM, <1ms):
- buffer: válido en cualquier geometría, requiere ``distance > 0``.
  Si ``distance`` falta en params, se emite warning (el sandbox
  fallará después si no hay default).
- centroid: válido en cualquier geometría; en Point devuelve el
  punto mismo (warning).
- area: solo polígonos; en Line se sugiere ``length``.
- length: válido en Line y Polygon (perímetro); en Point se rechaza
  (no hay longitud meaningful).
- intersection / clip / difference: necesita 2 capas (params
  debe traer ``other_layer_key``).
- union / dissolve: válido siempre; warning si feature_count=1.
- distance: necesita 2 capas o feature de referencia.
- simplify: cualquier geometría con features > 0.
- convex_hull: warning si feature_count<3 (degenera).

Salió de `PythonAgent` (F4 del plan de calidad: agent.py tenía 1.181 líneas), tal cual.
"""


from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.python_agent.agent")


def _ok(reason: str = "", warning: str | None = None) -> dict:
    return {
        "feasible": True,
        "reason": reason or "op aplicable",
        "alternative": None,
        "warning": warning,
    }

def _bad(reason: str, alternative: str | None = None) -> dict:
    return {
        "feasible": False,
        "reason": reason,
        "alternative": alternative,
        "warning": None,
    }


def _regla_por_parametros(op: str, params: dict, geom: str) -> dict | None:
    """Operaciones que dependen de sus parámetros: buffer (distancia), binarias y distancia (otra capa)."""
    # buffer
    if op == "buffer":
        distance = params.get("distance")
        if distance is None:
            # No es bloqueante — el sandbox/LLM pueden inferir un
            # default. Pero avisamos para que el caller no asuma OK.
            return _ok(
                "buffer aplicable, pero falta param 'distance'",
                warning="distance no especificada — el sandbox usará default o fallará",
            )
        if isinstance(distance, (int, float)) and distance <= 0:
            return _bad(f"distance debe ser > 0, recibí {distance}")
        return _ok(f"buffer válido en {geom or 'cualquier geom'}")

    # intersection / clip / difference (binarias)
    if op in ("intersection", "intersect", "clip", "recortar",
              "difference", "diff", "diferencia"):
        if not params.get("other_layer_key"):
            return _bad(
                f"{op} es binaria — necesita una segunda capa "
                "(params.other_layer_key)",
                alternative=None,
            )
        return _ok(f"{op} válida con segunda capa")

    # distance
    if op in ("distance", "distancia"):
        if not params.get("other_layer_key") and not params.get("reference_feature"):
            return _bad(
                "distance necesita una referencia (otra capa o feature)",
                alternative=None,
            )
        return _ok("distance válida con referencia")
    return None


def _regla_por_geometria(op: str, geom: str, geometry_type: str | None) -> dict | None:
    """Operaciones que dependen del tipo de geometría: centroide, área y longitud."""
    is_point = "point" in geom
    is_line = "line" in geom or "linestring" in geom
    is_polygon = "polygon" in geom

    # centroid
    if op in ("centroid", "center", "centroides"):
        if is_point:
            return _ok(
                "centroide de Point es el punto mismo",
                warning="centroide redundante sobre puntos — considera saltarlo",
            )
        return _ok(f"centroide válido en {geom}")

    # area
    if op in ("area", "área", "areas"):
        if is_polygon:
            return _ok("área válida en polígonos")
        return _bad(
            f"área requiere polígonos, no {geometry_type!r}",
            alternative="length" if is_line else None,
        )

    # length
    if op in ("length", "longitud", "perimeter", "perímetro"):
        if is_line:
            return _ok("longitud válida en líneas")
        if is_polygon:
            return _ok("longitud=perímetro en polígonos")
        return _bad(
            f"longitud requiere lines/polygons, no {geometry_type!r}",
            alternative=None,
        )
    return None


def _regla_por_conteo(op: str, fc: int) -> dict | None:
    """Operaciones que dependen del número de elementos (unión, envolvente) o que siempre aplican."""
    # union / dissolve
    if op in ("union", "unir", "dissolve", "disolver", "merge"):
        if fc == 1:
            return _ok(
                f"{op} sobre 1 feature es no-op",
                warning="solo 1 feature — la unión es trivial",
            )
        return _ok(f"{op} válida sobre {fc} features")

    # simplify
    if op in ("simplify", "simplificar"):
        return _ok("simplify válida en cualquier geometría")

    # convex_hull
    if op in ("convex_hull", "convex", "hull", "envolvente"):
        if fc < 3:
            return _ok(
                f"convex_hull con {fc} feature(s) degenera "
                "a línea o punto",
                warning="convex_hull necesita ≥3 features para ser útil",
            )
        return _ok("convex_hull válido")

    # bbox / extent
    if op in ("bbox", "extent", "bounding_box"):
        return _ok("bbox/extent válido en cualquier geometría")

    # filter / where: trivialmente viable
    if op in ("filter", "filtrar", "where", "select"):
        return _ok("filter es viable sobre cualquier capa")
    return None


class ValidacionMixin:
    """Viabilidad de una operación espacial para unos datos (reglas deterministas)."""

    async def validate_operation(
        self,
        *,
        op_name: str,
        feature_count: int,
        geometry_type: str | None = None,
        params: dict | None = None,
    ) -> dict:
        """¿La operación espacial pedida es viable para estos datos?

        Llamada típica: ``PlannerAgent`` genera un step
        ``action_type=spatial_operation`` con query "compute centroid".
        Antes de aceptarlo, llama ``PythonAgent.validate_operation`` y si
        no es viable, ajusta el plan (ej. saltar el step o sustituir
        por otra op).

        Reglas deterministas (sin LLM, <1ms): ver el docstring del módulo.

        Defensa de entrada:
        - ``feature_count`` se coerciona a int (no-int → no viable).
        - ``op_name`` case-insensitive, ``None``/empty → fallback "desconocida".

        Returns:
            ``{feasible: bool, reason: str, alternative: str|None,
            warning: str|None}``. ``alternative`` solo cuando no es
            viable; ``warning`` cuando es viable pero subóptimo.
        """
        # Coerción de feature_count.
        try:
            fc = int(feature_count)
        except (TypeError, ValueError):
            return {
                "feasible": False,
                "reason": f"feature_count inválido: {feature_count!r} no es int",
                "alternative": None,
                "warning": None,
            }

        op = (op_name or "").lower().strip()
        geom = (geometry_type or "").lower()
        params = params or {}

        if fc <= 0:
            return _bad("no hay features de entrada", alternative=None)

        regla = (_regla_por_parametros(op, params, geom) or _regla_por_geometria(op, geom, geometry_type)
                 or _regla_por_conteo(op, fc))
        if regla is not None:
            return regla

        # Op no reconocida — no rechazamos, solo avisamos al caller para
        # que tome la decisión informada.
        return _ok(
            f"op {op!r} no está en la lista de reglas — viabilidad por LLM",
            warning="op desconocida — la viabilidad real la decidirá el sandbox",
        )
