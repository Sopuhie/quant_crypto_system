"""One-command launcher for web dashboard and sandbox trading engine."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from utils.logger import get_logger, setup_logging

logger = get_logger("SystemRunner")

PROJECT_ROOT = Path(__file__).resolve().parent


def launch_system() -> None:
    logger.info("Initializing Quant Crypto System Dual-Engine Launch...")

    env = os.environ.copy()
    env["BINANCE_SANDBOX"] = "true"

    logger.info("Launching Web Dashboard on Port 8000...")
    web_process = subprocess.Popen(
        [sys.executable, "run_web.py"],
        cwd=PROJECT_ROOT,
        env=env,
    )

    time.sleep(2)

    logger.info("Launching Core Quantitative Engine in SANDBOX Simulation Mode...")
    engine_process = subprocess.Popen(
        [sys.executable, "main.py", "--sandbox"],
        cwd=PROJECT_ROOT,
        env=env,
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down both engines safely...")
        for process in (engine_process, web_process):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        logger.info("System terminated cleanly.")


if __name__ == "__main__":
    setup_logging()
    launch_system()
