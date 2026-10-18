"""End-to-end measurements through real loopback UDP sockets and production modules."""
from collections import defaultdict, deque
from dataclasses import asdict, replace
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import platform
import threading
import time

import numpy as np

from uav_telemetry.impairments.proxy import UDPProxy
from uav_telemetry.mavlink.source import synthetic_source
from uav_telemetry.transport.receiver import Receiver
from uav_telemetry.utils.config import Config
from uav_telemetry.utils.events import EventLog, atomic_json


def distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"samples": len(values), "mean": float(np.mean(values)), "p50": float(np.median(values)),
            "p95": float(np.percentile(values, 95)), "std": float(np.std(values)),
            "min": float(min(values)), "max": float(max(values))}


def build_report(config: Config, sent: list[dict], proxy: UDPProxy, receiver: Receiver,
                 start: float, completed: float) -> tuple[dict, list[dict]]:
    inputs = list(proxy.records)
    forwarded = defaultdict(deque)
    for row in inputs:
        if "forwarded_monotonic_s" in row:
            forwarded[row["sha256"]].append(row)
    received = {}
    for row in receiver.records:
        queue = forwarded[row["sha256"]]
        if queue:
            received[queue.popleft()["id"]] = row["received_monotonic_s"]
    source_hashes = defaultdict(list)
    for row in sent:
        source_hashes[row["sha256"]].append(row["sent_monotonic_s"])
    unique = all(len(values) == 1 for values in source_hashes.values())
    delays, residence, rows = [], [], []
    for entry in inputs:
        row = dict(entry)
        arrival = received.get(row["id"])
        row["receiver_monotonic_s"] = arrival
        row["received"] = arrival is not None
        row["source_to_receiver_ms"] = None
        if arrival is not None and unique and row["sha256"] in source_hashes:
            value = (arrival - source_hashes[row["sha256"]][0]) * 1000
            delays.append(value)
            row["source_to_receiver_ms"] = value
        if "observed_proxy_residence_ms" in row:
            residence.append(row["observed_proxy_residence_ms"])
        rows.append(row)
    snapshot = receiver.snapshot(completed)
    recovery = None
    if config.outage_duration_s:
        restoration = start + config.outage_start_s + config.outage_duration_s
        after = [t for t in receiver.engine.heartbeat_arrivals if t >= restoration]
        recovery = {"restoration_elapsed_s": restoration - start,
                    "definition": "First valid selected-source HEARTBEAT arrival after scheduled link restoration",
                    "first_heartbeat_recovery_s": min(after) - restoration if after else None,
                    "disconnection_detected": any(e["to"] == "DISCONNECTED" and config.outage_start_s <= e["elapsed_s"] <= config.outage_start_s + config.outage_duration_s + config.heartbeat_timeout_s for e in snapshot["events"])}
    counters = proxy.snapshot()
    report = {"schema_version": 1, "source_mode": "synthetic", "config": asdict(config),
        "utc_completed": datetime.now(timezone.utc).isoformat(), "python_version": platform.python_version(),
        "observation_start_monotonic_s": start, "drained_end_monotonic_s": completed,
        "source_sent_datagrams": len(sent), "proxy": counters,
        "receiver_datagrams": snapshot["received_datagrams"], "matched_received_datagrams": len(received),
        "matched_datagram_pdr": len(received) / counters["input"] if counters["input"] else None,
        "proxy_forwarding_ratio": counters["forwarded"] / counters["input"] if counters["input"] else None,
        "measurement_population": "All proxy-ingress datagrams in this isolated run, matched byte-for-byte at the receiver after drain",
        "local_same_clock_one_way_ms": distribution(delays), "proxy_residence_ms": distribution(residence),
        "delay_variation_definition": "Population standard deviation of matched local one-way delays; not arrival-interval variation",
        "clock_note": "Source and receiver threads share this process's monotonic clock; not remote-vehicle one-way latency",
        "recovery": recovery, "receiver": snapshot,
        "capture_complete": len(inputs) == counters["input"] and len(receiver.records) == snapshot["received_datagrams"]}
    snapshot["matched_datagram_pdr"] = report["matched_datagram_pdr"]
    snapshot["one_way_latency_ms"] = report["local_same_clock_one_way_ms"]
    snapshot["capture_complete"] = True
    report["receiver"] = snapshot
    if counters["pending"] or counters["input"] != counters["forwarded"] + counters["dropped"]:
        raise RuntimeError("Experiment ended before the proxy drained")
    if not report["capture_complete"]:
        raise RuntimeError("Bounded in-memory event capture overflowed; shorten experiment or process JSONL offline")
    return report, rows


