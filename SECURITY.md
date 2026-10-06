# Seguridad

GEO_COPILOT ejecuta dos artefactos que no escribió una persona: **SQL PostGIS** contra una base
de datos y **código Python** contra un intérprete. Los dos los redacta un modelo de lenguaje a
partir de lo que alguien escribió en un chat. Eso convierte una consulta de usuario en una
superficie de ejecución, y es la razón por la que este archivo tiene detalle en vez de tres
líneas de trámite.

El diseño asume ese riesgo. Lo contienen tres capas: aprobación humana, contenedor aislado y un
rol de base de datos sin privilegios. Acá están las tres, qué se considera un fallo de seguridad,
y qué es comportamiento conocido que ya está escrito en las auditorías del repositorio.

---

## Antes que nada: qué trae y qué no (desde la fase 6)

- **Hay identidad de usuarios (OIDC).** Cada petición lleva el token de una persona y la API lo
  verifica. Sus sesiones, su workspace y sus proyectos no existen para nadie más. El nginx **ya no
  inyecta ninguna clave**: antes era un BFF que daba la API entera a quien alcanzara el puerto.
  Ver `docs/sistema/18-identidad-y-organizaciones.md`.
- **Hay TLS** en el frontend. El certificado de desarrollo es autofirmado; en producción va el de
  la organización.
- **Hay roles** (lectura, analista, administración) aplicados en un solo punto por el que pasan el
  agente y los paneles.
- **Hay auditoría** de solo añadir, encadenada por hash.
- **Sin `OIDC_ISSUER` sigue siendo el modo de desarrollo:** sin login, un único usuario `dev`
  administrador. El compose ata los puertos a `127.0.0.1` por defecto. No expongas una instalación
  sin OIDC.

Todavía no es un producto «listo para internet» sin revisión: la fase 7 (operación en producción)
cierra lo que queda (Redis obligatorio, varios workers, observabilidad, despliegue reproducible).

---

## Versiones con soporte

| Versión | Rama | Correcciones de seguridad |
|---|---|---|
| 0.1.x | `main` | Sí |
| Cualquier commit anterior | — | No |

El proyecto está en alfa (`Development Status :: 3 - Alpha`, en `pyproject.toml`). No hay ramas de
mantenimiento: lo que se corrige, se corrige sobre `main`.

---

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Un issue con una prueba de concepto de escape del sandbox queda
indexado y clonado antes de que exista el parche.

Canal privado:

1. **Correo a `sebastian.forero.77@gmail.com`** con el asunto `[SEGURIDAD] GEO_COPILOT`.
2. Si el repositorio ya tiene activado *Private vulnerability reporting*, sirve igual: pestaña
   **Security → Report a vulnerability** en `https://github.com/geoai-latam/GEO_COPILOT`.

Con qué llega antes el arreglo:

- **Versión o commit** exacto (`git rev-parse HEAD`).
- **Cómo levantaste el stack**: `docker compose --env-file .env -f docker/docker-compose.yml up`,
  o instalación manual con el backend fuera de Docker.
- **Proveedor y modelo de LLM** (`LLM_PROVIDER`, `LLM_MODEL`). Importa: la forma del SQL y del
  Python generado cambia entre proveedores.
- **La consulta exacta** que escribiste en el chat, y el SQL o el código que el sistema mostró en
  el panel de aprobación.
- **Qué esperabas y qué pasó**, con el impacto concreto: lectura de un archivo del contenedor,
  escritura en la base, una petición saliente que nadie pidió.
- Logs relevantes, **sin claves**. `backend.log` guarda prompts, SQL generado e identificadores de
  sesión; revísalo antes de adjuntarlo.

Qué esperar de vuelta:

- Un acuse de recibo lo antes posible, y después el diagnóstico: si se arregla, si se documenta
  como abierto conocido o si se descarta, con el porqué.
- Divulgación coordinada: se publica cuando exista el parche. Con crédito a tu nombre si lo
  quieres.

