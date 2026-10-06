# 19 · Despliegue en producción (runbook)

Cómo poner GEO_COPILOT en una VM limpia, actualizarlo, respaldarlo y volver atrás. Se sigue en
orden y sin saltos. Lo que cada paso comprueba está en «**Verificar**»: si una verificación falla,
**no se sigue**.

## 0. Qué se necesita

| Qué | Mínimo para el piloto |
|---|---|
| VM | Ubuntu 24.04 LTS · 4 vCPU · 16 GB RAM · 100 GB SSD |
| Red | un nombre DNS (p. ej. `geo.organizacion.org`) apuntando a la VM; 443 (y 80, que redirige) abiertos; salida a Internet (LLM, imágenes satelitales, ArcGIS) |
| Certificado | el de la organización para ese nombre (`tls.crt` con la cadena + `tls.key`) |
| Identidad | un proveedor OIDC (Keycloak, Entra ID u otro que emita el token de acceso como JWT) con un cliente público `geo-copilot-web` (PKCE) cuya URL de retorno es `https://<dominio>/*`; el token de acceso lleva `aud` = `OIDC_AUDIENCE` (en Keycloak, un audience-mapper como el de `docker/keycloak/geo-realm.json`; en Entra ID, emisor y audiencia van en pareja según la versión del token: v2.0 —manifiesto de la API con `accessTokenAcceptedVersion: 2`— → `https://login.microsoftonline.com/<tenant>/v2.0` y el Application (client) ID; v1.0 → `https://sts.windows.net/<tenant>/` y el Application ID URI `api://…`; otra combinación da 401 a todo), un claim de organización y otro de rol (ver `docs/sistema/18-identidad-y-organizaciones.md`). El cliente confidencial de la API solo hace falta con servidores MCP de `auth: token_exchange` |
| LLM | credenciales del proveedor (Azure OpenAI, OpenAI, Anthropic o compatible) |
| Datos | el volcado de la BD de dominio (`02_catastro_real.dump`) si se carga el catastro de ejemplo |

## 1. Preparar la VM

```bash
# Docker Engine + plugin compose (≥ 2.24: el compose de producción usa !reset/!override)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version          # ≥ v2.24
```

**Verificar:** `docker run --rm hello-world` imprime el saludo.

## 2. Traer la versión

```bash
sudo mkdir -p /opt/geo-copilot && sudo chown "$USER" /opt/geo-copilot
git clone --branch v1.0.0 --depth 1 https://github.com/geoai-latam/GEO_COPILOT.git /opt/geo-copilot
cd /opt/geo-copilot
```

Del repositorio se usan: los compose (`docker/*.yml`), los scripts de arranque de la BD
(`docker/init-db`), la configuración de nginx y del sandbox, y los scripts de operación. Las
**imágenes** vienen del registro, no se construyen aquí. Las publica el CI
(`.github/workflows/ci.yml`) al etiquetar la versión, en dos tiempos: el job `images` construye
cada imagen y la escanea con Trivy (bloquea una vulnerabilidad CRÍTICA o ALTA con arreglo), y el
job `publicar` empuja a GHCR **esa misma imagen** (comprueba sus capas) solo si toda la suite
quedó en verde: backend (ruff, mypy, pytest), integración con PostGIS, frontend (vitest, build,
Playwright), las cinco imágenes escaneadas y, en una etiqueta `v*`, las evaluaciones con el LLM
real sobre el umbral. Si algo falla, esa versión no llega al registro.

Si el paquete del registro es privado: `echo <token> | docker login ghcr.io -u <usuario> --password-stdin`.

## 3. Configurar

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Rellenar **todos** los marcadores `__RELLENAR…__`. Cada secreto aleatorio:
`openssl rand -base64 36 | tr -d '/+=' | cut -c1-40` (y `SECRETS_KEK`: `openssl rand -base64 32`).
`VERSION` debe coincidir con la etiqueta del paso 2. Las claves de los MCP se escriben una vez
(`IMAGERY_MCP_APP_KEY`, `ARCGIS_MCP_APP_KEY`, `LUGARES_MCP_APP_KEY`): las listas `…_KEYS` las toman de ahí.

El archivo solo se usa para interpolar los compose: la app recibe únicamente las variables
listadas en `app.environment` de `docker/docker-compose.prod.yml` (no la contraseña del
superusuario de la BD ni las listas de claves de los MCP). Una variable que no esté en esa lista
**no llega a la app y no avisa**: para fijar otro ajuste, añadirlo allí sin valor (`NOMBRE:`).

