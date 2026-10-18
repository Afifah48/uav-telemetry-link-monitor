# Measurement model and limits

## Three different populations

1. A **UDP datagram** is the proxy's unit of loss, delay and reordering.
2. A **MAVLink frame/message** is a protocol object decoded from received bytes. It has a modulo-256 sequence number and a system/component ID.
3. A **physical RF packet** belongs to a real radio/MAC implementation. None is measured here.

The synthetic encoder packs up to two real MAVLink 2 frames per datagram. At 100 source ticks/s, position and attitude are emitted at 100 Hz, heartbeat/GPS at 5 Hz, SYS_STATUS at 2 Hz and VFR_HUD at 10 Hz. A ten-second source sends 2,220 MAVLink frames in 1,150 UDP datagrams. At other source rates, the optional message rates are quantized to integer tick intervals. Source coordinates and vehicle values are explicitly synthetic test data.

`pymavlink.dialects.v20.common` performs framing and decoding. Parser state is separated by UDP peer; it can consume multiple frames in one datagram or a frame split over consecutive datagrams. UDP does not guarantee fragment order or delivery: a dropped/reordered application fragment cannot be magically recovered. Robust parsing resynchronizes on subsequent valid frames, with BAD_DATA events counted. The common dialect is used; custom-dialect support and MAVLink signing/authentication are not configured. CRC checking is not sender authentication.

## Exact experimental PDR

All experiments bind isolated loopback sockets on dynamically assigned ports. One finite source population is sent, then the proxy drains its scheduled queue and the receiver is allowed to finish. Every ingress datagram has a local ID and SHA-256 payload digest. Forwarded ingress records are matched by digest and occurrence to receiver records. Counts have a common population and drained window:

$$
\mathrm{PDR}_{UDP}=\frac{N_{\text{matched receiver datagrams}}}{N_{\text{proxy ingress datagrams}}}.
$$

The report separately records source sends, proxy ingress, forwarding, intentional drops, receiver arrivals and bytes. This exposes source→proxy loss, proxy→receiver loss and undecodable payloads instead of conflating them. The proxy forwarding ratio is forwarded/ingress; it need not equal end-delivery PDR on a loaded network. The controlled local run verifies the counts agree after intentional drops. Capture overflow and an undrained queue fail the experiment instead of silently producing a ratio.

Live reception normally has no trustworthy transmitted count or matching end boundary. Its dashboard PDR is **Unavailable**. PDR appears only from a completed report with the same observation-start token as the snapshot. A previous report cannot be mixed into a new live run. Completed snapshots are labeled as recordings; their connection state and freshness are values at capture time, not a claim that the vehicle is still connected.

## Sequence-gap inference

Sequence trackers are keyed by UDP peer plus system ID and component ID, across all message types. No sequence loss is inferred from an absent message type. Missing initial/final messages cannot be inferred when they lack bracketing frames.

The tracker unwraps the 8-bit counter using the nearest signed modulo difference in [-128,127]. Advancing messages create pending gaps. An out-of-order message inside the 64-message default window repairs its gap. Gaps older than that window become a confirmed **estimate**; duplicates and too-old/ambiguous messages are counted separately. State is reset after a configured inactivity interval, creating a new epoch rather than inventing a large gap.

This is not exact loss ground truth. More than 127 missing messages, very late traffic, restarts, multiple emitters sharing an endpoint/ID and sequence resets are ambiguous. An outage can therefore show zero inferred gaps after an epoch reset while proxy logs correctly show hundreds of dropped datagrams. Aggregated type-filtered streams also cannot be treated as a complete sequence population. The project never divides a sequence-gap estimate by decoded message count and calls it datagram PDR.

## Time, latency and jitter

All local intervals use `time.monotonic()`. UTC timestamps are log metadata, not duration clocks. Vehicle `time_boot_ms` and `time_usec` values are retained with their field names; their epochs are not assumed comparable to the host clock.

