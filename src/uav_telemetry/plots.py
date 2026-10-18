"""Plots only measured experiment data; RF plots are explicitly analytical."""
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "uav-telemetry-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def plot_results(results: dict, output: Path) -> None:
    names = list(results)
    plt.figure(figsize=(8, 4))
    plt.bar(names, [100 * results[n]["matched_datagram_pdr"] for n in names], color=["#167d8d", "#38a3a5", "#e9a23b", "#d35d5d", "#566eb8"])
    plt.ylabel("Matched UDP datagram delivery (%)")
    plt.title("Measured loopback delivery after proxy queue drain")
    plt.ylim(0, 108)
    plt.grid(axis="y", alpha=0.2)
    plt.tight_layout()
    plt.savefig(output / "delivery.png", dpi=160)
    plt.close()
    plt.figure(figsize=(8, 4))
    for name, report in results.items():
        stats = report["local_same_clock_one_way_ms"]
        if stats:
            plt.errorbar(name, stats["mean"], yerr=[[stats["mean"] - stats["min"]], [stats["max"] - stats["mean"]]], fmt="o", capsize=5, label=name)
    plt.ylabel("Same-process source → receiver delay (ms)")
    plt.title("Measured mean and min/max; shared monotonic clock")
    plt.grid(axis="y", alpha=0.2)
    plt.tight_layout()
    plt.savefig(output / "delay.png", dpi=160)
    plt.close()
    report = results["outage"]
    history = report["receiver"]["history"]
    plt.figure(figsize=(9, 4))
    plt.plot([h["elapsed_s"] for h in history], [np.nan if h["heartbeat_age_s"] is None else h["heartbeat_age_s"] for h in history], label="Valid heartbeat arrival age")
    c = report["config"]
    plt.axvspan(c["outage_start_s"], c["outage_start_s"] + c["outage_duration_s"], alpha=0.2, color="red", label="Complete forwarding outage")
    plt.axhline(c["heartbeat_timeout_s"], color="gray", ls="--", label="Disconnect threshold")
    plt.xlabel("Elapsed local monotonic time (s)")
    plt.ylabel("Heartbeat freshness (s)")
    plt.title("Outage detection and recovery from decoded HEARTBEAT messages")
    plt.legend(fontsize=8)
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(output / "outage.png", dpi=160)
    plt.close()
