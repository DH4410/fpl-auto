"""
Season-long FPL strategy backtester.

Trains on historical Vaastav data then simulates a complete test season:
  - Initial squad via MIP optimizer (using historical average ppg)
  - Weekly free transfers (1 per GW, bankable to 5)
  - Optional chips at programmatic timing
  - Actual points from Vaastav ``total_points`` column

Three built-in strategies:
  "passive"     -- pick initial squad once, never transfer
  "1ft"         -- 1 greedy free transfer per GW
  "wildcard20"  -- 1ft for GWs 1-19, full wildcard at GW20, 1ft after

Run multiple strategies to compare total season points:

    from bot.backtester import SeasonBacktester, compare_strategies
    bt = SeasonBacktester(history, test_season="2024-25")
    results = compare_strategies(bt, ["passive", "1ft", "wildcard20"])
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

from .fpl_rules import (
    CHIP_BENCH_BOOST, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_WILDCARD,
    FREE_TRANSFERS_PER_GW, GKP, DEF, MID, FWD,
    MAX_BANKED_FT, SQUAD_COMPOSITION, STARTING_XI_SIZE,
    FPLRules, RULES,
)
from .optimizer import SquadOptimizer

log = logging.getLogger(__name__)

_POS_MAP = {"GKP": GKP, "DEF": DEF, "MID": MID, "FWD": FWD}


class SeasonBacktester:
    """Simulate a full FPL season and score actual historical points."""

    def __init__(
        self,
        history_df: pd.DataFrame,
        test_season: str,
        budget: float = 100.0,
    ):
        self.hist = history_df.copy()
        self.test_season = test_season
        self.budget = budget
        self.rules = FPLRules()
        self.opt = SquadOptimizer()

        # Normalise element_type to int if it arrived as "GKP"/"DEF"/...
        if "element_type" in self.hist.columns:
            self.hist["element_type"] = (
                self.hist["element_type"]
                .apply(lambda x: _POS_MAP.get(str(x), x) if not str(x).isdigit() else int(x))
                .astype(int)
            )
        elif "position" in self.hist.columns:
            self.hist["element_type"] = self.hist["position"].map(_POS_MAP).fillna(3).astype(int)

        # Encode team names as stable ints (optimizer needs int team IDs)
        if self.hist["team"].dtype == object:
            teams = sorted(self.hist["team"].dropna().unique())
            team_map = {t: i + 1 for i, t in enumerate(teams)}
            self.hist["team_id"] = self.hist["team"].map(team_map).fillna(0).astype(int)
        else:
            self.hist["team_id"] = self.hist["team"].astype(int)

    # ------------------------------------------------------------------
    # Pool building
    # ------------------------------------------------------------------

    def _expected_pts(self, before_gw: int) -> pd.Series:
        """Historical average pts per appearance, using only data before this GW."""
        train = self.hist[
            (self.hist["season"] != self.test_season)
            | (self.hist["GW"] < before_gw)
        ]
        return train.groupby("element")["total_points"].mean()

    def _build_pool(self, before_gw: int) -> pd.DataFrame:
        """Player pool for GW `before_gw`, with prices and expected points."""
        exp = self._expected_pts(before_gw)

        # Player metadata from test season: use the earliest available GW row
        test = self.hist[self.hist["season"] == self.test_season]
        earliest = (
            test.sort_values("GW")
            .groupby("element")
            .first()
            .reset_index()
        )
        # For prices: prefer the row just before this GW
        if before_gw > 1:
            prev = test[test["GW"] == before_gw - 1]
            if not prev.empty:
                price_lookup = prev.set_index("element")["value"].astype(int)
                earliest["value"] = earliest["element"].map(price_lookup).fillna(earliest["value"])

        meta = earliest[
            ["element", "element_type", "team_id", "value"]
        ].copy()
        meta.columns = ["element", "element_type", "team", "now_cost"]

        # Add display name
        name_col = next((c for c in ("web_name", "name", "second_name") if c in earliest.columns), None)
        if name_col:
            meta["web_name"] = earliest[name_col].values
        else:
            meta["web_name"] = meta["element"].astype(str)

        meta["expected_points"] = meta["element"].map(exp).fillna(2.0)
        return meta.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _gw_actuals(self, gw: int) -> tuple[dict, dict]:
        """(points_by_player, minutes_by_player) from actual Vaastav data."""
        rows = self.hist[
            (self.hist["season"] == self.test_season) & (self.hist["GW"] == gw)
        ]
        pts = rows.set_index("element")["total_points"].fillna(0).astype(int).to_dict()
        mins = rows.set_index("element")["minutes"].fillna(0).astype(int).to_dict()
        return pts, mins

    # ------------------------------------------------------------------
    # Transfer helpers
    # ------------------------------------------------------------------

    def _greedy_transfer(
        self,
        squad_df: pd.DataFrame,
        pool: pd.DataFrame,
        bank: float,
    ) -> tuple[pd.DataFrame, float, bool]:
        """Transfer out the squad's weakest player, in the pool's best replacement.

        Returns (new_squad, new_bank, transfer_made).
        """
        pool_idx = pool.set_index("element")

        # Player to sell: lowest expected pts among current squad
        squad = squad_df.copy()
        squad["_exp"] = squad["element"].map(
            pool_idx["expected_points"]
        ).fillna(0)
        out_row = squad.sort_values("_exp").iloc[0]

        out_sell_tenths = self.rules.calc_selling_price_tenths(
            int(out_row.get("purchase_price", out_row["now_cost"]) or out_row["now_cost"]),
            int(out_row["now_cost"]),
        )
        budget_tenths = out_sell_tenths + int(round(bank * 10))

        # Find replacement: same position, not in squad, within budget, max-3-club
        pos = int(out_row["element_type"])
        owned = set(squad["element"].astype(int)) - {int(out_row["element"])}
        club_counts = squad[squad["element"] != int(out_row["element"])]["team"].value_counts()

        cands = pool[
            (pool["element_type"] == pos)
            & (~pool["element"].isin(owned))
            & (pool["now_cost"].astype(int) <= budget_tenths)
            & (pool["team"].map(lambda t: int(club_counts.get(t, 0)) < 3))
        ].sort_values("expected_points", ascending=False)

        if cands.empty:
            return squad_df, bank, False

        in_row = cands.iloc[0]
        new_bank = (budget_tenths - int(in_row["now_cost"])) / 10.0

        new_squad = squad[squad["element"] != int(out_row["element"])].copy()
        in_dict = in_row.to_dict()
        in_dict["purchase_price"] = int(in_row["now_cost"])
        new_squad = pd.concat(
            [new_squad, pd.DataFrame([in_dict])], ignore_index=True
        )
        return new_squad, new_bank, True

    # ------------------------------------------------------------------
    # Fallback squad (when MIP fails)
    # ------------------------------------------------------------------

    def _fallback_squad(self, pool: pd.DataFrame) -> pd.DataFrame:
        """Greedy positional fill when the MIP fails."""
        pool = pool.sort_values("expected_points", ascending=False)
        slots = dict(SQUAD_COMPOSITION)  # {GKP:2, DEF:5, MID:5, FWD:3}
        used = {k: 0 for k in slots}
        rows = []
        for _, r in pool.iterrows():
            et = int(r["element_type"])
            if used.get(et, 0) < slots.get(et, 0):
                rows.append(r)
                used[et] += 1
            if sum(used.values()) == 15:
                break
        df = pd.DataFrame(rows)
        df["purchase_price"] = df["now_cost"].astype(int)
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Core simulation
    # ------------------------------------------------------------------

    def run(self, strategy: str = "1ft") -> pd.DataFrame:
        """Simulate a full season and return per-GW results.

        Parameters
        ----------
        strategy : str
            "passive"     -- never transfer
            "1ft"         -- 1 greedy free transfer per GW
            "wildcard20"  -- 1ft for GWs 1-19, wildcard at GW20, 1ft after
        """
        test_gws = sorted(
            self.hist.loc[self.hist["season"] == self.test_season, "GW"].unique()
        )
        if not test_gws:
            raise ValueError(f"No data for test season {self.test_season!r}")

        results = []
        squad_df: pd.DataFrame | None = None
        bank = 0.0
        free_transfers = 1
        chips_used: set[str] = set()
        cumulative = 0
        xi_ids: list[int] = []
        bench_ids: list[int] = []
        captain_id: int = 0
        vice_id: int = 0

        for gw in test_gws:
            pool = self._build_pool(before_gw=gw)
            chip: str | None = None
            n_transfers = 0

            # ---- decide squad for this GW --------------------------------
            if squad_df is None:
                # GW1: initial squad via MIP
                try:
                    res = self.opt.optimize_initial_squad(
                        pool, self.budget, "expected_points"
                    )
                    squad_ids = [p["element"] for p in res["squad"]]
                    squad_df = pool[pool["element"].isin(squad_ids)].copy()
                    squad_df["purchase_price"] = squad_df["now_cost"].astype(int)
                    bank = self.budget - squad_df["now_cost"].sum() / 10.0
                    xi_ids    = [p["element"] for p in res["starting_xi"]]
                    bench_ids = [p["element"] for p in res["bench"]]
                    captain_id = res["captain"]
                    vice_id    = res["vice_captain"]
                    n_transfers = 0
                except Exception as exc:
                    log.warning("GW%d MIP failed (%s); using greedy fallback", gw, exc)
                    squad_df = self._fallback_squad(pool)
                    squad_ids = squad_df["element"].tolist()
                    xi_ids    = squad_ids[:STARTING_XI_SIZE]
                    bench_ids = squad_ids[STARTING_XI_SIZE:]
                    best_in_xi = max(xi_ids, key=lambda e: float(
                        pool.set_index("element")["expected_points"].get(e, 0)))
                    second = max((e for e in xi_ids if e != best_in_xi), key=lambda e: float(
                        pool.set_index("element")["expected_points"].get(e, 0)))
                    captain_id, vice_id = best_in_xi, second
                    bank = 0.0

            else:
                squad_ids = squad_df["element"].astype(int).tolist()

                # Wildcard at GW20 if the strategy says so
                if (
                    strategy == "wildcard20"
                    and gw == 20
                    and CHIP_WILDCARD not in chips_used
                ):
                    try:
                        pre_wc_ids = set(squad_ids)  # old squad, before wildcard replaces it
                        res = self.opt.optimize_initial_squad(
                            pool, self.budget + bank, "expected_points"
                        )
                        squad_ids = [p["element"] for p in res["squad"]]
                        squad_df = pool[pool["element"].isin(squad_ids)].copy()
                        squad_df["purchase_price"] = squad_df["now_cost"].astype(int)
                        bank = (self.budget + bank) - squad_df["now_cost"].sum() / 10.0
                        xi_ids    = [p["element"] for p in res["starting_xi"]]
                        bench_ids = [p["element"] for p in res["bench"]]
                        captain_id = res["captain"]
                        vice_id    = res["vice_captain"]
                        chip = CHIP_WILDCARD
                        chips_used.add(CHIP_WILDCARD)
                        free_transfers = 1
                        n_transfers = sum(1 for e in squad_ids if e not in pre_wc_ids)
                    except Exception as exc:
                        log.warning("GW%d wildcard MIP failed: %s", gw, exc)

                elif strategy != "passive":
                    # One greedy transfer
                    new_squad, new_bank, made = self._greedy_transfer(
                        squad_df, pool, bank
                    )
                    if made:
                        squad_df = new_squad
                        bank = new_bank
                        n_transfers = 1
                    squad_ids = squad_df["element"].astype(int).tolist()

                # Re-optimise XI from the (possibly updated) squad
                try:
                    exp_s = pool.set_index("element")["expected_points"]
                    squad_with_pts = squad_df.copy()
                    squad_with_pts["expected_points"] = (
                        squad_with_pts["element"].map(exp_s).fillna(0)
                    )
                    res_xi = self.opt.optimize_starting_xi(
                        squad_with_pts, "expected_points"
                    )
                    xi_ids    = [p["element"] for p in res_xi["starting_xi"]]
                    bench_ids = [p["element"] for p in res_xi["bench"]]
                    captain_id = res_xi["captain"]
                    vice_id    = res_xi["vice_captain"]
                except Exception as exc:
                    log.warning("GW%d XI opt failed (%s); keeping previous XI", gw, exc)

            # ---- score actual GW -----------------------------------------
            points_by_player, played_minutes = self._gw_actuals(gw)
            pos_map = squad_df.set_index("element")["element_type"].astype(int).to_dict()

            xi_dicts    = [{"element": e, "element_type": pos_map.get(e, MID)} for e in xi_ids]
            bench_dicts = [{"element": e, "element_type": pos_map.get(e, MID)} for e in bench_ids]

            gw_result = self.rules.score_gameweek(
                xi_dicts, bench_dicts, points_by_player,
                captain_id, vice_id, played_minutes,
                chip, n_transfers, free_transfers,
            )
            cumulative += gw_result["total"]

            results.append({
                "gw":            gw,
                "strategy":      strategy,
                "pts":           gw_result["total"],
                "xi_pts":        gw_result["xi_points"],
                "captain_bonus": gw_result["captain_points"],
                "hits":          gw_result["hits"],
                "bench_pts":     gw_result["bench_points"],
                "chip":          chip,
                "n_transfers":   n_transfers,
                "captain_id":    captain_id,
                "free_transfers":free_transfers,
                "bank":          round(bank, 1),
                "cumulative":    cumulative,
            })

            # Roll FT bank: one new FT per week, capped at 5
            # roll_free_transfers(banked, used) -> min(5, max(0, banked-used) + 1)
            used_ft = n_transfers if chip not in (CHIP_WILDCARD, CHIP_FREE_HIT) else 0
            free_transfers = self.rules.roll_free_transfers(free_transfers, used_ft)

        return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------

def compare_strategies(
    backtester: SeasonBacktester,
    strategies: Sequence[str] = ("passive", "1ft", "wildcard20"),
) -> pd.DataFrame:
    """Run multiple strategies and return a combined per-GW DataFrame."""
    frames = []
    for strat in strategies:
        log.info("Running strategy: %s", strat)
        try:
            df = backtester.run(strategy=strat)
            frames.append(df)
        except Exception as exc:
            import traceback
            log.error("Strategy %r failed: %s", strat, exc)
            print(f"[backtester] strategy '{strat}' failed: {exc}")
            traceback.print_exc()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
