from collections import deque
import base64
import hashlib
from pathlib import Path
import socket
import threading
import time

from uav_telemetry.mavlink.codec import Decoder
from uav_telemetry.metrics.engine import MetricsEngine
from uav_telemetry.utils.config import Config
from uav_telemetry.utils.events import EventLog, atomic_json


class Receiver(threading.Thread):
    def __init__(self, bind: tuple, config: Config, log: EventLog, snapshot_path: Path | None = None, start: float | None = None):
        super().__init__(daemon=True, name="mavlink-receiver")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**20)
        self.sock.bind(bind)
        self.sock.settimeout(0.02)
        self.address = self.sock.getsockname()
        self.stop_event = threading.Event()
        self.engine = MetricsEngine(config, start)
        self.decoder = Decoder()
        self.records = deque(maxlen=200000)
        self.log = log
        self.snapshot_path = snapshot_path
        self.error = None
        self.snapshot_write_errors = 0

    def run(self) -> None:
        last_snapshot = 0.0
        try:
            while not self.stop_event.is_set():
                try:
                    payload, peer = self.sock.recvfrom(65535)
                    now = time.monotonic()
                    record = {"sha256": hashlib.sha256(payload).hexdigest(), "received_monotonic_s": now, "bytes": len(payload)}
                    self.records.append(record)
                    self.engine.datagram(payload, now)
                    messages = self.decoder.feed(payload, peer)
                    for message in messages:
                        self.engine.ingest(message, now, peer)
                    self.log.emit("receiver_datagram", peer=list(peer), decoded_messages=len(messages),
                                  elapsed_s=now - self.engine.start, payload_b64=base64.b64encode(payload).decode(), **record)
                except socket.timeout:
                    now = time.monotonic()
                self.engine.tick(now)
                if now - last_snapshot >= 0.1:
                    snapshot = self.snapshot(now)
                    with self.engine.lock:
                        self.engine.history.append({"elapsed_s": now - self.engine.start,
                            "messages": snapshot["received_messages"], "datagrams": snapshot["received_datagrams"],
                            "heartbeat_age_s": snapshot["heartbeat_age_s"], "state": snapshot["state"]})
                    if self.snapshot_path:
                        try:
                            atomic_json(self.snapshot_path, snapshot)
                        except OSError as exc:
                            # Display I/O must not terminate communication reception.
                            self.snapshot_write_errors += 1
                            self.log.emit("snapshot_write_error", error=str(exc))
                    last_snapshot = now
        except Exception as exc:
            self.error = exc
            self.log.emit("receiver_error", error=str(exc))
        finally:
            self.sock.close()

    def snapshot(self, now: float | None = None) -> dict:
        result = self.engine.snapshot(now)
        result.update(parser_bad_data_events=self.decoder.bad_data, parser_peer_evictions=self.decoder.peer_evictions,
                      local_receive_endpoint=list(self.address), snapshot_write_errors=self.snapshot_write_errors)
        return result

    def close(self) -> None:
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=3)
        else:
            self.sock.close()
        if self.is_alive():
            raise RuntimeError("Receiver did not stop")
        if self.error:
            raise RuntimeError("Receiver failed") from self.error
