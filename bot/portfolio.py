"""
Multi-account portfolio manager — Module 9.

Supports 2-500+ accounts with different strategy "profiles". Each profile has:
- temperature: how random captain/transfer picks are (0=deterministic, 1=very random)
- captain_risk_weight: how much start-probability weighs against raw xPts (0=ignore, 1=full)
- ep_blend_alpha: ML weight in the ML+FPL-xP captain blend (0=pure xP, 1=pure ML)

Diversity across profiles means that if the consensus pick gets injured or blanks,
a subset of accounts will still have done something different.

Usage:
    from bot.portfolio import PortfolioManager, PRESET_PROFILES
    pm = PortfolioManager()
    picks = pm.run_all(captain_scores, forecasts, current_gw, n_accounts=10)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy profiles
# ---------------------------------------------------------------------------

@dataclass
class StrategyProfile:
    """Hyperparameters that govern one account's pick behaviour.

    Attributes
    ----------
    name:
        Human-readable label.
    temperature:
        Softmax temperature for stochastic captain/transfer selection.
        0.0 = fully deterministic (greedy argmax).
        1.0 = proportional to exp(score) — noticeable randomness.
        Higher values flatten the distribution toward uniform sampling.
    captain_risk_weight:
        Weight of start-probability in captain score.
        cap_score = xPts * (captain_risk_weight + (1-captain_risk_weight)*p60).
        0.0 = captain is chosen on raw xPts alone (maximum risk tolerance).
        1.0 = captain score fully penalised by start probability (most cautious).
    ep_blend_alpha:
        ML fraction in the ML+FPL-xP captain blend (0=pure FPL xP, 1=pure ML).
    transfer_temperature:
        Softmax temperature for the transfer suggestion. Defaults to temperature.
    """
    name: str
    temperature: float = 0.0
    captain_risk_weight: float = 0.5
    ep_blend_alpha: float = 0.3
    transfer_temperature: float | None = None

    def __post_init__(self) -> None:
        if self.transfer_temperature is None:
            self.transfer_temperature = self.temperature


# Built-in presets covering the main strategic archetypes.
PRESET_PROFILES: dict[str, StrategyProfile] = {
    "greedy":      StrategyProfile("greedy",      temperature=0.0, captain_risk_weight=0.2, ep_blend_alpha=0.5),
    "balanced":    StrategyProfile("balanced",    temperature=0.1, captain_risk_weight=0.5, ep_blend_alpha=0.3),
    "cautious":    StrategyProfile("cautious",    temperature=0.0, captain_risk_weight=0.9, ep_blend_alpha=0.2),
    "contrarian":  StrategyProfile("contrarian",  temperature=0.6, captain_risk_weight=0.3, ep_blend_alpha=0.4),
    "differential":StrategyProfile("differential",temperature=0.8, captain_risk_weight=0.5, ep_blend_alpha=0.3),
}


# ---------------------------------------------------------------------------
# Sampling utilities
# ---------------------------------------------------------------------------

def sample_with_temperature(
    scores: pd.Series,
    temperature: float,
    rng: np.random.Generator | None = None,
) -> int:
    """Softmax-temperature sample from ``scores``, returning an element ID.

    temperature=0.0 returns argmax (deterministic).
    temperature>0.0 converts scores to softmax probabilities and samples.
    """
    if rng is None:
        rng = np.random.default_rng()

    valid = scores[scores > 0].dropna()
    if valid.empty:
        return int(scores.idxmax()) if not scores.empty else -1

    if temperature <= 0.0:
        return int(valid.idxmax())

    logits = valid.values.astype(float)
    # Subtract max for numerical stability before exp
    logits = (logits - logits.max()) / (temperature + 1e-9)
    probs = np.exp(logits)
    probs /= probs.sum()
    chosen_idx = rng.choice(len(valid), p=probs)
    return int(valid.index[chosen_idx])


def captain_scores_for_profile(
    base_xpts: pd.Series,
    p60: pd.Series,
    profile: StrategyProfile,
) -> pd.Series:
    """Blend raw xPts with p60 start-probability according to the profile's risk weight.

    cap_score = xPts * (k + (1-k)*p60) where k = captain_risk_weight.
    k=1.0 → pure xPts, k=0.0 → fully p60-penalised.
    """
    k = profile.captain_risk_weight
    p60_aligned = p60.reindex(base_xpts.index).fillna(0.7)
    return base_xpts * (k + (1.0 - k) * p60_aligned)


# ---------------------------------------------------------------------------
# Portfolio manager
# ---------------------------------------------------------------------------

class PortfolioManager:
    """Run captain/transfer picks for multiple accounts with different profiles.

    Parameters
    ----------
    profiles:
        Mapping of profile_name → StrategyProfile. Defaults to PRESET_PROFILES.
    seed:
        Base random seed. Each account gets a deterministic child seed derived
        from ``seed + account_index`` so results are reproducible.
    """

    def __init__(
        self,
        profiles: dict[str, StrategyProfile] | None = None,
        seed: int = 42,
    ) -> None:
        self.profiles = profiles or PRESET_PROFILES
        self.seed = seed

    def generate_profiles_for_n(self, n: int) -> list[tuple[str, StrategyProfile]]:
        """Assign profiles round-robin to ``n`` accounts.

        Returns a list of ``(account_name, profile)`` pairs. Profiles are
        drawn from the preset list in a cycle, with temperature nudged slightly
        per cycle to prevent identical picks within the same profile bucket.
        """
        preset_list = list(self.profiles.items())
        assignments: list[tuple[str, StrategyProfile]] = []
        for i in range(n):
            base_name, base_profile = preset_list[i % len(preset_list)]
            cycle = i // len(preset_list)
            # Small temperature increment per cycle so accounts diverge across cycles
            temp_nudge = cycle * 0.05
            profile = StrategyProfile(
                name=f"{base_name}_{i}",
                temperature=min(1.0, base_profile.temperature + temp_nudge),
                captain_risk_weight=base_profile.captain_risk_weight,
                ep_blend_alpha=base_profile.ep_blend_alpha,
            )
            assignments.append((f"account_{i}", profile))
        return assignments

    def pick_captain(
        self,
        base_xpts: pd.Series,
        p60: pd.Series,
        profile: StrategyProfile,
        account_index: int = 0,
    ) -> int:
        """Return the recommended captain element ID for one account.

        Parameters
        ----------
        base_xpts:
            Expected points per player (element IDs as index).
        p60:
            Start/60-minute probability per player (element IDs as index).
        profile:
            Strategy profile governing risk weight and temperature.
        account_index:
            Used to derive a deterministic RNG seed.
        """
        scores = captain_scores_for_profile(base_xpts, p60, profile)
        rng = np.random.default_rng(self.seed + account_index)
        return sample_with_temperature(scores, profile.temperature, rng)

    def run_all(
        self,
        base_xpts: pd.Series,
        p60: pd.Series,
        n_accounts: int,
    ) -> pd.DataFrame:
        """Generate captain picks for ``n_accounts`` accounts.

        Returns a DataFrame with columns:
        ``account``, ``profile``, ``captain_id``, ``captain_score``, ``temperature``.
        """
        assignments = self.generate_profiles_for_n(n_accounts)
        rows: list[dict[str, Any]] = []
        for i, (account_name, profile) in enumerate(assignments):
            captain_id = self.pick_captain(base_xpts, p60, profile, i)
            score = float(base_xpts.get(captain_id, 0.0))
            rows.append({
                "account": account_name,
                "profile": profile.name,
                "captain_id": captain_id,
                "captain_score_xpts": score,
                "temperature": profile.temperature,
                "captain_risk_weight": profile.captain_risk_weight,
            })
        df = pd.DataFrame(rows)
        # Summary statistics: how many unique captains were chosen
        n_unique = df["captain_id"].nunique()
        log.info(
            "PortfolioManager: %d accounts → %d unique captain picks",
            n_accounts, n_unique,
        )
        return df

    def captain_distribution(
        self,
        base_xpts: pd.Series,
        p60: pd.Series,
        n_accounts: int,
    ) -> pd.DataFrame:
        """Return captain picks with percentage share per captain.

        Useful for understanding how many accounts converge on the same pick.
        """
        picks = self.run_all(base_xpts, p60, n_accounts)
        dist = (
            picks.groupby("captain_id")
            .agg(
                n_accounts=("account", "count"),
                profile_names=("profile", lambda x: ", ".join(x.unique()[:3])),
                avg_captain_score=("captain_score_xpts", "mean"),
            )
            .assign(share_pct=lambda d: (d["n_accounts"] / n_accounts * 100).round(1))
            .sort_values("n_accounts", ascending=False)
            .reset_index()
        )
        return dist
