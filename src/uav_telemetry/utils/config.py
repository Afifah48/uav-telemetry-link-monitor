from dataclasses import dataclass, fields
from pathlib import Path
import math
import yaml


def endpoint(value: str) -> tuple[str, int]:
    """Parse an IPv4/hostname:port endpoint; no hidden port defaults."""
    try:
        host, port = value.rsplit(":", 1)
        port = int(port)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Endpoint must be host:port") from exc
    if not host or not 0 <= port <= 65535:
        raise ValueError("Invalid UDP endpoint")
    return host, port


@dataclass(frozen=True)
class Config:
    proxy_bind: str = "127.0.0.1:14600"
    receiver_bind: str = "127.0.0.1:14601"
    loss_probability: float = 0.0
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    reorder_probability: float = 0.0
    reorder_extra_ms: float = 150.0
    allow_reordering: bool = False
    outage_start_s: float = 3.0
    outage_duration_s: float = 0.0
    heartbeat_warn_s: float = 0.8
    heartbeat_timeout_s: float = 1.5
    inactivity_timeout_s: float = 2.0
    sequence_reset_s: float = 2.0
    sequence_window: int = 64
    target_system: int = 1
    target_component: int = 1
    source_rate_hz: float = 100.0
    duration_s: float = 10.0
    seed: int = 48
    max_pending: int = 10000
    source_mode: str = "synthetic"

    def __post_init__(self) -> None:
        endpoint(self.proxy_bind)
        endpoint(self.receiver_bind)
        names = ("loss_probability", "delay_ms", "jitter_ms", "reorder_probability", "reorder_extra_ms",
                 "outage_start_s", "outage_duration_s", "heartbeat_warn_s", "heartbeat_timeout_s",
                 "inactivity_timeout_s", "sequence_reset_s", "source_rate_hz", "duration_s")
        if any(not isinstance(getattr(self, n), (float, int)) or not math.isfinite(getattr(self, n))
               or getattr(self, n) < 0 for n in names):
            raise ValueError("Timing/rate/probability settings must be finite and nonnegative")
        if not 0 <= self.loss_probability <= 1 or not 0 <= self.reorder_probability <= 1:
            raise ValueError("Probabilities must be in [0,1]")
        if not 0 < self.heartbeat_warn_s < self.heartbeat_timeout_s or self.inactivity_timeout_s <= 0:
            raise ValueError("Require 0 < warning < heartbeat timeout and positive inactivity timeout")
        if self.sequence_reset_s <= 0 or not 0 < self.source_rate_hz <= 1000:
            raise ValueError("Positive reset time and source rate in (0,1000] required")
        for name in ("sequence_window", "target_system", "target_component", "seed", "max_pending"):
            if not isinstance(getattr(self, name), int) or isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be an integer")
        if not 1 <= self.sequence_window < 128 or not 1 <= self.target_system <= 255 or not 1 <= self.target_component <= 255:
            raise ValueError("Sequence window must be 1..127; source IDs must be 1..255")
        if self.seed < 0 or self.max_pending < 1 or self.source_mode not in ("synthetic", "replay", "px4"):
            raise ValueError("Invalid seed, queue bound or source mode")
        if not isinstance(self.allow_reordering, bool):
            raise ValueError("allow_reordering must be boolean")
        if self.reorder_probability and not self.allow_reordering:
            raise ValueError("Enable allow_reordering when requesting reordering")


def load_config(path: str | Path) -> Config:
    values = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) - {f.name for f in fields(Config)}:
        raise ValueError("Expected a mapping of known configuration keys")
    return Config(**values)
