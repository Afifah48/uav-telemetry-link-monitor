# Validation record

Local validation date: 2026-10-18 (Asia/Calcutta). Python 3.13.5, Windows, isolated project `.venv`; pymavlink 2.4.50. Exact tested dependencies are in `requirements-lock.txt`.

## Executed commands

```text
python -m pip install -e ".[dev]" --no-build-isolation
python -m pytest -q --cov=uav_telemetry --cov-report=term-missing --cov-report=json:results/coverage.json --junitxml=results/tests.xml
telemetry-monitor experiments --config configs/baseline.yaml --output results/experiments
telemetry-monitor rf --output results/rf
telemetry-monitor demo --config configs/degraded.yaml --duration 12 --output results/demo --port 8766
telemetry-monitor dashboard --snapshot results/demo/snapshot.json --report results/demo/report.json --port 8766
python scripts/verify_processes.py --output results/process-check
python -m pip wheel --no-deps --no-build-isolation --no-cache-dir --wheel-dir .venv/wheelhouse .
python -m pip check
```

The wheel was installed into a separate target directory and its dashboard resource checked. Tests and the experiment suite execute the production source/proxy/receiver modules, including genuine UDP sockets and pymavlink frames. The dashboard was inspected in a browser while traffic arrived and while displaying a completed capture; screenshots are saved in `docs/images/`.

## Tests and coverage

Final local result: 48 passed, zero failed, one skipped. The skipped test is the explicitly opt-in PX4 integration check, because PX4 SITL/Gazebo was not available. Approximately 86% statement coverage is reported over the full Python package, including CLI and plotting code. Untested plotting branches are separately executed by the numerical suite, not hidden from the coverage denominator. JUnit/coverage artifacts retain exact results.

The default unit/integration suite checks heartbeat reception, timeout/reconnect, data sentinels and nonfinite values; missing types versus actual sequence gaps; modulo wrapping and late gap repair; source/peer separation; malformed payload resynchronization; deterministic Bernoulli decisions; actual UDP forwarding and inferred/ground-truth counts; reordering; outage recovery; source and receiver replay formats; sustained reception; dashboard report-window matching; atomic snapshot sharing retries and display-I/O failure isolation; RF units/margins; configuration validation; and standalone CLI entry points.

## Measured runs

The five ten-second scenarios have 1,150 source/ingress datagrams each. All forwarded datagrams were matched at the receiver in the completed local runs. Moderate loss dropped 59 datagrams; severe loss dropped 221. The three-second outage dropped 345 and was detected; the next valid heartbeat arrived 0.0183766 seconds after scheduled restoration. Reordering reversed 429 datagram departures, while the receiver counted 830 out-of-order protocol frames and zero final inferred missing frames. The five scenarios decoded 9,890 MAVLink messages in total.

The separate-process run verified 92 sent/forwarded/received datagrams and 178 decoded messages with no BAD_DATA events. The twelve-second demo decoded 2,525 messages from 1,310 received datagrams. A separate 90-second live run decoded 18,987 messages from 9,837 received datagrams without parser errors after the snapshot-write fix; its temporary high-volume logs are not part of the checked-in benchmark artifacts.

## Issue found and corrected during real UI validation

The first live dashboard run exposed a Windows sharing violation when replacing a snapshot file during a read. Reception stopped, and the downstream closed UDP endpoint also surfaced as a proxy failure. That run is not claimed as successful and is excluded from committed performance artifacts.

Atomic writes now retry transient sharing failures. Persistent snapshot I/O failure is logged/counted without terminating reception. Regression tests inject both failure cases; a subsequent live run completed successfully while the browser polled snapshots. The dashboard also validates the observation-start token before incorporating a completed report, preventing a previous run's PDR from appearing in a new capture.

## Not validated

PX4/Gazebo integration, aircraft hardware, radio coverage, MAVLink signing/custom dialects, remote synchronized-clock latency and operational flight reliability were not tested. No source-to-aircraft command path exists. GitHub Actions is configured for Linux/Python 3.11 and 3.13; hosted execution status is separate from this local record.
