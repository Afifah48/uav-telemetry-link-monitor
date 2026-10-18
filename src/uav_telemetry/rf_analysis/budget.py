from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import numpy as np


@dataclass(frozen=True)
class LinkBudget:
    frequency_hz: float = 2.4e9
    transmit_dbm: float = 20.0
    transmit_gain_dbi: float = 2.0
    receive_gain_dbi: float = 2.0
    sensitivity_dbm: float = -100.0
    other_losses_db: float = 3.0
    fade_margin_db: float = 10.0

    def __post_init__(self) -> None:
        if any(not math.isfinite(v) for v in asdict(self).values()) or self.frequency_hz <= 0:
            raise ValueError("Finite parameters and positive frequency required")
        if self.other_losses_db < 0 or self.fade_margin_db < 0:
            raise ValueError("Loss and fade-margin inputs must be nonnegative")

    def evaluate(self, distance_m) -> dict:
        distance = np.asarray(distance_m, dtype=float)
        if not np.all(np.isfinite(distance)) or np.any(distance <= 0):
            raise ValueError("Distance must be positive and finite, in metres")
        fspl = 20 * np.log10(4 * np.pi * distance * self.frequency_hz / 299792458.0)
        received = self.transmit_dbm + self.transmit_gain_dbi + self.receive_gain_dbi - fspl - self.other_losses_db
        return {"distance_m": distance, "fspl_db": fspl, "received_dbm": received,
                "raw_margin_db": received - self.sensitivity_dbm,
                "available_margin_db": received - self.sensitivity_dbm - self.fade_margin_db}


def generate(output: Path, budget: LinkBudget | None = None) -> None:
    from uav_telemetry.plots import plt
    budget = budget or LinkBudget()
    output.mkdir(parents=True, exist_ok=True)
    values = budget.evaluate(np.geomspace(100, 20000, 200))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].semilogx(values["distance_m"], values["received_dbm"], label="Free-space analytical received power")
    axes[0].axhline(budget.sensitivity_dbm, ls="--", color="gray", label="Assumed sensitivity")
    axes[0].set(ylabel="Received power (dBm)", xlabel="Distance (m)")
    axes[1].semilogx(values["distance_m"], values["available_margin_db"], label="Margin after reserved fade margin")
    axes[1].axhline(0, color="gray", ls="--")
    axes[1].set(ylabel="Available link margin (dB)", xlabel="Distance (m)")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7)
    fig.suptitle("Analytical 2.4 GHz example — not measured UAV coverage")
    fig.tight_layout()
    fig.savefig(output / "rf_budget.png", dpi=160)
    plt.close(fig)
    (output / "rf_budget.json").write_text(json.dumps({"model": "Analytical free-space only; no validated RF coverage",
        "parameters": asdict(budget), "values": {k: v.tolist() for k, v in values.items()}}, indent=2), encoding="utf-8")
