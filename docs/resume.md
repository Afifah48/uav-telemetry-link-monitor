# Resume suggestions

**UAV Telemetry Link Monitor** | Python, pymavlink, UDP, pytest, MAVLink 2

- Built a read-only MAVLink telemetry pipeline with UDP transport, seeded impairment injection, source-separated sequence tracking, heartbeat monitoring and a real-time ground-station dashboard.
- Evaluated five reliability scenarios across 5,750 UDP datagrams; measured 94.87% and 80.78% delivery under 5% and 20% configured loss, with recorded JSON/CSV ground truth and same-clock delay measurements.
- Validated nominal forwarding, malformed-frame recovery, reordering and outage reconnection using automated tests; demonstrated recovery from a three-second outage in 18.4 ms to the next valid heartbeat in the recorded synthetic run.

The last number is phase-dependent local recovery, not a guaranteed network response time. For a less timing-specific third bullet, use: “Verified 92/92 datagram delivery and 178 decoded MAVLink messages across separate source, proxy and receiver processes; added 48 automated tests with approximately 86% statement coverage.”

Do not claim flight testing, RF coverage measurements or validated PX4 SITL integration. PX4 instructions and an opt-in test are provided, but SITL was unavailable in the development environment.
