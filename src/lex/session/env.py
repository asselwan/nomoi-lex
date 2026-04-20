"""Environment detection for Lex."""

import os
from typing import Literal

LexEnv = Literal["development", "staging", "production"]


def get_env() -> LexEnv:
    """Read LEX_ENV from environment. Defaults to 'development'."""
    raw = os.environ.get("LEX_ENV", "development").lower()
    if raw in ("production", "prod"):
        return "production"
    if raw in ("staging", "stg"):
        return "staging"
    return "development"


def is_dev() -> bool:
    return get_env() == "development"
