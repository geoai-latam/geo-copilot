# postgis-mcp — servidor GeoMCP de SQL multi-BD (F5, T5.1). Contexto: raíz del repo.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/postgis_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# el kit del servidor y EL MISMO validador SQL del núcleo (packages/geo_sql_guard)
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
COPY packages/geo_sql_guard /opt/geo_sql_guard
RUN pip install --no-cache-dir /opt/geo_mcp_kit /opt/geo_sql_guard
COPY services/postgis_mcp/postgis_mcp/ ./postgis_mcp/
RUN useradd --uid 10003 --no-create-home sqlmcp
USER 10003
EXPOSE 9300
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9300/health || exit 1
CMD ["python", "-m", "postgis_mcp.server"]
