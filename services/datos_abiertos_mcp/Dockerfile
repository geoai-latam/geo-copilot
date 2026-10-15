# datos-abiertos-mcp — portales de datos abiertos (datos.gov.co, CKAN). Contexto: raíz del repo.
FROM python:3.11-slim
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen.
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/datos_abiertos_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[otel,red]"

# F7 (S7.2): las herramientas de empaquetado no hacen falta para ejecutar (Trivy).
RUN pip uninstall -y wheel setuptools pip
COPY services/datos_abiertos_mcp/datos_abiertos_mcp/ ./datos_abiertos_mcp/
RUN useradd --uid 10009 --no-create-home datosmcp
USER 10009
EXPOSE 9900
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9900/health || exit 1
CMD ["python", "-m", "datos_abiertos_mcp.server"]
