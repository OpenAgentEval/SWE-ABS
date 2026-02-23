import logging
from pathlib import Path
import sys




def close_logger(logger):
    # To avoid too many open files
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def setup_global_logger(log_file: Path, mode: str = "w", add_stdout: bool = False):
    """
        Set up the global logger to write logs to a specified file, with optional output to stdout.

            Args:
                log_file (Path): Path to the log file.
                mode (str): Mode for writing the log file; defaults to 'w' (overwrite), can be 'a' (append).
                add_stdout (bool): Whether to also output logs to stdout (console); defaults to False.
    """
    # Ensure the log file directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Create or retrieve the root logger
    logger = logging.getLogger(log_file.name)
    logger.setLevel(logging.INFO)  # Set minimum level; specific level is controlled by handlers

    # Clear existing handlers to avoid duplicate log entries
    logger.handlers.clear()

    # Create file handler
    file_handler = logging.FileHandler(log_file, mode=mode, encoding='utf-8')
    file_handler.setLevel(logging.INFO)  # Log all levels to file
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Add stdout handler if needed
    if add_stdout:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.INFO)  # Console outputs INFO level and above only
        stdout_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        stdout_handler.setFormatter(stdout_formatter)
        logger.addHandler(stdout_handler)

    # Optional: prevent log propagation to parent logger (usually no change needed)
    # logger.propagate = False
    return logger
