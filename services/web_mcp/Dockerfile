# web-mcp — APIs web públicas (relatoría de Colombia Compra, BanRep SDMX). Contexto: raíz del repo.
FROM python:3.11-slim
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen.
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/web_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[otel,red]"

# F7 (S7.2): las herramientas de empaquetado no hacen falta para ejecutar (Trivy).
RUN pip uninstall -y wheel setuptools pip
COPY services/web_mcp/web_mcp/ ./web_mcp/
RUN useradd --uid 10013 --no-create-home webmcp
USER 10013
EXPOSE 9940
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9940/health || exit 1
CMD ["python", "-m", "web_mcp.server"]
