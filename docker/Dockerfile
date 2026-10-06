# GEO_COPILOT - Dockerfile (F7, S7.2: multi-stage)
#
# 1. `construccion`: resuelve y empaqueta TODO en wheels (el paquete y sus dependencias).
# 2. `ejecucion`: instala solo esos wheels. Sin compiladores, sin cabeceras, sin el árbol del
#    repo (antes `COPY . .` + `pip install -e .` metía tests, scripts y docs en la imagen y
#    la dejaba dependiente de las fuentes montadas).
#
# Todas las dependencias geo (geopandas, shapely, pyproj, rasterio…) traen wheels manylinux
# con sus bibliotecas nativas: no hace falta GDAL del sistema ni build-essential.

FROM python:3.11-slim AS construccion

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml README.md ./
COPY packages/geo_sql_guard ./packages/geo_sql_guard
COPY src ./src
RUN pip wheel --wheel-dir /wheels ./packages/geo_sql_guard . && ls /wheels | wc -l


FROM python:3.11-slim AS ejecucion

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Docker CLI (solo el cliente, no el daemon) para SEC-SANDBOX-RCE p2: cuando
# SANDBOX_BACKEND=docker, la app ejecuta `docker exec` contra el contenedor
# `sandbox` endurecido. Se copia el binario estático de la imagen oficial
# docker:cli (mínimo, sin apt). Requiere montar el socket de Docker en el
# servicio app (ver docker/docker-compose.yml). Con el backend por defecto
# ('subprocess') este binario queda inerte.
# F7: 29 (Go 1.26): el binario de la 27 traía la stdlib de Go con CVE-2025-68121 (CRÍTICA, Trivy).
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker

# F7 (S7.2): parches de seguridad del sistema base (Trivy marcaba perl-base, util-linux, gzip…
# con arreglo publicado). Sin herramientas nuevas: solo actualiza lo instalado.
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen: sin esto, la
# caché de buildx reutilizaba la capa de upgrade de antes del parche (pcre2, CVE-2026-103111).
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r geocopilot && useradd -r -g geocopilot geocopilot

# Solo los wheels: ni fuentes ni herramientas de compilación en la imagen final.
RUN --mount=type=bind,from=construccion,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels geo_copilot geo_sql_guard
# Las herramientas de empaquetado no hacen falta para EJECUTAR la app y traían vulnerabilidades
# propias y de lo que vendorizan (Trivy: wheel, el jaraco.context de setuptools, el msgpack de pip).
# Fuera de la imagen final: instalar ya se hizo arriba.
RUN pip uninstall -y wheel setuptools pip

WORKDIR /app
# Lo único del repo que la app lee en ejecución: su configuración (YAML de MCP y del
# catálogo de regiones) y la capa semántica (overrides).
COPY --chown=geocopilot:geocopilot config ./config
COPY --chown=geocopilot:geocopilot semantic_layer ./semantic_layer
# F7 (auditoría): las migraciones NO leen init-db: cada revisión lleva su SQL congelado
# (src/geo_copilot/migraciones/versions), así que la imagen no copia 06/10.

# R0.12 (auditoría 2026-07-26, AUD-121): quitar el bit setuid de todos los
# binarios de la imagen. python:3.11-slim trae 8 heredados de util-linux y
# passwd (mount, umount, su, passwd, chsh, chfn, gpasswd, newgrp) que esta
# aplicación no usa jamás, y que son el primer peldaño de cualquier escalada
# desde el usuario no privilegiado. Combinado con `cap_drop: ALL` en compose,
# deja al contenedor sin vías locales de subir de privilegio.
RUN find / -xdev -perm -4000 -type f -exec chmod u-s {} + 2>/dev/null || true

# S2.4: punto de montaje del volumen `ws_exports` (GeoParquet de los datasets
# del workspace). Un volumen nombrado nuevo hereda el dueño de este directorio,
# así `geocopilot` puede escribir; el sandbox lo monta de solo lectura.
RUN mkdir -p /data/workspace && chown geocopilot:geocopilot /data/workspace

# Switch to non-root user
USER geocopilot

# Health check — golpea el endpoint /health real, no solo importa el
# módulo. Usa urllib (stdlib) para no depender de curl/wget en la imagen.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=5).status == 200 else 1)" \
        || exit 1

# Entrada del proceso — arranca el servidor ASGI. Procesos: WEB_CONCURRENCY (uvicorn lo lee);
# con más de 1, el estado compartido va por Redis (F7, S7.1).
CMD ["uvicorn", "geo_copilot.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
