"""F7 (auditoría): la prueba de carga E7.5 (scripts/prueba_carga.py) solo «pasa» si de verdad midió.

Lógica pura (percentiles, espera de aprobación, saltos de reloj, identidades, umbrales) y el
cliente contra un transporte simulado: un sondeo que falla no deja la consulta colgada y una
sesión que no abre hace fallar la corrida en vez de tumbarla.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

_RUTA = Path(__file__).resolve().parents[1] / "scripts" / "prueba_carga.py"
_spec = importlib.util.spec_from_file_location("prueba_carga", _RUTA)
assert _spec is not None and _spec.loader is not None
pc = importlib.util.module_from_spec(_spec)
sys.modules["prueba_carga"] = pc  # las dataclasses resuelven sus anotaciones por el módulo
_spec.loader.exec_module(pc)


def _medida(tipo="sql", segundos=10.0, espera=0.0, codigo=200, estado="completed", aprobaciones=0):
    return pc.Medida(0, tipo, "q", segundos, espera, codigo, estado, aprobaciones)


def _corrida_sana(**extra):
    res = pc.Resultado(**extra)
    for t in pc.TIPOS:
        res.medidas.append(_medida(tipo=t, segundos=12.0))
        res.contar(200)
    return res


def _evaluar(res):
    return pc.evaluar(res, usuarios=5, minutos=15, identidades=5, inicio=0.0, fin=900.0)


# ── percentiles ──────────────────────────────────────────────────────────────

def test_percentil_vacio_y_por_rango():
    assert pc._p([], 0.95) == 0.0
    assert pc._p([3.0, 1.0, 2.0], 0.5) == 2.0
    assert pc._p(list(map(float, range(1, 101))), 0.95) == 95.0


def test_percentiles_solo_de_las_consultas_completadas():
    res = _corrida_sana()
    # #83: muchas caídas rápidas (429 en 20 ms) no pueden bajar el p95 de las completadas
    for _ in range(30):
        res.medidas.append(_medida(segundos=0.02, codigo=429, estado="Too Many Requests"))
    informe, _ = _evaluar(res)
    assert informe["p50_s"] == 12.0
    assert informe["p95_s"] == 12.0
    assert informe["fallidas_p95_s"] == pytest.approx(0.02)
    assert informe["por_tipo"]["sql"]["completadas"] == 1
    assert informe["por_tipo"]["sql"]["n"] == 31


# ── espera de aprobación ────────────────────────────────────────────────────

def test_espera_no_descuenta_el_computo_previo_a_la_aprobacion():
    # #67: consulta empezó en 0, la aprobación apareció en 25 s y se aprobó en 27 s.
    # Sin created_at: desde que se VIO (26) hasta que se aprobó, no desde el inicio.
    assert pc.espera_aprobacion(sin_verla=24.0, visto=26.0, aprobado=27.0) == pytest.approx(1.0)
    # con created_at, el instante real de aparición
    assert pc.espera_aprobacion(24.0, 26.0, 27.0, creado=25.0) == pytest.approx(2.0)


def test_created_at_se_recorta_a_la_ventana_de_sondeos():
    # reloj del servidor muy atrasado: no puede descontar el cómputo de antes del último sondeo
    assert pc.espera_aprobacion(24.0, 26.0, 27.0, creado=-100.0) == pytest.approx(3.0)
    # muy adelantado: como mínimo, lo que se vio esperar
    assert pc.espera_aprobacion(24.0, 26.0, 27.0, creado=500.0) == pytest.approx(1.0)


def test_created_at_a_reloj_monotonico():
    instante = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
    desfase = instante.timestamp() - 100.0  # ese instante es el monotónico 100
    assert pc.a_monotonico(instante.isoformat(), desfase) == pytest.approx(100.0)
    # sin zona horaria o ilegible: no se adivina
    assert pc.a_monotonico("2026-10-03T12:00:00", desfase) is None
    assert pc.a_monotonico("ayer", desfase) is None
    assert pc.a_monotonico(None, desfase) is None


# ── suspensión del equipo ───────────────────────────────────────────────────

def test_salto_de_reloj_detecta_suspension_en_cualquiera_de_los_relojes():
    # Windows: los dos relojes avanzan durante la suspensión
    assert pc.salto_de_reloj((0.0, 0.0), (32000.0, 32000.0), 30) == pytest.approx(32000.0)
    # Linux: el monotónico se para, el de pared no
    assert pc.salto_de_reloj((0.0, 0.0), (32000.0, 1.0), 30) == pytest.approx(32000.0)
    # latido normal
    assert pc.salto_de_reloj((0.0, 0.0), (1.01, 1.0), 30) is None


def test_pausa_invalida_la_corrida():
    informe, umbrales = _evaluar(_corrida_sana(pausas_s=[31800.0]))
    assert informe["valida"] is False
    assert not dict(umbrales)["el equipo no se suspendió durante la corrida"]


# ── umbrales ────────────────────────────────────────────────────────────────

def test_corrida_sana_pasa():
    informe, _ = _evaluar(_corrida_sana())
    assert informe["valida"] is True
    assert informe["duracion_real_s"] == 900.0


def test_cero_consultas_no_pasa():
    # #69: todas las sesiones con 401 → antes «0 ≥ 0» y salida 0
    res = pc.Resultado(sesiones_fallidas=[f"u{i}: 401" for i in range(5)])
    for _ in range(5):
        res.contar(401)
    informe, umbrales = _evaluar(res)
    u = dict(umbrales)
    assert informe["valida"] is False
    assert not u["todos los usuarios abrieron sesión"]
    assert not u["consultas completadas ≥ 95 %"]


def test_cero_consultas_sin_sesiones_fallidas_tampoco_pasa():
    informe, _ = _evaluar(pc.Resultado())
    assert informe["valida"] is False


def test_mezcla_incompleta_no_pasa():
    # la corrida suspendida midió solo `sql`
    res = pc.Resultado()
    for _ in range(5):
        res.medidas.append(_medida("sql"))
    informe, umbrales = _evaluar(res)
    assert informe["valida"] is False
    assert any(nombre.startswith("se midió la mezcla entera (faltan medir") and not ok for nombre, ok in umbrales)


def test_errores_del_cliente_invalidan():
    informe, _ = _evaluar(_corrida_sana(errores_cliente=["u3: ConnectError: x"]))
    assert informe["valida"] is False


def test_errores_de_sondeo_se_informan_sin_invalidar_por_si_solos():
    informe, _ = _evaluar(_corrida_sana(errores_sondeo=["u1: RemoteProtocolError"]))
    assert informe["errores_sondeo"] == ["u1: RemoteProtocolError"]
    assert informe["valida"] is True


def test_umbral_de_completadas():
    res = _corrida_sana()
    res.medidas.append(_medida(codigo=200, estado="failed"))
    informe, umbrales = _evaluar(res)  # 5 de 6 completadas < 95 %
    assert not dict(umbrales)["consultas completadas ≥ 95 %"]
    assert informe["valida"] is False


# ── identidades ─────────────────────────────────────────────────────────────

def test_identidades_desde_el_entorno():
    ids = pc.identidades({"CARGA_TOKENS": "t1, t2", "API_KEYS": "k1,k1", "API_KEY": "ignorada"})
    assert ids == [{"Authorization": "Bearer t1"}, {"Authorization": "Bearer t2"}, {"X-API-Key": "k1"}]
    assert pc.identidades({"API_KEY": "k"}) == [{"X-API-Key": "k"}]
    assert pc.identidades({}) == []


def test_varias_api_keys_no_son_varias_identidades(monkeypatch, capsys):
    """La app tiene UNA API key: con varias, todas serían el mismo principal (y las demás, 401)."""
    with pytest.raises(pc.IdentidadesInvalidas, match="CARGA_TOKENS"):
        pc.identidades({"API_KEYS": "k1,k2"})
    monkeypatch.delenv("CARGA_TOKENS", raising=False)
    monkeypatch.setenv("API_KEYS", "k1,k2")
    assert pc.main(["--minutos", "0"]) == 2
    assert "UNA sola" in capsys.readouterr().out


async def _usuario_que_no_hace_nada(*_a, **_k):
    return None


def test_con_api_key_siempre_avisa_aunque_haya_un_solo_usuario(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CARGA_TOKENS", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setattr(pc, "usuario", _usuario_que_no_hace_nada)
    pc.main(["--usuarios", "1", "--minutos", "0", "--salida", str(tmp_path / "c.json")])
    salida = capsys.readouterr().out
    assert pc.AVISO_API_KEY in salida and "comparten identidad" not in salida


def test_con_tokens_de_oidc_no_hay_aviso_de_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CARGA_TOKENS", "t1,t2")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(pc, "usuario", _usuario_que_no_hace_nada)
    pc.main(["--usuarios", "2", "--minutos", "0", "--salida", str(tmp_path / "c.json")])
    assert pc.AVISO_API_KEY not in capsys.readouterr().out


def test_ni_el_script_ni_la_plantilla_presentan_la_api_key_como_identidad_por_usuario():
    cabecera = _RUTA.read_text(encoding="utf-8").split('"""', 2)[1]
    assert "UNA API key" in cabecera and "CARGA_TOKENS" in cabecera
    assert "claves de servicio separadas por comas" not in cabecera
    plantilla = (_RUTA.parents[1] / ".env.production.example").read_text(encoding="utf-8")
    assert "CARGA_TOKENS" in plantilla and "o API_KEYS" not in plantilla


