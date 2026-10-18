import argparse
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

from uav_telemetry.utils.config import Config, endpoint, load_config
from uav_telemetry.utils.events import EventLog, atomic_json


def wait_for_duration(duration: float, stop: threading.Event) -> None:
    start = time.monotonic()
    try:
        while not stop.wait(0.1) and (duration == 0 or time.monotonic() - start < duration):
            pass
    except KeyboardInterrupt:
        stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MAVLink telemetry and UDP reliability experiments")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "experiments", "source", "proxy", "receive", "dashboard", "replay", "rf"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path)
        sub.add_argument("--output", type=Path, default=Path("results/demo"))
        sub.add_argument("--duration", type=float)
        if name in ("demo", "dashboard"):
            sub.add_argument("--port", type=int, default=8765)
        if name in ("source", "replay"):
            sub.add_argument("--endpoint", default="127.0.0.1:14600")
        if name in ("receive", "proxy"):
            sub.add_argument("--bind")
        if name in ("receive", "dashboard"):
            sub.add_argument("--snapshot", type=Path, default=Path("results/live/snapshot.json"))
        if name == "dashboard":
            sub.add_argument("--report", type=Path)
        if name == "receive":
            sub.add_argument("--mode", choices=("synthetic", "replay", "px4"))
        if name == "replay":
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--speed", type=float, default=1)
    args = parser.parse_args()
    try:
        config = load_config(args.config) if args.config else Config()
        if args.duration is not None:
            config = replace(config, duration_s=args.duration)
        args.output.mkdir(parents=True, exist_ok=True)
        if args.command in ("demo", "experiments"):
            from uav_telemetry.experiments import run_experiment, run_suite
            if args.command == "demo":
                run_experiment(config, args.output, args.port)
            else:
                run_suite(config, args.output)
        elif args.command == "source":
            from uav_telemetry.mavlink.source import synthetic_source
            log = EventLog(args.output / "source.jsonl")
            try:
                records = synthetic_source(endpoint(args.endpoint), config.duration_s, config.source_rate_hz, threading.Event(), log,
                                           system=config.target_system, component=config.target_component)
                print(json.dumps({"sent_datagrams": len(records), "source_mode": "synthetic"}))
            finally:
                log.close()
        elif args.command == "replay":
            from uav_telemetry.mavlink.source import replay
            print(json.dumps({"sent_datagrams": replay(args.input, endpoint(args.endpoint), args.speed), "source_mode": "replay"}))
        elif args.command == "proxy":
            from uav_telemetry.impairments.proxy import UDPProxy
            log = EventLog(args.output / "proxy.jsonl")
            proxy = UDPProxy(endpoint(args.bind or config.proxy_bind), endpoint(config.receiver_bind), config, log)
            try:
                proxy.start()
                print(f"Proxy {proxy.address} -> {proxy.destination}", flush=True)
                wait_for_duration(config.duration_s, proxy.stop_event)
            finally:
                proxy.close()
                atomic_json(args.output / "proxy_stats.json", proxy.snapshot())
                log.close()
        elif args.command == "receive":
            from uav_telemetry.transport.receiver import Receiver
            if args.mode:
                config = replace(config, source_mode=args.mode)
            log = EventLog(args.output / "receiver.jsonl")
            receiver = Receiver(endpoint(args.bind or config.receiver_bind), config, log, args.snapshot)
            try:
                receiver.start()
                print(f"Read-only receiver bound to {receiver.address}", flush=True)
                wait_for_duration(config.duration_s, receiver.stop_event)
            finally:
                receiver.close()
                atomic_json(args.snapshot, receiver.snapshot())
                log.close()
        elif args.command == "dashboard":
            from uav_telemetry.dashboard.server import start_server
            server = start_server(args.snapshot, args.report, args.port)
            print(f"Dashboard: http://127.0.0.1:{server.server_port}", flush=True)
            try:
                wait_for_duration(config.duration_s if args.duration is not None else 0, threading.Event())
            finally:
                server.shutdown()
                server.server_close()
        else:
            from uav_telemetry.rf_analysis.budget import generate
            generate(args.output)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"telemetry-monitor: {exc}\n")


if __name__ == "__main__":
    main()
