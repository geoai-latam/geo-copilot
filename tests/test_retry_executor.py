"""
Tests for the unified RetryExecutor (Fase 6 #4 / ORC-4).
"""

from __future__ import annotations

import pytest

from geo_copilot.orchestrator.retry import AttemptOutcome, RetryExecutor


@pytest.mark.asyncio
async def test_success_on_first_attempt() -> None:
    """No retry when the first attempt succeeds."""
    calls: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        calls.append(idx)
        return AttemptOutcome(success=True, payload={"ok": True})

    result = await RetryExecutor(max_attempts=3).run(attempt=attempt)

    assert result.outcome.success is True
    assert result.outcome.payload == {"ok": True}
    assert result.attempts == 1
    assert calls == [0]
    assert result.errors == []


@pytest.mark.asyncio
async def test_retries_until_success() -> None:
    """Retries until success and stops calling further attempts."""
    calls: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        calls.append(idx)
        if idx < 2:
            return AttemptOutcome(success=False, error=f"err-{idx}")
        return AttemptOutcome(success=True, payload={"finally": True})

    result = await RetryExecutor(max_attempts=5).run(attempt=attempt)

    assert result.outcome.success is True
    assert result.attempts == 3
    assert calls == [0, 1, 2]
    assert result.errors == ["err-0", "err-1"]


@pytest.mark.asyncio
async def test_exhausts_retries_and_returns_last_error() -> None:
    """Stops after ``max_attempts`` and surfaces the last error."""
    calls: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        calls.append(idx)
        return AttemptOutcome(success=False, error=f"err-{idx}")

    result = await RetryExecutor(max_attempts=3).run(attempt=attempt)

    assert result.outcome.success is False
    assert result.outcome.error == "err-2"
    assert result.attempts == 3
    assert calls == [0, 1, 2]
    assert result.errors == ["err-0", "err-1", "err-2"]


@pytest.mark.asyncio
async def test_no_retry_short_circuits() -> None:
    """``no_retry=True`` is returned immediately without retrying."""
    calls: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        calls.append(idx)
        return AttemptOutcome(success=False, payload={"paused": True}, no_retry=True)

    result = await RetryExecutor(max_attempts=5).run(attempt=attempt)

    assert result.outcome.success is False
    assert result.outcome.no_retry is True
    assert result.outcome.payload == {"paused": True}
    assert result.attempts == 1
    assert calls == [0]


@pytest.mark.asyncio
async def test_exception_in_attempt_is_captured() -> None:
    """An exception raised by ``attempt`` becomes a failure outcome."""

    async def attempt(_idx: int) -> AttemptOutcome:
        raise RuntimeError("boom")

    result = await RetryExecutor(max_attempts=2).run(attempt=attempt)

    assert result.outcome.success is False
    assert "boom" in (result.outcome.error or "")
    assert result.attempts == 2
    assert all("boom" in e for e in result.errors)


@pytest.mark.asyncio
async def test_correct_returning_false_aborts_retries() -> None:
    """If ``correct`` returns False, we stop retrying."""
    calls: list[int] = []
    corrections: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        calls.append(idx)
        return AttemptOutcome(success=False, error=f"err-{idx}")

    async def correct(_err: str, idx: int) -> bool:
        corrections.append(idx)
        return False

    result = await RetryExecutor(max_attempts=5).run(attempt=attempt, correct=correct)

    # We attempted once, asked correct() which said "no", stopped.
    assert calls == [0]
    assert corrections == [0]
    assert result.outcome.success is False


@pytest.mark.asyncio
async def test_correct_returning_true_allows_next_attempt() -> None:
    """``correct`` returning True permits the next retry."""
    attempts: list[int] = []
    corrections: list[int] = []

    async def attempt(idx: int) -> AttemptOutcome:
        attempts.append(idx)
        if idx < 1:
            return AttemptOutcome(success=False, error="initial")
        return AttemptOutcome(success=True, payload={"fixed": True})

    async def correct(_err: str, idx: int) -> bool:
        corrections.append(idx)
        return True

    result = await RetryExecutor(max_attempts=3).run(attempt=attempt, correct=correct)

    assert attempts == [0, 1]
    assert corrections == [0]
    assert result.outcome.success is True


@pytest.mark.asyncio
async def test_on_retry_is_invoked_between_attempts() -> None:
    """The notification callback fires between failed attempts."""
    notifications: list[tuple[int, int, str]] = []

    async def attempt(idx: int) -> AttemptOutcome:
        if idx < 2:
            return AttemptOutcome(success=False, error=f"err-{idx}")
        return AttemptOutcome(success=True)

    async def on_retry(next_attempt: int, total: int, last_error: str) -> None:
        notifications.append((next_attempt, total, last_error))

    await RetryExecutor(max_attempts=3).run(attempt=attempt, on_retry=on_retry)

    # Notified before attempt 2 and before attempt 3 (1-based for users).
    assert notifications == [(2, 3, "err-0"), (3, 3, "err-1")]


@pytest.mark.asyncio
async def test_on_retry_not_called_after_last_attempt() -> None:
    """We don't notify the user about a 'next' attempt that won't happen."""
    notifications: list[tuple[int, int, str]] = []

    async def attempt(_idx: int) -> AttemptOutcome:
        return AttemptOutcome(success=False, error="always")

    async def on_retry(next_attempt: int, total: int, last_error: str) -> None:
        notifications.append((next_attempt, total, last_error))

    await RetryExecutor(max_attempts=2).run(attempt=attempt, on_retry=on_retry)

    # 2 attempts → only one notification (between attempt 1 and 2).
    assert len(notifications) == 1


def test_invalid_max_attempts() -> None:
    """``max_attempts < 1`` is rejected at construction time."""
    with pytest.raises(ValueError):
        RetryExecutor(max_attempts=0)