No le pongo un número de horas a eso a propósito. Esto lo mantiene **una persona**, no un equipo
de guardia, y un plazo que no pueda cumplir en una semana mala vale menos que decirte de entrada
cómo funciona. Si el reporte es grave y no tienes respuesta, insiste: no es desinterés.

**No hay programa de recompensas.** Lo que sí hay es crédito público a quien reporte, salvo que
prefieras el anonimato.

---

## El modelo de amenaza

Quien ataque no necesita una cuenta. Le bastan dos caminos:

1. **Directo.** Escribe en el chat una consulta que induzca al modelo a generar SQL o Python
   hostil.
2. **Indirecto.** Publica un dataset en un catálogo abierto —ArcGIS Hub, por ejemplo— con
   instrucciones metidas en el campo `title` o `description`. El descubrimiento lo encuentra, ese
   texto entra al contexto del modelo, y el sistema actúa sobre él sin que el atacante haya
   hablado nunca con la aplicación. No es teórico: es el hallazgo R0.9 de la auditoría del 26 de
   julio de 2026.

Entre esa entrada y la ejecución hay tres capas. Se aplican en este orden.

### Capa 1 — Aprobación humana (HITL)

Ninguna acción sensible se ejecuta sin que una persona la vea y la apruebe en el panel.

| Acción | Agente | Cuándo se pide |
|---|---|---|
| `SQL_EXECUTION` | GISAgent | Cualquier `SELECT` generado |
| `CODE_EXECUTION` | PythonAgent | Cualquier código generado |
| `DATA_IMPORT` | DataAgent | Cargas grandes de datos externos |
| `PLAN_APPROVAL` | Planner | Planes multi-paso |

- `HITL_ENABLED=true` es el default (`core/config.py`). Apagarlo desarma la capa 1 completa.
- **El gate es una allowlist y falla cerrado.** Solo `APPROVED` ejecuta; `MODIFIED` ejecuta lo que
  editó la persona. Todo lo demás —`REJECTED`, `EXPIRED`, y cualquier estado que se añada
  mañana— cancela. El timeout por defecto son 300 segundos: si no contestas, no corre nada.
- **Lo que apruebas es lo que se ejecuta.** El artefacto viaja íntegro al panel y se emite un
  `content_sha256`; antes de ejecutar se recalcula la huella y aborta con
  `ApprovedContentMismatch` si cambió (remediación R0.8, en las dos rutas: SQL y Python).

Aprobar sin leer anula esta capa. El panel muestra el SQL y el código completos justamente para
que se puedan leer.

### Capa 2 — Sandbox en contenedor

- `SANDBOX_BACKEND=docker` es el **default** desde la remediación R0.5. El backend `subprocess`
  ejecuta el código del modelo dentro del proceso `app`, con `DATABASE_URL`, las claves del LLM y
  `DOCKER_HOST` en el entorno; `enforce_sandbox_backend` (`api/auth.py`) lo rehúsa fuera de
  desarrollo.
- El contenedor `sandbox` corre con `network:none`, `read_only`, `cap_drop: ALL`, sin privilegios
  nuevos y con usuario no root.
- `app` **no monta el socket de Docker**. Llega al daemon por un mediador haproxy con allowlist de
  ruta exacta y filtro sobre el cuerpo (`Privileged`, `User`, `HostConfig`), en una red interna
  donde solo están `app` y el mediador (remediación R0.4).
- El filtro AST del sandbox es **defensa en profundidad, no la barrera**. El propio módulo se
  autodescribe así. La contención real es el contenedor.

### Capa 3 — Rol de base de datos de mínimos privilegios

