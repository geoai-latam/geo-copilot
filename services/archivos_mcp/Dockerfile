# archivos-mcp — archivos geoespaciales con DuckDB-spatial (F5, T5.6). Contexto: raíz del repo.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/archivos_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
COPY packages/geo_sql_guard /opt/geo_sql_guard
RUN pip install --no-cache-dir /opt/geo_mcp_kit /opt/geo_sql_guard
# Extensiones de DuckDB instaladas en la imagen: el contenedor corre con el sistema de archivos
# de solo lectura y sin descargar nada en tiempo de ejecución.
ENV DUCKDB_EXTENSION_DIR=/opt/duckdb_ext
RUN python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb_ext'\"); c.execute('INSTALL spatial'); c.execute('INSTALL httpfs')" \
 && chmod -R a+rX /opt/duckdb_ext
COPY services/archivos_mcp/archivos_mcp/ ./archivos_mcp/
RUN useradd --uid 10006 --no-create-home archivos
USER 10006
EXPOSE 9600
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9600/health || exit 1
CMD ["python", "-m", "archivos_mcp.server"]