def test_nunca_falsifica_x_forwarded_for():
    assert "X-Forwarded-For" not in _RUTA.read_text(encoding="utf-8").split('"""', 2)[2]


def test_con_un_token_por_usuario_la_api_key_ni_se_usa_ni_cuenta(monkeypatch, tmp_path, capsys):
    """Antes la clave se sumaba como identidad aunque nadie la usara: avisaba e inflaba el informe."""
    monkeypatch.setenv("CARGA_TOKENS", "t1,t2")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEY", "k")
    vistos = {}

    async def falso_usuario(i, base, cabeceras, fin, sondeo, res, transporte=None):
        vistos[i] = cabeceras

    monkeypatch.setattr(pc, "usuario", falso_usuario)
    salida = tmp_path / "c.json"
    pc.main(["--usuarios", "2", "--minutos", "0", "--salida", str(salida)])
    texto = capsys.readouterr().out
    assert pc.AVISO_API_KEY not in texto and "La API key no se usa" in texto
    assert all("X-API-Key" not in c for c in vistos.values())
    assert json.loads(salida.read_text(encoding="utf-8"))["identidades"] == 2


def test_con_mas_usuarios_que_tokens_la_api_key_si_se_usa_y_avisa(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CARGA_TOKENS", "t1")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEY", "k")
    vistos = {}

    async def falso_usuario(i, base, cabeceras, fin, sondeo, res, transporte=None):
        vistos[i] = cabeceras

    monkeypatch.setattr(pc, "usuario", falso_usuario)
    salida = tmp_path / "c.json"
    pc.main(["--usuarios", "2", "--minutos", "0", "--salida", str(salida)])
    assert pc.AVISO_API_KEY in capsys.readouterr().out
    assert vistos[1] == {"X-API-Key": "k"}
    assert json.loads(salida.read_text(encoding="utf-8"))["identidades"] == 2


