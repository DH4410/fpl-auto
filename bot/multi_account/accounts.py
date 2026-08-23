"""Credential-safe account registry primitives.

The repository must never contain real FPL credentials. Production credentials
are expected through the ``FPL_ACCOUNTS_JSON`` environment variable, normally
backed by an encrypted CI secret.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """One authorised FPL login.

    ``email`` and ``password`` are deliberately hidden from dataclass repr so an
    accidental ``log.info(account)`` cannot leak credentials into CI output.
    """

    account_id: str
    email: str = field(repr=False)
    password: str = field(repr=False)
    display_name: str = ""
    requested_profile: str = ""

    @property
    def public_name(self) -> str:
        return self.display_name.strip() or self.account_id

    def public_dict(self) -> dict[str, str]:
        """Credential-free representation safe for dashboards and state."""
        return {
            "account_id": self.account_id,
            "display_name": self.public_name,
            "requested_profile": self.requested_profile,
        }


def stable_account_id(email: str) -> str:
    """Return an opaque, deterministic ID without exposing the email address."""
    normalized = (email or "").strip().lower()
    if not normalized:
        raise ValueError("Cannot derive account ID from an empty email")
    digest = hashlib.blake2s(normalized.encode("utf-8"), digest_size=6).hexdigest()
    return f"acct_{digest}"


def _coerce_accounts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("accounts")
    if not isinstance(payload, list):
        raise ValueError("FPL_ACCOUNTS_JSON must be a JSON list or {'accounts': [...]} object")
    return payload


def parse_accounts_json(raw: str) -> list[AccountRecord]:
    """Parse and validate a runtime account secret.

    Accepted per-account fields are ``email``, ``password``, optional
    ``display_name``, optional ``account_id`` and optional ``profile``.
    """
    if not raw or not raw.strip():
        raise ValueError("Account JSON is empty")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Account JSON is invalid: {exc.msg}") from exc

    rows = _coerce_accounts(payload)
    accounts: list[AccountRecord] = []
    seen_emails: set[str] = set()
    seen_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Account row {index} must be an object")

        email = str(row.get("email") or "").strip()
        password = str(row.get("password") or "")
        if not email:
            raise ValueError(f"Account row {index} has no email")
        if not password:
            raise ValueError(f"Account row {index} has no password")

        email_key = email.lower()
        if email_key in seen_emails:
            raise ValueError(f"Duplicate email at account row {index}")
        seen_emails.add(email_key)

        account_id = str(row.get("account_id") or stable_account_id(email)).strip()
        if not account_id:
            raise ValueError(f"Account row {index} has an empty account_id")
        if account_id in seen_ids:
            raise ValueError(f"Duplicate account_id at account row {index}: {account_id}")
        seen_ids.add(account_id)

        accounts.append(
            AccountRecord(
                account_id=account_id,
                email=email,
                password=password,
                display_name=str(row.get("display_name") or "").strip(),
                requested_profile=str(row.get("profile") or "").strip(),
            )
        )

    if not accounts:
        raise ValueError("No accounts were supplied")
    return accounts


def load_accounts_from_env(env_var: str = "FPL_ACCOUNTS_JSON") -> list[AccountRecord]:
    """Load accounts from an environment variable backed by a secret store."""
    raw = os.environ.get(env_var, "")
    if not raw.strip():
        raise RuntimeError(f"{env_var} is unset; no account credentials were loaded")
    return parse_accounts_json(raw)
