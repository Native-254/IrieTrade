# utils/logger.py
import sys

from loguru import logger

from utils.config import CONFIG


def setup_logger():
    """Configure the logger based on settings."""
    logger.remove()
    log_level = CONFIG["general"]["log_level"]
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"

    logger.add(sys.stderr, format=log_format, level=log_level, colorize=True)
    logger.add(
        "logs/bot_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        format=log_format,
        level="DEBUG",
    )
    return logger


log = setup_logger()
