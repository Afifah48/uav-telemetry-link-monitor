from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import threading
import time


def start_server(snapshot: Path, report: Path | None = None, port: int = 8765) -> ThreadingHTTPServer:
    """Serve an atomic receiver snapshot; bind only to loopback."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                payload = files("uav_telemetry.dashboard").joinpath("index.html").read_bytes()
                content_type = "text/html; charset=utf-8"
            elif self.path == "/api/snapshot":
                try:
                    value = json.loads(snapshot.read_text(encoding="utf-8"))
                    value["snapshot_age_s"] = max(0, time.monotonic() - value["snapshot_monotonic_s"])
                    if report and report.exists():
                        measured = json.loads(report.read_text(encoding="utf-8"))
                        if value.get("observation_start_monotonic_s") is not None and measured.get("observation_start_monotonic_s") == value["observation_start_monotonic_s"]:
                            value["matched_datagram_pdr"] = measured.get("matched_datagram_pdr")
                            value["one_way_latency_ms"] = measured.get("local_same_clock_one_way_ms")
                            value["recovery"] = measured.get("recovery")
                            value["proxy"] = measured.get("proxy")
                except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
                    value = {"state": "WAITING", "source_mode": "Unavailable", "error": "No complete receiver snapshot available"}
                payload = json.dumps(value, allow_nan=False).encode()
                content_type = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="read-only-dashboard")
    thread.start()
    return server
