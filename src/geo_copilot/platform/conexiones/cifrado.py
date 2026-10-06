"""Cifrado de los secretos de las conexiones (F6, S6.2): sobre (envelope) con AES-256-GCM.

Cada secreto se cifra con una clave de datos (DEK) propia y aleatoria; la DEK se guarda
cifrada con la clave maestra (KEK), que NO está en la BD: viene del entorno (`SECRETS_KEK`,
32 bytes en base64) o, en producción, de un KMS/Vault que la inyecte ahí. Robar la BD no da
los secretos.

El cifrado va atado a su fila (datos asociados = organización + id de la conexión): un
secreto copiado a otra conexión u otra organización no se descifra.

Rotación: la KEK nueva va en `SECRETS_KEK` y las anteriores en `SECRETS_KEK_ANTERIORES`
(separadas por comas); cada sobre lleva la huella de la KEK con la que se cifró.

Formato (v1): b"v1" | huella KEK (8) | nonce DEK (12) | DEK cifrada (48) | nonce (12) | datos+tag
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"v1"


class CifradoNoConfigurado(Exception):
    """No hay clave maestra: no se pueden guardar ni leer secretos de conexiones."""


class SecretoIlegible(Exception):
    """El sobre no corresponde a esta fila, a ninguna clave conocida, o está dañado."""


def _huella(kek: bytes) -> bytes:
    return hashlib.sha256(kek).digest()[:8]


def _clave(b64: str) -> bytes:
    kek = base64.b64decode(b64.strip())
    if len(kek) != 32:
        raise CifradoNoConfigurado("SECRETS_KEK debe ser de 32 bytes en base64 (AES-256)")
    return kek


class Cifrador:
    def __init__(self, actual: bytes, anteriores: list[bytes] | None = None) -> None:
        self._actual = actual
        self._por_huella = {_huella(k): k for k in [actual, *(anteriores or [])]}

    @classmethod
    def desde_entorno(cls, entorno: dict[str, str] | None = None) -> Cifrador:
        env = entorno if entorno is not None else dict(os.environ)
        crudo = (env.get("SECRETS_KEK") or "").strip()
        if not crudo:
            raise CifradoNoConfigurado(
                "falta SECRETS_KEK (clave maestra para cifrar los secretos de las conexiones)")
        anteriores = [_clave(k) for k in (env.get("SECRETS_KEK_ANTERIORES") or "").split(",") if k.strip()]
        return cls(_clave(crudo), anteriores)

    def cifrar(self, texto: str, *, org_id: str, conexion_id: str) -> bytes:
        aad = f"{org_id}\x00{conexion_id}".encode()
        dek = AESGCM.generate_key(bit_length=256)
        n_dek, n_datos = os.urandom(12), os.urandom(12)
        dek_cifrada = AESGCM(self._actual).encrypt(n_dek, dek, aad)
        datos = AESGCM(dek).encrypt(n_datos, texto.encode("utf-8"), aad)
        return _VERSION + _huella(self._actual) + n_dek + dek_cifrada + n_datos + datos

    def descifrar(self, sobre: bytes, *, org_id: str, conexion_id: str) -> str:
        from cryptography.exceptions import InvalidTag

        if len(sobre) < 2 + 8 + 12 + 48 + 12 + 16 or sobre[:2] != _VERSION:
            raise SecretoIlegible("formato desconocido")
        huella, resto = sobre[2:10], sobre[10:]
        kek = self._por_huella.get(huella)
        if kek is None:
            raise SecretoIlegible("cifrado con una clave maestra que no está configurada")
        aad = f"{org_id}\x00{conexion_id}".encode()
        n_dek, dek_cifrada, n_datos, datos = resto[:12], resto[12:60], resto[60:72], resto[72:]
        try:
            dek = AESGCM(kek).decrypt(n_dek, dek_cifrada, aad)
            return AESGCM(dek).decrypt(n_datos, datos, aad).decode("utf-8")
        except InvalidTag:
            raise SecretoIlegible("el secreto no corresponde a esta conexión o fue alterado") from None

    def recifrar(self, sobre: bytes, *, org_id: str, conexion_id: str) -> bytes:
        """Rotación: el mismo secreto con la clave maestra actual."""
        return self.cifrar(self.descifrar(sobre, org_id=org_id, conexion_id=conexion_id),
                           org_id=org_id, conexion_id=conexion_id)
