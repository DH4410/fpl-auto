"""Multi-account runtime primitives for FPL Auto.

This package intentionally contains no committed credentials. Runtime account
secrets are loaded by :mod:`bot.multi_account.accounts` and strategy metadata is
safe to persist separately.
"""

from .accounts import AccountRecord, load_accounts_from_env, stable_account_id
from .profiles import StrategyProfile, build_strategy_profile

__all__ = [
    "AccountRecord",
    "StrategyProfile",
    "build_strategy_profile",
    "load_accounts_from_env",
    "stable_account_id",
]
