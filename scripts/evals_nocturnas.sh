#!/bin/sh
# F7 (S7.4): bench agéntico de PUNTA A PUNTA contra el stack desplegado (LLM real + PostGIS del
# catastro + sandbox). Lo que GitHub no puede correr (el catastro no está en el repositorio).
#
# Uso (en la VM, desde el checkout del repositorio en la versión desplegada):
#   sh scripts/evals_nocturnas.sh            # umbral por defecto 0.85
#   UMBRAL_BENCH=0.9 sh scripts/evals_nocturnas.sh
# Cron diario (crontab -e):
#   0 3 * * *  cd /opt/geo-copilot && sh scripts/evals_nocturnas.sh >> /var/log/geo-evals.log 2>&1
#
# Deja el JSON de la corrida en bench_results/ y sale con código 1 si el score cae bajo el umbral
# (con MAILTO en el crontab, llega un correo). Corre en un contenedor de un solo uso de la imagen
# de `app` con la MISMA configuración que la app (BD, Redis, LLM, sandbox).
set -eu
cd "$(dirname "$0")/.."

UMBRAL="${UMBRAL_BENCH:-0.85}"
ENV_FILE="${ENV_FILE:-.env.production}"
mkdir -p bench_results

# F7 (auditoría): el contenedor escribe como `geocopilot` (uid de sistema de la imagen), no como
# quien lanza el cron, y en Linux no podía escribir en bench_results/ (del usuario del checkout):
# la corrida entera terminaba en PermissionError sin JSON. Escribe en una carpeta temporal abierta
# solo durante la corrida y el resultado se copia después, con el dueño de quien lo lanza.
SALIDA="$(mktemp -d)"
trap 'rm -rf "$SALIDA"' EXIT
chmod 0777 "$SALIDA"

rc=0
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --env-file "$ENV_FILE" \
  run --rm --no-deps \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$SALIDA:/app/bench_results" \
  -e PYTHONPATH=/app \
  app python tests/agentic_bench/runner.py \
    --out /app/bench_results --label nocturna --umbral "$UMBRAL" || rc=$?

if ls "$SALIDA"/*.json >/dev/null 2>&1; then
  cp "$SALIDA"/*.json bench_results/
else
  # sin JSON no hay medida: no se da por buena aunque el contenedor haya salido con 0
  echo "evals nocturnas: la corrida no dejó resultado en bench_results/ (código $rc)" >&2
  [ "$rc" -ne 0 ] || rc=1
fi
exit "$rc"
