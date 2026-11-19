"""Safe parsing helpers for frontmatter values."""


def safe_int(value, default: int = 0) -> int:
    """Safely convert a value to int, returning *default* on failure.

    Handles None, non-numeric strings (e.g. ``importance: high``), floats,
    non-finite numbers, and any other type that ``int()`` would reject.
    """
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default
