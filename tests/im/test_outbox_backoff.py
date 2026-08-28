"""Regression tests for the shared IM outbox retry backoff.

The defects these pin, with the values reachable from the shipped config:

- OneBot/QQBot: ``outbox_max_attempts=10`` and ``outbox_retry_delay_seconds=60``
  produced a final wait of ``60 * 2**9`` = 30720 s (about 8.5 hours) of
  ``asyncio.sleep``, which looks identical to a hung queue.
- Telegram: no exponential term at all, so an explicitly rejected send retried
  on a flat interval — different behavior for the same class of failure.
- None of them jittered, so every page of a split long reply retried in lockstep.
"""

from __future__ import annotations

import random

import pytest

from kirara_ai.im.outbox_backoff import (
    MAX_RETRY_DELAY_SECONDS,
    RETRY_JITTER_RATIO,
    retry_backoff_seconds,
)


def test_backoff_grows_geometrically_without_jitter():
    delays = [
        retry_backoff_seconds(1.0, attempt, jitter=False) for attempt in range(1, 6)
    ]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_backoff_is_capped_at_the_documented_ceiling():
    # The pre-fix formula returned 60 * 2**9 == 30720 seconds here.
    delay = retry_backoff_seconds(60.0, 10, jitter=False)

    assert delay == MAX_RETRY_DELAY_SECONDS


def test_backoff_cap_holds_for_an_absurd_attempt_count():
    """A huge exponent must not build an overflowing float before the cap."""
    assert retry_backoff_seconds(60.0, 4096, jitter=False) == MAX_RETRY_DELAY_SECONDS


def test_zero_base_delay_disables_waiting():
    assert retry_backoff_seconds(0.0, 5) == 0.0
    assert retry_backoff_seconds(-1.0, 5) == 0.0


def test_zero_ceiling_disables_waiting():
    assert retry_backoff_seconds(10.0, 3, max_delay_seconds=0.0) == 0.0


def test_first_attempt_uses_the_base_delay():
    assert retry_backoff_seconds(2.5, 1, jitter=False) == 2.5
    # attempt_count below 1 must not produce a negative exponent
    assert retry_backoff_seconds(2.5, 0, jitter=False) == 2.5


def test_jitter_only_shortens_the_wait_and_stays_inside_the_ratio():
    rng = random.Random(1234)
    base = retry_backoff_seconds(4.0, 3, jitter=False)
    lowest = base * (1 - RETRY_JITTER_RATIO)

    for _ in range(200):
        delay = retry_backoff_seconds(4.0, 3, rng=rng)
        assert lowest <= delay <= base


def test_jitter_actually_spreads_concurrent_retries():
    rng = random.Random(7)
    delays = {retry_backoff_seconds(4.0, 3, rng=rng) for _ in range(50)}

    assert len(delays) > 1


def test_jittered_delay_never_exceeds_the_ceiling():
    rng = random.Random(99)

    for _ in range(100):
        assert retry_backoff_seconds(600.0, 8, rng=rng) <= MAX_RETRY_DELAY_SECONDS


def test_custom_ceiling_is_honored():
    assert retry_backoff_seconds(1.0, 10, max_delay_seconds=3.0, jitter=False) == 3.0


@pytest.mark.parametrize("attempt", [1, 2, 3, 5, 8, 13])
def test_delay_is_monotonic_up_to_the_ceiling(attempt: int):
    current = retry_backoff_seconds(1.0, attempt, jitter=False)
    previous = retry_backoff_seconds(1.0, attempt - 1, jitter=False)

    assert current >= previous
