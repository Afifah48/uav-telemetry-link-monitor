"""Run the measured five-scenario suite from the repository root."""
from pathlib import Path
from uav_telemetry.experiments import run_suite
from uav_telemetry.utils.config import load_config

if __name__ == "__main__":
    run_suite(load_config("configs/baseline.yaml"), Path("results/experiments"))
