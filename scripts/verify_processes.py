"""Verify the documented source/proxy/receiver commands as separate processes."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys

import yaml


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/process-check"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    proxy_port, receiver_port = free_port(), free_port()
    while receiver_port == proxy_port:
        receiver_port = free_port()
    config = args.output / "config.yaml"
    config.write_text(yaml.safe_dump({"proxy_bind": f"127.0.0.1:{proxy_port}", "receiver_bind": f"127.0.0.1:{receiver_port}", "duration_s": 2}), encoding="utf-8")
    prefix = [sys.executable, "-m", "uav_telemetry.cli"]
    snapshot = args.output / "snapshot.json"
    receiver = subprocess.Popen(prefix + ["receive", "--config", str(config), "--snapshot", str(snapshot), "--output", str(args.output / "receiver")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proxy = None
    try:
        assert "receiver bound" in receiver.stdout.readline(), "Receiver did not start"
        proxy = subprocess.Popen(prefix + ["proxy", "--config", str(config), "--duration", "1.5", "--output", str(args.output / "proxy")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert "Proxy" in proxy.stdout.readline(), "Proxy did not start"
        source = subprocess.run(prefix + ["source", "--endpoint", f"127.0.0.1:{proxy_port}", "--duration", "0.8", "--output", str(args.output / "source")], capture_output=True, text=True, timeout=8, check=True)
        sent = json.loads(source.stdout)["sent_datagrams"]
        proxy.communicate(timeout=8)
        receiver.communicate(timeout=8)
        assert proxy.returncode == receiver.returncode == 0
        received = json.loads(snapshot.read_text())
        forwarded = json.loads((args.output / "proxy" / "proxy_stats.json").read_text())
        assert sent == forwarded["input"] == forwarded["forwarded"] == received["received_datagrams"]
        assert received["parser_bad_data_events"] == 0
        result = {"sent_datagrams": sent, "forwarded_datagrams": forwarded["forwarded"],
                  "received_datagrams": received["received_datagrams"], "decoded_messages": received["received_messages"],
                  "parser_bad_data_events": received["parser_bad_data_events"], "separate_processes_verified": True}
        (args.output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        for process in (proxy, receiver):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