def test_sin_usuarios_sale_con_error(monkeypatch, capsys):
    monkeypatch.setenv("CARGA_TOKENS", "t1")
    assert pc.main(["--usuarios", "0", "--minutos", "0"]) == 2
    assert "al menos un usuario" in capsys.readouterr().out


def test_sin_identidad_sale_con_error(monkeypatch, capsys):
    for var in ("CARGA_TOKENS", "API_KEYS", "API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert pc.main(["--minutos", "0"]) == 2
    assert "Falta la identidad" in capsys.readouterr().out


def test_identidad_compartida_avisa_y_espacia_el_sondeo(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CARGA_TOKENS", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEY", "k")
    vistos = {}

    async def falso_usuario(i, base, cabeceras, fin, sondeo, res, transporte=None):
        vistos[i] = (cabeceras, sondeo)

    monkeypatch.setattr(pc, "usuario", falso_usuario)
    salida = tmp_path / "carga.json"
    codigo = pc.main(["--usuarios", "3", "--minutos", "0", "--salida", str(salida)])
    assert "comparten identidad" in capsys.readouterr().out
    assert {s for _, s in vistos.values()} == {pc.SONDEO_BASE_S * 3}
    # sin consultas la corrida no vale, pero el informe queda escrito
    assert codigo == 1
    assert json.loads(salida.read_text(encoding="utf-8"))["identidades"] == 1


# ── el cliente contra un transporte simulado ────────────────────────────────

def test_sesion_que_no_abre_por_transporte_se_registra_sin_tumbar():
    # #70: un ConnectError al abrir sesión ya no escapa del usuario
    def manejador(request):
        raise httpx.ConnectError("caído", request=request)

    res = pc.Resultado()
    asyncio.run(pc.usuario(0, "http://carga.test", {"X-API-Key": "k"}, 0.0, 0.01, res,
                           transporte=httpx.MockTransport(manejador)))
    assert res.sesiones_fallidas == ["u0: ConnectError"]
    assert res.codigos == {599: 1}


def test_sondeo_sobrevive_a_un_error_y_aprueba():
    # #71: el primer sondeo falla por transporte; el siguiente ve la aprobación y la consulta termina
    aprobada = asyncio.Event()
    sondeos = {"n": 0}

    async def manejador(request):
        ruta = request.url.path
        if ruta == "/api/v1/approval/pending":
            sondeos["n"] += 1
            if sondeos["n"] == 1:
                raise httpx.RemoteProtocolError("cortada", request=request)
            if aprobada.is_set():
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"approval_id": "a1",
                                              "created_at": datetime.now(UTC).isoformat()}])
        if ruta == "/api/v1/approval/a1":
            aprobada.set()
            return httpx.Response(200, json={"status": "approved"})
        if ruta == "/api/v1/query/":
            await asyncio.wait_for(aprobada.wait(), timeout=5)
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(404)

    async def correr():
        res = pc.Resultado()
        async with httpx.AsyncClient(base_url="http://carga.test",
                                     transport=httpx.MockTransport(manejador)) as c:
            m = await pc.consulta(c, 0, "s1", "sql", "trae los lotes", 0.01, res)
        return res, m

    res, m = asyncio.run(correr())
    assert m.codigo == 200 and m.estado == "completed"
    assert m.aprobaciones == 1
    assert 0.0 <= m.espera_aprobacion <= m.segundos
    assert res.errores_sondeo == ["u0: RemoteProtocolError"]
    assert res.errores_cliente == []
    assert res.codigos[599] == 1


