import copy
import logging
import os
from logging.handlers import RotatingFileHandler

log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_path = os.path.join(log_dir, "bot.log")


class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[34m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record):
        record_copy = copy.copy(record)
        levelname = record_copy.levelname
        if levelname in self.COLORS:
            record_copy.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
        return super().format(record_copy)


handler = RotatingFileHandler(
    log_path, maxBytes=5 * 1024 * 1024, backupCount=1, encoding="utf-8", mode="w"
)
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p"
)
color_formatter = ColorFormatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p"
)
handler.setFormatter(formatter)
console_handler.setFormatter(color_formatter)
logging.basicConfig(level=logging.INFO, handlers=[handler, console_handler])
logger = logging.getLogger(__name__)