The controlled experiment runs source/proxy/receiver in the same Python process. Unique byte-identical datagrams connect each source send time to its receiver arrival on a shared monotonic clock. **Local same-clock one-way delay** is arrival minus source-send time. If source payload digests repeat, this source timestamp pairing is ambiguous and latency is left unavailable. Remote PX4/live reception does not use this measurement without clock synchronization and correlation metadata.

The report distinguishes:

- Requested schedule delay: intentional base delay plus a bounded uniform jitter draw, any deliberate extra reorder delay and FIFO holdback when ordering is preserved.
- Observed proxy residence: ingress→forward time, including scheduler and Python overhead.
- Local same-clock one-way delay: source→receiver arrival through the real loopback stack.
- Delay variation: population standard deviation of the matched one-way delays. This is an explicitly defined sample statistic, not an RFC 3550 estimator.
- Arrival interval variation: standard deviation of consecutive UDP arrival intervals. Changing send cadence, batching and loss affect it; it is **not** network delay jitter or end-to-end latency.

A zero-delay configuration therefore has nonzero observed local delay. Source pacing, scheduling, Python work and synchronous event logging affect measurements. Results are prototype loopback measurements, not radio or avionics timing guarantees.

## Impairment scheduler

Seeded Bernoulli loss acts on each ingress datagram, not on individual MAVLink frames. For fixed ingress order, random decisions repeat. Delay jitter is uniform in [-jitter_ms,+jitter_ms], with negative total delay clamped to zero. With ordering preserved, a due time cannot precede the previous due time; this FIFO holdback changes the observed distribution and may create bursts. Enabling reordering permits independent due times; a configured fraction gets an additional delay. A heap schedules departures, and reversed ingress IDs count observed reordered datagrams.

An outage covers [start,start+duration) relative to proxy start. Ingress during the outage is dropped, and queued packets becoming due during it are dropped too. No datagrams are intentionally forwarded during the interval. Kernel scheduling may shift exact boundary counts across runs. A finite queue drops excess entries with the separate `queue_overflow` reason. This is network-level degradation; bandwidth shaping, RF fading, interference and retransmissions are not modeled.

The seed fixes random decisions; it does not make OS arrival times or outage boundary races bit-for-bit deterministic. The logs record what actually occurred.

## Freshness, disconnection and recovery

Freshness is elapsed time since the most recent decoded, valid **arrival** for the selected source/type, not the physical age of a remotely sampled state. Unknown and invalid values are `null`/Unavailable, including battery sentinels, invalid headings, NaNs and never-seen message types.

State is WAITING before selected telemetry, NO_HEARTBEAT when other telemetry arrives without a heartbeat, CONNECTED with a recent heartbeat, DEGRADED beyond its warning age and DISCONNECTED beyond the heartbeat/inactivity threshold. The receiver periodically updates these states even when no datagrams arrive. A new valid selected-source heartbeat can reconnect it. Another vehicle cannot keep the selected vehicle's heartbeat alive.

Outage recovery is the first valid selected-source HEARTBEAT arrival after the scheduled restoration time minus that restoration time. The report also records whether a disconnection was actually detected. Recovery depends on heartbeat phase, message rate and scheduling; a short measured recovery in an aligned synthetic run is not a guaranteed reaction time.

## Capture and operating limits

Detailed logs stream to JSONL and are intentionally retained for replay/audit. They grow with run duration. Receiver/proxy in-memory capture is bounded to 200,000 datagrams and histories are bounded separately; completed experiment reporting rejects a truncated capture. Synthetic-source send records are retained for timestamp pairing, so controlled demos should remain finite and modest. The default suite uses 1,150 datagrams per run. Long deployments need log rotation, streaming joins, telemetry-source restart detection and operational supervision.

Windows can briefly deny replacing a snapshot file while a reader holds it. Atomic snapshot replacement retries transient sharing failures. Persistent display-write errors are logged and counted without terminating MAVLink reception. A failed communication thread is surfaced as an error on shutdown; results are not reported as successful after such a failure.