`OIDC_JWKS_URL` y `OIDC_TOKEN_URL` son opcionales y la plantilla las deja comentadas: vacías, la
API las lee del documento de descubrimiento del emisor (`<OIDC_ISSUER>/.well-known/openid-configuration`).
Solo se fijan si la API no alcanza la URL pública del emisor. Si el proveedor no responde o su
documento no casa, las peticiones con Bearer reciben **503** con la causa y el log de la app dice
`[identidad] …` (ver `docs/sistema/18-identidad-y-organizaciones.md`).

Certificado y datos:

```bash
mkdir -p docker/certs                               # no viene en el repositorio
cp /ruta/tls.crt /ruta/tls.key docker/certs/
cp /ruta/02_catastro_real.dump docker/init-db/     # opcional: datos de dominio
```

Sin `tls.crt` y `tls.key` en `docker/certs/`, el frontend **no arranca**: en producción el
directorio se monta de solo lectura, así que no se genera un autofirmado; el log del frontend
dice «falta el certificado de la organización en /etc/nginx/certs».

**Verificar:**

```bash
grep -nE '__[A-Z_]+__' .env.production       # sin salida: no queda ningún marcador
C="docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --env-file .env.production"
$C config -q                                 # sin errores (falta una obligatoria → la nombra)
```

## 4. Arrancar

```bash
# en cada sesión nueva de la terminal, la misma definición de `C` del paso 3
C="docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --env-file .env.production"
$C pull
$C up -d --no-build
```

`pull` falla si a alguna imagen le falta la versión en el registro (el CI no la publicó porque
algo de la suite falló, ver paso 2): entonces **no se sigue**. Nada se construye en la VM
(`--no-build`, y el compose de producción no tiene `build:`).

El primer arranque restaura el volcado (varios minutos, según su tamaño; se sigue con
`$C logs -f postgis` hasta «Restore catastro completado»). Mientras tanto `up -d` espera: postgis
tiene hasta 30 min para quedar sano. El servicio `migrar` pone el esquema de la plataforma en su
versión y termina; recién entonces arranca `app`.

**Verificar:**

```bash
$C ps -a                                     # app (cada réplica), imagery, arcgis y frontend «healthy»; migrar «Exited (0)»
$C logs migrar | tail -3                     # «Running upgrade … 0002_plataforma_f6» (o nada que hacer)
$C exec app python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read()[:80])"
curl -sI https://<dominio>/ | head -1        # HTTP/2 200
```

Y como usuario: abrir `https://<dominio>`, entrar con una cuenta de la organización, preguntar
«¿qué entidades hay disponibles?» y «trae los lotes de la manzana 004503009» (aprobar la consulta):
4 lotes en el mapa.

## 5. Operación diaria

