# almacen-demo — MCP tabular G0 de prueba (F5, T5.4). Contexto: raíz del repo.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/tabular_demo_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
COPY packages/geo_sql_guard /opt/geo_sql_guard
RUN pip install --no-cache-dir /opt/geo_mcp_kit /opt/geo_sql_guard
COPY services/tabular_demo_mcp/tabular_demo_mcp/ ./tabular_demo_mcp/
RUN useradd --uid 10005 --no-create-home almacen
USER 10005
EXPOSE 9500
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9500/health || exit 1
CMD ["python", "-m", "tabular_demo_mcp.server"]
