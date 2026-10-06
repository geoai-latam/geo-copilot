"""
Tests E2E del RetryExecutor con escenarios realistas — añadidos 2026-05-24.

Los tests existentes en `test_retry_executor.py` son unit-tests sólidos
del executor (validan: success first try, retry until success, exhausted,
no_retry short-circuit, on_retry callbacks). Lo que NO cubrían son
**escenarios end-to-end** donde:

1. El corrector REAL recibe un error y produce código/SQL nuevo.
2. La siguiente iteración corre el nuevo código (no el original).
3. El loop termina con `correction_info` que el responder puede usar.
4. Errores no-corregibles abortan limpiamente sin desperdiciar reintentos.

Estos tests NO mockean el corrector — simulan el comportamiento real con
funciones que efectivamente transforman el estado entre intentos.
"""

from __future__ import annotations

import pytest

from geo_copilot.orchestrator.retry import (
    AttemptOutcome,
    RetryExecutor,
)


# =============================================================================
# Escenario 1: SQL syntax error → corrector arregla → éxito
# =============================================================================
@pytest.mark.asyncio
async def test_sql_correction_arrives_at_valid_sql_after_2_attempts():
    """Simula el flujo real:

    1. Intento 1: SQL con typo `SELCT * FROM parcelas` → error de sintaxis
    2. Corrector: detecta typo, propone `SELECT * FROM parcelas`
    3. Intento 2: SQL corregido → éxito

    Verifica que el state mutado por el corrector ES lo que ejecuta el
    siguiente attempt (no un mock independiente).
    """
    # Estado externo que el corrector muta entre intentos.
    state = {"sql": "SELCT * FROM parcelas", "rows": None}

    async def attempt(idx: int) -> AttemptOutcome:
        sql = state["sql"]
        if not sql.upper().startswith("SELECT"):
            return AttemptOutcome(
                success=False,
                error=f'syntax error at "SELCT" (intento {idx + 1})',
            )
        state["rows"] = [{"id": 1}, {"id": 2}]
        return AttemptOutcome(success=True, payload={"rows": state["rows"]})

    correction_history: list[tuple[str, str]] = []

    async def correct(error: str, idx: int) -> bool:
        """Corrector real (heurístico simple para test, pero efectivo)."""
        old_sql = state["sql"]
        if "SELCT" in old_sql:
            state["sql"] = old_sql.replace("SELCT", "SELECT")
            correction_history.append((old_sql, state["sql"]))
            return True
        return False  # nada que corregir

    result = await RetryExecutor(max_attempts=3).run(
        attempt=attempt, correct=correct,
    )

    assert result.outcome.success is True
    assert result.attempts == 2, "debió tener éxito en el 2do intento"
    assert result.outcome.payload["rows"] == [{"id": 1}, {"id": 2}]
    # El corrector se invocó UNA vez (entre intento 1 y 2).
    assert len(correction_history) == 1
    assert correction_history[0] == (
        "SELCT * FROM parcelas",
        "SELECT * FROM parcelas",
    )
    # El estado final tiene el SQL corregido — esto es lo que verifica que
    # el side-effect del corrector REALMENTE se aplicó.
    assert state["sql"] == "SELECT * FROM parcelas"


# =============================================================================
# Escenario 2: 3 errores → corrección progresiva → 4to intento exitoso
# =============================================================================
@pytest.mark.asyncio
async def test_progressive_corrections_over_multiple_attempts():
    """El corrector va aprendiendo de cada error. Cada iteración produce
    un SQL distinto. Verifica que el executor pasa los errores acumulados
    correctamente y que después de N correcciones el código funciona."""

    # Errores que devuelve el "DB" según el estado del SQL.
    state = {
        "stage": 0,  # 0=typo, 1=col missing, 2=schema missing, 3=ok
    }
    attempts_record: list[str] = []
    correction_calls: list[tuple[str, int]] = []

    async def attempt(idx: int) -> AttemptOutcome:
        attempts_record.append(f"attempt-{idx}-stage-{state['stage']}")
        if state["stage"] == 0:
            return AttemptOutcome(success=False, error="syntax error: SELCT")
        if state["stage"] == 1:
            return AttemptOutcome(success=False, error="column 'nombe' does not exist")
        if state["stage"] == 2:
            return AttemptOutcome(success=False, error="relation 'parcelas' does not exist")
        return AttemptOutcome(success=True, payload={"final_stage": state["stage"]})

    async def correct(error: str, idx: int) -> bool:
        correction_calls.append((error, idx))
        # Cada corrección avanza el "stage" simulando que el LLM aprende.
        state["stage"] += 1
        return True

    result = await RetryExecutor(max_attempts=4).run(
        attempt=attempt, correct=correct,
    )

    assert result.outcome.success is True
    assert result.attempts == 4
    assert result.outcome.payload["final_stage"] == 3
    # Errores acumulados — exactamente 3 (los 3 fallidos antes del éxito).
    assert len(result.errors) == 3
    # Corrector se invocó 3 veces entre intentos.
    assert len(correction_calls) == 3
    # Cada corrección recibió el error específico del intento previo.
    assert "SELCT" in correction_calls[0][0]
    assert "nombe" in correction_calls[1][0]
    assert "parcelas" in correction_calls[2][0]
    # Trazabilidad: los 4 intentos en orden.
    assert attempts_record == [
        "attempt-0-stage-0",
        "attempt-1-stage-1",
        "attempt-2-stage-2",
        "attempt-3-stage-3",
    ]


