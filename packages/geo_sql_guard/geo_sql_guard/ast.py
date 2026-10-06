"""Validación del SQL del LLM por su ESTRUCTURA, no por su texto (R0.7, AUD-04).

Por qué existe
--------------
`SQLValidator` decide mirando el SQL **como cadena**: busca palabras prohibidas
(`DROP`, `pg_read_file`, ...). Ese enfoque es frágil por construcción, porque el
mismo SQL admite infinitos disfraces textuales que significan lo mismo para el
motor. La auditoría lo demostró::

    SELECT '/*' AS a, pg_read_file('/etc/passwd') AS b, '*/' AS c

El validador borraba "el comentario" `/* ... */` —que no era tal, era un literal
de cadena— y con él borraba `pg_read_file` de lo que iba a inspeccionar. Escaneaba
un texto mutilado, lo daba por bueno, y luego se ejecutaba el SQL íntegro.

Aquí se hace lo contrario: se **parsea** el SQL igual que lo entendería
PostgreSQL y se pregunta por su forma. Los literales ya están resueltos como
datos, así que el disfraz deja de importar. Se valida en POSITIVO:

  - un solo statement, y de tipo SELECT (o WITH que termine en SELECT)
  - todas las funciones invocadas están en una allowlist
  - nada de DML/DDL en ningún punto del árbol
  - las funciones métricas (`ST_Area`, `ST_Distance`, `ST_Length`, `ST_Buffer`)
    reciben una geometría en metros, no en grados (auditoría 2026-09-08, §1.4)

MODO DE OPERACIÓN
-----------------
Se controla con ``settings.sql_ast_validation``:

  ``off``      no se ejecuta.
  ``shadow``   se ejecuta y se REGISTRA lo que rechazaría, pero NO bloquea.
               Fue el default hasta el 2026-09-24 (hoy lo es ``enforce``, tras
               medirlo contra el bench agéntico): sirve para medir sobre SQL real cuánto se
               rompería antes de activarlo. Revisa los logs
               ``[SQL-AST][SOMBRA]`` antes de pasar a ``enforce``.
  ``enforce``  bloquea de verdad.

El objetivo del modo sombra es que la decisión de activar esto se tome con datos
—qué SQL legítimo se rechazaría— y no con una corazonada.

MEDICIÓN INICIAL (2026-07-27, sobre el corpus del propio repositorio)
--------------------------------------------------------------------
75 sentencias SELECT/WITH extraídas de literales de cadena de todo el árbol:

    aceptadas   58  (77 %)
    rechazadas  17
        13  acceso a catálogo del sistema
         2  no se pudo parsear
         1  se esperaba 1 statement, hay 2
         1  funciones fuera de la allowlist

Leído en detalle, el riesgo real de rotura es prácticamente nulo:

  - Los 13 de "catálogo del sistema" son consultas del INTROSPECTOR DE ESQUEMA
    (`semantic/introspector.py`: information_schema, pg_class, pg_index). Son
    código de la aplicación y **no pasan por `SQLValidator`** — verificado: los
    únicos llamadores de `.validate()` están en `gis_agent/agent.py`, sobre SQL
    del modelo. No se romperían.
  - 1 stacked query y 1 `pg_sleep` son los payloads maliciosos de los tests:
    rechazarlos es el objetivo.
  - De los 2 que no parsean, uno es un placeholder de documentación
    (`ST_DWithin(...)`) y el otro una cadena truncada en un test.

La medición también destapó dos defectos de ESTE analizador, ya corregidos:
sqlglot modela `AND`/`OR` como subclases de `Func` (se contaban como funciones
desconocidas) y `EXISTS` igual. Sin medir contra SQL real no habrían aparecido:
ése es justo el trabajo que el modo sombra existe para hacer.

REGLA DE UNIDADES (auditoría 2026-09-08, §1.4) — SIN MEDIR TODAVÍA
------------------------------------------------------------------
La comprobación de unidades sobre `ST_Area`, `ST_Perimeter`, `ST_Length`,
`ST_Distance`, `ST_DWithin`, `ST_Buffer` y `ST_ClusterDBSCAN` se añadió DESPUÉS
de la medición de arriba, así que esos porcentajes no la incluyen. Entra por el
mismo camino y con la misma semántica: en `shadow` registra y no bloquea. Antes
de pasar a `enforce` hay que volver a mirar los logs `[SQL-AST][SOMBRA]`
filtrando por `unidades:`.

LÍMITES CONOCIDOS DE LA REGLA DE UNIDADES
-----------------------------------------
Dos falsos positivos NO se pueden resolver desde el árbol sintáctico, y son la
razón por la que esto no puede pasar a `enforce` sin medir antes:

  1. **Una columna declarada `geography` en la base.** `ST_Area(recorrido)` es
     correcto si `recorrido` es `geography`, y el árbol no distingue esa columna
     de una `geometry`. Haría falta el catálogo, que este módulo no consulta a
     propósito (es un validador puro y sin E/S).
  2. **El alias de un CTE.** En
     ``WITH m AS (SELECT ST_Transform(geom, 9377) AS g FROM lotes)
       SELECT ST_Area(g) FROM m``
     el `ST_Transform` está en otro nodo del árbol y `g` es sólo un nombre.
     Resolverlo pide un análisis de alcance que aquí no existe.

Los casos que sí se resuelven —funciones transparentes a las unidades
(`ST_Union`, `ST_MakeValid`, …), casts encadenados, SRID métricos comprobados
contra la base EPSG— están cubiertos y tienen test.

La pregunta "¿ese SRID mide en metros?" la contesta `core/formatters.py`, que
es también de donde sale la nota de unidades que ve el LLM en su prompt. Es a
propósito: el prompt y el validador tienen que decir lo mismo.
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import expressions as exp

logger = logging.getLogger("geo_sql_guard")

# F4: el catálogo de funciones y el análisis de unidades/SRID viven en sus módulos; se
# reexportan porque `from geo_sql_guard.ast import ...` (y el * de sql_ast_validator) los usa desde aquí.
# `_SRID_COLUMNAS` NO: `fijar_srid_de_columnas` la reasigna en unidades y una copia aquí quedaría vieja.
from geo_sql_guard.funciones import (  # noqa: F401
    _AGREGADOS_Y_ESCALARES,
    _FUNCIONES_METRICAS,
    _POSTGIS,
    _TRANSPARENTES_A_LAS_UNIDADES,
    FUNCIONES_PERMITIDAS,
)
from geo_sql_guard.unidades import (  # noqa: F401
    _argumento_en_metros,
    _argumentos,
    _clase_de_unidades,
    _literal_entero,
    _nombre_de_funcion,
    _sin_parentesis,
    _srid_de,
    _srid_mezclado,
    fijar_srid_de_columnas,
)

DIALECTO = "postgres"


# Esquemas que nunca son datos de dominio. `pg_authid` guarda los hashes SCRAM;
# `information_schema` describe toda la instancia. Leer de aquí es exfiltración.
_ESQUEMAS_PROHIBIDOS = {"pg_catalog", "information_schema", "pg_toast"}

# Nodos que NO pueden aparecer en ninguna parte del árbol: cualquiera de ellos
# significa que esto no es una consulta de solo lectura.
_NODOS_PROHIBIDOS: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Grant, exp.Merge,
)


# La pregunta "¿ese SRID sirve para medir?" NO se contesta aquí: se importa de
# `core/formatters.py`, que es de donde sale también la nota de unidades que ve
# el LLM en su prompt (`semantic/layer.py:440`). Tener las dos respuestas en
# sitios distintos fue el defecto real (revisión del 8-sep-2026): el prompt le
# decía al modelo que 9377 era de "unidades por verificar" mientras el validador
# le exigía un SRID métrico. Una definición, dos consumidores.
#
# La primera versión de este módulo decidía con el rango 4000–4999 ("el bloque
# geodésico de EPSG"). Enumerado contra la base local (pyproj 3.7.2 / PROJ
# 9.5.1) el atajo resultó falso: de los 804 CRS geográficos vigentes 385 caen
# FUERA del rango —6318 NAD83(2011), 7844 GDA2020, 5340 POSGAR 2007
# (Argentina)— y de los 5.291 proyectados 215 caen DENTRO —4484-4489, las UTM
# de México, 4647 ETRS89/UTM 32N—. Acertaba en los once SRID colombianos y era
# falso como afirmación general. Y además el rango no ve la unidad: EPSG:2276
# (Texas North Central) es proyectado y devuelve PIES cuadrados.


# ---------------------------------------------------------------------------
# SRID mezclado (V3 de F2, H14). `ST_Intersects(c.shape, ST_Transform(b, 32618))`
# con `shape` en 4326 NO da error: el prefiltro por bbox compara grados contra
# metros, nunca solapa, y el COUNT devuelve 0. El usuario oyó "no hay
# construcciones" donde había 8184. Es un hecho verificable del SQL, no un
# juicio: los dos lados de un predicado espacial deben estar en el mismo SRID.
# ---------------------------------------------------------------------------
_PREDICADOS_BINARIOS = frozenset({
    "st_intersects", "st_within", "st_contains", "st_containsproperly", "st_covers",
    "st_coveredby", "st_touches", "st_crosses", "st_overlaps", "st_disjoint",
    "st_equals", "st_intersection", "st_difference", "st_symdifference",
    "st_dwithin", "st_distance",
})


class ResultadoAST(dict):
    """Diagnóstico estructural de una consulta. Es un dict para serializarse fácil."""

    @property
    def aceptada(self) -> bool:
        return bool(self.get("aceptada"))


def _nombre_calificado(esquema: str, tabla: str) -> str:
    # Sin esquema, PostgreSQL resuelve por search_path; el de la app es el
    # default (`public`). Se compara siempre en la forma `esquema.tabla`.
    return f"{esquema or 'public'}.{tabla}"


def analizar(sql: str, tablas_permitidas: set[str] | None = None) -> ResultadoAST:  # noqa: C901, PLR0912
    """Analizar la ESTRUCTURA del SQL y decidir si es una lectura admisible.

    No lanza nunca: un SQL que no parsea se reporta como no aceptado con el
    motivo, para que el modo sombra pueda registrarlo sin tumbar la petición.

    ``tablas_permitidas`` (S0.2 del plan de plataforma, #13): conjunto de
    ``esquema.tabla`` en minúsculas que el SQL puede leer: las entidades que el
    semantic layer descubrió. Con ``None`` no se comprueba (tests y usos sin
    catálogo); con un conjunto vacío se rechaza toda tabla (falla cerrado).
    Antes, `SELECT ... FROM cualquier_tabla` pasaba si no era del catálogo del
    sistema, y el rol `gis_readonly` puede leer TODO `public`.
    """
    motivos: list[str] = []
    funciones: set[str] = set()
    tablas: set[str] = set()

    try:
        arboles = sqlglot.parse(sql, dialect=DIALECTO)
    except Exception as exc:  # noqa: BLE001 — ParseError/TokenError/RecursionError...: todo fallo de parseo RECHAZA (falla cerrado); sin traceback, el motivo viaja en el resultado
        return ResultadoAST(
            aceptada=False,
            motivos=[f"no se pudo parsear: {exc}"],
            funciones=[], tablas=[], n_statements=0,
        )

    arboles = [a for a in arboles if a is not None]

    # 1) Un único statement. `SELECT 1; DROP TABLE x` son DOS: aquí no hay
    #    coincidencia de palabras que valga, se cuentan nodos raíz.
    if len(arboles) != 1:
        motivos.append(f"se esperaba 1 statement, hay {len(arboles)}")

    for arbol in arboles:
        # 2) La raíz debe ser una lectura.
        if not isinstance(arbol, (exp.Select, exp.Union, exp.Subquery)):
            if isinstance(arbol, exp.With):
                pass  # WITH ... SELECT es lectura; el cuerpo se revisa abajo
            else:
                motivos.append(f"statement no es de lectura: {type(arbol).__name__}")

        # 3) Ningún nodo de escritura en TODO el árbol (incluye CTEs y subconsultas).
        for prohibido in arbol.find_all(*_NODOS_PROHIBIDOS):
            motivos.append(f"operación de escritura: {type(prohibido).__name__}")

        # 4) Funciones invocadas, contra la allowlist. El nombre lo resuelve
        #    `_nombre_de_funcion`, que absorbe la asimetría Anonymous/nativa.
        for nodo in arbol.find_all(exp.Func):
            # sqlglot modela los operadores booleanos (`AND`, `OR`) como
            # subclases de Func. No son llamadas a función y contarlos hacía
            # que un `WHERE activo = true AND ST_Within(...)` se rechazara por
            # "función desconocida: and". Detectado midiendo el corpus real.
            if isinstance(nodo, exp.Connector):
                continue
            # Cada rama de un `CASE WHEN … THEN …` es un nodo `If` hijo del `Case`: estructura,
            # no una llamada a una función `if` (T5.1: el servidor MCP de SQL, que aplica el
            # validador de verdad, rechazaba `SUM(CASE WHEN … END)`; en el núcleo pasaba
            # inadvertido porque ahí corre en modo sombra).
            if isinstance(nodo, exp.If) and isinstance(nodo.parent, exp.Case):
                continue
            nombre = _nombre_de_funcion(nodo)
            if nombre:
                funciones.add(nombre)

            # 4-bis) Unidades (auditoría 2026-09-08, §1.4). Estar en la
            #        allowlist no basta: `ST_Area(geom)` sobre las entidades de
            #        este repo (todas `srid: 4326`) devuelve grados², y el
            #        narrador los rotula "km²".
            if nombre in _PREDICADOS_BINARIOS:
                mezcla = _srid_mezclado(nombre, nodo)
                if mezcla:
                    motivos.append(mezcla)

            if nombre in _FUNCIONES_METRICAS:
                argumentos = _argumentos(nodo)
                for indice in _FUNCIONES_METRICAS[nombre]:
                    if indice >= len(argumentos):
                        continue
                    ok, detalle = _argumento_en_metros(argumentos[indice])
                    if not ok:
                        motivos.append(
                            f"unidades: {nombre} mide sobre grados, no metros "
                            f"({detalle})"
                        )

        # 5) Tablas. Leer catálogos del sistema es exfiltración, no consulta de
        #    dominio: `SELECT rolpassword FROM pg_authid` no invoca ninguna
        #    función prohibida, así que sin esto pasaría el filtro.
        #    Los alias de CTE (`WITH m AS (...) SELECT ... FROM m`) aparecen como
        #    tablas en el árbol pero no lo son: no se comprueban contra la lista.
        ctes = {c.alias.lower() for c in arbol.find_all(exp.CTE) if c.alias}
        for nodo in arbol.find_all(exp.Table):
            if nodo.name:
                esquema = (nodo.db or "").lower()
                nombre_tabla = nodo.name.lower()
                tablas.add(f"{esquema}.{nombre_tabla}" if esquema else nombre_tabla)
                calificado = _nombre_calificado(esquema, nombre_tabla)
                if (
                    esquema in _ESQUEMAS_PROHIBIDOS
                    or nombre_tabla.startswith("pg_")
                ):
                    motivos.append(f"acceso a catálogo del sistema: {nombre_tabla}")
                elif (
                    tablas_permitidas is not None
                    and not (not esquema and nombre_tabla in ctes)
                    and calificado not in tablas_permitidas
                ):
                    # Mismo significado que "no existe" para quien lo lee
                    # después (corrector y saneador): para esta app, una tabla
                    # fuera del catálogo NO existe.
                    motivos.append(f"tabla no disponible en el catálogo: {calificado}")

        # (H20, retirado en T3.0: la regla que VETABA la proximidad sin
        #  prefiltro decidía por el modelo —en tablas chicas ese SQL es válido—.
        #  Quedan los HECHOS: el prompt explica el índice y un timeout llega al
        #  corrector con su causa probable, y el LLM decide cómo reescribir.)

    desconocidas = sorted(funciones - FUNCIONES_PERMITIDAS)
    if desconocidas:
        motivos.append(f"funciones fuera de la allowlist: {desconocidas}")

    return ResultadoAST(
        aceptada=not motivos,
        motivos=motivos,
        funciones=sorted(funciones),
        tablas=sorted(tablas),
        n_statements=len(arboles),
    )
