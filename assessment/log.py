import logging


def get_logger(name: str) -> logging.Logger:
    """Return a logger scoped under the assessment namespace."""
    if not name.startswith("assessment."):
        name = f"assessment.{name}"
    return logging.getLogger(name)
