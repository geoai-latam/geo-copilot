"""Los logs no llevan credenciales: el store de sesiones imprimía la URL de Redis
con la contraseña en claro (visto en la validación de F3 al activar Redis)."""

from geo_copilot.api.dependencies import _sin_credenciales


def test_la_url_de_redis_se_loguea_sin_contrasena():
    assert _sin_credenciales("redis://:s3cr3t@redis:6379/0") == "redis://***@redis:6379/0"
    assert _sin_credenciales("redis://user:pw@h/1") == "redis://***@h/1"
    assert "s3cr3t" not in _sin_credenciales("rediss://:s3cr3t@redis:6380/0")


def test_sin_credenciales_la_url_queda_igual():
    assert _sin_credenciales("redis://redis:6379/0") == "redis://redis:6379/0"