- La aplicación se conecta como **`geo_app`**: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`,
  miembro de `gis_readonly`, creado por `docker/init-db/04_geo_app_role.sql`.
- `POSTGRES_USER` (`geo_user`) es **superusuario** en la imagen `postgis/postgis` y queda
  reservado para inicialización y mantenimiento. Una transacción `READ ONLY` no contiene a un
  superusuario: verificado contra el contenedor, `pg_read_file('/etc/passwd')` devuelve el fichero
  y `pg_authid` expone los hashes SCRAM.
- El SQL del modelo corre tras un `SET LOCAL ROLE gis_readonly` cuyo chequeo previo **falla
  cerrado**: si el rol no está disponible, no se ejecuta SQL (remediación R0.6). Además,
  transacción de solo lectura, `statement_timeout` y `LIMIT` capado.

### Capa 4 — Identidad, propiedad y roles (F6)

- **Tokens.** Verificados con las claves públicas del emisor (solo algoritmos asimétricos), con
  `iss`, `aud` y `exp`.
- **Propiedad.** Cada sesión, workspace y proyecto tiene dueño. Para otra persona no existe (404).
- **WebSocket.** Se abre con un ticket de un solo uso por sesión, así que el token no viaja en la URL.
- **Roles por riesgo.** Un visor no calcula ni escribe.
- **Herramientas MCP de una organización.** No existen para otra.
- **Credenciales de las conexiones.** Cifradas en sobre (AES-256-GCM, clave maestra fuera de la BD).
  Nunca vuelven en respuestas ni logs.
- **Red de esas conexiones.** Solo `https` público, a la IP validada y fijada (anti-rebinding).
- **Token exchange.** A los MCP que lo declaran les llega un token del usuario, nunca la clave de
  servicio en nombre de una persona.

### Capa 5 — Auditoría (F6)

`plataforma.auditoria` registra consultas, ejecuciones (con resultado `ok`, `error` o `denegado`),
solicitudes y decisiones HITL (con quién decidió) y la administración de conexiones y tools. La app
no puede cambiarla ni borrarla. Si alguien con acceso directo a la BD la toca, la cadena de hashes
lo delata (`GET /auditoria/verificar`).

### Lo que no es una capa

El validador de texto del SQL filtra por coincidencia de subcadenas y se le escapan formas
conocidas. El validador estructural con `sqlglot` existe, pero va en modo `shadow`
(`SQL_AST_VALIDATION=shadow`): registra qué rechazaría y no bloquea. Trátalos como telemetría.

---

## Qué cuenta como vulnerabilidad acá

Repórtalo por el canal privado:

- **Escapar del sandbox**: ejecutar fuera del contenedor, conseguir egreso de red desde él, o
  escribir en el sistema de archivos del host.
- **Saltarse el HITL**: que un SQL o un código se ejecuten sin aprobación, o que lo ejecutado
  difiera de lo aprobado (huella `content_sha256`).
- **Escalar en la base de datos**: pasar de `geo_app` / `gis_readonly` a superusuario, leer
  `pg_authid` o `pg_read_file`, escribir en tablas de dominio.
- **Alcanzar el daemon de Docker** a través del mediador: crear un contenedor, montar el socket,
  levantar algo con `Privileged`.
- **SSRF**: lograr que el backend visite una URL que no pidió el usuario y no salió de
  `found_services`.
- **Robar la `API_KEY`**: que aparezca en el bundle del navegador, en una respuesta, o en un log
  accesible.
- **Leer la sesión de otra persona**: aprobar por ella, leer su SQL, vaciar su conversación.
- **Filtración de secretos** en imágenes de Docker, logs o mensajes de error al cliente.
- **XSS o contenido activo** servido desde el mismo origen al que nginx adjunta la clave. El proxy
  de teselas es el sitio clásico: R0.10 fue exactamente eso, vía `image/svg+xml`.
- **Denegación de servicio barata**: una consulta que tumbe el stack o bloquee la base.

## Qué es comportamiento conocido y no una vulnerabilidad

- **Que el modelo genere SQL o Python malos.** Para eso está el panel de aprobación. Aprobar sin
  leer es una decisión del operador.
- **Que un análisis dé un número equivocado** —áreas en grados rotuladas como km², un shapefile
  sin `.prj` etiquetado 4326— es un error de corrección geoespacial. Va en un **issue público**,
  no por el canal privado. Hay varios abiertos en `docs/AUDITORIA_2026-09-08.md` §1.
- **El modo desarrollo.** Con `ENVIRONMENT=development` la API va sin autenticación y `/docs` está
  abierto. Es el default, es conocido, y está en la lista de abiertos de abajo.
- **Publicar los puertos a `0.0.0.0` a mano**, o correr a propósito con
  `SANDBOX_BACKEND=subprocess`.
- **El costo en tokens** de una consulta que dispare muchas llamadas al LLM.
- **Un CVE en una dependencia sin ruta de explotación demostrada acá.** Abre un issue normal con
  el aviso. Si tienes la ruta, entonces sí: canal privado.

---

## Abiertos conocidos (no hace falta reportarlos)

Están verificados, con archivo y línea, en
[`docs/AUDITORIA_2026-09-08.md`](docs/AUDITORIA_2026-09-08.md) §3 y en
[`docs/AUDITORIA_SEGURIDAD_2026-07-27.md`](docs/AUDITORIA_SEGURIDAD_2026-07-27.md), sección de
pendientes:

1. `SQL_AST_VALIDATION` sigue en `shadow`. **AUD-04 no está cerrado: está observado.** El control
   vivo es una denylist de texto a la que se le escapan formas conocidas.
2. ~~El WebSocket no valida el `session_id` de la ruta~~ — **cerrado en F6**: el socket exige un
   ticket de un solo uso de SU sesión y la propiedad por identidad (la sesión de otro: 4404).
3. El filtro AST del sandbox no aplica `FORBIDDEN_NAMES` a accesos por atributo, y
   `FORBIDDEN_METHODS` no bloquea escrituras (`to_file`, `to_csv`, `savefig`).
4. `allowed_domains` se omite en nueve rutas de los conectores, y `POST /discovery/load` acepta un
   `service_url` arbitrario del cliente.
5. Tres canales devuelven el error crudo al cliente; `error_sanitizer.py` es una denylist que hay
   que invertir a allowlist.
6. El default de `ENVIRONMENT` es el inseguro.
7. `.dockerignore` no excluye `*.log`, así que `backend.log` se hornea en la imagen con
   `COPY . .`.

Riesgos aceptados de la fase 6, con su porqué:

8. **Si escribir la auditoría falla, la acción sigue** (queda un ERROR en el log). Para el piloto
   se prefiere no bloquear el trabajo por un fallo del registro. Pasar a *fail-closed* es una línea
   en `platform/auditoria.py`.
9. **Los tokens del navegador se guardan en `localStorage`** para que una pestaña nueva no pida
   entrar otra vez. Un XSS podría leerlos. El de acceso vive 5 minutos, y la CSP y DOMPurify son la
   contención.
10. **El realm de desarrollo trae un cliente con contraseña directa** (`geo-copilot-e2e`) para los
    tests automáticos. No debe existir en el proveedor de producción.

Si encuentras una **explotación concreta** que vaya más allá de lo que esas dos auditorías ya
describen, esa sí: canal privado.

---

## Secretos

- `.env` está en `.gitignore` y nunca se ha commiteado. La auditoría del 8 de septiembre de 2026
  barrió el historial completo —428 commits en ese momento— buscando `sk-`, `AIza`, `ghp_`,
  `xoxb-`, `AKIA` y `-----BEGIN`, y salió limpio.
- Las contraseñas de `.env.example` van **comentadas** a propósito. El compose las declara con
  `${VAR:?}`, así que sin definirlas el stack no arranca. Ese fallo ruidoso es intencional: evita
  que un despliegue quede corriendo con una contraseña publicada en un repositorio.
- Si se te escapa una clave en un commit, **rótala en el proveedor**. Borrarla con `git rm` o
  reescribir el historial no la quita de los forks, de los clones ni de las cachés de GitHub: la
  clave vieja sigue siendo válida hasta que la revoques.

---

## Alcance

Cubre el código de este repositorio: el backend `app`, el `frontend`, el servicio
`services/imagery_mcp` y los archivos de despliegue de `docker/`. No cubre los proveedores de LLM,
PostGIS, Redis, los catálogos externos que se consultan, ni tu despliegue.
