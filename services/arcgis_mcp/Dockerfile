# arcgis-mcp — servidor GeoMCP de ArcGIS (F5, T5.2). Contexto: raíz del repo.
FROM python:3.11-slim
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen: sin esto, la
# caché de buildx reutilizaba la capa de upgrade de antes del parche (pcre2, CVE-2026-103111).
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/arcgis_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[otel]"

# F7 (S7.2): las herramientas de empaquetado no hacen falta para ejecutar y traen vulnerabilidades
# propias y vendorizadas (Trivy); fuera de la imagen final.
RUN pip uninstall -y wheel setuptools pip
COPY services/arcgis_mcp/arcgis_mcp/ ./arcgis_mcp/
RUN useradd --uid 10004 --no-create-home arcgismcp
USER 10004
EXPOSE 9400
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9400/health || exit 1
CMD ["python", "-m", "arcgis_mcp.server"]