| Qué | Cómo |
|---|---|
| Logs | `$C logs -f app` (JSON: `correlation_id` enlaza todas las líneas de una petición; el mismo id vuelve en la cabecera `X-Request-ID`) |
| Métricas y trazas | `$C --profile observabilidad up -d --no-build` y `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` en `.env.production` → Jaeger en `127.0.0.1:16686`, Prometheus en `127.0.0.1:9090` (por túnel SSH). Umbrales: `docker/observabilidad/alertas.yml` (la caída de la app o de una réplica, la latencia de los turnos —`ConsultaLenta`, sin contar la espera humana, que va aparte en `geo_hitl_espera_segundos`— y el LLM: `LlmFallando`, `LlmSinCuotaOClave`, `LlmSaturado`). **Las alertas no avisan a nadie**: no hay Alertmanager, así que solo se ven en la UI de Prometheus (pestaña *Alerts*, estado «firing»); ver «Recibir las alertas» abajo. Jaeger guarda las trazas en disco (volumen `jaeger_datos`, 7 días; sobreviven a un reinicio); para muestrear, `OTEL_TRACES_SAMPLER(_ARG)` |
| Errores | `SENTRY_DSN` en `.env.production` |
| Evaluación nocturna | `crontab -e` → `0 3 * * * cd /opt/geo-copilot && sh scripts/evals_nocturnas.sh >> /var/log/geo-evals.log 2>&1` (sale ≠ 0 bajo el umbral; con `MAILTO` llega un correo) |
| Escalar | `WEB_CONCURRENCY` (procesos por réplica) y `APP_REPLICAS` (réplicas de la app; nginx reparte) en `.env.production`, y `$C up -d --no-build`. El estado va por Redis. Antes de subir usuarios, medir la cuota del LLM con la prueba de carga (abajo) |
| Proveedor del LLM saturado | Ante un 429 por límite de tasa, cada llamada al LLM espera y reintenta hasta `LLM_ESPERA_SATURACION_S` **segundos de reloj** (por llamada, no por turno; decide si se reintenta, no corta un intento en curso), y nunca más allá del plazo del turno (`TOTAL_EXECUTION_TIMEOUT`, 120 s por defecto, más `HITL_TIMEOUT`, 300 s, con aprobaciones; los dos se fijan en `.env.production` y llegan a la app): lo que pase antes. nginx espera a `/api/` **480 s** (`docker/nginx.frontend.conf`, dentro de la imagen del frontend): si `TOTAL_EXECUTION_TIMEOUT + HITL_TIMEOUT` pasa de 450 s, hay que subir ese `proxy_read_timeout` (en una versión nueva de la imagen, o montando la conf cambiada sobre `/etc/nginx/templates/app.conf.template` `:ro`); si no, nginx corta con un 504 un turno que la app iba a responder. Agotado, el usuario lee que el proveedor está saturado. Un 429 `insufficient_quota` (saldo o cuota de facturación agotados) **no se espera**: falla al momento con su propio mensaje («avisa al administrador»), y cuenta en `geo_llm_fallos_total{tipo="cuota_agotada"}`. Subir la espera no sustituye a dimensionar la cuota del deployment |
| Aprobar herramientas nuevas de un MCP | `$C exec app python -m geo_copilot.platform.mcp.aprobar` (§6), panel Conexiones (admin) o `POST /api/v1/connections/<srv>/tools/<tool>/approve` |

### Recibir las alertas (Alertmanager)

El stack evalúa las reglas pero no notifica. Para que lleguen por correo o a un webhook (Teams,
Slack, un sistema de guardias):

1. Añadir a `docker/docker-compose.prod.yml` un servicio `alertmanager` (imagen
   `prom/alertmanager` con versión fijada), en el perfil `observabilidad`, en la red de
   Prometheus, sin puertos publicados (o en `127.0.0.1`), con su `alertmanager.yml` montado `:ro`
   y un receptor (`email_configs` con el SMTP de la organización, o `webhook_configs`). Los
   secretos del receptor van en `.env.production`, no en el repositorio.
2. En `docker/observabilidad/prometheus.yml`, descomentar el bloque `alerting:` que apunta a
   `alertmanager:9093`.
3. `$C --profile observabilidad up -d --no-build` y comprobar que Prometheus lo descubrió:
   `$C exec prometheus wget -qO- http://localhost:9090/api/v1/alertmanagers` lista
   `alertmanager:9093` en `activeAlertmanagers`.

### Prueba de carga

`scripts/prueba_carga.py` simula N usuarios por la puerta pública (nginx) y compara con los
umbrales de `alertas.yml`. Consulta las manzanas del catastro de ejemplo (sin él, adaptar su
mezcla a los datos de la organización).

El límite de peticiones (`RATE_LIMIT_REQUESTS` por minuto y ruta) es **por identidad** (el
principal: organización + usuario), no por IP: cabeceras como `X-Forwarded-For` no separan
usuarios. Por eso cada usuario simulado necesita su identidad: un JWT de OIDC por usuario en
`CARGA_TOKENS` (separados por comas), de cuentas de prueba distintas y que no caduquen durante la
corrida (o saldrán 401 a mitad y la prueba fallará). Es la única forma de tener usuarios
independientes. El script también acepta la clave de servicio (`API_KEY`), pero la app tiene
UNA API key: con ella todos los usuarios son el mismo principal (`servicio:api-key`) y comparten
un único cupo. El script lo avisa siempre que algún usuario la usa (con un token por usuario,
la clave no se usa), rechaza varias claves (no serían varias identidades) y espacia el sondeo de
aprobaciones, pero entonces mide el límite y no el sistema. Si se usa así, subir
`RATE_LIMIT_REQUESTS` solo durante la prueba y decirlo en el informe.

```bash
CARGA_TOKENS=<jwt1>,<jwt2>,<jwt3>,<jwt4>,<jwt5> \
  python scripts/prueba_carga.py --usuarios 5 --minutos 15 --base https://<dominio>
```

