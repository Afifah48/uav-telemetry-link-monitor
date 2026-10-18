from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.request import urlopen

import pytest

from uav_telemetry.dashboard.server import start_server
from uav_telemetry.experiments import run_experiment
from uav_telemetry.impairments.proxy import Scheduler, UDPProxy
from uav_telemetry.mavlink.source import Encoder, replay
from uav_telemetry.rf_analysis.budget import LinkBudget
from uav_telemetry.transport.receiver import Receiver
from uav_telemetry.utils.config import Config, endpoint
from uav_telemetry.utils.events import EventLog
from uav_telemetry.utils.events import atomic_json


def test_deterministic_loss_and_statistical_expectation():
    config = replace(Config(), loss_probability=0.2, delay_ms=40)
    first, second = Scheduler(config), Scheduler(config)
    a = [first.schedule(i / 100) for i in range(10000)]
    b = [second.schedule(i / 100) for i in range(10000)]
    assert a == b
    count = sum(reason == "random_loss" for _, reason in a)
    assert abs(count - 2000) < 6 * (10000 * 0.2 * 0.8)**0.5


def test_outage_boundaries():
    scheduler = Scheduler(replace(Config(), outage_start_s=1, outage_duration_s=2))
    assert scheduler.schedule(0.999)[1] is None
    assert scheduler.schedule(1)[1] == "outage"
    assert scheduler.schedule(2.999)[1] == "outage"
    assert scheduler.schedule(3)[1] is None


def test_preserve_order_and_optional_reordering():
    preserved = Scheduler(replace(Config(), delay_ms=20, jitter_ms=20))
    times = [preserved.schedule(i / 1000)[0] for i in range(100)]
    assert times == sorted(times)
    reordered = Scheduler(replace(Config(), allow_reordering=True, reorder_probability=0.5, reorder_extra_ms=50))
    times = [reordered.schedule(i / 1000)[0] for i in range(100)]
    assert any(a > b for a, b in zip(times, times[1:]))


def test_real_udp_proxy_forwarding():
    log = EventLog()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
        target.bind(("127.0.0.1", 0))
        target.settimeout(2)
        proxy = UDPProxy(("127.0.0.1", 0), target.getsockname(), Config(), log)
        try:
            proxy.start()
            payload = b"".join(Encoder().datagrams(0, 100))
            source.sendto(payload, proxy.address)
            assert target.recvfrom(65535)[0] == payload
        finally:
            proxy.close()
        assert proxy.snapshot()["input"] == proxy.snapshot()["forwarded"] == 1


def test_nominal_end_to_end(tmp_path):
    report = run_experiment(replace(Config(), duration_s=0.35), tmp_path)
    assert report["source_sent_datagrams"] == report["proxy"]["input"] == report["receiver_datagrams"]
    assert report["matched_datagram_pdr"] == 1
    assert report["receiver"]["received_messages"] > report["receiver_datagrams"]
    assert report["receiver"]["parser_bad_data_events"] == 0
    assert report["local_same_clock_one_way_ms"]["min"] >= 0


def test_loss_matches_proxy_ground_truth(tmp_path):
    report = run_experiment(replace(Config(), duration_s=0.4, loss_probability=0.2, delay_ms=10), tmp_path)
    p = report["proxy"]
    assert p["input"] == p["forwarded"] + p["dropped"]
    assert p["dropped"] > 0
    assert report["receiver_datagrams"] == p["forwarded"]
    assert report["matched_datagram_pdr"] == pytest.approx(p["forwarded"] / p["input"])
    assert report["local_same_clock_one_way_ms"]["min"] >= 9


def test_outage_disconnect_and_recovery(tmp_path):
    config = replace(Config(), duration_s=2.1, outage_start_s=0.45, outage_duration_s=0.8,
                     heartbeat_warn_s=0.25, heartbeat_timeout_s=0.4, inactivity_timeout_s=0.6)
    report = run_experiment(config, tmp_path)
    assert report["recovery"]["disconnection_detected"]
    assert report["receiver"]["reconnections"] >= 1
    assert 0 <= report["recovery"]["first_heartbeat_recovery_s"] < 0.5


def test_reordering_is_repaired_without_false_loss(tmp_path):
    report = run_experiment(replace(Config(), duration_s=0.5, allow_reordering=True,
                                   reorder_probability=0.4, reorder_extra_ms=30), tmp_path)
    assert report["matched_datagram_pdr"] == 1
    assert report["proxy"]["reordered"] > 0
    sources = list(report["receiver"]["sources"].values())
    assert sum(s["out_of_order"] for s in sources) > 0
    assert sum(s["missing_estimate"] for s in sources) == 0


