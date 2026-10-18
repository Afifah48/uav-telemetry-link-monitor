from datetime import datetime, timezone
from pathlib import Path
import json
import threading
import time


def atomic_json(path: Path, value: dict) -> None:
    """Atomic replace prevents the HTTP dashboard reading half a JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    # Windows readers/antivirus can briefly deny replacement of an open file.
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(0.005 * 2**attempt, 0.05))


class EventLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8") if path else None

    def emit(self, event: str, **fields) -> None:
        entry = {"event": event, "monotonic_s": time.monotonic(),
                 "utc": datetime.now(timezone.utc).isoformat(), **fields}
        if self.file:
            with self.lock:
                self.file.write(json.dumps(entry, allow_nan=False) + "\n")
                self.file.flush()

    def close(self) -> None:
        if self.file:
            self.file.close()