La salida (`bench_results/carga*.json`) es local y no se versiona; la corrida que vale como
evidencia se copia a `docs/validacion/evidencia/fase-7/`. El equipo que corre la prueba no debe
suspenderse durante ella (el script lo detecta y la invalida).

## 6. Actualizar a otra versión

Antes, un respaldo de la BD (paso 7): es lo único que permite volver atrás si la versión nueva
cambia el esquema.

```bash
cd /opt/geo-copilot
git fetch --tags && git checkout v1.1.0
sed -i 's/^VERSION=.*/VERSION=1.1.0/' .env.production
diff .env.production.example .env.production   # variables nuevas de la plantilla → añadirlas
$C pull && $C up -d --no-build   # `migrar` aplica las revisiones nuevas antes de la app
```

Las aprobaciones pendientes sobreviven al reinicio (Redis): al aprobarlas, el turno se retoma.

**Herramientas MCP que la versión nueva cambió o añadió.** El pinning deshabilita una herramienta
cuya descripción o esquema cambió, y deja pendiente una nueva, hasta que un administrador la
apruebe. Tras cada actualización hay que revisarlas; si no, el agente trabaja sin ellas en
silencio. En la revisión del 2026-10-04, `archivos_estadisticas` (la que calcula promedios sobre
todo el archivo) llevaba días pendiente, y el agente respondía con el promedio de una muestra:

```bash
$C exec app python -m geo_copilot.platform.mcp.aprobar                      # lista las pendientes y lo que leerá el LLM
$C exec app python -m geo_copilot.platform.mcp.aprobar --pendientes --por <quién> --si
```

Hace lo mismo que el botón del panel Conexiones (fija la huella y queda en la auditoría como
`tool.reaprobar`, vía `cli`), y la app la habilita en su próxima revalidación del catálogo (cada 60 s).
Sin `--si` solo muestra: se aprueba leyendo la descripción, no a ciegas. Sirve para los servidores
de la plataforma (el YAML); las conexiones de una organización se aprueban en su panel.

### Si el `.env.production` salió de la plantilla anterior a la auditoría de F7

`diff` muestra las diferencias, pero estas cambian el comportamiento y conviene revisarlas una a una
antes del `up`:

- **La app ya no recibe el `.env.production` entero.** Antes el compose de producción se lo pasaba
  completo (`env_file`); ahora (`env_file: !reset []`) solo le llegan las variables listadas en
  `app.environment` de `docker/docker-compose.prod.yml`. Cualquier ajuste propio que se hubiera
  añadido a mano al `.env.production` y no esté en esa lista **deja de tener efecto, sin error**:
  añadirlo a la lista como `NOMBRE:` (sin valor). Para comprobarlo, tras el `up`:

  ```bash
  # ajustes propios (los que la plantilla no trae) que la app NO recibe
  nombres() { grep -oE '^[A-Z_][A-Z0-9_]*' "$1" | sort -u; }
  comm -23 <(nombres .env.production) <(nombres .env.production.example) \
    | comm -23 - <($C exec -T app env | grep -oE '^[A-Z_][A-Z0-9_]*' | sort -u)
  ```

  Las variables de la plantilla no se comparan (las de infraestructura que la app no recibe, como
  las contraseñas o las `…_MCP_KEYS`, están ahí): con un `.env.production` hecho solo de la
  plantilla no sale nada. Cada nombre que salga es un ajuste añadido a mano que la app ya no ve
  (si es de la app, añadirlo a `app.environment` como `NOMBRE:`) o una variable que la plantilla
  nueva quitó (`POSTGRES_DB`, `POSTGRES_USER`, `OIDC_JWKS_URL_INTERNA`…: ver más abajo).
- `TOTAL_EXECUTION_TIMEOUT` y `HITL_TIMEOUT` llegan a la app y se pueden subir, pero nginx espera
  a `/api/` 480 s: si su suma pasa de 450 s, subir también `proxy_read_timeout` (paso 5).
- Hay métricas y alertas nuevas: `geo_hitl_espera_segundos` (la espera a una aprobación, que ya no
  cuenta en `ConsultaLenta`), `geo_llm_fallos_total{tipo}`, `geo_llm_reintentos_total` y las
  alertas `LlmFallando`, `LlmSinCuotaOClave` y `LlmSaturado`. Como todas, **no notifican a nadie**
  mientras no haya Alertmanager (paso 5, «Recibir las alertas»).