def _proveedor_y_api(expira: int = 300, rechaza: bool = False):
    """IdP simulado (token endpoint con rotación) + API que acepta solo el último Bearer emitido."""
    estado = {"emitidos": 0, "vigente": None, "refrescos": []}

    def manejar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/token":
            cuerpo = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            estado["refrescos"].append(cuerpo["refresh_token"])
            if rechaza:
                return httpx.Response(400, json={"error": "invalid_grant"})
            estado["emitidos"] += 1
            estado["vigente"] = f"acceso-{estado['emitidos']}"
            return httpx.Response(200, json={"access_token": estado["vigente"], "expires_in": expira,
                                             "refresh_token": f"refresco-{estado['emitidos']}"})
        ok = req.headers.get("Authorization") == f"Bearer {estado['vigente']}"
        return httpx.Response(200 if ok else 401, json={})

    return estado, httpx.MockTransport(manejar)


@pytest.mark.asyncio
async def test_el_token_se_renueva_antes_de_caducar_con_el_refresh_token_rotado():
    estado, t = _proveedor_y_api(expira=30)  # 30 s < margen de 60 s: cada petición renueva
    auth = pc.TokenRenovable("https://idp/token", "cli", "refresco-0", transporte=t)
    async with httpx.AsyncClient(base_url="https://api", auth=auth, transport=t) as c:
        assert (await c.get("/x")).status_code == 200
        assert (await c.get("/x")).status_code == 200
    assert estado["refrescos"] == ["refresco-0", "refresco-1"]  # usa el refresh token NUEVO
    assert auth.renovaciones == 2


@pytest.mark.asyncio
async def test_un_401_renueva_una_vez_y_repite_la_peticion():
    estado, t = _proveedor_y_api(expira=3600)
    auth = pc.TokenRenovable("https://idp/token", "cli", "r0", transporte=t)
    async with httpx.AsyncClient(base_url="https://api", auth=auth, transport=t) as c:
        assert (await c.get("/x")).status_code == 200
        estado["vigente"] = "revocado-en-el-servidor"  # el proveedor invalidó el acceso antes de tiempo
        assert (await c.get("/x")).status_code == 200
    assert auth.renovaciones == 2


@pytest.mark.asyncio
async def test_si_el_proveedor_no_renueva_se_dice_por_que():
    _, t = _proveedor_y_api(rechaza=True)
    auth = pc.TokenRenovable("https://idp/token", "cli", "r0", transporte=t)
    async with httpx.AsyncClient(base_url="https://api", auth=auth, transport=t) as c:
        with pytest.raises(pc.RenovacionFallida, match="400"):
            await c.get("/x")


def test_refrescos_sin_endpoint_no_son_identidades_validas():
    with pytest.raises(pc.IdentidadesInvalidas):
        pc.identidades_renovables({"CARGA_REFRESCOS": "a,b"})
    ids = pc.identidades_renovables({"CARGA_REFRESCOS": "a,b,a", "CARGA_TOKEN_URL": "https://idp/t",
                                     "CARGA_CLIENTE": "c"})
    assert [i.refresco for i in ids] == ["a", "b"]  # repetidos cuentan una vez
