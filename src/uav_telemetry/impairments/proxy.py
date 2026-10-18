from collections import Counter, deque
import hashlib
import heapq
import random
import socket
import threading
import time

from uav_telemetry.utils.config import Config
from uav_telemetry.utils.events import EventLog


class Scheduler:
    """All random draws depend on ingress order and seed, not thread timing."""
    def __init__(self, config: Config):
        self.config = config
        self.rng = random.Random(config.seed)
        self.last_due = 0.0

    def outage(self, elapsed: float) -> bool:
        c = self.config
        return c.outage_duration_s > 0 and c.outage_start_s <= elapsed < c.outage_start_s + c.outage_duration_s

    def schedule(self, elapsed: float) -> tuple[float | None, str | None]:
        loss_draw = self.rng.random()
        jitter = self.rng.uniform(-self.config.jitter_ms, self.config.jitter_ms)
        reorder_draw = self.rng.random()
        if self.outage(elapsed):
            return None, "outage"
        if loss_draw < self.config.loss_probability:
            return None, "random_loss"
        delay = max(0, self.config.delay_ms + jitter) / 1000
        if reorder_draw < self.config.reorder_probability:
            delay += self.config.reorder_extra_ms / 1000
        due = elapsed + delay
        if not self.config.allow_reordering:
            due = max(due, self.last_due)
        self.last_due = due
        return due, None


class UDPProxy(threading.Thread):
    def __init__(self, bind: tuple, destination: tuple, config: Config, log: EventLog, start: float | None = None):
        super().__init__(daemon=True, name="udp-impairment-proxy")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**20)
        self.sock.bind(bind)
        self.sock.settimeout(0.002)
        self.address = self.sock.getsockname()
        self.destination = destination
        self.config = config
        self.scheduler = Scheduler(config)
        self.log = log
        self.start_time = time.monotonic() if start is None else start
        self.stop_event = threading.Event()
        self.pending = []
        self.records = deque(maxlen=200000)
        self.counters = Counter(input=0, forwarded=0, dropped=0, reordered=0)
        self.highest_forwarded = -1
        self.lock = threading.RLock()
        self.error = None

    def _drop(self, row: dict, reason: str) -> None:
        row["drop_reason"] = reason
        self.counters["dropped"] += 1
        self.counters[reason] += 1
        self.log.emit("proxy_drop", **row)

    def _forward_due(self, now: float) -> None:
        while self.pending and self.pending[0][0] <= now:
            _, _, payload, row = heapq.heappop(self.pending)
            if self.scheduler.outage(now - self.start_time):
                self._drop(row, "outage_queued")
                continue
            self.sock.sendto(payload, self.destination)
            sent = time.monotonic()
            row["forwarded_monotonic_s"] = sent
            row["observed_proxy_residence_ms"] = 1000 * (sent - row["ingress_monotonic_s"])
            self.counters["forwarded"] += 1
            if row["id"] < self.highest_forwarded:
                self.counters["reordered"] += 1
            self.highest_forwarded = max(self.highest_forwarded, row["id"])
            self.log.emit("proxy_forward", **row)

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    payload, peer = self.sock.recvfrom(65535)
                    now = time.monotonic()
                    with self.lock:
                        row = {"id": self.counters["input"], "sha256": hashlib.sha256(payload).hexdigest(),
                            "bytes": len(payload), "ingress_monotonic_s": now, "elapsed_s": now - self.start_time}
                        self.records.append(row)
                        self.counters["input"] += 1
                        due, reason = self.scheduler.schedule(row["elapsed_s"])
                        self.log.emit("proxy_input", peer=list(peer), **row)
                        if reason:
                            self._drop(row, reason)
                        elif len(self.pending) >= self.config.max_pending:
                            self._drop(row, "queue_overflow")
                        else:
                            row["injected_schedule_delay_ms"] = (due - row["elapsed_s"]) * 1000
                            heapq.heappush(self.pending, (self.start_time + due, row["id"], payload, row))
                except socket.timeout:
                    pass
                with self.lock:
                    self._forward_due(time.monotonic())
        except Exception as exc:
            self.error = exc
            self.log.emit("proxy_error", error=str(exc))
        finally:
            self.sock.close()

    def snapshot(self) -> dict:
        with self.lock:
            return {**dict(self.counters), "pending": len(self.pending), "bind": list(self.address),
                    "destination": list(self.destination), "model": "UDP datagram degradation; no RF propagation"}

    def close(self) -> None:
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=3)
        else:
            self.sock.close()
        if self.is_alive():
            raise RuntimeError("Proxy did not stop")
        if self.error:
            raise RuntimeError("Proxy failed") from self.error