def test_receiver_stability_and_malformed_datagrams():
    receiver = Receiver(("127.0.0.1", 0), Config(), EventLog())
    encoder = Encoder()
    expected = 0
    try:
        receiver.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            source.sendto(b"malformed bytes", receiver.address)
            for i in range(350):
                payloads = encoder.datagrams(i, 1000)
                for payload in payloads:
                    source.sendto(payload, receiver.address)
                    expected += 1
                time.sleep(0.001)
        deadline = time.monotonic() + 2
        while receiver.engine.datagrams < expected + 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        receiver.close()
    assert receiver.engine.datagrams == expected + 1
    assert receiver.decoder.bad_data > 0
    assert receiver.decoder.valid_messages >= 700
    assert receiver.error is None


@pytest.mark.parametrize("capture_name", ["source.jsonl", "receiver.jsonl"])
def test_capture_replay_uses_real_frames(tmp_path, capture_name):
    capture = tmp_path / "capture"
    original = run_experiment(replace(Config(), duration_s=0.12), capture)
    receiver = Receiver(("127.0.0.1", 0), replace(Config(), source_mode="replay"), EventLog())
    try:
        receiver.start()
        count = replay(capture / capture_name, receiver.address, speed=4)
        time.sleep(0.1)
    finally:
        receiver.close()
    assert count == original["source_sent_datagrams"]
    assert receiver.decoder.valid_messages == original["receiver"]["received_messages"]


def test_dashboard_empty_then_measured_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    server = start_server(path, port=0)
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url) as response:
            html = response.read().decode()
            assert "Unavailable" in html and "flight-control" in html
        with urlopen(url + "/api/snapshot") as response:
            assert json.load(response)["state"] == "WAITING"
        path.write_text(json.dumps({"state": "CONNECTED", "snapshot_monotonic_s": time.monotonic()}))
        with urlopen(url + "/api/snapshot") as response:
            assert json.load(response)["state"] == "CONNECTED"
    finally:
        server.shutdown()
        server.server_close()


def test_rf_units_and_margin():
    budget = LinkBudget()
    result = budget.evaluate(1000)
    assert float(result["fspl_db"]) == pytest.approx(100.052, abs=0.001)
    assert float(result["received_dbm"]) == pytest.approx(21 - 100.052, abs=0.001)
    assert float(result["available_margin_db"]) == pytest.approx(10.948, abs=0.001)
    with pytest.raises(ValueError):
        budget.evaluate(0)
    with pytest.raises(ValueError):
        LinkBudget(frequency_hz=-1)


def test_atomic_snapshot_retries_temporary_windows_lock(tmp_path, monkeypatch):
    real_replace = Path.replace
    calls = []
    def transient_replace(path, target):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("Temporary sharing violation")
        return real_replace(path, target)
    monkeypatch.setattr(Path, "replace", transient_replace)
    path = tmp_path / "snapshot.json"
    atomic_json(path, {"state": "CONNECTED"})
    assert json.loads(path.read_text())["state"] == "CONNECTED"
    assert len(calls) == 2


def test_snapshot_failure_does_not_stop_receiver(tmp_path, monkeypatch):
    import uav_telemetry.transport.receiver as receiver_module
    def fail(*args):
        raise PermissionError("Unavailable display file")
    monkeypatch.setattr(receiver_module, "atomic_json", fail)
    receiver = Receiver(("127.0.0.1", 0), Config(), EventLog(), tmp_path / "snapshot.json")
    try:
        receiver.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            for payload in Encoder().datagrams(0, 100):
                source.sendto(payload, receiver.address)
            time.sleep(0.15)
    finally:
        receiver.close()
    assert receiver.snapshot_write_errors > 0
    assert receiver.decoder.valid_messages == 6
    assert receiver.error is None


def test_dashboard_never_mixes_different_measurement_windows(tmp_path):
    snapshot, report = tmp_path / "snapshot.json", tmp_path / "report.json"
    snapshot.write_text(json.dumps({"snapshot_monotonic_s": time.monotonic(), "observation_start_monotonic_s": 1,
                                   "matched_datagram_pdr": None}))
    report.write_text(json.dumps({"observation_start_monotonic_s": 2, "matched_datagram_pdr": 0.123}))
    server = start_server(snapshot, report, port=0)
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/snapshot") as response:
            assert json.load(response)["matched_datagram_pdr"] is None
        report.write_text(json.dumps({"observation_start_monotonic_s": 1, "matched_datagram_pdr": 0.987}))
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/snapshot") as response:
            assert json.load(response)["matched_datagram_pdr"] == 0.987
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.px4
@pytest.mark.skipif(not os.environ.get("UAV_PX4_ENDPOINT"), reason="PX4 SITL not available; set UAV_PX4_ENDPOINT after configuring PX4 output")
def test_live_px4_receive():
    receiver = Receiver(endpoint(os.environ["UAV_PX4_ENDPOINT"]), replace(Config(), source_mode="px4"), EventLog())
    try:
        receiver.start()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not receiver.engine.message_counts.get("HEARTBEAT"):
            time.sleep(0.05)
    finally:
        receiver.close()
    assert receiver.engine.message_counts.get("HEARTBEAT", 0) > 0
    assert receiver.decoder.valid_messages > 0
