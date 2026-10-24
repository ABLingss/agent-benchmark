"""Thread-safe routing state. Prefixes are observations, not claimed KV-cache hits."""
from dataclasses import dataclass, field
from fractions import Fraction
import threading


class RoutingError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass
class Backend:
    id: str
    base_url: str
    capacity: int = 2
    key_env: str = ""
    healthy: bool = True
    in_flight: int = 0
    prefixes: list = field(default_factory=list)


def prompt_tokens(messages):
    """Scheduling convention: role and text split on whitespace; not model BPE."""
    return tuple(token for item in messages for token in (item["role"] + " " + item["content"]).split())


def prefix_length(left, right):
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


class Router:
    def __init__(self, backends):
        self.backends = list(backends)
        self.cursor = 0
        self.lock = threading.RLock()
        ids = [b.id for b in self.backends]
        if len(ids) != len(set(ids)) or any(not b.id or type(b.capacity) is not int or b.capacity < 1 for b in self.backends):
            raise ValueError("invalid backends")

    def _round_robin(self):
        for offset in range(len(self.backends)):
            index = (self.cursor + offset) % len(self.backends)
            backend = self.backends[index]
            if backend.healthy and backend.in_flight < backend.capacity:
                self.cursor = (index + 1) % len(self.backends)
                return backend
        raise RoutingError(503, "no_backend")

    def _choose(self, tokens, policy):
        # The caller holds self.lock.
        if policy != "round_robin":
            raise RoutingError(400, "invalid_policy")
        return self._round_robin()

    def reserve(self, tokens, policy="round_robin"):
        with self.lock:
            backend = self._choose(tokens, policy)
            affinity = max((prefix_length(tokens, old) for old in backend.prefixes), default=0)
            backend.in_flight += 1
            return backend, affinity

    def release(self, backend, tokens, success):
        with self.lock:
            if success:
                backend.prefixes.append(tuple(tokens))
                backend.prefixes[:] = backend.prefixes[-32:]
            backend.in_flight -= 1

    def snapshot(self):
        with self.lock:
            return [{"id": b.id, "healthy": b.healthy, "capacity": b.capacity,
                     "in_flight": b.in_flight, "observed_prefixes": len(b.prefixes)} for b in self.backends]