# =============================================================================
# Escenario 3: Error terminal (LLM no puede corregir) → abort limpio
# =============================================================================
@pytest.mark.asyncio
async def test_corrector_signals_unfixable_aborts_without_using_remaining_attempts():
    """Si el LLM responde "NO_CORRECTION_POSSIBLE", el corrector devuelve
    False y el executor NO desperdicia los intentos restantes."""

    async def attempt(idx: int) -> AttemptOutcome:
        return AttemptOutcome(
            success=False,
            error="DB connection refused (kernel panic, no recuperable)",
        )

    correction_attempts = 0

    async def correct(error: str, idx: int) -> bool:
        nonlocal correction_attempts
        correction_attempts += 1
        # LLM señala que no se puede corregir (DB caída, no es bug de código).
        return False

    result = await RetryExecutor(max_attempts=5).run(
        attempt=attempt, correct=correct,
    )

    # Solo 1 intento + 1 corrección fallida → 1 attempt total.
    assert result.attempts == 1
    assert result.outcome.success is False
    assert correction_attempts == 1
    assert len(result.errors) == 1
    assert "kernel panic" in result.errors[0]


# =============================================================================
# Escenario 4: Excepción en attempt → capturada y reintentada
# =============================================================================
@pytest.mark.asyncio
async def test_exception_in_attempt_is_treated_as_recoverable_error():
    """Una excepción Python (no controlada) en attempt no debe matar el
    executor — se trata como un error normal y se reintenta. Crítico para
    robustez: errores transitorios de red/IO ocurren."""
    call_count = 0

    async def flaky_attempt(idx: int) -> AttemptOutcome:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            # Excepción no controlada (típico de fallo de red transitorio)
            raise ConnectionResetError(f"connection lost (try {call_count})")
        return AttemptOutcome(success=True, payload={"data": "ok"})

    result = await RetryExecutor(max_attempts=5).run(attempt=flaky_attempt)

    assert result.outcome.success is True
    assert result.attempts == 3
    # Los 2 errores anteriores se capturaron como mensajes.
    assert len(result.errors) == 2
    assert all("connection lost" in e for e in result.errors)


# =============================================================================
# Escenario 5: Corrector mismo levanta excepción → se trata como False
# =============================================================================
@pytest.mark.asyncio
async def test_correct_raising_exception_is_treated_as_no_correction():
    """Si el corrector explota (ej. el LLM da timeout), NO debe propagarse
    al caller — el executor debe tratarlo como 'no se pudo corregir' y
    abortar limpiamente con el último error."""

    async def attempt(idx: int) -> AttemptOutcome:
        return AttemptOutcome(success=False, error=f"fail-{idx}")

    correct_calls = 0

    async def broken_correct(error: str, idx: int) -> bool:
        nonlocal correct_calls
        correct_calls += 1
        raise TimeoutError("LLM timeout")  # ← debería caer como False

    result = await RetryExecutor(max_attempts=5).run(
        attempt=attempt, correct=broken_correct,
    )

    assert result.outcome.success is False
    # Solo 1 attempt: después de la excepción del corrector, abortamos.
    assert result.attempts == 1
    assert correct_calls == 1
    # El error capturado es el del attempt (no el del corrector).
    assert result.outcome.error == "fail-0"


# =============================================================================
# Escenario 6: on_retry callback se invoca con info correcta entre intentos
# =============================================================================
@pytest.mark.asyncio
async def test_on_retry_receives_correct_metadata_for_websocket():
    """`on_retry(next_attempt, max_attempts, last_error)` es lo que el
    grafo usa para emitir `send_retry_started` por WebSocket — el
    frontend muestra "Reintentando 2/3...". Si los argumentos están mal,
    el UI miente. Verifica los argumentos exactos."""

    notifications: list[tuple[int, int, str]] = []

    async def attempt(idx: int) -> AttemptOutcome:
        if idx < 2:
            return AttemptOutcome(success=False, error=f"err-{idx}")
        return AttemptOutcome(success=True)

    async def correct(error: str, idx: int) -> bool:
        return True

    async def on_retry(next_attempt: int, max_attempts: int, last_error: str) -> None:
        notifications.append((next_attempt, max_attempts, last_error))

    result = await RetryExecutor(max_attempts=3).run(
        attempt=attempt, correct=correct, on_retry=on_retry,
    )

    assert result.outcome.success is True
    assert result.attempts == 3
    # Notificaciones: entre intento 1→2 y 2→3.
    assert len(notifications) == 2
    # `next_attempt` es 1-based para humanos (segundo intento = 2).
    assert notifications[0] == (2, 3, "err-0")
    assert notifications[1] == (3, 3, "err-1")


# =============================================================================
# Escenario 7: no_retry termina inmediato sin importar éxito/error
# =============================================================================
@pytest.mark.asyncio
async def test_no_retry_terminates_on_business_signal_not_just_success():
    """`no_retry=True` se usa para indicar que el estado NO es un error
    técnico sino una transición intencional (ej. el plan se pausó
    esperando input del usuario). No debe contarse como error ni
    reintentar."""

    async def attempt(idx: int) -> AttemptOutcome:
        return AttemptOutcome(
            success=False,
            error="plan paused waiting for user selection",
            no_retry=True,  # ← señal de "no es error técnico"
            payload={"plan_paused": True},
        )

    correct_called = False

    async def correct(error: str, idx: int) -> bool:
        nonlocal correct_called
        correct_called = True
        return True

    result = await RetryExecutor(max_attempts=5).run(
        attempt=attempt, correct=correct,
    )

    # Aunque success=False, no se reintentó.
    assert result.attempts == 1
    assert correct_called is False, "no_retry NO debe disparar correction"
    assert result.outcome.payload["plan_paused"] is True
    assert result.outcome.no_retry is True
