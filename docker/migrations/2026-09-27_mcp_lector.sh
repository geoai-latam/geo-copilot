#!/bin/sh
# Migración T5.1 — rol `mcp_lector` (solo lectura del catastro) para una BD YA desplegada.
#
# init-db/08_mcp_lector.sh solo corre en un volumen FRESCO. Es idempotente, así que la
# migración es aplicar ese mismo archivo (no una copia que pueda divergir), con la clave
# del .env de la raíz:
#
#   sh docker/migrations/2026-09-27_mcp_lector.sh
set -eu
RAIZ="$(dirname "$0")/../.."
CLAVE="$(grep -E '^MCP_SQL_LECTOR_PASSWORD=' "$RAIZ/.env" | cut -d= -f2-)"
[ -n "$CLAVE" ] || { echo "falta MCP_SQL_LECTOR_PASSWORD en .env" >&2; exit 1; }
docker exec -i -e MCP_SQL_LECTOR_PASSWORD="$CLAVE" geo_copilot_db bash < "$(dirname "$0")/../init-db/08_mcp_lector.sh"
