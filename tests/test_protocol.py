from dataclasses import replace
import math

import pytest

from uav_telemetry.mavlink.codec import Decoder, mav
from uav_telemetry.mavlink.source import Encoder
from uav_telemetry.metrics.engine import MetricsEngine
from uav_telemetry.metrics.sequence import SequenceTracker
from uav_telemetry.utils.config import Config, endpoint, load_config


def messages(encoder=None):
    encoder = encoder or Encoder()
    decoder = Decoder()
    return [m for payload in encoder.datagrams(0, 100) for m in decoder.feed(payload, ("127.0.0.1", 1))]


def heartbeat(encoder=None):
    return next(m for m in messages(encoder) if m.get_type() == "HEARTBEAT")


def test_all_six_genuine_message_types():
    decoded = messages()
    assert {m.get_type() for m in decoded} == {"GLOBAL_POSITION_INT", "ATTITUDE", "HEARTBEAT", "SYS_STATUS", "GPS_RAW_INT", "VFR_HUD"}
    assert all(m.get_srcSystem() == 1 and m.get_srcComponent() == 1 for m in decoded)
    assert [m.get_seq() for m in decoded] == list(range(6))


def test_many_frames_per_datagram_and_fragmentation():
    payload = b"".join(Encoder().datagrams(0, 100))
    decoder = Decoder()
    assert decoder.feed(payload[:7], ("a", 1)) == []
    assert len(decoder.feed(payload[7:], ("a", 1))) == 6
    assert decoder.bad_data == 0


def test_crc_rejection_and_resynchronization():
    payload = Encoder().datagrams(0, 100)[0]
    broken = payload[:-1] + bytes([payload[-1] ^ 0x55])
    decoder = Decoder()
    decoded = decoder.feed(b"garbage\x00" + broken + payload, ("a", 1))
    assert decoder.bad_data > 0
    assert decoded[-1].get_type() == "ATTITUDE"
    assert len(decoded) < 6


def test_peer_parser_isolation_and_bounded_cache():
    payload = Encoder().datagrams(0, 100)[0]
    decoder = Decoder(max_peers=2)
    assert decoder.feed(payload[:5], ("a", 1)) == []
    assert len(decoder.feed(payload, ("b", 1))) == 2
    assert len(decoder.feed(payload[5:], ("a", 1))) == 2
    decoder.feed(payload, ("c", 1))
    assert len(decoder.parsers) == 2
    assert decoder.peer_evictions == 1


def test_sequence_wrap_and_duplicates():
    tracker = SequenceTracker()
    for seq in [254, 255, 0, 1, 1]:
        tracker.update(seq)
    assert tracker.received_unique == 4
    assert tracker.duplicates == 1
    assert tracker.snapshot()["missing_estimate"] == 0


def test_sequence_reordering_repairs_pending_gaps():
    tracker = SequenceTracker(window=8)
    for seq in [10, 13, 11, 12, 14]:
        tracker.update(seq)
    assert tracker.snapshot()["missing_estimate"] == 0
    assert tracker.out_of_order == 2


def test_sequence_confirmed_gap_and_epoch_reset():
    tracker = SequenceTracker(window=3)
    for seq in [0, 2, 3, 4, 5]:
        tracker.update(seq)
    assert tracker.confirmed_missing == 1
    tracker.update(1)
    assert tracker.too_old == 1
    tracker.reset()
    tracker.update(200)
    assert tracker.epochs == 1
    assert tracker.snapshot()["missing_estimate"] == 1


def test_heartbeat_timeout_and_reconnect():
    engine = MetricsEngine(Config(), start=0)
    engine.ingest(heartbeat(), 0, ("a", 1))
    assert engine.tick(0.5) == "CONNECTED"
    assert engine.tick(1.0) == "DEGRADED"
    assert engine.tick(2.0) == "DISCONNECTED"
    engine.ingest(heartbeat(), 2.1, ("a", 1))
    assert engine.state == "CONNECTED"
    assert engine.reconnections == 1


