# lugares-mcp — geocodificación (nombre de lugar → candidatos y límite). Contexto: raíz del repo.
FROM python:3.11-slim
# F7: invalida la caché del build una vez al día para que los parches del SO lleguen: sin esto, la
# caché de buildx reutilizaba la capa de upgrade de antes del parche (pcre2, CVE-2026-103111).
ARG PARCHES_SO=
RUN echo "parches del SO: ${PARCHES_SO:-sin fecha}" && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/lugares_mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[otel]"

# F7 (S7.2): las herramientas de empaquetado no hacen falta para ejecutar y traen vulnerabilidades
# propias y vendorizadas (Trivy); fuera de la imagen final.
RUN pip uninstall -y wheel setuptools pip
COPY services/lugares_mcp/lugares_mcp/ ./lugares_mcp/
RUN useradd --uid 10007 --no-create-home lugaresmcp
USER 10007
EXPOSE 9700
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9700/health || exit 1
CMD ["python", "-m", "lugares_mcp.server"]
