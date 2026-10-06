#!/bin/sh
# F6 (T6.2): si no se montó un certificado en /etc/nginx/certs, genera uno AUTOFIRMADO para
# que el frontend arranque por HTTPS en desarrollo. En producción se monta el de la
# organización (tls.crt + tls.key) y este script no hace nada.
#
# Corre desde /docker-entrypoint.d/ de la imagen oficial de nginx, antes de arrancar.
set -eu
DIR=/etc/nginx/certs
if [ -s "$DIR/tls.crt" ] && [ -s "$DIR/tls.key" ]; then
    exit 0
fi
# F7 (auditoría): producción monta los certificados :ro. Si faltan, generar uno es imposible y el
# error de openssl confundía: se dice qué falta y el contenedor no arranca. Se prueba escribiendo
# (`test -w` de busybox responde «sí» a root aunque el montaje sea de solo lectura).
if [ -d "$DIR" ]; then
    if ! motivo=$( (: > "$DIR/.escribible") 2>&1 ); then
        echo "ERROR: falta el certificado de la organización en $DIR (tls.crt + tls.key)." >&2
        echo "       No se genera uno autofirmado: el directorio no es escribible (${motivo##*: })." >&2
        echo "       Copia tls.crt (con la cadena) y tls.key a docker/certs/ y vuelve a arrancar." >&2
        exit 1
    fi
    rm -f "$DIR/.escribible"
fi
# Uno sin el otro: no se pisa la mitad que alguien montó a propósito
if [ -e "$DIR/tls.crt" ] || [ -e "$DIR/tls.key" ]; then
    echo "ERROR: en $DIR hay tls.crt o tls.key, pero no ambos (o uno está vacío)." >&2
    exit 1
fi
mkdir -p "$DIR"
echo "AVISO: sin certificado en $DIR: se genera uno AUTOFIRMADO (solo desarrollo)." >&2
openssl req -x509 -nodes -quiet -newkey rsa:2048 -days 825 \
    -keyout "$DIR/tls.key" -out "$DIR/tls.crt" \
    -subj "/CN=localhost/O=GEO Copilot (desarrollo)" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 "$DIR/tls.key"
