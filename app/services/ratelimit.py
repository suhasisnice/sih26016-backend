"""A small fixed-window rate limiter for the login route.

Deliberately in-process and dependency-free. What that buys and what it
costs, stated plainly rather than discovered later:

- It stops the thing that actually matters here — someone pointing a
  password list at /auth/login — because a guesser needs thousands of
  attempts and gets ten per five minutes.
- It counts per process. Two Render instances mean two independent
  counters, so the effective limit doubles. That is fine at one instance
  and honest to know before scaling up; past that this wants Redis, which
  is a dependency and a service this deployment does not otherwise need.
- It counts FAILURES, not attempts. Someone signing in correctly ten times
  in a row is not attacking anything, and locking them out would be the
  limiter causing the outage it exists to prevent.

Memory is bounded by pruning on write: entries age out of the window, and
empty keys are dropped, so a rotating-IP flood cannot grow the map without
also ageing its own earlier entries out of it.
"""

import time
from collections import defaultdict

from fastapi import Request

from app.config import settings

# ip -> list of failure timestamps, newest last.
_failures: dict[str, list[float]] = defaultdict(list)

# Prune the whole map at most this often, so a busy endpoint is not walking
# every key on every request.
_PRUNE_INTERVAL_SECONDS = 60.0
_last_prune = 0.0


def client_ip(request: Request) -> str:
    """The caller's address, as best it can be known behind a proxy.

    Render terminates TLS and forwards, so request.client.host is the
    proxy for every caller and useless as a key. X-Forwarded-For's first
    entry is the original client — spoofable by the client in general,
    which is why this is a rate-limit key and never an authorisation
    input.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL_SECONDS:
        return
    _last_prune = now
    window = settings.login_rate_limit_window_seconds
    for key in list(_failures):
        kept = [t for t in _failures[key] if now - t < window]
        if kept:
            _failures[key] = kept
        else:
            del _failures[key]


def retry_after_seconds(request: Request) -> int | None:
    """Seconds the caller must wait, or None if they may try now."""
    now = time.monotonic()
    _prune(now)

    window = settings.login_rate_limit_window_seconds
    key = client_ip(request)
    recent = [t for t in _failures[key] if now - t < window]
    _failures[key] = recent

    if len(recent) < settings.login_rate_limit_attempts:
        return None
    # The window is fixed, so the block lifts when the oldest failure in it
    # ages out. Always at least a second, so a caller never sees "wait 0".
    return max(1, int(window - (now - recent[0])) + 1)


def record_failure(request: Request) -> None:
    _failures[client_ip(request)].append(time.monotonic())


def clear(request: Request) -> None:
    """Forget a caller's failures after they authenticate successfully."""
    _failures.pop(client_ip(request), None)