def test_absent_message_types_are_unavailable_not_missing():
    engine = MetricsEngine(Config(), start=0)
    encoder = Encoder()
    for i in range(5):
        message = encoder.mav.heartbeat_encode(2, 12, 0, 0, 3)
        decoded = Decoder().feed(encoder.pack(message), ("a", 1))[0]
        engine.ingest(decoded, i / 10, ("a", 1))
    snapshot = engine.snapshot(0.5)
    assert snapshot["state"] == "CONNECTED"
    assert snapshot["telemetry"]["latitude_deg"] is None
    assert snapshot["freshness_s"]["GLOBAL_POSITION_INT"] is None
    assert next(iter(snapshot["sources"].values()))["missing_estimate"] == 0


def test_other_sources_do_not_keep_target_heartbeat_alive():
    engine = MetricsEngine(Config(), start=0)
    engine.ingest(heartbeat(), 0, ("a", 1))
    engine.ingest(heartbeat(Encoder(2, 1)), 2.0, ("a", 1))
    assert engine.tick(2.0) == "DISCONNECTED"
    assert len(engine.sources) == 2
    assert engine.message_counts["HEARTBEAT"] == 2


def test_decoded_units_and_unavailable_sentinels():
    engine = MetricsEngine(Config(), start=0)
    for message in messages():
        engine.ingest(message, 1, ("a", 1))
    values = engine.snapshot(1)["telemetry"]
    assert values["latitude_deg"] == pytest.approx(25.53)
    assert values["altitude_m_msl"] == 85
    assert values["battery_voltage_v"] == pytest.approx(11.8)
    encoder = Encoder()
    status = encoder.mav.sys_status_encode(0, 0, 0, 0, 65535, -1, -1, 0, 0, 0, 0, 0, 0)
    decoded = Decoder().feed(encoder.pack(status), ("a", 1))[0]
    engine.ingest(decoded, 2, ("a", 1))
    assert engine.telemetry["battery_voltage_v"] is None
    assert engine.telemetry["battery_remaining_pct"] is None
    assert engine.telemetry["battery_current_a"] is None


def test_nan_attitude_is_not_fresh_valid_data():
    encoder = Encoder()
    attitude = encoder.mav.attitude_encode(0, float("nan"), 0, 0, 0, 0, 0)
    engine = MetricsEngine(Config(), start=0)
    engine.ingest(Decoder().feed(encoder.pack(attitude), ("a", 1))[0], 1, ("a", 1))
    assert engine.telemetry["roll_deg"] is None
    assert "ATTITUDE" not in engine.last_valid
    assert engine.invalid_telemetry_messages == 1


def test_out_of_range_battery_and_heading_are_unavailable():
    encoder = Encoder()
    status = encoder.mav.sys_status_encode(0, 0, 0, 0, 11000, 0, 127, 0, 0, 0, 0, 0, 0)
    position = encoder.mav.global_position_int_encode(0, 0, 0, 0, 0, 0, 0, 0, 50000)
    engine = MetricsEngine(Config(), start=0)
    decoder = Decoder()
    for message in decoder.feed(encoder.pack(status) + encoder.pack(position), ("a", 1)):
        engine.ingest(message, 1, ("a", 1))
    assert engine.telemetry["battery_remaining_pct"] is None
    assert engine.telemetry["heading_deg"] is None


@pytest.mark.parametrize("changes", [{"loss_probability": -0.1}, {"loss_probability": 1.1},
    {"delay_ms": -1}, {"source_rate_hz": 0}, {"jitter_ms": math.nan}, {"sequence_window": 128},
    {"heartbeat_warn_s": 2}, {"proxy_bind": "invalid"}, {"source_mode": "real-drone"},
    {"reorder_probability": 0.3}, {"target_system": 0}, {"seed": -1}, {"max_pending": 0}])
def test_invalid_configuration(changes):
    with pytest.raises(ValueError):
        replace(Config(), **changes)


def test_yaml_and_unknown_keys(tmp_path):
    assert load_config("configs/baseline.yaml").source_mode == "synthetic"
    path = tmp_path / "bad.yaml"
    path.write_text("unknown: 1")
    with pytest.raises(ValueError):
        load_config(path)
    assert endpoint("127.0.0.1:0") == ("127.0.0.1", 0)
