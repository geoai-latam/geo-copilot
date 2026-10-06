"""El CATÁLOGO de funciones del guardián: PostGIS y agregados/escalares permitidos, las funciones
métricas y las que no cambian las unidades de su argumento.

Salió de `ast.py` (F4 del plan de calidad: ast.py tenía 615 líneas), tal cual.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Allowlist de funciones.
#
# Las PostGIS salen del inventario real de este repositorio (`grep -o 'ST_[A-Za-z_]+'`
# sobre src/, tests/, config/ y docs/), no de una lista genérica de internet.
# Si el modo sombra registra una función legítima que falta, se añade AQUÍ —
# ése es justo el trabajo que el modo sombra sirve para dimensionar.
# ---------------------------------------------------------------------------
_POSTGIS = {
    "st_area", "st_asgeojson", "st_asmvt", "st_asmvtgeom", "st_buffer",
    "st_centroid", "st_clusterdbscan", "st_collect", "st_contains",
    "st_convexhull", "st_coveredby", "st_covers", "st_crosses", "st_distance",
    "st_dwithin", "st_extent", "st_intersection", "st_intersects", "st_length",
    "st_makeenvelope", "st_overlaps", "st_setsrid", "st_simplify",
    "st_simplifypreservetopology", "st_srid", "st_tileenvelope", "st_touches",
    "st_transform", "st_union", "st_voronoi", "st_within", "st_xmax", "st_xmin",
    "st_ymax", "st_ymin", "st_geomfromtext", "st_geomfromgeojson", "st_x", "st_y",
    "st_perimeter", "st_envelope", "st_makevalid", "st_isvalid", "st_geometrytype",
    "st_numgeometries", "st_geometryn", "st_force2d", "st_pointonsurface",
    # H16 (V5 de F2): prefiltro indexable de proximidad, `col && ST_Expand(ref, d)`.
    "st_expand",
    # T5.4 (V5): leer la geometría como texto/binario para un cliente que no habla PostGIS (MCP
    # tabulares, adaptador tabular_geo). Solo serializan: no leen nada que la consulta no pida.
    "st_astext", "st_asewkt", "st_asbinary", "st_asewkb", "st_ashexewkb",
}


_AGREGADOS_Y_ESCALARES = {
    # agregación
    "count", "sum", "avg", "min", "max", "array_agg", "string_agg",
    "json_agg", "jsonb_agg", "json_build_object", "jsonb_build_object",
    "percentile_cont", "percentile_disc", "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp", "corr", "regr_slope",
    # ventana
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead",
    "first_value", "last_value", "cume_dist", "percent_rank", "width_bucket",
    # texto
    "lower", "upper", "trim", "btrim", "ltrim", "rtrim", "substring", "substr",
    "length", "char_length", "concat", "concat_ws", "replace", "split_part",
    "left", "right", "lpad", "rpad", "initcap", "unaccent", "format",
    "to_char", "position", "strpos", "regexp_replace", "regexp_match",
    "similarity",
    # numérico
    "abs", "round", "ceil", "ceiling", "floor", "trunc", "power", "sqrt",
    "exp", "ln", "log", "mod", "greatest", "least", "sign", "random",
    "cbrt", "degrees", "radians", "pi",
    # fecha
    "now", "current_date", "current_timestamp", "date_trunc", "date_part",
    "extract", "age", "to_date", "to_timestamp", "make_date",
    # nulos / condicional
    "coalesce", "nullif", "case", "cast", "nvl",
    # conversión / tipos
    "to_number", "to_json", "to_jsonb", "json_extract_path_text",
    "jsonb_extract_path_text", "array_length", "unnest", "generate_series",
    # Predicados que sqlglot modela como Func aunque sean sintaxis, no
    # llamadas. Detectados midiendo el corpus real del repositorio: sin ellos,
    # un `WHERE NOT EXISTS (SELECT 1 ...)` perfectamente válido se rechazaba.
    "exists", "in", "like", "ilike", "between", "any", "all", "not",
    # `&&` (intersección de bounding boxes, usa el índice GiST): sqlglot lo
    # modela como ArrayOverlaps. Sin él, el prefiltro indexable de H16 se rechazaba.
    "array_overlaps",
}


FUNCIONES_PERMITIDAS = _POSTGIS | _AGREGADOS_Y_ESCALARES


# ---------------------------------------------------------------------------
# Unidades (auditoría 2026-09-08, §1.4)
#
# Las entidades del semantic layer están declaradas en `srid: 4326`
# (`semantic_layer/entities.yaml`), es decir GRADOS. `ST_Area(geom)` sobre
# grados devuelve grados², no metros²: en Bogotá (lat 4,65) un grado² son
# 12.269,8 km² —área geodésica sobre WGS 84, medida con `pyproj.Geod`; la cifra
# de 12.351 km² que traía la auditoría sale de multiplicar 111,32 × 110,95 y
# sobreestima—, así que un lote de 300 m² se reporta como `0,00 km²`. El formato
# es plausible y la magnitud absurda, y nada lo detectaba:
# `sql_validator.WARNING_PATTERNS` no tiene ninguna regla de unidades y estas
# funciones estaban en la allowlist de arriba SIN que nadie mirara su argumento.
#
# Los prompts ya enseñan el patrón correcto (`gis_agent/agent.py:940-949`,
# `ST_Area(ST_Transform("geom", 4326)::geography)`), pero enseñar no es exigir:
# el modelo puede ignorarlo y hasta ahora nada lo contradecía. Aquí se exige.
#
# Valor: índice de los argumentos que deben venir en metros. `ST_Distance`
# compara dos geometrías, así que se revisan las dos; las demás miden una sola.
# ---------------------------------------------------------------------------
_FUNCIONES_METRICAS: dict[str, tuple[int, ...]] = {
    "st_area": (0,),
    "st_length": (0,),
    "st_perimeter": (0,),
    "st_buffer": (0,),
    "st_distance": (0, 1),
    # `ST_DWithin(a, b, 1000)` sobre grados pide un radio de MIL GRADOS y
    # devuelve la tabla entera: el filtro que el usuario creyó poner no
    # filtra nada. Se revisan las dos geometrías; el radio es el tercer
    # argumento y su unidad la fijan ellas.
    "st_dwithin": (0, 1),
    # `ST_ClusterDBSCAN(geom, 0.01, 5)` agrupa por 0,01 grados (~1,1 km en el
    # ecuador), no por un centímetro.
    "st_clusterdbscan": (0,),
}


# Funciones que devuelven una geometría en el MISMO sistema en el que entró.
# Recursar por ellas evita rechazar SQL correcto como
# `ST_Area(ST_Union(ST_Transform(geom, 9377)))`. `st_buffer` está aquí además
# de en la tabla de arriba: sobre `geography` devuelve `geography`, que es el
# patrón de `tests/test_gis_integration.py:188`.
_TRANSPARENTES_A_LAS_UNIDADES = frozenset({
    "st_buffer", "st_centroid", "st_collect", "st_convexhull", "st_envelope",
    "st_force2d", "st_geometryn", "st_intersection", "st_makevalid",
    "st_pointonsurface", "st_simplify", "st_simplifypreservetopology",
    "st_union",
})
