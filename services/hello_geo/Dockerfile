# hello-geo — servidor GeoMCP de ejemplo (S3.3). Contexto: raíz del repo.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY services/hello_geo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/geo_mcp_kit /opt/geo_mcp_kit
# F6: con el extra `oidc` acepta tokens de usuarios (token exchange)
RUN pip install --no-cache-dir "/opt/geo_mcp_kit[oidc]"
COPY services/hello_geo/hello_geo/ ./hello_geo/
RUN useradd --uid 10002 --no-create-home hello
USER 10002
EXPOSE 9200
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:9200/health || exit 1
CMD ["python", "-m", "hello_geo.server"]
