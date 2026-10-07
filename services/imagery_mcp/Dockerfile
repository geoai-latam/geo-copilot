# imagery-mcp — servicio MCP de análisis de imagery satelital (C3-v1)
FROM python:3.11-slim

# Runtime para rasterio: los wheels manylinux traen GDAL embebido, pero en
# slim faltan libs del sistema que GDAL enlaza (libexpat — su ausencia daba
# ImportError en el contenedor, cazado por el bench E2E) + certs/curl para
# las lecturas HTTPS de COGs.
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen: sin esto, la
# caché de buildx reutilizaba la capa de upgrade de antes del parche (pcre2, CVE-2026-103111).
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates curl libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
# Contexto de build = raíz del repo (S3.1): el servicio se construye sobre el
# kit común `packages/geo_mcp_kit`.
COPY services/imagery_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[otel]"
# Extensiones de DuckDB del catálogo (httpfs, spatial) instaladas en la imagen: en marcha el
# servicio corre sin HOME escribible y no las descargaría.
ENV DUCKDB_EXTENSION_DIR=/opt/duckdb_ext
# Presupuesto de memoria (el contenedor tiene 1 GiB; un OOM tumbaba el servicio a mitad de turno):
# GDAL por defecto cachea el 5 % de la RAM del HOST en bloques y 25 MB por archivo abierto (una
# escena son ~18 COG por carril); DuckDB, el 80 % del contenedor.
ENV GDAL_CACHEMAX=128     VSI_CACHE_SIZE=4194304     IMAGERY_DUCKDB_MEMORIA=256MB     IMAGERY_DUCKDB_HILOS=2
RUN python -c "import duckdb; c = duckdb.connect(); c.execute(\"SET extension_directory = '/opt/duckdb_ext'\"); c.execute('INSTALL httpfs; INSTALL spatial')" \
    && chmod -R a+rX /opt/duckdb_ext

# F7 (S7.2): las herramientas de empaquetado no hacen falta para ejecutar y traen vulnerabilidades
# propias y vendorizadas (Trivy); fuera de la imagen final.
RUN pip uninstall -y wheel setuptools pip

COPY services/imagery_mcp/imagery_mcp/ ./imagery_mcp/

# Usuario sin privilegios (mismo estándar que el sandbox endurecido).
RUN useradd --uid 10001 --no-create-home imagery
USER 10001

EXPOSE 9100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:9100/health || exit 1

CMD ["python", "-m", "imagery_mcp.server"]
