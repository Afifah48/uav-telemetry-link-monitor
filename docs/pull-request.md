## Result

Implement a read-only UAV telemetry link monitor with genuine pymavlink MAVLink 2 frames sent through a real UDP impairment proxy and receiver. Include per-source parsing/sequence tracking, telemetry validation, heartbeat freshness/disconnection/recovery, offline replay, a local ground-station dashboard and a separate analytical RF budget.

## Validation

- 48 local tests passed; one optional live PX4 test skipped because SITL was unavailable. Approximately 86% full-package statement coverage.
- Five ten-second scenarios processed 5,750 UDP datagrams. Measured PDR was 100% nominal, 94.87% with 5% configured loss, and 80.78% with 20% loss.
- A three-second outage was detected and recovered to a valid heartbeat 18.4 ms after restoration in the recorded, phase-aligned local run.
- Separate source/proxy/receiver processes verified 92/92 datagram delivery and 178 decoded messages with zero parser BAD_DATA events.
- Saved JSON/CSV/JSONL measurements, four plots, real dashboard screenshots, a replay fixture, test/coverage reports and a tested wheel build.
- GitHub Actions checks Python 3.11/3.13, short full-scenario execution, RF generation and separate-process verification.

## Measurement boundaries

UDP datagram ground truth is separate from MAVLink sequence-gap inference. Controlled same-process timing uses a shared monotonic clock; remote one-way latency is not claimed. Fixed-seed random decisions repeat, while OS timing/outage boundary counts may vary. Completed/live snapshot windows are kept separate. Windows display-file sharing failures are retried and isolated from reception.

PX4 configuration and an opt-in receive test are documented but not validated here. No aircraft commands, physical RF measurements, coverage certification or operational flight reliability claims.

All implementation work is on `feature/telemetry-monitor`; please review before merging into `main`.
