"""Prueba de carga E7.5 (F7): N usuarios simultáneos durante M minutos contra el stack desplegado.

Cada usuario abre su sesión y repite una mezcla realista de consultas (traer lotes de una manzana
—SQL con aprobación—, medirlos, colorearlos, preguntar por las entidades y, a veces, un NDVI),
pensando 10–20 s entre una y otra y APROBANDO como lo haría una persona (consulta las pendientes).
Todo entra por la puerta pública (nginx), cada usuario con una IDENTIDAD de verdad: no se falsifica
X-Forwarded-For (eso evadía el rate limit y medía otra cosa). Identidades, por variable de entorno:

    CARGA_TOKENS   Bearer JWT de OIDC separados por comas: la ÚNICA forma de tener usuarios
                   independientes (una persona por token, cada una con su cupo del rate limit).
                   Caducan: que duren la corrida o saldrán 401 a mitad, y la prueba fallará.
    CARGA_REFRESCOS   refresh tokens de OIDC separados por comas, con CARGA_TOKEN_URL (endpoint de
                   token del proveedor) y CARGA_CLIENTE (client_id público): cada usuario renueva su
                   Bearer antes de que caduque, como un cliente real. Es lo que sirve para corridas
                   más largas que la vida del token de acceso (5 min en el Keycloak de desarrollo).
    API_KEY        la clave de servicio de la app (API_KEYS se acepta con UNA sola clave). No da
                   un usuario por clave: la app tiene UNA API key, y todo el que entra con ella
                   es el mismo principal (`servicio:api-key`) con UN cupo. Se avisa siempre que
                   algún usuario la use; los tokens van antes y, si hay uno por usuario, no se usa.

Los usuarios se reparten entre las identidades; si comparten alguna, se avisa (comparten el cupo
del limitador) y el sondeo de aprobaciones se espacia en proporción para no agotarlo.

Mide, por tipo de consulta: latencia (sin la espera de aprobación), estado y errores; al final
compara con los umbrales de docker/observabilidad/alertas.yml y sale con código 1 si alguno se
incumple o si la corrida no vale (sesiones que no abrieron, errores del propio cliente, el equipo
se suspendió, no se midió la mezcla entera).

    CARGA_TOKENS=<jwt1>,…,<jwt5> python scripts/prueba_carga.py --usuarios 5 --minutos 15 \
        --base https://localhost:3443
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime

import httpx

MANZANAS = ["002529082", "008205002", "008535090", "005647089", "008415043", "002107030", "006413048",
            "004536034", "001317009", "005668009", "005113006", "005684016", "004598010", "002608018",
            "009136018"]

# sondeo de aprobaciones de un usuario con identidad propia; con identidad compartida se multiplica
SONDEO_BASE_S = 3.0
# un latido de 1 s que tarda más que esto = el proceso estuvo congelado (suspensión, hibernación)
PAUSA_MAXIMA_S = 30.0


@dataclass
class Medida:
    usuario: int
    tipo: str
    consulta: str
    segundos: float
    espera_aprobacion: float
    codigo: int
    estado: str
    aprobaciones: int = 0


@dataclass
class Resultado:
    medidas: list[Medida] = field(default_factory=list)
    peticiones: int = 0
    codigos: dict[int, int] = field(default_factory=dict)
    sesiones_fallidas: list[str] = field(default_factory=list)
    errores_cliente: list[str] = field(default_factory=list)
    errores_sondeo: list[str] = field(default_factory=list)
    pausas_s: list[float] = field(default_factory=list)

    def contar(self, codigo: int) -> None:
        self.peticiones += 1
        self.codigos[codigo] = self.codigos.get(codigo, 0) + 1


def _mezcla(n: int) -> list[tuple[str, str]]:
    m = MANZANAS[n % len(MANZANAS)]
    base = [
        ("sql", f"trae los lotes de la manzana {m}"),
        ("medir", "¿cuántas hectáreas suman esos lotes?"),
        ("simbologia", "colorea esos lotes por área en 3 clases"),
        ("directa", "¿qué entidades hay disponibles?"),
    ]
    if n % 3 == 2:
        base.append(("ndvi", "calcula el NDVI promedio de esos lotes con Sentinel-2 entre junio y agosto de 2026"))
    return base


TIPOS = tuple(dict.fromkeys(t for n in range(3) for t, _ in _mezcla(n)))


def _lista(valor: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in valor.replace("\n", ",").split(",") if x.strip()))


#: Lo que se le dice a quien prueba con la API key (siempre que la use).
AVISO_API_KEY = ("[!] Con la API key todos los usuarios que la usan son el MISMO principal "
                 "(`servicio:api-key`) y comparten UN cupo del rate limit: los 429 medirán el límite, no "
                 "el sistema. Para usuarios independientes, un JWT de OIDC por usuario en CARGA_TOKENS.")


class IdentidadesInvalidas(ValueError):
    pass


class RenovacionFallida(RuntimeError):
    pass


class TokenRenovable(httpx.Auth):
    """Bearer de OIDC que se renueva con su refresh token antes de caducar (y ante un 401)."""

    MARGEN_S = 60.0

    def __init__(self, url: str, cliente: str, refresco: str,
                 transporte: httpx.AsyncBaseTransport | None = None) -> None:
        self.url, self.cliente, self.refresco = url, cliente, refresco
        self.acceso: str | None = None
        self.caduca = 0.0
        self.renovaciones = 0
        self._transporte = transporte
        self._cerrojo: asyncio.Lock | None = None

    async def _renovar(self) -> None:
        async with httpx.AsyncClient(timeout=30, verify=False, transport=self._transporte) as c:
            r = await c.post(self.url, data={"grant_type": "refresh_token", "client_id": self.cliente,
                                             "refresh_token": self.refresco})
        if r.status_code != 200:
            raise RenovacionFallida(f"el proveedor no renovó el token: {r.status_code} {r.text[:200]}")
        datos = r.json()
        self.acceso = datos["access_token"]
        self.refresco = datos.get("refresh_token", self.refresco)  # rotación de refresh tokens
        self.caduca = time.monotonic() + float(datos.get("expires_in", 300))
        self.renovaciones += 1

    async def async_auth_flow(self, request: httpx.Request):  # type: ignore[override]
        self._cerrojo = self._cerrojo or asyncio.Lock()
        async with self._cerrojo:
            if self.acceso is None or time.monotonic() > self.caduca - self.MARGEN_S:
                await self._renovar()
        request.headers["Authorization"] = f"Bearer {self.acceso}"
        respuesta = yield request
        if respuesta.status_code == 401:  # caducó antes de lo anunciado: se renueva y se repite una vez
            async with self._cerrojo:
                await self._renovar()
            request.headers["Authorization"] = f"Bearer {self.acceso}"
            yield request


def identidades_renovables(entorno: Mapping[str, str],
                           transporte: httpx.AsyncBaseTransport | None = None) -> list[TokenRenovable]:
    refrescos = _lista(entorno.get("CARGA_REFRESCOS", ""))
    if not refrescos:
        return []
    url, cliente = entorno.get("CARGA_TOKEN_URL", "").strip(), entorno.get("CARGA_CLIENTE", "").strip()
    if not url or not cliente:
        raise IdentidadesInvalidas("CARGA_REFRESCOS necesita CARGA_TOKEN_URL y CARGA_CLIENTE para renovar.")
    return [TokenRenovable(url, cliente, r, transporte) for r in refrescos]


def identidades(entorno: Mapping[str, str]) -> list[dict[str, str]]:
    """Cabeceras de cada identidad distinta (repetidas cuentan una vez: compartirían cupo).

    F7 (auditoría): la app tiene UNA API key (`Settings.api_key`). Varias claves no son varias
    identidades: todas serían `servicio:api-key` y, salvo una, darían 401. Se rechaza."""
    tokens = _lista(entorno.get("CARGA_TOKENS", ""))
    claves = _lista(entorno.get("API_KEYS", "")) or _lista(entorno.get("API_KEY", ""))
    if len(claves) > 1:
        raise IdentidadesInvalidas(
            f"{len(claves)} API keys: la app acepta UNA sola y todo el que entra con ella es el mismo "
            "principal (un solo cupo). Para varios usuarios independientes, CARGA_TOKENS (OIDC).")
    return [{"Authorization": f"Bearer {t}"} for t in tokens] + [{"X-API-Key": k} for k in claves]


def espera_aprobacion(sin_verla: float, visto: float, aprobado: float, creado: float | None = None) -> float:
    """Segundos que la consulta estuvo parada esperando a la persona por UNA aprobación.

    F7 (auditoría): antes se descontaba desde el INICIO de la consulta, y con ello todo el cómputo
    previo a la aprobación (enrutado, SQL, validación): el p95 salía hasta un 40 % optimista. La
    aprobación apareció entre el último sondeo que no la vio (`sin_verla`) y el que la vio
    (`visto`); su `created_at` (ya en el reloj monotónico local) lo precisa, recortado a esa ventana
    por si los relojes de cliente y servidor no coinciden. Sin él se toma el extremo que menos
    descuenta: la latencia informada nunca queda por debajo de la real."""
    aparicion = visto if creado is None else min(max(creado, sin_verla), visto)
    return max(0.0, aprobado - aparicion)


def a_monotonico(creado: str | None, desfase: float) -> float | None:
    """`created_at` ISO del servidor → reloj monotónico local (`desfase` = time.time() − monotonic()).
    Sin zona horaria no se sabe qué instante es: None (se usa la cota conservadora)."""
    if not creado:
        return None
    try:
        instante = datetime.fromisoformat(creado)
    except ValueError:
        return None
    if instante.tzinfo is None:
        return None
    return instante.timestamp() - desfase


def salto_de_reloj(anterior: tuple[float, float], ahora: tuple[float, float], umbral: float) -> float | None:
    """Paso entre dos latidos (pared, monotónico) si supera `umbral`; si no, None.

    F7 (auditoría): una corrida E7.5 «pasó» con 5 consultas en 9 h porque el portátil se suspendió.
    Se miran los dos relojes: en Windows el monotónico (QueryPerformanceCounter) sigue corriendo con
    el equipo suspendido; en Linux se para y solo lo delata el de pared."""
    paso = max(ahora[0] - anterior[0], ahora[1] - anterior[1])
    return paso if paso > umbral else None


async def vigilar_pausas(res: Resultado, terminado: asyncio.Event, umbral: float) -> None:
    anterior = (time.time(), time.monotonic())
    while not terminado.is_set():
        await asyncio.sleep(1)
        ahora = (time.time(), time.monotonic())
        salto = salto_de_reloj(anterior, ahora, umbral)
        if salto is not None:
            res.pausas_s.append(round(salto, 1))
            print(f"[!] el proceso estuvo congelado {salto:.0f} s (¿suspensión?): la corrida no vale", flush=True)
        anterior = ahora


async def consulta(c: httpx.AsyncClient, i: int, sid: str, tipo: str, texto: str, sondeo: float,  # noqa: C901, PLR0915
                   res: Resultado) -> Medida:
    """Una consulta, aprobando como una persona lo que pida aprobación mientras dura."""
    esperas: list[float] = []
    inicio = time.monotonic()
    termino = asyncio.Event()

    async def aprobar() -> None:
        aprobadas: set[str] = set()
        sin_verla = inicio  # envío del último sondeo que respondió: lo que no listó apareció después
        while not termino.is_set():
            await asyncio.sleep(sondeo)
            enviado = time.monotonic()
            # F7 (auditoría): un error de un sondeo se cuenta y se sigue sondeando; antes mataba la
            # tarea en silencio y la consulta colgaba hasta el timeout de HITL (300 s) como fallo «del sistema»
            try:
                p = await c.get("/api/v1/approval/pending", params={"session_id": sid})
                res.contar(p.status_code)
                if p.status_code != 200:
                    continue
                visto = time.monotonic()
                for a in p.json():
                    if a["approval_id"] in aprobadas:
                        continue
                    ok = await c.post(f"/api/v1/approval/{a['approval_id']}",
                                      json={"action": "approve", "session_id": sid})
                    res.contar(ok.status_code)
                    if ok.is_success:
                        aprobadas.add(a["approval_id"])
                        creado = a_monotonico(a.get("created_at"), time.time() - time.monotonic())
                        esperas.append(espera_aprobacion(sin_verla, visto, time.monotonic(), creado))
                    else:
                        print(f"[u{i}] no se pudo aprobar {a['approval_id']}: {ok.status_code}", flush=True)
                sin_verla = enviado
            except httpx.HTTPError as exc:
                res.contar(599)
                res.errores_sondeo.append(f"u{i}: {type(exc).__name__}")
            except (ValueError, KeyError, TypeError) as exc:
                res.errores_sondeo.append(f"u{i}: respuesta de aprobaciones ilegible ({type(exc).__name__})")

    tarea = asyncio.create_task(aprobar())
    try:
        q = await c.post("/api/v1/query/", json={"query": texto, "session_id": sid})
        codigo = q.status_code
        try:
            cuerpo = q.json()
        except ValueError:
            cuerpo = {}
        if not isinstance(cuerpo, dict):
            cuerpo = {}
        est = cuerpo.get("status") or cuerpo.get("detail") or "?"
    except httpx.HTTPError as exc:
        codigo, est = 599, type(exc).__name__
    finally:
        dur = time.monotonic() - inicio
        termino.set()
        tarea.cancel()
        for r in await asyncio.gather(tarea, return_exceptions=True):
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                res.errores_cliente.append(f"u{i} sondeo: {type(r).__name__}: {r}")
    res.contar(codigo)
    return Medida(i, tipo, texto, dur, sum(esperas), codigo, str(est)[:60], len(esperas))


async def usuario(i: int, base: str, cabeceras: dict[str, str] | TokenRenovable, fin: float, sondeo: float,
                  res: Resultado, transporte: httpx.AsyncBaseTransport | None = None) -> None:
    auth = cabeceras if isinstance(cabeceras, TokenRenovable) else None
    fijas = cabeceras if isinstance(cabeceras, dict) else None
    async with httpx.AsyncClient(base_url=base, headers=fijas, auth=auth,
                                 verify=False, timeout=600, transport=transporte) as c:
        # F7 (auditoría): una sesión que no abre hace fallar la corrida (antes salía 0 con 0 consultas)
        try:
            r = await c.post("/api/v1/session/")
        except httpx.HTTPError as exc:
            res.contar(599)
            res.sesiones_fallidas.append(f"u{i}: {type(exc).__name__}")
            print(f"[u{i}] no pudo abrir sesión: {type(exc).__name__}", flush=True)
            return
        res.contar(r.status_code)
        if r.status_code != 201:
            res.sesiones_fallidas.append(f"u{i}: {r.status_code}")
            print(f"[u{i}] no pudo abrir sesión: {r.status_code}", flush=True)
            return
        sid = r.json()["session_id"]
        ronda = i  # cada usuario arranca en una manzana distinta
        await asyncio.sleep(random.uniform(0, 8))  # llegan escalonados
        while time.monotonic() < fin:
            for tipo, texto in _mezcla(ronda):
                if time.monotonic() >= fin:
                    break
                m = await consulta(c, i, sid, tipo, texto, sondeo, res)
                res.medidas.append(m)
                print(f"[u{i}] {tipo:10} {m.codigo} {m.estado[:10]:10} {m.segundos:6.1f}s", flush=True)
                await asyncio.sleep(random.uniform(10, 20))  # piensa
            ronda += 1


def _p(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def _completada(m: Medida) -> bool:
    return m.codigo == 200 and m.estado == "completed"


def _computo(m: Medida) -> float:
    # latencia de CÓMPUTO: sin la espera humana por las aprobaciones
    return m.segundos - m.espera_aprobacion


def evaluar(res: Resultado, *, usuarios: int, minutos: float, identidades: int, inicio: float,
            fin: float) -> tuple[dict, list[tuple[str, bool]]]:
    """Informe y umbrales de una corrida. Pura: la decisión de si E7.5 pasa vive aquí."""
    consultas = res.medidas
    completadas = [m for m in consultas if _completada(m)]
    fallidas = [m for m in consultas if not _completada(m)]
    # F7 (auditoría): los percentiles, solo de las completadas; un 429 en 20 ms o un 504 a los
    # 300 s no son latencia de una respuesta. La de las fallidas va aparte.
    computo = [_computo(m) for m in completadas]
    errores_5xx = sum(n for cod, n in res.codigos.items() if cod >= 500)
    tasa_5xx = errores_5xx / max(res.peticiones, 1)
    p95 = _p(computo, 0.95)
    informe: dict = {
        "inicio": inicio, "fin": fin, "duracion_real_s": round(fin - inicio, 1),
        "usuarios": usuarios, "minutos": minutos, "identidades": identidades,
        "consultas": len(consultas), "peticiones": res.peticiones, "codigos": res.codigos,
        "tasa_5xx": tasa_5xx,
        "consultas_fallidas": len(fallidas),
        "p50_s": _p(computo, 0.5), "p95_s": p95, "max_s": max(computo, default=0),
        "fallidas_p95_s": _p([_computo(m) for m in fallidas], 0.95),
        "por_tipo": {t: {"n": len([m for m in consultas if m.tipo == t]), "completadas": len(v),
                         "p50_s": _p(v, 0.5), "p95_s": _p(v, 0.95)}
                     for t in sorted({m.tipo for m in consultas})
                     for v in [[_computo(m) for m in completadas if m.tipo == t]]},
        "sesiones_fallidas": res.sesiones_fallidas,
        "errores_cliente": res.errores_cliente,
        "errores_sondeo": res.errores_sondeo,
        "pausas_s": res.pausas_s,
        "medidas": [asdict(m) for m in consultas],
    }
    faltan = [t for t in TIPOS if t not in {m.tipo for m in consultas}]
    umbrales = [
        ("todos los usuarios abrieron sesión", not res.sesiones_fallidas),
        ("sin errores del propio cliente de carga", not res.errores_cliente),
        ("el equipo no se suspendió durante la corrida", not res.pausas_s),
        ("se midió la mezcla entera" + (f" (faltan {', '.join(faltan)})" if faltan else ""), not faltan),
        ("5xx < 1 %", tasa_5xx < 0.01),
        ("p95 consulta < 60 s", p95 < 60),
        # sin esto la corrida E7.5 «pasó» con el 20 % de consultas caídas por 429 del proveedor;
        # y con 0 consultas «0 ≥ 0» también pasaba
        ("consultas completadas ≥ 95 %", bool(consultas) and len(completadas) >= 0.95 * len(consultas)),
    ]
    informe["umbrales"] = dict(umbrales)
    informe["valida"] = all(ok for _, ok in umbrales)
    return informe, umbrales


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0912, PLR0915
    ap = argparse.ArgumentParser()
    ap.add_argument("--usuarios", type=int, default=5)
    ap.add_argument("--minutos", type=float, default=15)
    ap.add_argument("--base", default="https://localhost:3443")
    ap.add_argument("--salida", default="bench_results/carga.json")
    ap.add_argument("--sondeo", type=float, default=None,
                    help=f"segundos entre sondeos de aprobaciones (por defecto {SONDEO_BASE_S:g} × usuarios por identidad)")
    ap.add_argument("--pausa-maxima", type=float, default=PAUSA_MAXIMA_S,
                    help="segundos congelado a partir de los cuales la corrida se invalida")
    args = ap.parse_args(argv)
    # la consola de Windows (cp1252) no codifica ✓/✗: el resumen moría con el informe ya escrito
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        ids: list[dict[str, str] | TokenRenovable] = [*identidades_renovables(os.environ), *identidades(os.environ)]
    except IdentidadesInvalidas as exc:
        print(f"Identidades no válidas: {exc}")
        return 2
    if not ids:
        print("Falta la identidad: define CARGA_TOKENS (Bearer OIDC, una por usuario) o API_KEY "
              "(la clave de servicio: un solo principal para todos).")
        return 2
    if args.usuarios < 1:
        print(f"--usuarios {args.usuarios}: hace falta al menos un usuario.")
        return 2
    # F7 (auditoría): solo cuentan las identidades que usa algún usuario. La API key va detrás de
    # los tokens: si hay uno por usuario no se usa, ni avisa ni infla `identidades` del informe.
    if any(isinstance(c, dict) and "X-API-Key" in c for c in ids[args.usuarios:]):
        print("La API key no se usa: hay un token de OIDC por usuario.", flush=True)
    ids = ids[:args.usuarios]
    if any(isinstance(c, dict) and "X-API-Key" in c for c in ids):
        print(AVISO_API_KEY, flush=True)
    por_identidad = math.ceil(args.usuarios / len(ids))
    sondeo = args.sondeo if args.sondeo is not None else SONDEO_BASE_S * por_identidad
    if por_identidad > 1:
        print(f"[!] {args.usuarios} usuarios y {len(ids)} identidad(es) distintas: hasta {por_identidad} "
              "usuarios comparten identidad y, con ella, el cupo del rate limit; los 429 que salgan medirán "
              "el límite, no el sistema. Para usuarios independientes, un JWT de OIDC por usuario "
              f"(CARGA_TOKENS). Sondeo de aprobaciones cada {sondeo:g} s.", flush=True)

    res = Resultado()
    inicio = time.time()
    fin = time.monotonic() + args.minutos * 60

    async def todos() -> None:
        terminado = asyncio.Event()
        vigia = asyncio.create_task(vigilar_pausas(res, terminado, args.pausa_maxima))
        resultados = await asyncio.gather(
            *(usuario(i, args.base, ids[i % len(ids)], fin, sondeo, res) for i in range(args.usuarios)),
            return_exceptions=True)
        terminado.set()
        await vigia
        # F7 (auditoría): una excepción de un usuario ya no tumba la corrida sin informe: queda escrita
        for i, r in enumerate(resultados):
            if isinstance(r, BaseException):
                res.errores_cliente.append(f"u{i}: {type(r).__name__}: {r}")

    asyncio.run(todos())

    informe, umbrales = evaluar(res, usuarios=args.usuarios, minutos=args.minutos, identidades=len(ids),
                                inicio=inicio, fin=time.time())
    os.makedirs(os.path.dirname(args.salida) or ".", exist_ok=True)
    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(informe, f, ensure_ascii=False, indent=2)

    print(f"\n{informe['consultas']} consultas de {args.usuarios} usuarios en {args.minutos:g} min "
          f"(reales {informe['duracion_real_s'] / 60:.1f} min) · {res.peticiones} peticiones · códigos {res.codigos}")
    print(f"p50 {informe['p50_s']:.1f} s · p95 {informe['p95_s']:.1f} s · máx {informe['max_s']:.1f} s · "
          f"5xx {informe['tasa_5xx']:.2%} · consultas no completadas {informe['consultas_fallidas']}")
    for t, d in informe["por_tipo"].items():
        print(f"  {t:10} n={d['n']:3} ok={d['completadas']:3}  p50 {d['p50_s']:5.1f} s  p95 {d['p95_s']:5.1f} s")
    for nombre, ok in umbrales:
        print(f"  {'✓' if ok else '✗'} {nombre}")
    for clave in ("sesiones_fallidas", "errores_cliente", "errores_sondeo"):
        if informe[clave]:
            print(f"  {clave}:", informe[clave][:10])
    fallidas = [(m["tipo"], m["codigo"], m["estado"]) for m in informe["medidas"]
                if not (m["codigo"] == 200 and m["estado"] == "completed")]
    if fallidas:
        print("  consultas no completadas:", fallidas[:10])
    renovables = [c for c in ids if isinstance(c, TokenRenovable)]
    if renovables:
        print(f"  tokens OIDC renovados: {sum(c.renovaciones for c in renovables)} veces entre {len(renovables)} usuarios")
    return 0 if informe["valida"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
