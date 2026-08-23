"""Deterministic, bounded strategy personalities for multi-account FPL bots."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    """High-level optimiser preferences.

    Values are intentionally normalised so downstream modules can map them onto
    their own scoring scales without embedding account-specific magic numbers.
    Most continuous values are in [0, 1]. ``fixture_horizon`` is gameweeks.
    """

    name: str
    archetype: str
    seed: int
    differential_weight: float
    template_weight: float
    transfer_patience: float
    hit_aversion: float
    captain_variance: float
    bench_investment: float
    price_change_weight: float
    injury_risk_aversion: float
    form_weight: float
    underlying_data_weight: float
    chip_aggression: float
    fixture_horizon: int

    def as_dict(self) -> dict:
        return asdict(self)


# Base profiles are deliberately competitive rather than novelty-only. The
# deterministic jitter applied later makes individual bots distinct even when
# they share an archetype.
_ARCHETYPES: dict[str, dict] = {
    "Template Anchor": dict(differential_weight=.18, template_weight=.85, transfer_patience=.78,
                            hit_aversion=.88, captain_variance=.15, bench_investment=.42,
                            price_change_weight=.35, injury_risk_aversion=.82, form_weight=.42,
                            underlying_data_weight=.82, chip_aggression=.32, fixture_horizon=6),
    "Differential Hunter": dict(differential_weight=.86, template_weight=.28, transfer_patience=.42,
                                hit_aversion=.58, captain_variance=.76, bench_investment=.38,
                                price_change_weight=.38, injury_risk_aversion=.58, form_weight=.62,
                                underlying_data_weight=.68, chip_aggression=.58, fixture_horizon=4),
    "Fixture Planner": dict(differential_weight=.42, template_weight=.58, transfer_patience=.74,
                            hit_aversion=.78, captain_variance=.34, bench_investment=.50,
                            price_change_weight=.30, injury_risk_aversion=.72, form_weight=.38,
                            underlying_data_weight=.78, chip_aggression=.44, fixture_horizon=8),
    "Form Chaser": dict(differential_weight=.55, template_weight=.48, transfer_patience=.34,
                        hit_aversion=.60, captain_variance=.48, bench_investment=.36,
                        price_change_weight=.54, injury_risk_aversion=.62, form_weight=.88,
                        underlying_data_weight=.52, chip_aggression=.52, fixture_horizon=3),
    "Value Builder": dict(differential_weight=.42, template_weight=.55, transfer_patience=.58,
                          hit_aversion=.76, captain_variance=.30, bench_investment=.46,
                          price_change_weight=.90, injury_risk_aversion=.70, form_weight=.48,
                          underlying_data_weight=.72, chip_aggression=.34, fixture_horizon=5),
    "Captain Maverick": dict(differential_weight=.52, template_weight=.50, transfer_patience=.62,
                             hit_aversion=.72, captain_variance=.94, bench_investment=.38,
                             price_change_weight=.34, injury_risk_aversion=.68, form_weight=.58,
                             underlying_data_weight=.72, chip_aggression=.55, fixture_horizon=5),
    "Patient Planner": dict(differential_weight=.32, template_weight=.66, transfer_patience=.94,
                            hit_aversion=.96, captain_variance=.24, bench_investment=.48,
                            price_change_weight=.24, injury_risk_aversion=.82, form_weight=.34,
                            underlying_data_weight=.88, chip_aggression=.26, fixture_horizon=7),
    "Aggressive Rebuilder": dict(differential_weight=.66, template_weight=.40, transfer_patience=.18,
                                 hit_aversion=.26, captain_variance=.62, bench_investment=.34,
                                 price_change_weight=.58, injury_risk_aversion=.54, form_weight=.68,
                                 underlying_data_weight=.64, chip_aggression=.78, fixture_horizon=4),
    "Attack Heavy": dict(differential_weight=.52, template_weight=.55, transfer_patience=.55,
                         hit_aversion=.70, captain_variance=.58, bench_investment=.24,
                         price_change_weight=.42, injury_risk_aversion=.62, form_weight=.58,
                         underlying_data_weight=.76, chip_aggression=.46, fixture_horizon=5),
    "Defence First": dict(differential_weight=.38, template_weight=.60, transfer_patience=.72,
                          hit_aversion=.82, captain_variance=.24, bench_investment=.58,
                          price_change_weight=.34, injury_risk_aversion=.76, form_weight=.38,
                          underlying_data_weight=.84, chip_aggression=.38, fixture_horizon=6),
    "Minutes Purist": dict(differential_weight=.34, template_weight=.62, transfer_patience=.70,
                           hit_aversion=.82, captain_variance=.22, bench_investment=.44,
                           price_change_weight=.28, injury_risk_aversion=.96, form_weight=.40,
                           underlying_data_weight=.84, chip_aggression=.34, fixture_horizon=5),
    "Balanced Analyst": dict(differential_weight=.48, template_weight=.55, transfer_patience=.62,
                             hit_aversion=.76, captain_variance=.40, bench_investment=.44,
                             price_change_weight=.42, injury_risk_aversion=.74, form_weight=.52,
                             underlying_data_weight=.78, chip_aggression=.42, fixture_horizon=6),
}


def _seed_for(account_id: str, season: str) -> int:
    token = f"{season}:{account_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(token).digest()[:8], "big")


def _clip(value: float) -> float:
    return round(max(0.05, min(0.95, value)), 3)


def build_strategy_profile(
    account_id: str,
    requested_profile: str = "",
    *,
    season: str = "2026-27",
) -> StrategyProfile:
    """Build a stable strategy profile for an account.

    The same account/season combination always receives the same archetype and
    jitter. Passing a valid ``requested_profile`` pins the archetype while still
    retaining deterministic per-bot variation.
    """
    if not account_id:
        raise ValueError("account_id is required")

    seed = _seed_for(account_id, season)
    rng = random.Random(seed)
    names = tuple(_ARCHETYPES)

    if requested_profile:
        if requested_profile not in _ARCHETYPES:
            raise ValueError(
                f"Unknown strategy profile {requested_profile!r}; choose one of: {', '.join(names)}"
            )
        archetype = requested_profile
    else:
        archetype = names[seed % len(names)]

    base = dict(_ARCHETYPES[archetype])
    continuous = [k for k in base if k != "fixture_horizon"]
    for key in continuous:
        # +/- 0.07 is enough to separate bots without destroying the archetype.
        base[key] = _clip(float(base[key]) + rng.uniform(-0.07, 0.07))

    horizon = int(base["fixture_horizon"] + rng.choice((-1, 0, 0, 0, 1)))
    base["fixture_horizon"] = max(3, min(8, horizon))

    suffix = account_id.removeprefix("acct_")[-4:].upper()
    return StrategyProfile(
        name=f"{archetype} {suffix}",
        archetype=archetype,
        seed=seed,
        **base,
    )
