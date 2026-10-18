from collections import Counter, deque
import math
import threading
import time

from .sequence import SequenceTracker
from uav_telemetry.utils.config import Config


def finite(value, scale: float = 1.0, sentinel=None) -> float | None:
    if value == sentinel:
        return None
    try:
        result = float(value) / scale
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class MetricsEngine:
    """Read-only decoded telemetry. Local durations always use monotonic clocks."""
    def __init__(self, config: Config, start: float | None = None):
        self.config = config
        self.start = time.monotonic() if start is None else start
        self.lock = threading.RLock()
        self.message_counts = Counter()
        self.sources = {}
        self.last_valid = {}
        self.vehicle_timestamps = {}
        self.last_any = None
        self.last_heartbeat = None
        self.telemetry = {key: None for key in (
            "latitude_deg", "longitude_deg", "altitude_m_msl", "relative_altitude_m", "roll_deg",
            "pitch_deg", "yaw_deg", "heading_deg", "ground_speed_m_s", "battery_voltage_v",
            "battery_remaining_pct", "battery_current_a", "gps_fix_type", "satellites", "vehicle_state")}
        self.state = "WAITING"
        self.events = deque(maxlen=200)
        self.history = deque(maxlen=600)
        self.heartbeat_arrivals = deque(maxlen=100000)
        self.reconnections = 0
        self.invalid_telemetry_messages = 0
        self.datagrams = 0
        self.bytes = 0
        self.arrival_intervals = deque(maxlen=10000)
        self.last_datagram = None

    def datagram(self, payload: bytes, now: float) -> None:
        with self.lock:
            self.datagrams += 1
            self.bytes += len(payload)
            if self.last_datagram is not None:
                self.arrival_intervals.append(now - self.last_datagram)
            self.last_datagram = now

    def ingest(self, message, now: float, peer: tuple) -> None:
        with self.lock:
            key = f"{peer[0]}:{peer[1]}/{message.get_srcSystem()}:{message.get_srcComponent()}"
            if key not in self.sources:
                if len(self.sources) >= 128:
                    oldest = min(self.sources, key=lambda k: self.sources[k]["last"])
                    del self.sources[oldest]
                self.sources[key] = {"tracker": SequenceTracker(self.config.sequence_window), "last": now, "messages": 0}
            source = self.sources[key]
            if now - source["last"] > self.config.sequence_reset_s:
                source["tracker"].reset()
            source["tracker"].update(message.get_seq())
            source["last"] = now
            source["messages"] += 1
            kind = message.get_type()
            self.message_counts[kind] += 1
            if (message.get_srcSystem(), message.get_srcComponent()) != (self.config.target_system, self.config.target_component):
                return
            self.last_any = now
            for field in ("time_boot_ms", "time_usec"):
                if hasattr(message, field):
                    self.vehicle_timestamps[kind] = {"field": field, "value": int(getattr(message, field)),
                        "basis": "Vehicle-provided clock; not assumed comparable to local monotonic time"}
            valid = False
            if kind == "HEARTBEAT":
                self.last_heartbeat = now
                self.heartbeat_arrivals.append(now)
                self.telemetry["vehicle_state"] = int(message.system_status)
                valid = True
            elif kind == "GLOBAL_POSITION_INT":
                lat, lon = message.lat / 1e7, message.lon / 1e7
                valid = -90 <= lat <= 90 and -180 <= lon <= 180
                self.telemetry.update(latitude_deg=lat if valid else None, longitude_deg=lon if valid else None,
                    altitude_m_msl=finite(message.alt, 1000), relative_altitude_m=finite(message.relative_alt, 1000),
                    heading_deg=finite(message.hdg, 100) if message.hdg <= 36000 else None,
                    ground_speed_m_s=math.hypot(message.vx, message.vy) / 100)
            elif kind == "ATTITUDE":
                values = [finite(v, math.pi / 180) for v in (message.roll, message.pitch, message.yaw)]
                valid = all(v is not None for v in values)
                self.telemetry.update(zip(("roll_deg", "pitch_deg", "yaw_deg"), values))
            elif kind == "SYS_STATUS":
                self.telemetry.update(battery_voltage_v=finite(message.voltage_battery, 1000, 65535),
                    battery_remaining_pct=finite(message.battery_remaining) if 0 <= message.battery_remaining <= 100 else None,
                    battery_current_a=finite(message.current_battery, 100, -1))
                valid = self.telemetry["battery_voltage_v"] is not None
            elif kind == "GPS_RAW_INT":
                self.telemetry.update(gps_fix_type=int(message.fix_type), satellites=finite(message.satellites_visible, 1, 255))
                valid = message.fix_type >= 2
            elif kind == "VFR_HUD":
                speed = finite(message.groundspeed)
                heading = finite(message.heading)
                self.telemetry.update(ground_speed_m_s=speed if speed is not None and speed >= 0 else None,
                                      heading_deg=heading if heading is not None and 0 <= heading <= 360 else None)
                valid = speed is not None and speed >= 0
            if valid:
                self.last_valid[kind] = now
            elif kind in ("GLOBAL_POSITION_INT", "ATTITUDE", "SYS_STATUS", "GPS_RAW_INT", "VFR_HUD"):
                self.invalid_telemetry_messages += 1
            self.tick(now)

    def tick(self, now: float) -> str:
        with self.lock:
            if self.last_any is None:
                state = "WAITING"
            elif self.last_heartbeat is None:
                state = "NO_HEARTBEAT" if now - self.last_any <= self.config.inactivity_timeout_s else "DISCONNECTED"
            elif now - self.last_heartbeat > self.config.heartbeat_timeout_s or now - self.last_any > self.config.inactivity_timeout_s:
                state = "DISCONNECTED"
            elif now - self.last_heartbeat > self.config.heartbeat_warn_s:
                state = "DEGRADED"
            else:
                state = "CONNECTED"
            if state != self.state:
                if self.state == "DISCONNECTED" and state in ("CONNECTED", "DEGRADED"):
                    self.reconnections += 1
                self.events.append({"elapsed_s": now - self.start, "from": self.state, "to": state})
                self.state = state
            return state

    def snapshot(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self.lock:
            self.tick(now)
            age = lambda value: None if value is None else max(0, now - value)
            intervals = list(self.arrival_intervals)
            mean_interval = sum(intervals) / len(intervals) if intervals else None
            variation = (sum((v - mean_interval)**2 for v in intervals) / len(intervals))**0.5 if intervals else None
            return {"schema_version": 1, "source_mode": self.config.source_mode, "state": self.state,
                "observation_start_monotonic_s": self.start,
                "elapsed_s": now - self.start, "snapshot_monotonic_s": now,
                "target_system": self.config.target_system, "target_component": self.config.target_component,
                "telemetry": self.telemetry.copy(), "heartbeat_age_s": age(self.last_heartbeat),
                "vehicle_timestamps": dict(self.vehicle_timestamps),
                "freshness_s": {name: age(self.last_valid.get(name)) for name in ("GLOBAL_POSITION_INT", "ATTITUDE", "HEARTBEAT", "SYS_STATUS", "GPS_RAW_INT", "VFR_HUD")},
                "received_datagrams": self.datagrams, "received_bytes": self.bytes,
                "message_counts": dict(self.message_counts), "received_messages": sum(self.message_counts.values()),
                "sources": {k: {"messages": v["messages"], **v["tracker"].snapshot()} for k, v in self.sources.items()},
                "arrival_interval_mean_ms": None if mean_interval is None else 1000 * mean_interval,
                "arrival_interval_std_ms": None if variation is None else 1000 * variation,
                "reconnections": self.reconnections, "invalid_telemetry_messages": self.invalid_telemetry_messages,
                "events": list(self.events), "history": list(self.history),
                "matched_datagram_pdr": None, "one_way_latency_ms": None,
                "measurement_note": "Live arrival variation is not network delay jitter; PDR requires a drained, matched observation window."}
