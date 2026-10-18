# PX4 SITL integration — not validated in this environment

The offline pipeline and tests were executed locally. **PX4/Gazebo was not installed or run**: the Windows environment had no available WSL distribution, Docker runtime or PX4 executable. The live integration test is explicitly skipped until a real stream is configured. Instructions below were checked against current official documentation on 2026-10-18; executing them on a suitable Linux host remains required.

## Start a supported simulated multirotor

Follow the official [PX4 development-environment setup](https://docs.px4.io/main/en/dev_setup/dev_env_linux_ubuntu.html) for the PX4 version you choose. Record its release/commit, OS and Gazebo version. From that checkout:

```bash
HEADLESS=1 make px4_sitl gz_x500
```

The [official Gazebo guide](https://docs.px4.io/main/en/sim_gazebo_gz/) documents the x500 target and headless launch. This monitor does not arm or command the simulated vehicle.

## Verify the actual UDP output

In the PX4 shell, run `mavlink status`. Inspect its running instances, local ports, destinations and message rates. Do not assume an output based solely on a familiar port number. The [simulation guide](https://docs.px4.io/main/en/simulation/) describes conventional remote ports 14550 for GCS and 14540 for offboard applications, but startup configuration determines the actual endpoints.

For a dedicated monitor stream, choose an unused local PX4 port and an explicitly configured proxy destination. Example for PX4 and monitor on the **same Linux host**, after verifying that local UDP 14610 is free:

```text
mavlink start -u 14610 -o 14600 -t 127.0.0.1 -m normal -r 20000
mavlink stream -u 14610 -s HEARTBEAT -r 1
mavlink stream -u 14610 -s ATTITUDE -r 20
mavlink stream -u 14610 -s GLOBAL_POSITION_INT -r 10
mavlink status
```

The official [MAVLink module reference](https://docs.px4.io/main/en/modules/modules_communication.html#mavlink) defines `-u` as local UDP port, `-o` as remote UDP port, `-t` as partner IP and the stream selection command. Confirm options against `mavlink help` in the installed version. These PX4-shell commands configure simulator output; the Python monitor sends no stream requests or vehicle commands.

## Run the receive path

From this project, use separate terminals:

```bash
telemetry-monitor receive --config configs/px4.yaml --mode px4 --duration 0 --snapshot results/live/snapshot.json --output results/live
telemetry-monitor proxy --config configs/px4.yaml --duration 0 --output results/live
telemetry-monitor dashboard --snapshot results/live/snapshot.json --port 8765
```

`configs/px4.yaml` listens at proxy 127.0.0.1:14600 and forwards to receiver 127.0.0.1:14601. Confirm the vehicle system/component IDs and update the target fields accordingly. Frames from other IDs are counted separately, while selected-vehicle state/freshness remains isolated. Unsupported or absent message types stay unavailable.

For containers, WSL or separate computers, loopback refers to that network namespace. Replace the PX4 partner IP with the reachable monitor-host address and set the proxy bind address appropriately. Verify UDP routing and port ownership; do not point both QGroundControl and this listener at an exclusive socket. The proxy only forwards toward the receiver.

## Opt-in live test

With the proxy running and PX4 sending to it, stop the normal receiver to free port 14601, then run:

```bash
# Bash
UAV_PX4_ENDPOINT=127.0.0.1:14601 python -m pytest -q -m px4
# PowerShell
$env:UAV_PX4_ENDPOINT='127.0.0.1:14601'
python -m pytest -q -m px4
```

The test binds a real receive socket, waits up to 12 seconds, and requires a decoded HEARTBEAT. It is read-only. Passing proves reception from the user-configured stream; it does not validate flight control, RF performance or every PX4 message type. Save the test output, PX4 commit, `mavlink status`, capture and selected IDs before describing PX4 integration as validated.
