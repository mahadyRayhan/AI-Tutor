# backend/app/core/utils.py

import os
import logging

def setup_logging(log_level_str: str, log_file: str) -> logging.Logger:
    """Sets up logging to console and file."""
    # Create the directory for the log file if it doesn't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    numeric_level = getattr(logging, log_level_str.upper(), None)
    if not isinstance(numeric_level, int):
        logging.basicConfig(level=logging.INFO)
        logging.warning(f'Invalid log level: {log_level_str}. Defaulting to INFO.')
        numeric_level = logging.INFO

    logger = logging.getLogger("AITutor")
    logger.setLevel(numeric_level)
    logger.propagate = False

    # Clear existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Console Handler
    ch = logging.StreamHandler()
    ch.setLevel(numeric_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File Handler
    try:
        fh = logging.FileHandler(log_file, mode='a')
        fh.setLevel(numeric_level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        logger.error(f"Failed to set up file handler at {log_file}: {e}", exc_info=True)

    logger.info(f"Logging initialized at level {log_level_str.upper()} to console and {log_file}")
    return logger