"""F7 (auditoría): el despliegue de producción — compose, plantilla de variables, runbook y scripts.

Lo que estos tests fijan lo destapó la auditoría de F7: el compose de producción pedía las claves
al Keycloak de desarrollo, metía en `app` la contraseña del superusuario, construía en la VM una
imagen que el CI había rechazado, y la plantilla y el runbook dejaban marcadores sin detectar o
variables que nunca llegaban a su servicio. Se leen los archivos tal cual (sin arrancar nada) y, si
hay Docker en la máquina, además se renderiza el compose con `docker compose config`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from geo_copilot.core.config import Settings

RAIZ = Path(__file__).resolve().parent.parent
BASE = RAIZ / "docker" / "docker-compose.yml"
PROD = RAIZ / "docker" / "docker-compose.prod.yml"
PLANTILLA = RAIZ / ".env.production.example"
RUNBOOK = RAIZ / "docs" / "sistema" / "19-despliegue-produccion.md"
EVALS = RAIZ / "scripts" / "evals_nocturnas.sh"
RESTORE = RAIZ / "docker" / "init-db" / "01_restore_catastro.sh"
NGINX = RAIZ / "docker" / "nginx.frontend.conf"

# Variables que el código lee con os.environ (no son campos de Settings) y que la app necesita.
ENTORNO_DIRECTO = {
    "WEB_CONCURRENCY", "PYTHONPATH", "PROMETHEUS_MULTIPROC_DIR", "DOCKER_HOST",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_TRACES_SAMPLER", "OTEL_TRACES_SAMPLER_ARG",
    "SENTRY_DSN", "SENTRY_TRACES_SAMPLE_RATE", "SECRETS_KEK", "SECRETS_KEK_ANTERIORES",
    "MCP_HOSTS_PERMITIDOS", "IMAGERY_MCP_API_KEY", "ARCGIS_MCP_APP_KEY", "LUGARES_MCP_APP_KEY",
}
# Secretos de infraestructura que el contenedor de la app NO debe recibir como variable.
SECRETOS_AJENOS = {
    "POSTGRES_PASSWORD", "POSTGRES_USER", "MCP_SQL_LECTOR_PASSWORD", "IMAGERY_MCP_KEYS", "LUGARES_MCP_KEYS",
    "ARCGIS_MCP_KEYS", "GEO_APP_PASSWORD", "REDIS_PASSWORD", "KC_API_CLIENT_SECRET",
}
MARCADOR = re.compile(r"__[A-Z_]+__")


# --------------------------------------------------------------------------- lectura


class _Etiqueta:
    """Valor con una etiqueta de compose (`!reset`, `!override`)."""

    def __init__(self, etiqueta: str, valor: object) -> None:
        self.etiqueta, self.valor = etiqueta, valor


class _Cargador(yaml.SafeLoader):
    pass


def _etiquetado(loader: yaml.SafeLoader, sufijo: str, nodo: yaml.Node) -> _Etiqueta:
    if isinstance(nodo, yaml.MappingNode):
        valor: object = loader.construct_mapping(nodo, deep=True)
    elif isinstance(nodo, yaml.SequenceNode):
        valor = loader.construct_sequence(nodo, deep=True)
    else:
        assert isinstance(nodo, yaml.ScalarNode)
        valor = loader.construct_scalar(nodo)
    return _Etiqueta(sufijo, valor)


_Cargador.add_multi_constructor("!", _etiquetado)


def _yaml(ruta: Path) -> dict:
    datos: dict = yaml.load(ruta.read_text(encoding="utf-8"), Loader=_Cargador)
    return datos


def _plantilla() -> dict[str, str]:
    """Variables DEFINIDAS (sin comentar) en la plantilla de producción."""
    variables = {}
    for linea in PLANTILLA.read_text(encoding="utf-8").splitlines():
        if linea.strip() and not linea.lstrip().startswith("#"):
            nombre, _, valor = linea.partition("=")
            variables[nombre.strip()] = valor
    return variables


def _plantilla_opcionales() -> set[str]:
    """Ajustes que la plantilla ofrece COMENTADOS (`# NOMBRE=`): opcionales, pero ofrecidos."""
    return set(re.findall(r"(?m)^#\s*([A-Z][A-Z0-9_]+)=", PLANTILLA.read_text(encoding="utf-8")))


def _servicios_de_produccion() -> set[str]:
    """Servicios que arrancan en producción: los del base sin perfil + los del overlay."""
    base = _yaml(BASE)["services"]
    return {n for n, s in base.items() if not s.get("profiles")} | set(_yaml(PROD)["services"])


def _texto_de_servicios(nombres: set[str]) -> str:
    texto = []
    for archivo in (BASE, PROD):
        servicios = _yaml(archivo)["services"]
        for n in nombres & set(servicios):
            texto.append(json.dumps(servicios[n], default=lambda o: o.valor, ensure_ascii=False))
    return "\n".join(texto)


def _env_app_prod() -> dict:
    env = _yaml(PROD)["services"]["app"]["environment"]
    assert isinstance(env, dict)
    return env


# --------------------------------------------------------------------------- plantilla


def test_la_plantilla_define_toda_variable_obligatoria_de_produccion():
    """Cada `${VAR:?…}` de un servicio que corre en producción está en la plantilla: si no, el
    `up` aborta en la VM con un error que el runbook no anticipa."""
    texto = _texto_de_servicios(_servicios_de_produccion())
    obligatorias = set(re.findall(r"\$\{([A-Z0-9_]+):\?", texto))
    assert obligatorias, "no se encontró ninguna variable obligatoria: ¿cambió el formato?"
    faltan = obligatorias - set(_plantilla())
    assert not faltan, f"obligatorias en el compose que la plantilla no define: {sorted(faltan)}"


def test_toda_variable_de_la_plantilla_llega_a_algun_servicio():
    """Una variable que no consume nadie es un secreto inútil (le pasó a MCP_SQL_LECTOR_PASSWORD,
    que nunca llegaba a postgis) o una opción que el operador cree que funciona y no hace nada."""
    compose = BASE.read_text(encoding="utf-8") + PROD.read_text(encoding="utf-8")
    interpoladas = set(re.findall(r"\$\{([A-Z0-9_]+)", compose))
    a_la_app = set(_env_app_prod())
    # las …_KEYS se interpolan desde la propia plantilla con ${…_APP_KEY}
    muertas = set(_plantilla()) - interpoladas - a_la_app
    assert not muertas, f"variables de la plantilla que ningún servicio recibe: {sorted(muertas)}"


def test_los_marcadores_de_la_plantilla_los_detecta_la_verificacion_del_runbook():
    """El runbook comprueba con `grep -nE '__[A-Z_]+__'`: todo lo que hay que rellenar tiene que
    caer en ese patrón (antes `__IDP__`, `__DOMINIO__` o `__MISMA_QUE_…__` escapaban a
    `grep -c __RELLENAR__`) y no puede haber otros estilos de marcador."""
    variables = _plantilla()
    con_marcador = {k for k, v in variables.items() if MARCADOR.search(v)}
    assert {"POSTGRES_PASSWORD", "OIDC_ISSUER", "ORG_PLATAFORMA",
            "CORS_ORIGINS", "AZURE_OPENAI_ENDPOINT"} <= con_marcador
    for nombre, valor in variables.items():
        for marca in MARCADOR.findall(valor):
            assert marca.startswith("__RELLENAR"), f"{nombre}: marcador {marca} fuera de la convención"
        assert "<" not in valor and "MISMA_QUE" not in valor, f"{nombre}: marcador sin detectar: {valor}"
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "grep -nE '__[A-Z_]+__' .env.production" in runbook
    assert "grep -c __RELLENAR__" not in runbook


def test_las_claves_de_los_mcp_se_escriben_una_sola_vez():
    """La lista que acepta cada MCP toma la clave de la app por interpolación: ya no puede quedar
    una distinta (todas las llamadas daban 401)."""
    variables = _plantilla()
    for servicio in ("IMAGERY", "ARCGIS"):
        lista = variables[f"{servicio}_MCP_KEYS"]
        assert f'"key":"${{{servicio}_MCP_APP_KEY}}"' in lista


def test_identidad_alineada_con_el_realm_de_referencia():
    """La audiencia es la que el realm de referencia pone en el token (geo-copilot-api), y los
    claims de organización/rol están a la vista para cambiarlos con otro proveedor."""
    variables = _plantilla()
    realm = (RAIZ / "docker" / "keycloak" / "geo-realm.json").read_text(encoding="utf-8")
    assert f'"included.custom.audience": "{variables["OIDC_AUDIENCE"]}"' in realm
    assert {"OIDC_ORG_CLAIM", "OIDC_ROLES_CLAIM"} <= set(variables)
    assert Settings.model_fields["oidc_audience"].default == variables["OIDC_AUDIENCE"]
    # el usuario y la BD de postgis están fijos en el base: no se ofrecen como configurables
    assert "POSTGRES_USER" not in variables and "POSTGRES_DB" not in variables
    assert "APP_REPLICAS" in variables


def test_las_urls_oidc_son_opcionales_y_sin_marcador_que_se_cuele():
    """Sin OIDC_JWKS_URL / OIDC_TOKEN_URL se usan las del descubrimiento del emisor. Un marcador
    activo (`OIDC_JWKS_URL=__RELLENAR_JWKS__`) que alguien dejara por «opcional» llegaría a la app
    como URL explícita, que manda sobre el descubrimiento: 401 a todo sin explicación."""
    variables = _plantilla()
    assert "OIDC_JWKS_URL" not in variables and "OIDC_TOKEN_URL" not in variables
    assert {"OIDC_JWKS_URL", "OIDC_TOKEN_URL"} <= _plantilla_opcionales()
    texto = PLANTILLA.read_text(encoding="utf-8")
    assert "RELLENAR_JWKS" not in texto and "jwks_uri" in texto
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "es nueva y obligatoria" not in runbook
    doc18 = (RAIZ / "docs" / "sistema" / "18-identidad-y-organizaciones.md").read_text(encoding="utf-8")
    assert "503" in doc18 and "__RELLENAR" in doc18  # qué se ve si falla y que el marcador se borra


def test_con_entra_id_emisor_y_audiencia_van_en_pareja():
    """En los tokens v2.0 de Entra `aud` es el client ID (GUID), no el `api://…`; y el `iss` de
    /v2.0 solo sale con accessTokenAcceptedVersion=2. La guía decía «Application ID URI» junto
    al emisor v2.0: esa pareja no casa nunca y daba 401 a todo."""
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for texto in (plantilla, runbook):
        assert "accessTokenAcceptedVersion" in texto
        assert "Application (client) ID" in texto
        assert "https://sts.windows.net/" in texto
    assert "en Entra ID, el Application ID URI de la API" not in plantilla


# --------------------------------------------------------------------------- compose de producción


def test_la_app_no_recibe_el_env_file_entero_ni_secretos_de_infraestructura():
    app = _yaml(PROD)["services"]["app"]
    assert isinstance(app["env_file"], _Etiqueta) and app["env_file"].etiqueta == "reset"
    env = _env_app_prod()
    assert not (set(env) & SECRETOS_AJENOS)
    # las del base tampoco (DATABASE_URL lleva GEO_APP_PASSWORD, que es la del rol de la app)
    assert not (set(_yaml(BASE)["services"]["app"]["environment"]) & SECRETOS_AJENOS)


def test_cada_variable_de_la_app_es_un_ajuste_que_el_codigo_lee():
    """Una errata en la lista explícita sería un ajuste que el operador pone y no hace nada."""
    campos = {nombre.upper() for nombre in Settings.model_fields}
    for nombre in _env_app_prod():
        assert nombre in campos or nombre in ENTORNO_DIRECTO, f"{nombre} no lo lee la app"


def test_toda_variable_de_la_plantilla_que_lee_la_app_le_llega():
    """También las que se ofrecen comentadas (TOTAL_EXECUTION_TIMEOUT): con `env_file: !reset` la app
    solo recibe lo listado, y un ajuste ofrecido que no llega no hace nada sin avisar."""
    campos = {nombre.upper() for nombre in Settings.model_fields}
    env = set(_env_app_prod()) | set(_yaml(BASE)["services"]["app"]["environment"])
    for nombre in set(_plantilla()) | _plantilla_opcionales():
        if nombre in campos or nombre in ENTORNO_DIRECTO:
            assert nombre in env, f"{nombre} está en la plantilla pero no llega a la app"


def test_todo_ajuste_de_la_app_que_nombra_el_runbook_le_llega():
    """El runbook presentaba TOTAL_EXECUTION_TIMEOUT como ajustable y no estaba en `app.environment`:
    con `env_file: !reset` ponerlo en .env.production no hacía nada, sin error."""
    campos = {nombre.upper() for nombre in Settings.model_fields}
    env = set(_env_app_prod()) | set(_yaml(BASE)["services"]["app"]["environment"])
    nombrados = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})", RUNBOOK.read_text(encoding="utf-8")))
    ajustes = {n for n in nombrados if n in campos or n in ENTORNO_DIRECTO}
    assert {"TOTAL_EXECUTION_TIMEOUT", "HITL_TIMEOUT", "LLM_ESPERA_SATURACION_S"} <= ajustes
    assert not ajustes - env, f"el runbook los da por ajustables y no llegan a la app: {sorted(ajustes - env)}"


def _notas_de_actualizacion() -> str:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    return runbook[runbook.index("plantilla anterior a la auditoría de F7"):runbook.index("## 7.")]


def test_el_runbook_avisa_de_lo_que_cambia_para_quien_actualiza():
    notas = _notas_de_actualizacion()
    assert "env_file" in notas and "`NOMBRE:`" in notas and "exec -T app env" in notas
    # la espera de nginx que citan las notas es la de la conf (y el margen, el del test de http)
    conf = NGINX.read_text(encoding="utf-8")
    api = conf[conf.index("location /api/"):]
    api = api[:api.index("}")]
    lectura = int(re.search(r"proxy_read_timeout\s+(\d+)s;", api).group(1))  # type: ignore[union-attr]
    assert f"{lectura} s" in notas and f"{lectura - 30} s" in notas and "proxy_read_timeout" in notas
    for nuevo in ("geo_hitl_espera_segundos", "LlmFallando", "LlmSinCuotaOClave", "LlmSaturado", "Alertmanager"):
        assert nuevo in notas, nuevo


def _bash() -> str | None:
    """Un bash que de verdad corre (en Windows, `bash` puede ser el lanzador de WSL sin distro)."""
    bash = shutil.which("bash")
    if bash is None:
        return None
    r = subprocess.run([bash, "-c", "comm --version"], capture_output=True)
    return bash if r.returncode == 0 else None


def test_la_comprobacion_de_ajustes_perdidos_solo_senala_los_propios(tmp_path: Path):
    """La orden de las notas, ejecutada: con un .env.production de la plantilla (opcionales
    incluidos) no señala nada de un despliegue correcto —lo de infraestructura, ni
    KC_API_CLIENT_SECRET, que llega renombrado—; sí el ajuste propio y lo que la plantilla quitó."""
    bash = _bash()
    if bash is None:
        pytest.skip("sin bash")
    notas = _notas_de_actualizacion()
    bloque = notas[notas.index("```bash\n", notas.index("Para comprobarlo")) + len("```bash\n"):]
    orden = textwrap.dedent(bloque[:bloque.index("```")])
    assert "exec -T app env" in orden

    plantilla = PLANTILLA.read_text(encoding="utf-8")
    (tmp_path / ".env.production.example").write_text(plantilla, encoding="utf-8", newline="\n")
    # el operador descomenta los opcionales y añade lo suyo
    propio = re.sub(r"(?m)^#\s*([A-Z][A-Z0-9_]+=)", r"\1", plantilla)
    propio += "MI_AJUSTE=1\nPOSTGRES_USER=geo_user\n"
    (tmp_path / ".env.production").write_text(propio, encoding="utf-8", newline="\n")
    definidas = set(re.findall(r"(?m)^([A-Z][A-Z0-9_]+)=", propio))
    # lo que ve el contenedor: lo que tiene valor en el compose y lo `NOMBRE:` que el .env define
    entorno = {**_yaml(BASE)["services"]["app"]["environment"], **_env_app_prod()}
    visto = [n for n, v in entorno.items() if v is not None or n in definidas]
    (tmp_path / "env_app").write_text(
        "".join(f"{n}=x\n" for n in [*visto, "PATH", "HOME", "HOSTNAME"]),
        encoding="utf-8", newline="\n")

    r = subprocess.run([bash, "-c", "falso() { cat env_app; }\nC=falso\n" + orden],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["MI_AJUSTE", "POSTGRES_USER"]


def test_en_produccion_las_claves_oidc_no_se_piden_al_keycloak_de_desarrollo():
    env = _env_app_prod()
    # sin valor: de .env.production si está, si no se deriva del emisor (nunca keycloak:8080)
    assert "OIDC_JWKS_URL" in env and env["OIDC_JWKS_URL"] is None
    assert "OIDC_TOKEN_URL" in env and env["OIDC_TOKEN_URL"] is None


def test_produccion_no_construye_imagenes_en_la_vm():
    for nombre, servicio in _yaml(PROD)["services"].items():
        if "${IMAGEN_PREFIJO" in str(servicio.get("image", "")) and nombre != "migrar":
            build = servicio.get("build")
            assert isinstance(build, _Etiqueta) and build.etiqueta == "reset", nombre
    runbook = RUNBOOK.read_text(encoding="utf-8")
    ups = re.findall(r"\$C (?:--profile \S+ )?up -d[^\n`]*", runbook)
    assert ups and all("--no-build" in u for u in ups), ups


def test_migrar_usa_el_usuario_y_la_bd_que_crea_postgis():
    url = _yaml(PROD)["services"]["migrar"]["environment"]["MIGRACIONES_DATABASE_URL"]
    postgis = _yaml(BASE)["services"]["postgis"]["environment"]
    assert url.startswith(f"postgresql://{postgis['POSTGRES_USER']}:")
    assert url.endswith(f"@postgis:5432/{postgis['POSTGRES_DB']}")


def test_postgis_tiene_tiempo_para_restaurar_el_volcado_en_produccion():
    periodo = _yaml(PROD)["services"]["postgis"]["healthcheck"]["start_period"]
    assert re.fullmatch(r"\d+m", periodo) and int(periodo[:-1]) >= 10


def test_el_frontend_expuesto_a_internet_va_endurecido():
    frontend = _yaml(PROD)["services"]["frontend"]
    assert frontend["cap_drop"] == ["ALL"]
    assert "NET_RAW" not in frontend["cap_add"] and "SYS_ADMIN" not in frontend["cap_add"]
    assert frontend["read_only"] is True
    assert "no-new-privileges:true" in frontend["security_opt"]
    # lo que escribe nginx al arrancar: la conf de envsubst, temporales y el pid
    destinos = {t.split(":")[0] for t in frontend["tmpfs"]}
    assert {"/etc/nginx/conf.d", "/var/cache/nginx", "/run"} <= destinos
    assert all("size=" in t for t in frontend["tmpfs"])
    assert frontend["volumes"].valor == ["./certs:/etc/nginx/certs:ro"]
    # leer tls.key ajena basta con saltar permisos de LECTURA; DAC_OVERRIDE salta también escritura
    assert "DAC_OVERRIDE" not in frontend["cap_add"] and "DAC_READ_SEARCH" in frontend["cap_add"]


def test_el_frontend_solo_comparte_red_con_la_app():
    """nginx solo habla con app:8000: no debe ver redis, postgis ni los MCP."""
    prod = _yaml(PROD)
    frontend = prod["services"]["frontend"]["networks"]
    assert isinstance(frontend, _Etiqueta) and frontend.etiqueta == "override"
    assert frontend.valor == ["borde"]
    assert "borde" in prod["services"]["app"]["networks"]
    assert not prod["networks"]["borde"].get("internal")  # con internal no se publica 80/443
    en_borde = {n for n, s in prod["services"].items()
                if "borde" in str(getattr(s.get("networks"), "valor", s.get("networks")))}
    assert en_borde == {"frontend", "app"}
    # lo único a lo que nginx hace proxy es la app
    conf = (RAIZ / "docker" / "nginx.frontend.conf").read_text(encoding="utf-8")
    assert set(re.findall(r"^\s*server\s+([a-z0-9_-]+):", conf, re.M)) == {"app"}


# --------------------------------------------------------------------------- compose base


def test_postgis_recibe_la_clave_del_lector_mcp():
    """init-db/08 la lee del entorno de postgis: sin pasarla, el rol nunca se creaba."""
    assert "MCP_SQL_LECTOR_PASSWORD" in _yaml(BASE)["services"]["postgis"]["environment"]
    assert "MCP_SQL_LECTOR_PASSWORD" in (RAIZ / "docker/init-db/08_mcp_lector.sh").read_text(encoding="utf-8")


def test_redis_cli_se_autentica_dentro_del_contenedor():
    """El respaldo del runbook (`exec redis redis-cli …`) necesita la clave dentro del contenedor."""
    assert "REDISCLI_AUTH" in _yaml(BASE)["services"]["redis"]["environment"]
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert 'redis-cli -a "$REDIS_PASSWORD"' not in runbook
    assert "$C exec -T redis redis-cli --rdb" in runbook and "$C cp redis:" in runbook


def test_el_tmpfs_de_metricas_tiene_tope():
    tmpfs = _yaml(BASE)["services"]["app"]["tmpfs"]
    metricas = [t for t in tmpfs if t.startswith("/tmp/prometheus")]
    assert metricas and re.search(r"(^|,)size=\d+[kmg]", metricas[0].split(":", 1)[1])


def test_jaeger_guarda_en_disco_y_vuelve_tras_caerse():
    """Un tope por número de trazas no acota bytes (una sesión WebSocket es una traza): en memoria
    el OOM lo mataba, sin `restart` no volvía y se perdían todas."""
    base = _yaml(BASE)
    jaeger = base["services"]["jaeger"]
    env = jaeger["environment"]
    assert env["SPAN_STORAGE_TYPE"] == "badger" and env["BADGER_EPHEMERAL"] == "false"
    assert "MEMORY_MAX_TRACES" not in env
    assert re.fullmatch(r"\d+h", env["BADGER_SPAN_STORE_TTL"])
    # los directorios caen dentro del volumen con nombre (persisten entre recreaciones)
    volumen, destino = jaeger["volumes"][0].split(":")
    assert volumen in base["volumes"]
    assert env["BADGER_DIRECTORY_KEY"].startswith(destino + "/")
    assert env["BADGER_DIRECTORY_VALUE"].startswith(destino + "/")
    assert jaeger["restart"] == "unless-stopped"
    assert "GOMEMLIMIT" in env


def test_todo_servicio_con_otel_recibe_el_endpoint():
    """Un MCP que instala geo_mcp_kit[otel] sin OTEL_EXPORTER_OTLP_ENDPOINT no continúa la traza."""
    revisados = 0
    for nombre, servicio in _yaml(BASE)["services"].items():
        build = servicio.get("build")
        if not isinstance(build, dict):
            continue
        dockerfile = (RAIZ / build["dockerfile"]).read_text(encoding="utf-8")
        if "[otel]" not in dockerfile:
            continue
        revisados += 1
        env = servicio.get("environment") or {}
        nombres = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in nombres, nombre
    assert revisados >= 2  # imagery-mcp y arcgis-mcp


# --------------------------------------------------------------------------- runbook y scripts


def test_runbook_verifica_lo_que_de_verdad_muestra_docker():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    # `ps` sin -a no lista a migrar (terminado)
    assert "$C ps -a" in runbook and re.search(r"^\$C ps(?! -a)", runbook, re.M) is None
    # imagery, arcgis y el frontend traen HEALTHCHECK en su imagen: «Up» a secas dejaría pasar un
    # «(unhealthy)», y pedir «healthy» a una imagen sin HEALTHCHECK no se cumpliría nunca
    linea_ps = next(linea for linea in runbook.splitlines() if linea.startswith("$C ps -a"))
    sanos = linea_ps.split("«healthy»")[0]
    assert all(s in sanos for s in ("imagery", "arcgis", "app", "frontend")), sanos
    for servicio in ("imagery_mcp", "arcgis_mcp"):
        dockerfile = (RAIZ / "services" / servicio / "Dockerfile").read_text(encoding="utf-8")
        assert "HEALTHCHECK" in dockerfile, servicio
    # la sonda del frontend prueba SU nginx (certificado y estáticos), no la app: /health la atraviesa
    frontend = (RAIZ / "docker" / "Dockerfile.frontend").read_text(encoding="utf-8")
    sonda = frontend[frontend.index("HEALTHCHECK"):].split("CMD", 1)[1].splitlines()[0]
    assert "https://127.0.0.1/" in sonda and "/health" not in sonda, sonda
    assert "APP_REPLICAS" in runbook
    # volver atrás: una orden de restauración concreta, no solo «restaurar el respaldo»
    assert "pg_restore" in runbook and "--create" in runbook and "dropdb" in runbook


def test_la_prueba_de_carga_se_ofrece_con_sus_requisitos():
    """La mezcla de prueba_carga.py consulta el catastro de ejemplo y pide identidades por
    variable de entorno: quien la corre siguiendo la plantilla o el runbook tiene que saberlo."""
    script = (RAIZ / "scripts" / "prueba_carga.py").read_text(encoding="utf-8")
    assert "MANZANAS" in script and "CARGA_TOKENS" in script and "API_KEY" in script
    for texto in (PLANTILLA.read_text(encoding="utf-8"), RUNBOOK.read_text(encoding="utf-8")):
        assert "prueba_carga.py" in texto
        assert "catastro de ejemplo" in texto and "CARGA_TOKENS" in texto
        # la API key no es una identidad por usuario: un solo principal, un solo cupo
        assert "UNA API key" in texto or "UNA sola API key" in texto


def test_evals_nocturnas_no_escribe_en_una_carpeta_del_usuario_del_checkout():
    """El contenedor escribe como `geocopilot`: montar bench_results/ directamente daba
    PermissionError en Linux al final de toda la corrida."""
    script = EVALS.read_text(encoding="utf-8")
    assert '-v "$PWD/bench_results:' not in script
    assert 'SALIDA="$(mktemp -d)"' in script and 'chmod 0777 "$SALIDA"' in script
    assert 'exit "$rc"' in script


@pytest.mark.skipif(shutil.which("sh") is None, reason="sin sh")
def test_evals_nocturnas_es_sh_valido():
    assert subprocess.run(["sh", "-n", str(EVALS)], capture_output=True).returncode == 0


def test_el_rol_catastro_no_puede_iniciar_sesion():
    script = RESTORE.read_text(encoding="utf-8")
    assert "catastro_dev_pw" not in script
    assert "CREATE ROLE catastro NOLOGIN" in script


# --------------------------------------------------------------------------- render real


def _docker_compose() -> list[str] | None:
    if shutil.which("docker") is None:
        return None
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    return ["docker", "compose"] if r.returncode == 0 else None


@pytest.fixture
def renderizado(tmp_path: Path) -> dict:
    compose = _docker_compose()
    if compose is None:
        pytest.skip("sin docker compose")
    env = PLANTILLA.read_text(encoding="utf-8")
    env = env.replace("IMAGERY_MCP_APP_KEY=__RELLENAR__", "IMAGERY_MCP_APP_KEY=clave-imagery-1")
    env = env.replace("ARCGIS_MCP_APP_KEY=__RELLENAR__", "ARCGIS_MCP_APP_KEY=clave-arcgis-2")
    archivo = tmp_path / "env.production"
    archivo.write_text(env, encoding="utf-8")
    r = subprocess.run(
        [*compose, "-f", str(BASE), "-f", str(PROD), "--env-file", str(archivo),
         "config", "--format", "json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    servicios: dict = json.loads(r.stdout)["services"]
    return servicios


def test_render_produccion(renderizado: dict):
    app = renderizado["app"]
    env = app["environment"]
    assert "env_file" not in app and "build" not in app
    assert not (set(env) & SECRETOS_AJENOS)
    assert "keycloak" not in json.dumps(env)
    # sin definir en .env.production → no llega (null en el render; el contenedor no la ve)
    assert env.get("OIDC_TOKEN_URL") is None
    assert "build" not in renderizado["frontend"]
    # la lista de cada MCP lleva la MISMA clave que manda la app
    imagery = json.loads(renderizado["imagery-mcp"]["environment"]["IMAGERY_MCP_KEYS"])
    assert imagery[0]["key"] == env["IMAGERY_MCP_API_KEY"] == "clave-imagery-1"
    arcgis = json.loads(renderizado["arcgis-mcp"]["environment"]["ARCGIS_MCP_KEYS"])
    assert arcgis[0]["key"] == env["ARCGIS_MCP_APP_KEY"] == "clave-arcgis-2"
    assert renderizado["migrar"]["environment"]["MIGRACIONES_DATABASE_URL"].startswith(
        "postgresql://geo_user:")
    # la red del borde ya renderizada: el frontend solo en ella, la app en ella y en las suyas
    assert set(renderizado["frontend"]["networks"]) == {"borde"}
    assert {"borde", "geo_network", "docker_proxy"} <= set(app["networks"])
    assert set(renderizado["frontend"]["cap_add"]) == {
        "NET_BIND_SERVICE", "SETUID", "SETGID", "CHOWN", "DAC_READ_SEARCH"}


def test_los_usuarios_del_realm_de_desarrollo_tienen_id_fijo():
    """F7 (T7.6): sin `id`, cada vez que compose recrea el Keycloak de desarrollo el realm se
    reimporta con UUID nuevos: ana deja de ser la dueña de sus sesiones y proyectos (404 en todo lo
    suyo, visto en la suite de integración)."""
    import json

    realm = json.loads((RAIZ / "docker" / "keycloak" / "geo-realm.json").read_text(encoding="utf-8"))
    ids = {u["username"]: u.get("id") for u in realm["users"]}
    assert all(ids.values()), f"usuarios sin id fijo: {[u for u, i in ids.items() if not i]}"
    assert len(set(ids.values())) == len(ids)