def run_experiment(config: Config, output: Path, dashboard_port: int | None = None) -> dict:
    if config.duration_s <= 0 or config.source_mode != "synthetic":
        raise ValueError("Controlled experiments require finite duration and synthetic source")
    output.mkdir(parents=True, exist_ok=True)
    logs = [EventLog(output / name) for name in ("source.jsonl", "proxy.jsonl", "receiver.jsonl")]
    start = time.monotonic()
    receiver = Receiver(("127.0.0.1", 0), config, logs[2], output / "snapshot.json", start)
    proxy = UDPProxy(("127.0.0.1", 0), receiver.address, config, logs[1], start)
    server = None
    stop = threading.Event()
    try:
        receiver.start()
        proxy.start()
        if dashboard_port is not None:
            from uav_telemetry.dashboard.server import start_server
            server = start_server(output / "snapshot.json", output / "report.json", dashboard_port)
            print(f"Dashboard: http://127.0.0.1:{server.server_port}", flush=True)
        sent = synthetic_source(proxy.address, config.duration_s, config.source_rate_hz, stop, logs[0], start,
                                config.target_system, config.target_component)
        # Keep ingress alive briefly, then wait for the scheduled datagram population.
        time.sleep(0.08)
        deadline = time.monotonic() + max(2, (config.delay_ms + config.jitter_ms + config.reorder_extra_ms) / 1000 + 1)
        while proxy.snapshot()["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
        proxy.close()
        time.sleep(0.08)
        receiver.close()
        completed = time.monotonic()
        report, rows = build_report(config, sent, proxy, receiver, start, completed)
        atomic_json(output / "report.json", report)
        atomic_json(output / "snapshot.json", report["receiver"])
        fields = sorted(set().union(*(row.keys() for row in rows))) if rows else ["id"]
        with (output / "datagrams.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps({"output": str(output), "input": report["proxy"]["input"],
                          "received": report["matched_received_datagrams"], "pdr": report["matched_datagram_pdr"],
                          "messages": report["receiver"]["received_messages"], "recovery": report["recovery"]}), flush=True)
        return report
    finally:
        stop.set()
        proxy.close()
        receiver.close()
        if server:
            server.shutdown()
            server.server_close()
        for log in logs:
            log.close()


def scenarios(base: Config) -> dict[str, Config]:
    return {
        "nominal": replace(base, loss_probability=0, delay_ms=0, jitter_ms=0, outage_duration_s=0, reorder_probability=0, allow_reordering=False),
        "moderate": replace(base, loss_probability=0.05, delay_ms=40, jitter_ms=0, outage_duration_s=0, reorder_probability=0, allow_reordering=False),
        "severe": replace(base, loss_probability=0.20, delay_ms=80, jitter_ms=35, outage_duration_s=0, reorder_probability=0, allow_reordering=False),
        "outage": replace(base, loss_probability=0, delay_ms=0, jitter_ms=0, outage_start_s=base.duration_s * 0.3,
                          outage_duration_s=base.duration_s * 0.3, reorder_probability=0, allow_reordering=False),
        "reordering": replace(base, loss_probability=0, delay_ms=15, jitter_ms=10, allow_reordering=True, reorder_probability=0.25, reorder_extra_ms=150, outage_duration_s=0),
    }


def run_suite(base: Config, output: Path) -> dict:
    results = {name: run_experiment(config, output / name) for name, config in scenarios(base).items()}
    atomic_json(output / "summary.json", results)
    from uav_telemetry.plots import plot_results
    plot_results(results, output)
    return results
