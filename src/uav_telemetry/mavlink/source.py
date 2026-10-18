"""Synthetic test trajectory encoded as genuine MAVLink; never flight commands."""
import base64
import hashlib
import json
import math
from pathlib import Path
import socket
import threading
import time

from .codec import mav
from uav_telemetry.utils.events import EventLog


class Encoder:
    def __init__(self, system: int = 1, component: int = 1):
        self.mav = mav.MAVLink(None, srcSystem=system, srcComponent=component)

    def pack(self, message) -> bytes:
        payload = message.pack(self.mav)
        self.mav.seq = (self.mav.seq + 1) % 256
        return payload

    def datagrams(self, tick: int, rate: float, batch_size: int = 2) -> list[bytes]:
        t = tick / rate
        ms = int(t * 1000) % 2**32
        messages = [
            self.mav.global_position_int_encode(ms, int((25.53 + 0.0001 * math.sin(t / 5)) * 1e7),
                int((84.85 + 0.0001 * math.cos(t / 5)) * 1e7), 85000, 35000, 400, 200, 0, int((t * 8) % 360 * 100)),
            self.mav.attitude_encode(ms, 0.08 * math.sin(t), 0.04 * math.cos(t), (t * 0.05) % (2 * math.pi), 0, 0, 0),
        ]
        def due(hz: float) -> bool:
            return tick % max(1, round(rate / hz)) == 0
        if due(5):
            messages.append(self.mav.heartbeat_encode(mav.MAV_TYPE_QUADROTOR, mav.MAV_AUTOPILOT_PX4, 0, 0, mav.MAV_STATE_STANDBY))
        if due(2):
            messages.append(self.mav.sys_status_encode(0, 0, 0, 120, 11800, 250, 83, 0, 0, 0, 0, 0, 0))
        if due(5):
            messages.append(self.mav.gps_raw_int_encode(int(t * 1e6), 3, int(25.53e7), int(84.85e7), 85000, 80, 100, 450, 9000, 12))
        if due(10):
            messages.append(self.mav.vfr_hud_encode(5.2, 4.5, int((t * 8) % 360), 35, 85, 0))
        frames = [self.pack(msg) for msg in messages]
        return [b"".join(frames[i:i + batch_size]) for i in range(0, len(frames), batch_size)]


def synthetic_source(destination: tuple, duration: float, rate: float, stop: threading.Event,
                     log: EventLog, start: float | None = None, system: int = 1, component: int = 1) -> list[dict]:
    encoder = Encoder(system, component)
    start = time.monotonic() if start is None else start
    records = []
    tick = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop.is_set() and (duration == 0 or tick / rate < duration):
            if stop.wait(max(0, start + tick / rate - time.monotonic())):
                break
            for payload in encoder.datagrams(tick, rate):
                timestamp = time.monotonic()
                sock.sendto(payload, destination)
                entry = {"id": len(records), "elapsed_s": timestamp - start, "sent_monotonic_s": timestamp,
                         "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
                records.append(entry)
                log.emit("source_send", **entry, payload_b64=base64.b64encode(payload).decode())
            tick += 1
    return records


def replay(path: Path, destination: tuple, speed: float = 1.0, stop: threading.Event | None = None) -> int:
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Replay speed must be positive and finite")
    stop = stop or threading.Event()
    start = time.monotonic()
    count = 0
    previous = 0.0
    first = None
    with path.open(encoding="utf-8") as capture, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for line in capture:
            row = json.loads(line)
            if row.get("event") not in ("source_send", "receiver_datagram"):
                continue
            elapsed = float(row["elapsed_s"])
            if not math.isfinite(elapsed) or elapsed < previous:
                raise ValueError("Capture times must be finite, nonnegative and ordered")
            previous = elapsed
            if first is None:
                first = elapsed
            if stop.wait(max(0, start + (elapsed - first) / speed - time.monotonic())):
                break
            payload = base64.b64decode(row["payload_b64"], validate=True)
            if not 0 < len(payload) <= 65507:
                raise ValueError("Invalid captured UDP payload length")
            sock.sendto(payload, destination)
            count += 1
    return count
