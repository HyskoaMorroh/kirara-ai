"""Bounded, jittered retry backoff shared by every IM outbox.

Each adapter's outbox grew its own delay formula. OneBot and QQBot used a raw
``retry_delay_seconds * 2 ** (attempt - 1)`` with no cap: with the configuration
surface's own maximums (``outbox_max_attempts`` 10, ``outbox_retry_delay_seconds``
60) the last wait was about 8.5 hours, which is indistinguishable from a stuck
queue. Telegram was worse than "no exponential term": it read neither setting —
``max_attempts`` and ``retry_delay_seconds`` were assigned and never used, so an
explicit ``RetryAfter`` went straight to the dead-letter path and the documented,
editable configuration changed nothing at all. Neither had jitter, so every page
of a split reply retried in lockstep and hit a rate-limited upstream at the same
instant.

This module owns the one formula so the adapters cannot drift again. It is used
by the OneBot, QQBot and Telegram outboxes; WeCom has no retry path at all
(every failure there is either accepted or quarantined as unknown).
"""

from __future__ import annotations

import random

#: Hard ceiling for a single retry wait, regardless of configuration.
#:
#: Five minutes is longer than any transient IM-side rejection needs and short
#: enough that an operator watching the queue sees it move. A queue that still
#: fails after this is a real outage and belongs in the dead-letter path, not in
#: an ever-growing sleep.
MAX_RETRY_DELAY_SECONDS = 300.0

#: Fraction of the computed delay that is randomized away from the peak.
#:
#: Applied as full jitter over ``[delay * (1 - RETRY_JITTER_RATIO), delay]`` so a
#: retry never waits *longer* than the deterministic schedule promises — only
#: earlier. That keeps the documented upper bound honest while still spreading
#: concurrent pages of one long reply.
RETRY_JITTER_RATIO = 0.2


def retry_backoff_seconds(
    base_delay_seconds: float,
    attempt_count: int,
    *,
    max_delay_seconds: float = MAX_RETRY_DELAY_SECONDS,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Return the wait before the next attempt.

    :param base_delay_seconds: configured base interval; ``0`` disables waiting.
    :param attempt_count: attempts already made (``1`` means the first failure).
    :param max_delay_seconds: ceiling applied after the exponential growth.
    :param jitter: subtract up to :data:`RETRY_JITTER_RATIO` of the delay.
    :param rng: injectable source of randomness so tests stay deterministic.
    :return: seconds to wait, never negative and never above ``max_delay_seconds``.
    """
    if base_delay_seconds <= 0:
        return 0.0
    ceiling = max(0.0, max_delay_seconds)
    if ceiling == 0.0:
        return 0.0

    exponent = max(0, attempt_count - 1)
    # Cap the exponent before the shift so a large attempt_count cannot build a
    # huge float only to have it thrown away by min().
    if exponent > 64:
        delay = ceiling
    else:
        delay = min(ceiling, base_delay_seconds * (2**exponent))

    if jitter and delay > 0:
        source = rng or random
        delay -= delay * RETRY_JITTER_RATIO * source.random()
    return max(0.0, min(ceiling, delay))
