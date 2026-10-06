"""Seed de demostración + capa semántica: que lo que el LLM lee exista de verdad.

Cierra dos hallazgos de la revisión del 2026-09-08, y los cierra de forma que
no puedan volver sin que la suite lo diga:

1. Un clon limpio arrancaba con la base VACÍA. El dump real (522 MB) está en
   `.gitignore`, `01_restore_catastro.sh` lo salta en silencio, y el seed que
   ya existía en `tests/fixtures/` no lo cableaba nadie. Ahora hay una copia en
   `docker/init-db/02_seed_demo.sql`, que el entrypoint de Postgres ejecuta
   solo. Copia, no `import`: el initdb solo corre lo que está en ese
   directorio. Estos tests son los que evitan que las dos copias diverjan.

2. `semantic_layer/entities.yaml` describía seis tablas inexistentes, y ese
   texto entra al prompt del LLM (`data_agent/agent.py:269-270`). Acá se
   comprueba que cada tabla y cada columna que declara el YAML activo existan
   en el `CREATE TABLE` del seed.

Todo es lectura de archivos: no hace falta Docker ni una BD levantada.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent

SEED_INITDB = RAIZ / "docker" / "init-db" / "02_seed_demo.sql"
SEED_FIXTURE = RAIZ / "tests" / "fixtures" / "seed_catastro.sql"
ENTITIES = RAIZ / "semantic_layer" / "entities.yaml"
EJEMPLO = RAIZ / "semantic_layer" / "entities.example.yaml"


def _texto(ruta: Path) -> str:
    """Contenido con finales de línea normalizados (el checkout es Windows)."""
    return ruta.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _tablas_del_seed(sql: str) -> dict[str, set[str]]:
    """`{'catastro.construcciones': {'objectid', 'concodigo', …}, …}`.

    Parsea los `CREATE TABLE` del seed. Se queda con el primer token de cada
    línea del cuerpo y descarta las que empiezan por `CONSTRAINT`.
    """
    tablas: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE (?:IF NOT EXISTS )?([\w.]+)\s*\((.*?)\n\);",
        sql,
        re.DOTALL | re.IGNORECASE,
    ):
        nombre, cuerpo = match.group(1), match.group(2)
        columnas = set()
        for linea in cuerpo.splitlines():
            linea = linea.strip()
            if not linea or linea.upper().startswith(("CONSTRAINT", "PRIMARY KEY", "--")):
                continue
            columnas.add(linea.split()[0].lower())
        tablas[nombre.lower()] = columnas
    return tablas


# =============================================================================
# 1. El seed llega a un clon limpio
# =============================================================================
class TestSeedCableado:
    def test_el_seed_esta_en_init_db(self):
        """Sin este archivo, `docker compose up` levanta una BD vacía."""
        assert SEED_INITDB.exists(), (
            f"{SEED_INITDB} no existe: quien clone el repo arranca sin datos y "
            f"ninguna consulta del README funciona"
        )

    def test_las_dos_copias_son_identicas(self):
        """El fixture de tests y el seed del stack tienen que ser el mismo SQL."""
        assert _texto(SEED_INITDB) == _texto(SEED_FIXTURE), (
            "docker/init-db/02_seed_demo.sql y tests/fixtures/seed_catastro.sql "
            "divergieron. Son copias a propósito (el initdb sólo ejecuta lo que "
            "está en su directorio): copia una sobre la otra."
        )

    def test_corre_antes_del_grant_de_gis_readonly(self):
        """El orden alfabético del initdb no es cosmético.

        `03_gis_readonly.sql:26` hace `GRANT USAGE ON SCHEMA catastro` sin
        comprobar que el esquema exista, y el entrypoint de Postgres corre los
        `.sql` con `ON_ERROR_STOP=1`. Si el seed corriera DESPUÉS, en un clon
        sin el dump real el bootstrap entero abortaría ahí (reproducido: psql
        sale con código 3, `no existe el esquema «catastro»`).
        """
        init_db = SEED_INITDB.parent
        ejecutables = sorted(
            p.name for p in init_db.iterdir() if p.suffix in {".sql", ".sh"}
        )
        assert ejecutables.index("02_seed_demo.sql") < ejecutables.index(
            "03_gis_readonly.sql"
        ), f"el seed tiene que ir antes del GRANT sobre catastro; orden actual: {ejecutables}"
        assert ejecutables.index("01_restore_catastro.sh") < ejecutables.index(
            "02_seed_demo.sql"
        ), "el seed tiene que correr DESPUÉS del restore, para poder apartarse si hay datos"

    @pytest.mark.parametrize("tabla", ["construcciones", "lotes"])
    def test_los_insert_estan_protegidos(self, tabla):
        """Quien ya tiene el dump real no puede perder datos ni recibir duplicados.

        Los `INSERT` y los `CREATE INDEX` van dentro de un `IF EXISTS (SELECT 1
        FROM …) THEN … ELSE`: si la tabla tiene filas, el seed no toca nada.
        Sin el guard, los objectid 1-25 chocarían con la PK del dump y
        abortarían el initdb.
        """
        sql = _texto(SEED_INITDB)
        guard = f"IF EXISTS (SELECT 1 FROM catastro.{tabla})"
        assert guard in sql, f"falta el guard de tabla vacía para catastro.{tabla}"

        # El INSERT tiene que estar DENTRO del bloque DO que abre ese guard.
        pos_guard = sql.index(guard)
        pos_insert = sql.index(f"INSERT INTO catastro.{tabla} VALUES")
        pos_fin = sql.index("END IF;", pos_guard)
        assert pos_guard < pos_insert < pos_fin, (
            f"el INSERT de catastro.{tabla} quedó fuera del guard: sobrescribiría "
            f"o duplicaría los datos de quien tenga el dump real"
        )

    def test_no_hay_vacuum(self):
        """`VACUUM` no corre dentro de un bloque de transacción, y sobre el dump
        real (522 MB) barría cada página para no recuperar nada: la tabla acaba
        de cargarse. `ANALYZE` sí, que es lo que necesita el planner."""
        sql = _texto(SEED_INITDB)
        ejecutable = "\n".join(
            linea for linea in sql.splitlines() if not linea.lstrip().startswith("--")
        )
        assert "VACUUM" not in ejecutable.upper()
        assert "ANALYZE catastro.construcciones;" in ejecutable
        assert "ANALYZE catastro.lotes;" in ejecutable


# =============================================================================
# 2. La capa semántica describe lo que la base tiene
# =============================================================================
class TestCapaSemanticaContraElSeed:
    @pytest.fixture(scope="class")
    def config(self):
        return yaml.safe_load(_texto(ENTITIES))

    @pytest.fixture(scope="class")
    def tablas(self):
        return _tablas_del_seed(_texto(SEED_INITDB))

    def test_el_seed_declara_las_dos_tablas(self, tablas):
        assert set(tablas) == {"catastro.construcciones", "catastro.lotes"}

    def test_toda_entidad_apunta_a_una_tabla_real(self, config, tablas):
        """El hallazgo original: seis entidades, cero tablas existentes."""
        for nombre, ent in config["entities"].items():
            cualificada = f"{ent['schema']}.{ent['table']}".lower()
            assert cualificada in tablas, (
                f"la entidad '{nombre}' declara {cualificada}, que no existe en el "
                f"seed. Ese texto entra al prompt del LLM: el modelo escribirá SQL "
                f"contra una tabla fantasma. Tablas reales: {sorted(tablas)}"
            )

    def test_toda_columna_declarada_existe(self, config, tablas):
        for nombre, ent in config["entities"].items():
            reales = tablas[f"{ent['schema']}.{ent['table']}".lower()]
            for campo, datos in ent["fields"].items():
                columna = datos["column"].lower()
                assert columna in reales, (
                    f"{nombre}.{campo} apunta a la columna '{columna}', que no está "
                    f"en {ent['schema']}.{ent['table']} ({sorted(reales)})"
                )

    def test_la_columna_de_geometria_existe(self, config, tablas):
        """Antes decía `geom` en las seis entidades. La columna se llama `shape`,
        y `catalog_search._generate_join_example` construye SQL con ese nombre."""
        for nombre, ent in config["entities"].items():
            reales = tablas[f"{ent['schema']}.{ent['table']}".lower()]
            assert ent["geometry_column"].lower() in reales, (
                f"la geometría de '{nombre}' ({ent['geometry_column']}) no existe; "
                f"columnas: {sorted(reales)}"
            )

    def test_las_metricas_no_miden_en_grados(self, config):
        """SRID 4326 son grados: `ST_Area(shape)` da grados², no m².

        El validador AST rechaza ese SQL, así que una métrica sin `::geography`
        sería una métrica que el sistema no puede ejecutar nunca.
        """
        for nombre, ent in config["entities"].items():
            for metrica in ent.get("metrics", []):
                expr = metrica["expression"]
                if re.search(r"ST_(Area|Length|Distance|Perimeter)", expr, re.IGNORECASE):
                    assert "::geography" in expr or "ST_Transform" in expr, (
                        f"{nombre}.{metrica['name']} = {expr!r} mide sobre grados; "
                        f"el validador AST la rechazará"
                    )

    def test_cada_columna_real_esta_documentada(self, config, tablas):
        """Al revés que los tests de arriba: que no falte nada por describir.

        Si el LLM no sabe que una columna existe, no la usa nunca.
        """
        declaradas: dict[str, set[str]] = {}
        for ent in config["entities"].values():
            clave = f"{ent['schema']}.{ent['table']}".lower()
            declaradas[clave] = {d["column"].lower() for d in ent["fields"].values()} | {
                ent["geometry_column"].lower()
            }
        for tabla, columnas in tablas.items():
            faltan = columnas - declaradas.get(tabla, set())
            assert not faltan, f"{tabla}: columnas reales sin documentar en el YAML: {sorted(faltan)}"


# =============================================================================
# 3. La plantilla vieja no puede volver a ser la configuración activa
# =============================================================================
class TestPlantillaDeEjemplo:
    def test_el_ejemplo_existe_y_esta_rotulado(self):
        assert EJEMPLO.exists()
        cabecera = _texto(EJEMPLO)[:1500]
        assert "EJEMPLO" in cabecera and "NO SE CARGA" in cabecera

    def test_el_ejemplo_no_es_el_que_carga_la_app(self):
        """`config.semantic_layer_path` apunta al activo, no a la plantilla."""
        from geo_copilot.core.config import Settings

        ruta = Settings.model_fields["semantic_layer_path"].default
        assert ruta.endswith("entities.yaml")
        assert "example" not in ruta

    def test_las_seis_entidades_ficticias_salieron_del_archivo_activo(self):
        activo = _texto(ENTITIES)
        for tabla in (
            "cat_parcelas_2024",
            "div_politica_mun",
            "hidro_rios_prin",
            "infra_vias_nac",
            "amenaza_inund_2023",
            "equip_publico",
        ):
            assert tabla not in activo, (
                f"'{tabla}' no existe en ninguna base de este repositorio "
                f"(verificado con pg_restore sobre el dump real): no puede volver "
                f"al YAML que alimenta el prompt"
            )
