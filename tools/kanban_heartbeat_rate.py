"""Client-side rate limit for kanban heartbeat writes (worker runtime path).

A worker must not flood the board with heartbeat rows: every write touches
``tasks.last_heartbeat_at`` and appends a ``heartbeat`` event, and the
dispatcher's stale-claim watchdog reads that column as the liveness signal. A
storm of heartbeats from a worker that makes no real progress (provider down,
tight tool loop) keeps the run looking "alive" and suppresses the stale-reclaim
that would otherwise free the board slot (Lauf 59088: 151 heartbeats on one
run, the task blocked for 151 minutes until a manual reclaim).

Decision (Team-Klärung t_9d2cdb94, scrummaster):

* At least ``HEARTBEAT_MIN_INTERVAL_SECONDS`` (30 s) between two heartbeat
  writes for the same task. Writes inside that window are **dropped**, not
  queued — a queued burst would arrive later and defeat the purpose.
* Storm guard: more than ``HEARTBEAT_STORM_THRESHOLD_PER_MINUTE`` (10)
  heartbeats per minute for one task means the excess is dropped and the
  suspicion is reported, so the caller can record it on the board.

Both limits live on the client side (this module) because the worker is the
only party that knows how often it is *asking* to write; the dispatcher-side
counterpart (ignoring excess heartbeats for liveness) is a separate card.

State is per-process and per-task, guarded by a lock: the agent loop may touch
activity from several threads, and a race may cost one extra write but must
never raise into the loop.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Minimum gap between two accepted heartbeat writes for the same task.
HEARTBEAT_MIN_INTERVAL_SECONDS = 30.0

# More than this many heartbeats per minute for one task is a storm: the
# excess is dropped and the suspicion is reported.
HEARTBEAT_STORM_THRESHOLD_PER_MINUTE = 10

# Window used to count the heartbeats per minute.
STORM_WINDOW_SECONDS = 60.0

# Verdicts returned by :meth:`HeartbeatRateLimiter.check`.
ALLOW = "allow"
DEDUPE = "dedupe"
STORM = "storm"


@dataclass
class _TaskState:
    """Per-task limiter state."""

    # Monotonic timestamp of the last accepted write (0.0 = none yet).
    last_accepted: float = 0.0
    # Monotonic timestamps of the recent *requests* (how often the worker asked
    # to write) — the storm signal.
    requests: List[float] = field(default_factory=list)
    # Monotonic timestamps of the recent accepted writes.
    recent: List[float] = field(default_factory=list)
    # Whether a storm was already reported for the current burst (one report
    # per burst, not one per dropped heartbeat).
    storm_reported: bool = False


@dataclass(frozen=True)
class Decision:
    """Outcome of a heartbeat write request."""

    verdict: str
    # Seconds until the next write is accepted (0.0 when allowed).
    retry_after: float = 0.0
    # Heartbeats seen for this task inside the last minute.
    per_minute: int = 0
    # True for the first dropped heartbeat of a storm burst (report it once).
    first_in_burst: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


class HeartbeatRateLimiter:
    """Thread-safe, per-task client-side rate limiter for heartbeat writes."""

    def __init__(
        self,
        min_interval: float = HEARTBEAT_MIN_INTERVAL_SECONDS,
        storm_threshold: int = HEARTBEAT_STORM_THRESHOLD_PER_MINUTE,
        window: float = STORM_WINDOW_SECONDS,
    ) -> None:
        self._min_interval = float(min_interval)
        self._storm_threshold = int(storm_threshold)
        self._window = float(window)
        self._lock = threading.Lock()
        self._state: Dict[str, _TaskState] = {}

    def check(self, task_id: str, now: Optional[float] = None) -> Decision:
        """Decide whether a heartbeat write for ``task_id`` may happen now.

        The call is a pure gate: it does **not** write anything. On ``ALLOW``
        the write is expected to happen; on ``DEDUPE``/``STORM`` the caller
        must drop the write.

        Two counters are kept per task, because they answer different
        questions: ``requests`` counts how often the worker *asked* to write
        (the storm signal — a tight loop asks far more often than it writes),
        ``recent`` counts the writes that were actually accepted.
        """
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            state = self._state.setdefault(task_id, _TaskState())
            state.requests = [t for t in state.requests if (ts - t) < self._window]
            state.recent = [t for t in state.recent if (ts - t) < self._window]
            if len(state.requests) >= self._storm_threshold:
                # Storm: the worker asks for heartbeats far more often than the
                # board needs. Drop this one and report the suspicion once per
                # burst (not once per dropped heartbeat).
                state.requests.append(ts)
                first = not state.storm_reported
                state.storm_reported = True
                return Decision(
                    verdict=STORM,
                    retry_after=self._window,
                    per_minute=len(state.requests),
                    first_in_burst=first,
                )
            state.requests.append(ts)
            if state.last_accepted and (ts - state.last_accepted) < self._min_interval:
                # Inside the minimum gap: drop the write, do not queue it.
                return Decision(
                    verdict=DEDUPE,
                    retry_after=self._min_interval - (ts - state.last_accepted),
                    per_minute=len(state.requests),
                )
            # Accepted: stamp the window, count the write, and reset the storm
            # flag once the previous minute was quiet again.
            state.last_accepted = ts
            state.recent.append(ts)
            state.storm_reported = False
            return Decision(verdict=ALLOW, per_minute=len(state.requests))

    def reset(self, task_id: Optional[str] = None) -> None:
        """Forget the limiter state for one task (or all tasks)."""
        with self._lock:
            if task_id is None:
                self._state.clear()
            else:
                self._state.pop(task_id, None)

    def state_snapshot(self, task_id: str) -> Dict[str, float]:
        """Inspect the limiter state of a task (diagnostics/tests)."""
        with self._lock:
            state = self._state.get(task_id)
            if state is None:
                return {"last_accepted": 0.0, "recent": 0.0, "requests": 0.0, "storm_reported": 0.0}
            return {
                "last_accepted": state.last_accepted,
                "recent": float(len(state.recent)),
                "requests": float(len(state.requests)),
                "storm_reported": 1.0 if state.storm_reported else 0.0,
            }


# Process-wide limiter shared by every heartbeat write path of this worker.
_RATE_LIMITER = HeartbeatRateLimiter()


def limiter() -> HeartbeatRateLimiter:
    """The process-wide heartbeat rate limiter."""
    return _RATE_LIMITER


def check(task_id: str, now: Optional[float] = None) -> Decision:
    """Gate a heartbeat write for ``task_id`` on the process-wide limiter."""
    return _RATE_LIMITER.check(task_id, now=now)


def reset(task_id: Optional[str] = None) -> None:
    """Reset the process-wide limiter (one task, or everything)."""
    _RATE_LIMITER.reset(task_id)
