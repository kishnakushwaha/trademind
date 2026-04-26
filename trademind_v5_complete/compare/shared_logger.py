"""
compare/shared_logger.py
Shared CSV logger. Each strategy writes to its own file.
No shared state — strategies never touch each other's logs.
"""

import csv
import os
import logging
from datetime import datetime

LOGS_DIR = "logs"

# One CSV per strategy
LOG_FILES = {
    "A": "logs/strategy_a_rulebased.csv",
    "B": "logs/strategy_b_sonnet.csv",
    "C": "logs/strategy_c_opus.csv",
}

HEADERS = [
    "date", "ticker", "signal", "score", "confidence",
    "entry_price", "sl_price", "target_1",
    "exit_price", "exit_date", "pnl",
    "result",       # WIN / LOSS / OPEN
    "correct",      # YES / NO / PENDING
    "detail",       # reasoning or rule breakdown
    "api_cost_usd", # 0 for rule-based
]


def ensure_log(strategy_id: str):
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = LOG_FILES[strategy_id]
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=HEADERS).writeheader()


def append_signal(strategy_id: str, row: dict):
    path = LOG_FILES[strategy_id]
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        full_row = {h: row.get(h, "") for h in HEADERS}
        writer.writerow(full_row)


def load_all(strategy_id: str) -> list[dict]:
    path = LOG_FILES[strategy_id]
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return list(csv.DictReader(f))


def save_all(strategy_id: str, rows: list[dict]):
    path = LOG_FILES[strategy_id]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)


def get_logger(name: str) -> logging.Logger:
    import colorlog
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = colorlog.StreamHandler()
        handler.setFormatter(colorlog.ColoredFormatter(
            f"%(log_color)s%(asctime)s [{name}] %(message)s",
            datefmt="%H:%M:%S",
            log_colors={"DEBUG":"cyan","INFO":"green",
                        "WARNING":"yellow","ERROR":"red","CRITICAL":"bold_red"}
        ))
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
    return logger
