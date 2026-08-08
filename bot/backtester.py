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
import traceback
from typing import Sequence

import numpy as np
import pandas as pd

from .fpl_rules import (
    CHIP_FREE_HIT, CHIP_WILDCARD, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST,
    GKP, DEF, MID, FWD,
    SQUAD_COMPOSITION, STARTING_XI_SIZE,
    FPLRules,
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
        expected_points_fn=None,
    ):
        self.hist = history_df.copy()
        self.test_season = test_season
        self.budget = budget
        self.rules = FPLRules()
        self.opt = SquadOptimizer()

        # ── validate required columns ─────────────────────────────────────
        required = {"element", "GW", "season", "total_points", "value",
                    "minutes", "team"}
        missing = required - set(self.hist.columns)
        if missing:
            raise ValueError(
                f"history_df is missing required columns: {sorted(missing)}\n"
                f"Available: {sorted(self.hist.columns.tolist())}"
            )

        # ── normalise element_type ────────────────────────────────────────
        if "element_type" in self.hist.columns:
            self.hist["element_type"] = (
                self.hist["element_type"]
                .apply(lambda x: _POS_MAP.get(str(x), x)
                       if not str(x).isdigit() else int(x))
                .astype(int)
            )
        elif "position" in self.hist.columns:
            self.hist["element_type"] = (
                self.hist["position"].map(_POS_MAP).fillna(MID).astype(int)
            )
        else:
            raise ValueError(
                "history_df needs 'element_type' (int) or 'position' (GKP/DEF/MID/FWD)"
            )

        # Drop manager rows (element NaN or position not in standard set)
        self.hist = self.hist[self.hist["element_type"].isin([GKP, DEF, MID, FWD])]
        self.hist = self.hist.dropna(subset=["element"])
        self.hist["element"] = self.hist["element"].astype(int)

        # ── encode team as int for the MIP club-limit constraint ──────────
        if not pd.api.types.is_numeric_dtype(self.hist["team"]):
            teams = sorted(self.hist["team"].dropna().unique())
            team_map = {t: i + 1 for i, t in enumerate(teams)}
            self.hist["team_id"] = self.hist["team"].map(team_map).fillna(0).astype(int)
        else:
            self.hist["team_id"] = self.hist["team"].astype(int)

        # ── display name column ───────────────────────────────────────────
        for cand in ("web_name", "name", "second_name"):
            if cand in self.hist.columns:
                self.hist["_name"] = self.hist[cand]
                break
        else:
            self.hist["_name"] = self.hist["element"].astype(str)

        # ── value must be integer tenths (e.g. 65 = £6.5m) ──────────────
        self.hist["value"] = pd.to_numeric(self.hist["value"], errors="coerce").fillna(50).astype(int)

        # Optional callable(before_gw: int) -> pd.Series indexed by element.
        # When supplied, replaces the rolling-mean expected_points with ML predictions.
        self.expected_points_fn = expected_points_fn

        print(f"[backtester] init OK — {len(self.hist):,} rows, "
              f"seasons: {sorted(self.hist['season'].unique())}")

    # ------------------------------------------------------------------
    # Pool building
    # ------------------------------------------------------------------

    def _expected_pts(self, before_gw: int) -> pd.Series:
        """Per-GW average points using only data strictly before this GW."""
        train = self.hist[
            (self.hist["season"] != self.test_season)
            | (self.hist["GW"] < before_gw)
        ]
        return train.groupby("element")["total_points"].mean()

    def _build_pool(self, before_gw: int) -> pd.DataFrame:
        """Player pool for GW ``before_gw``, with prices and expected points."""
        if self.expected_points_fn is not None:
            exp = self.expected_points_fn(before_gw)
        else:
            exp = self._expected_pts(before_gw)

        test = self.hist[self.hist["season"] == self.test_season].copy()
        earliest = (
            test.sort_values("GW")
            .groupby("element", as_index=False)
            .first()
        )

        # Use price from the previous GW if available
        if before_gw > 1:
            prev = test[test["GW"] == before_gw - 1]
            if not prev.empty:
                price_lu = prev.set_index("element")["value"]
                # Drop duplicate element entries (can arise in DGW fixtures).
                price_lu = price_lu[~price_lu.index.duplicated(keep="last")]
                earliest["value"] = (
                    earliest["element"].map(price_lu).fillna(earliest["value"])
                )

        pool = pd.DataFrame({
            "element":        earliest["element"].astype(int),
            "element_type":   earliest["element_type"].astype(int),
            "team":           earliest["team_id"].astype(int),
            "now_cost":       earliest["value"].astype(int),
            "web_name":       earliest["_name"].fillna(earliest["element"].astype(str)),
            "expected_points": earliest["element"].map(exp).fillna(2.0),
        })
        return pool.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _gw_actuals(self, gw: int) -> tuple[dict, dict]:
        """Return (points_by_element, minutes_by_element) for a GW."""
        rows = self.hist[
            (self.hist["season"] == self.test_season) & (self.hist["GW"] == gw)
        ]
        pts  = rows.set_index("element")["total_points"].fillna(0).astype(int).to_dict()
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
        """Make the transfer with the highest marginal expected-points gain."""
        pool_idx = pool.set_index("element")

        squad = squad_df.copy()
        squad["_exp"] = squad["element"].map(
            pool_idx["expected_points"]
        ).fillna(0)

        best_gain = 0.0  # only transfer if gain is positive
        best_out: pd.Series | None = None
        best_in: pd.Series | None = None
        best_new_bank = bank

        for _, out_row in squad.iterrows():
            # Use pool's current price, not the stale now_cost stored at purchase time
            current_cost = int(
                pool_idx["now_cost"].get(int(out_row["element"]), out_row["now_cost"])
            )
            purchase_price = int(
                out_row.get("purchase_price", current_cost) or current_cost
            )
            out_sell_tenths = self.rules.calc_selling_price_tenths(
                purchase_price, current_cost
            )
            budget_tenths = out_sell_tenths + int(round(bank * 10))

            pos = int(out_row["element_type"])
            owned = set(squad["element"].astype(int)) - {int(out_row["element"])}
            club_counts = (
                squad[squad["element"] != int(out_row["element"])]["team"].value_counts()
            )

            cands = pool[
                (pool["element_type"] == pos)
                & (~pool["element"].isin(owned))
                & (pool["now_cost"].astype(int) <= budget_tenths)
                & (pool["team"].map(lambda t: int(club_counts.get(t, 0)) < 3))
            ].sort_values("expected_points", ascending=False)

            if cands.empty:
                continue

            in_row = cands.iloc[0]
            gain = float(in_row["expected_points"]) - float(out_row["_exp"])
            if gain > best_gain:
                best_gain = gain
                best_out = out_row
                best_in = in_row
                best_new_bank = (budget_tenths - int(in_row["now_cost"])) / 10.0

        if best_out is None:
            return squad_df, bank, False

        new_squad = squad[squad["element"] != int(best_out["element"])].copy()
        in_dict = best_in.to_dict()
        in_dict["purchase_price"] = int(best_in["now_cost"])
        new_squad = pd.concat(
            [new_squad, pd.DataFrame([in_dict])], ignore_index=True
        )
        return new_squad, best_new_bank, True

    # ------------------------------------------------------------------
    # Fallback squad (when MIP fails)
    # ------------------------------------------------------------------

    def _fallback_squad(self, pool: pd.DataFrame) -> pd.DataFrame:
        """Greedy positional fill — used when the MIP solver fails."""
        pool = pool.sort_values("expected_points", ascending=False)
        slots = dict(SQUAD_COMPOSITION)
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
        if df.empty:
            raise RuntimeError("fallback squad is empty — pool has too few players")
        df["purchase_price"] = df["now_cost"].astype(int)
        return df.reset_index(drop=True)

    def _best_xi_from_pool(
        self, squad_ids: list[int], pool: pd.DataFrame
    ) -> tuple[list[int], list[int], int, int]:
        """Pick XI/bench/captain without the MIP solver (pure ranking fallback)."""
        pool_idx = pool.set_index("element")
        in_pool = [e for e in squad_ids if e in pool_idx.index]
        ranked = sorted(in_pool, key=lambda e: -float(pool_idx["expected_points"].get(e, 0)))

        xi, bench = ranked[:STARTING_XI_SIZE], ranked[STARTING_XI_SIZE:]
        capt = xi[0] if xi else 0
        vice = xi[1] if len(xi) > 1 else capt
        return xi, bench, capt, vice

    # ------------------------------------------------------------------
    # Core simulation
    # ------------------------------------------------------------------

    def run(self, strategy: str = "1ft",
            chip_gws: dict | None = None) -> pd.DataFrame:
        """Simulate a full season and return per-GW results.

        chip_gws maps gameweek → chip name, e.g.
          {26: "bboost", 33: "3xc", 34: "freehit"}
        Chips are applied exactly once; scheduling a chip a second time is a no-op.
        """
        chip_gws = chip_gws or {}
        test_gws = sorted(
            self.hist.loc[self.hist["season"] == self.test_season, "GW"].unique()
        )
        if not test_gws:
            raise ValueError(f"No data for test season {self.test_season!r}")

        print(f"[backtester] {strategy}: running {len(test_gws)} GWs ...")

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
            try:
                pool = self._build_pool(before_gw=gw)
                chip: str | None = None
                n_transfers = 0

                # ── decide squad ──────────────────────────────────────────
                if squad_df is None:
                    # GW1: build initial squad via MIP (fallback to greedy)
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
                    except Exception as exc:
                        print(f"[backtester] GW{gw} MIP failed ({exc}); using greedy fallback")
                        squad_df = self._fallback_squad(pool)
                        squad_ids = squad_df["element"].astype(int).tolist()
                        xi_ids, bench_ids, captain_id, vice_id = (
                            self._best_xi_from_pool(squad_ids, pool)
                        )
                        bank = max(0.0, self.budget - squad_df["now_cost"].sum() / 10.0)

                else:
                    squad_ids = squad_df["element"].astype(int).tolist()

                    # Wildcard at GW20
                    if (
                        strategy == "wildcard20"
                        and gw == 20
                        and CHIP_WILDCARD not in chips_used
                    ):
                        try:
                            pre_wc_ids = set(squad_ids)
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
                            print(f"[backtester] GW{gw} wildcard MIP failed ({exc}); skipping")

                    elif strategy != "passive":
                        # Use all available free transfers greedily (no hits).
                        n_transfers = 0
                        for _ in range(free_transfers):
                            new_squad, new_bank, made = self._greedy_transfer(
                                squad_df, pool, bank
                            )
                            if not made:
                                break
                            squad_df = new_squad
                            bank = new_bank
                            n_transfers += 1
                        squad_ids = squad_df["element"].astype(int).tolist()

                    # Re-optimise XI each week
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
                        print(f"[backtester] GW{gw} XI-opt failed ({exc}); using ranking fallback")
                        xi_ids, bench_ids, captain_id, vice_id = (
                            self._best_xi_from_pool(squad_ids, pool)
                        )

                # ── apply scheduled chips ────────────────────────────────
                scheduled = chip_gws.get(gw)
                if scheduled and scheduled not in chips_used:
                    if scheduled == CHIP_FREE_HIT:
                        # Build a fresh squad from players with a fixture this GW
                        # (players who appear in the actual GW data had teams playing).
                        gw_elements = set(
                            self.hist.loc[
                                (self.hist["season"] == self.test_season)
                                & (self.hist["GW"] == gw),
                                "element",
                            ].astype(int)
                        )
                        fh_pool = pool[pool["element"].isin(gw_elements)].copy()
                        if len(fh_pool) >= 15:
                            pool_costs = pool.set_index("element")["now_cost"]
                            fh_budget = (
                                squad_df["element"].map(pool_costs)
                                .fillna(squad_df["now_cost"])
                                .sum() / 10.0
                                + bank
                            )
                            try:
                                res_fh = self.opt.optimize_initial_squad(
                                    fh_pool, fh_budget, "expected_points"
                                )
                                fh_ids = set(p["element"] for p in res_fh["squad"])
                                fh_squad = fh_pool[fh_pool["element"].isin(fh_ids)].copy()
                                fh_squad["purchase_price"] = fh_squad["now_cost"].astype(int)
                                # Score with FH squad; restore real squad after
                                fh_xi_ids    = [p["element"] for p in res_fh["starting_xi"]]
                                fh_bench_ids = [p["element"] for p in res_fh["bench"]]
                                fh_capt      = res_fh["captain"]
                                fh_vice      = res_fh["vice_captain"]
                                # Override XI for this GW only
                                xi_ids, bench_ids = fh_xi_ids, fh_bench_ids
                                captain_id, vice_id = fh_capt, fh_vice
                                squad_df_for_gw = fh_squad
                                chip = CHIP_FREE_HIT
                                chips_used.add(CHIP_FREE_HIT)
                                n_transfers = 0  # no real transfers during FH
                            except Exception as exc:
                                print(f"[backtester] GW{gw} FH MIP failed ({exc}); no chip")
                                squad_df_for_gw = squad_df
                        else:
                            squad_df_for_gw = squad_df
                    else:
                        # TC or BB: just tag the chip; squad/XI unchanged
                        chip = scheduled
                        chips_used.add(scheduled)
                        squad_df_for_gw = squad_df
                else:
                    squad_df_for_gw = squad_df

                # ── score actual GW ───────────────────────────────────────
                points_by_player, played_minutes = self._gw_actuals(gw)
                pos_map = (
                    squad_df_for_gw.set_index("element")["element_type"].astype(int).to_dict()
                )

                xi_dicts    = [{"element": e, "element_type": pos_map.get(e, MID)} for e in xi_ids]
                bench_dicts = [{"element": e, "element_type": pos_map.get(e, MID)} for e in bench_ids]

                gw_result = self.rules.score_gameweek(
                    xi_dicts, bench_dicts, points_by_player,
                    captain_id, vice_id, played_minutes,
                    chip, n_transfers, free_transfers,
                )
                cumulative += gw_result["total"]

                results.append({
                    "gw":             gw,
                    "strategy":       strategy,
                    "pts":            gw_result["total"],
                    "xi_pts":         gw_result["xi_points"],
                    "captain_bonus":  gw_result["captain_points"],
                    "hits":           gw_result["hits"],
                    "bench_pts":      gw_result["bench_points"],
                    "chip":           chip,
                    "n_transfers":    n_transfers,
                    "captain_id":     captain_id,
                    "free_transfers": free_transfers,
                    "bank":           round(bank, 1),
                    "cumulative":     cumulative,
                })

                used_ft = n_transfers if chip not in (CHIP_WILDCARD, CHIP_FREE_HIT) else 0
                free_transfers = self.rules.roll_free_transfers(free_transfers, used_ft)

            except Exception as exc:
                print(f"[backtester] GW{gw} strategy={strategy} FAILED: {exc}")
                traceback.print_exc()
                # Record 0-point blank for this GW so the season isn't lost
                results.append({
                    "gw": gw, "strategy": strategy,
                    "pts": 0, "xi_pts": 0, "captain_bonus": 0,
                    "hits": 0, "bench_pts": 0, "chip": None,
                    "n_transfers": 0, "captain_id": 0,
                    "free_transfers": free_transfers,
                    "bank": round(bank, 1), "cumulative": cumulative,
                })

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
        print(f"\n{'='*50}\nStrategy: {strat}\n{'='*50}")
        try:
            df = backtester.run(strategy=strat)
            if not df.empty:
                total = df["pts"].sum()
                print(f"  → {strat}: {total} total pts over {len(df)} GWs")
                frames.append(df)
        except Exception as exc:
            print(f"[backtester] strategy '{strat}' failed at top level: {exc}")
            traceback.print_exc()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