- `ENVIRONMENT`, `SESSION_BACKEND`, `TRUST_PROXY_HEADERS`, `POSTGRES_DB` y `POSTGRES_USER` **ya no
  tienen efecto**: los fija el compose de producción (`production`, `redis`, `true`, `geo_copilot`,
  `geo_user`). Quitarlos del `.env.production` para que nadie crea que cambiarlos cambia algo.
- `OIDC_AUDIENCE` es la audiencia del **token de acceso** (`geo-copilot-api` en el realm de
  referencia; en Entra ID, la de la versión del token, ver paso 0), no el cliente del navegador
  (`geo-copilot-web`, el valor de la plantilla antigua). Si no casa, la API responde 401 a todo.
- `OIDC_JWKS_URL` es opcional: vacía, la API toma la `jwks_uri` del descubrimiento del emisor
  (sirve en Keycloak, Entra ID y Google), así que un `.env.production` antiguo sin ella funciona.
  Si se copió de una plantilla intermedia con `OIDC_JWKS_URL=__RELLENAR_JWKS__`, **borrar la
  línea** (o poner la URL de verdad): una URL explícita manda sobre el descubrimiento y el
  marcador daría 401 a todo. `OIDC_JWKS_URL_INTERNA` y `OIDC_TOKEN_URL_INTERNA` ya no se usan; el
  endpoint de token, solo con `auth: token_exchange`, es `OIDC_TOKEN_URL` (también opcional:
  vacía, el `token_endpoint` del descubrimiento).
- `IMAGERY_MCP_KEYS` y `ARCGIS_MCP_KEYS` toman la clave de `…_APP_KEY` (`"key":"${…_APP_KEY}"`):
  copiarlas de la plantilla nueva en vez de mantener la clave escrita dos veces.
- `MCP_SQL_LECTOR_PASSWORD` y `KC_API_CLIENT_SECRET` pasan a opcionales; `APP_REPLICAS` es nueva
  (por defecto 1).
- En la BD ya creada, el rol `catastro` del volcado era LOGIN con una contraseña publicada en el
  repositorio; ahora basta con que exista:
  `$C exec -T postgis sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER ROLE catastro NOLOGIN PASSWORD NULL"'`.

## 7. Respaldos

```bash
# BD: dominio + plataforma (dueños, conexiones cifradas, auditoría) + workspaces
$C exec -T postgis sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > respaldo-$(date +%F).dump
# Redis (sesiones y aprobaciones en curso; AOF activado): instantánea fuera del contenedor
$C exec -T redis redis-cli --rdb /data/respaldo.rdb
$C cp redis:/data/respaldo.rdb redis-$(date +%F).rdb
```

(redis-cli se autentica con `REDISCLI_AUTH`, que el compose le pone al contenedor.) Los
respaldos se copian **fuera de la VM**: en el disco de la VM no protegen de perderla.

Guardar también `.env.production`, **en particular `SECRETS_KEK`**: sin ella, las credenciales
de las conexiones de las organizaciones no se pueden descifrar.

## 8. Volver atrás

Las migraciones solo van hacia delante: si la versión nueva añadió una revisión, el `migrar` de
la anterior no la conoce («Can't locate revision») y la app no arranca. En ese caso se restaura el
respaldo tomado ANTES de actualizar (paso 6), que trae su propio `alembic_version`:

```bash
git checkout v1.0.0
sed -i 's/^VERSION=.*/VERSION=1.0.0/' .env.production
$C stop app frontend                         # nadie conectado a la BD
# solo si la versión nueva cambió el esquema: BD recreada desde el respaldo, con sus permisos
$C exec -T postgis sh -c 'dropdb -U "$POSTGRES_USER" --force "$POSTGRES_DB"'
$C exec -T postgis sh -c 'pg_restore -U "$POSTGRES_USER" -d postgres --create --exit-on-error'    < respaldo-AAAA-MM-DD.dump
$C pull && $C up -d --no-build
```

Lo escrito después del respaldo (conversaciones, auditoría, conexiones nuevas) se pierde.
**Verificar:** lo mismo que en el paso 4.

## 9. Qué NO hace este despliegue

- Alta disponibilidad de la BD o de Redis (una sola VM): el piloto acepta minutos de caída.
- Renovación automática del certificado: la hace la organización.
- Avisar de las alertas: sin Alertmanager solo se ven en Prometheus (cómo añadirlo, en el paso 5).
- Un IdP propio: se usa el de la organización (el Keycloak del compose es de desarrollo).
