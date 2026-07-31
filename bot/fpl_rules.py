"""
Exact encoding of the Fantasy Premier League 2026/27 ruleset.

Every other module in this package defers to this one for scoring, legality and
pricing. Nothing here talks to the network or executes anything -- it is pure
rule arithmetic, so it can be unit-tested and reused by the optimiser
(`optimizer.py`), the simulator (`simulator.py`) and the points predictor
(`models.FPLPointsPredictor`).

Design notes
------------
* **Money is handled in integer tenths of a million** internally. FPL prices are
  served as integers (``now_cost=75`` means £7.5m) and the selling-price rule
  involves a floor division that produces off-by-£0.1 errors if done in floats.
  Public helpers accept and return £m floats; the arithmetic underneath is
  integer.
* **Auto-substitution respects the goalkeeper slot.** A bench outfielder can
  never enter the XI in place of the goalkeeper, and the reserve keeper can only
  replace the keeper. The generic "formation remains valid" phrasing hides this
  constraint, so it is enforced explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

GKP = 1
DEF = 2
MID = 3
FWD = 4

POSITION_NAMES = {GKP: "GKP", DEF: "DEF", MID: "MID", FWD: "FWD"}
POSITION_IDS = {v: k for k, v in POSITION_NAMES.items()}

# ---------------------------------------------------------------------------
# Squad composition
# ---------------------------------------------------------------------------

SQUAD_SIZE = 15
SQUAD_COMPOSITION = {GKP: 2, DEF: 5, MID: 5, FWD: 3}

STARTING_BUDGET = 100.0          # £m
STARTING_BUDGET_TENTHS = 1000    # integer tenths

MAX_PLAYERS_PER_CLUB = 3

STARTING_XI_SIZE = 11
#: Minimum number of each position that must appear in the starting XI.
FORMATION_MINIMUMS = {GKP: 1, DEF: 3, MID: 2, FWD: 1}
#: Exactly one goalkeeper starts; the other 10 slots are outfield.
FORMATION_MAXIMUMS = {GKP: 1, DEF: 5, MID: 5, FWD: 3}

BENCH_SIZE = 4

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

#: Playing 1-59 minutes.
POINTS_PLAYING_UNDER_60 = 1
#: Playing 60 or more minutes.
POINTS_PLAYING_60_PLUS = 2
MINUTES_APPEARANCE_THRESHOLD = 1
MINUTES_FULL_APPEARANCE_THRESHOLD = 60

#: Goal scored, by position.
POINTS_GOAL = {GKP: 6, DEF: 6, MID: 5, FWD: 4}

#: Assist (all positions).
POINTS_ASSIST = 3
#: "Fantasy assist" -- penalty/free-kick won that is scored directly, or a
#: rebound from the player's own shot/save that is scored. Same value as a
#: regular assist.
POINTS_FANTASY_ASSIST = 3

#: Clean sheet, requires 60+ minutes played.
POINTS_CLEAN_SHEET = {GKP: 4, DEF: 4, MID: 1, FWD: 0}

#: Goalkeepers earn 1 point per 3 saves (integer division, remainder discarded).
SAVES_PER_POINT = 3
POINTS_PER_SAVE_BLOCK = 1

POINTS_PENALTY_SAVE = 5
POINTS_PENALTY_MISS = -2

POINTS_YELLOW_CARD = -1
#: A red card is -3. A second-yellow dismissal scores the yellow *and* the red.
POINTS_RED_CARD = -3

POINTS_OWN_GOAL = -2

#: GKP/DEF only: -1 per 2 goals conceded while the player is on the pitch.
GOALS_CONCEDED_PER_PENALTY = 2
POINTS_PER_GOALS_CONCEDED_BLOCK = -1
CONCEDING_POSITIONS = frozenset({GKP, DEF})

# --- Defensive contribution (introduced 2025/26, live for 2026/27) ----------

#: Defenders: +2 when Clearances + Blocks + Interceptions + Tackles >= 10.
DEFCON_THRESHOLD_DEF = 10
#: Midfielders and forwards: +2 when CBIT + Ball Recoveries >= 12.
DEFCON_THRESHOLD_MID_FWD = 12
POINTS_DEFENSIVE_CONTRIBUTION = 2

# --- Bonus -----------------------------------------------------------------

#: Bonus points awarded to the top three BPS scorers in each match.
BONUS_POINTS_BY_RANK = (3, 2, 1)
MAX_BONUS = 3

# ---------------------------------------------------------------------------
# Transfers, hits and multipliers
# ---------------------------------------------------------------------------

FREE_TRANSFERS_PER_GW = 1
#: Free transfers roll over, but no more than five may be banked at once.
MAX_BANKED_FT = 5
#: Each transfer beyond the available free transfers costs 4 points.
HIT_COST = 4

CAPTAIN_MULTIPLIER = 2
VICE_CAPTAIN_MULTIPLIER = 2   # applied only if the captain plays 0 minutes
TRIPLE_CAPTAIN_MULTIPLIER = 3
BENCH_MULTIPLIER = 0          # bench scores nothing unless Bench Boost is live

#: Selling price: the manager keeps the original cost plus 50% of any profit,
#: rounded *down* to the nearest £0.1m. Price falls are absorbed in full.
SELL_PROFIT_SHARE_DENOMINATOR = 2

# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------

CHIP_WILDCARD = "wildcard"
CHIP_FREE_HIT = "freehit"
CHIP_TRIPLE_CAPTAIN = "3xc"
CHIP_BENCH_BOOST = "bboost"

CHIP_NAMES = (CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST)

#: Human-readable labels, for reports and the RL agent's action space.
CHIP_LABELS = {
    CHIP_WILDCARD: "Wildcard",
    CHIP_FREE_HIT: "Free Hit",
    CHIP_TRIPLE_CAPTAIN: "Triple Captain",
    CHIP_BENCH_BOOST: "Bench Boost",
}

TOTAL_GAMEWEEKS = 38
#: Two full sets of chips. The first-half set expires at the GW19 deadline; the
#: second-half set only unlocks at GW20.
FIRST_HALF_GWS = range(1, 20)    # GW1-19
SECOND_HALF_GWS = range(20, 39)  # GW20-38
CHIP_HALF_BOUNDARY = 20

#: Only one chip may be played in any single gameweek.
MAX_CHIPS_PER_GW = 1


def chip_half(gameweek: int) -> int:
    """Return 1 for a first-half gameweek (1-19), 2 for a second-half one."""
    if gameweek < CHIP_HALF_BOUNDARY:
        return 1
    return 2


def chip_valid_gws(chip: str, half: int) -> range:
    """Gameweeks in which a given chip *set* may be played.

    Both sets contain all four chips; the only restriction is the half boundary,
    so `chip` is accepted for symmetry and validated rather than used.
    """
    if chip not in CHIP_NAMES:
        raise ValueError(f"unknown chip {chip!r}; expected one of {CHIP_NAMES}")
    return FIRST_HALF_GWS if half == 1 else SECOND_HALF_GWS


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

def to_tenths(value: float | int) -> int:
    """Convert a price in £m (7.5, or 100 for the budget) to integer tenths.

    The input is **always** interpreted as millions, whether int or float, so
    ``to_tenths(100) == 1000``. An earlier version guessed that large ints were
    already tenths; that made ``to_tenths(100)`` return a £10m budget, which is
    exactly the kind of silent, severe error this module exists to prevent.
    Values that are already in tenths (FPL's ``now_cost``) should be used
    directly and never passed through here.

    >>> to_tenths(7.5)
    75
    >>> to_tenths(100)
    1000
    """
    return int(round(float(value) * 10))


def to_millions(tenths: int) -> float:
    """Convert integer tenths (75) back to a £m float (7.5)."""
    return round(tenths / 10.0, 1)


# ---------------------------------------------------------------------------
# Rules engine
# ---------------------------------------------------------------------------

@dataclass
class FPLRules:
    """Callable encoding of the FPL ruleset.

    Instantiate once and share it: the object is stateless apart from the
    constants copied onto it for convenience, so it is safe to reuse across the
    optimiser and simulator.
    """

    max_banked_ft: int = MAX_BANKED_FT
    hit_cost: int = HIT_COST
    captain_multiplier: int = CAPTAIN_MULTIPLIER
    triple_captain_multiplier: int = TRIPLE_CAPTAIN_MULTIPLIER
    budget: float = STARTING_BUDGET
    max_per_club: int = MAX_PLAYERS_PER_CLUB
    squad_composition: dict = field(default_factory=lambda: dict(SQUAD_COMPOSITION))
    chip_names: tuple = CHIP_NAMES

    # -- squad legality ----------------------------------------------------

    def validate_squad(self, picks: Sequence[dict], budget: float | None = None
                       ) -> tuple[bool, str]:
        """Check a 15-player squad against every squad-level rule.

        Each pick is a mapping with at least ``element_type`` (1-4), ``team``
        (club id) and a price -- either ``now_cost`` (tenths) or ``price`` (£m).

        Returns ``(is_valid, reason)``; ``reason`` is ``"ok"`` when valid.
        """
        picks = list(picks)

        if len(picks) != SQUAD_SIZE:
            return False, f"squad has {len(picks)} players, expected {SQUAD_SIZE}"

        by_position: dict[int, int] = {}
        by_club: dict[int, int] = {}
        total_tenths = 0

        for p in picks:
            et = int(p["element_type"])
            if et not in SQUAD_COMPOSITION:
                return False, f"unknown element_type {et}"
            by_position[et] = by_position.get(et, 0) + 1

            club = p.get("team")
            if club is None:
                return False, "pick is missing a team/club id"
            by_club[club] = by_club.get(club, 0) + 1

            total_tenths += self._price_tenths(p)

        for et, required in SQUAD_COMPOSITION.items():
            have = by_position.get(et, 0)
            if have != required:
                return False, (f"{POSITION_NAMES[et]}: have {have}, "
                               f"need exactly {required}")

        over = [c for c, n in by_club.items() if n > self.max_per_club]
        if over:
            return False, (f"more than {self.max_per_club} players from "
                           f"club(s) {sorted(over)}")

        cap = STARTING_BUDGET_TENTHS if budget is None else to_tenths(budget)
        if total_tenths > cap:
            return False, (f"squad costs {to_millions(total_tenths)}m, "
                           f"budget is {to_millions(cap)}m")

        return True, "ok"

    def validate_formation(self, starting_xi: Sequence[dict]) -> bool:
        """True when the 11 given players form a legal FPL formation.

        Legal means: exactly 11 players, exactly 1 goalkeeper, and at least
        3 DEF / 2 MID / 1 FWD.
        """
        xi = list(starting_xi)
        if len(xi) != STARTING_XI_SIZE:
            return False

        counts = {GKP: 0, DEF: 0, MID: 0, FWD: 0}
        for p in xi:
            et = int(p["element_type"])
            if et not in counts:
                return False
            counts[et] += 1

        if counts[GKP] != FORMATION_MAXIMUMS[GKP]:
            return False
        for et, minimum in FORMATION_MINIMUMS.items():
            if counts[et] < minimum:
                return False
        for et, maximum in FORMATION_MAXIMUMS.items():
            if counts[et] > maximum:
                return False
        return True

    # -- scoring -----------------------------------------------------------

    def calc_points(self, player_stats: dict) -> int:
        """Score one player's single-match stat line under the 2026/27 rules.

        Recognised keys (all optional except ``element_type`` and ``minutes``)::

            element_type          1=GKP 2=DEF 3=MID 4=FWD  (or ``position``)
            minutes               0-90+
            goals_scored          int
            assists               int
            fantasy_assists       int   penalty won / rebound scored
            goals_conceded        int   conceded while on the pitch
            clean_sheet           bool  optional override; otherwise derived
                                        from goals_conceded == 0 and 60+ mins
            saves                 int   GKP only
            penalties_saved       int
            penalties_missed      int
            yellow_cards          int
            red_cards             int
            own_goals             int
            bonus                 int   0-3, already resolved from BPS
            clearances, blocks, interceptions, tackles, recoveries  int
            clearances_blocks_interceptions  int  (CBI aggregate alternative)
            defensive_contribution           int  (pre-aggregated CBIT/CBIRT)

        The multiplier (captaincy, bench) is *not* applied here; callers own it.
        """
        et = int(player_stats.get("element_type")
                 or POSITION_IDS.get(player_stats.get("position"), 0))
        if et not in POSITION_NAMES:
            raise ValueError(f"calc_points needs a valid element_type, got {et!r}")

        minutes = int(player_stats.get("minutes", 0) or 0)
        pts = 0

        # Appearance points.
        if minutes >= MINUTES_FULL_APPEARANCE_THRESHOLD:
            pts += POINTS_PLAYING_60_PLUS
        elif minutes >= MINUTES_APPEARANCE_THRESHOLD:
            pts += POINTS_PLAYING_UNDER_60
        else:
            # A player who did not appear scores nothing at all -- not even
            # negative points from cards they cannot have received.
            return 0

        pts += POINTS_GOAL[et] * int(player_stats.get("goals_scored", 0) or 0)
        pts += POINTS_ASSIST * int(player_stats.get("assists", 0) or 0)
        pts += POINTS_FANTASY_ASSIST * int(player_stats.get("fantasy_assists", 0) or 0)

        # Clean sheet: 60+ minutes and nothing conceded while on the pitch.
        goals_conceded = int(player_stats.get("goals_conceded", 0) or 0)
        clean_sheet = player_stats.get("clean_sheet")
        if clean_sheet is None:
            clean_sheet = (minutes >= MINUTES_FULL_APPEARANCE_THRESHOLD
                           and goals_conceded == 0)
        if clean_sheet and minutes >= MINUTES_FULL_APPEARANCE_THRESHOLD:
            pts += POINTS_CLEAN_SHEET[et]

        # Goals conceded: GKP/DEF lose a point per two conceded.
        if et in CONCEDING_POSITIONS and goals_conceded:
            pts += POINTS_PER_GOALS_CONCEDED_BLOCK * (
                goals_conceded // GOALS_CONCEDED_PER_PENALTY)

        # Saves: goalkeepers only, 1 point per full block of three.
        if et == GKP:
            saves = int(player_stats.get("saves", 0) or 0)
            pts += POINTS_PER_SAVE_BLOCK * (saves // SAVES_PER_POINT)

        pts += POINTS_PENALTY_SAVE * int(player_stats.get("penalties_saved", 0) or 0)
        pts += POINTS_PENALTY_MISS * int(player_stats.get("penalties_missed", 0) or 0)

        # Cards. A second-yellow red scores both the yellow and the red, which
        # falls out naturally from summing the two raw counts.
        pts += POINTS_YELLOW_CARD * int(player_stats.get("yellow_cards", 0) or 0)
        pts += POINTS_RED_CARD * int(player_stats.get("red_cards", 0) or 0)

        pts += POINTS_OWN_GOAL * int(player_stats.get("own_goals", 0) or 0)

        if self.qualifies_for_defcon(et, player_stats):
            pts += POINTS_DEFENSIVE_CONTRIBUTION

        bonus = int(player_stats.get("bonus", 0) or 0)
        pts += max(0, min(MAX_BONUS, bonus))

        return int(pts)

    @staticmethod
    def defcon_count(element_type: int, stats: dict) -> int:
        """Defensive-contribution tally for a player.

        Defenders count Clearances + Blocks + Interceptions + Tackles (CBIT).
        Midfielders and forwards additionally count Ball Recoveries (CBIRT).

        Accepts either the itemised components, the FPL ``clearances_blocks_
        interceptions`` aggregate, or a pre-computed ``defensive_contribution``.
        """
        if stats.get("defensive_contribution") is not None:
            return int(stats["defensive_contribution"] or 0)

        cbi = stats.get("clearances_blocks_interceptions")
        if cbi is not None:
            base = int(cbi or 0)
        else:
            base = (int(stats.get("clearances", 0) or 0)
                    + int(stats.get("blocks", 0) or 0)
                    + int(stats.get("interceptions", 0) or 0))

        total = base + int(stats.get("tackles", 0) or 0)
        if element_type in (MID, FWD):
            total += int(stats.get("recoveries", 0) or 0)
        return total

    @classmethod
    def defcon_threshold(cls, element_type: int) -> int:
        """The CBIT/CBIRT count needed for the +2. Goalkeepers cannot earn it."""
        if element_type == DEF:
            return DEFCON_THRESHOLD_DEF
        if element_type in (MID, FWD):
            return DEFCON_THRESHOLD_MID_FWD
        return math.inf  # goalkeepers are not eligible

    @classmethod
    def qualifies_for_defcon(cls, element_type: int, stats: dict) -> bool:
        """True when the player's defensive actions earn the +2 point bonus."""
        if element_type == GKP:
            return False
        return cls.defcon_count(element_type, stats) >= cls.defcon_threshold(element_type)

    @staticmethod
    def bonus_from_bps(bps_by_player: dict) -> dict:
        """Resolve raw BPS into 3/2/1 bonus points for one match.

        Ties share the higher award exactly as FPL does: two players tied on the
        top BPS both take 3 and the next takes 1; three players tied on top all
        take 3 and no further bonus is issued.
        """
        if not bps_by_player:
            return {}

        awards = {pid: 0 for pid in bps_by_player}
        ranked = sorted(set(bps_by_player.values()), reverse=True)

        rank_slot = 0
        for score in ranked:
            if rank_slot >= len(BONUS_POINTS_BY_RANK):
                break
            tied = [pid for pid, v in bps_by_player.items() if v == score]
            award = BONUS_POINTS_BY_RANK[rank_slot]
            for pid in tied:
                awards[pid] = award
            rank_slot += len(tied)
        return awards

    # -- pricing -----------------------------------------------------------

    def calc_selling_price(self, buy_price: float, current_price: float) -> float:
        """Price received when selling a player, in £m.

        The manager keeps the purchase price plus 50% of any profit, rounded
        *down* to the nearest £0.1m. A price *fall* is absorbed in full -- the
        player sells at his current (lower) price, with no rounding benefit.

        >>> r = FPLRules()
        >>> r.calc_selling_price(5.0, 5.5)   # +0.5 profit, keep 0.2
        5.2
        >>> r.calc_selling_price(5.0, 5.4)   # +0.4 profit, keep 0.2
        5.2
        >>> r.calc_selling_price(5.0, 4.7)   # loss absorbed in full
        4.7
        >>> r.calc_selling_price(5.0, 5.0)
        5.0
        """
        return to_millions(self.calc_selling_price_tenths(
            to_tenths(buy_price), to_tenths(current_price)))

    @staticmethod
    def calc_selling_price_tenths(buy_tenths: int, current_tenths: int) -> int:
        """Integer-tenths form of :meth:`calc_selling_price`.

        Kept separate so the optimiser can build MIP coefficients without ever
        touching floating point money.
        """
        profit = current_tenths - buy_tenths
        if profit <= 0:
            return current_tenths
        return buy_tenths + profit // SELL_PROFIT_SHARE_DENOMINATOR

    # -- transfers ---------------------------------------------------------

    def roll_free_transfers(self, banked: int, used: int) -> int:
        """Free transfers carried into the next gameweek.

        One FT is earned each week; the bank is capped at :data:`MAX_BANKED_FT`
        and can never go negative (extra transfers are paid for with hits, they
        do not create a deficit).
        """
        remaining = max(0, banked - used)
        return min(self.max_banked_ft, remaining + FREE_TRANSFERS_PER_GW)

    def transfer_cost(self, n_transfers: int, free_transfers: int,
                      chip: str | None = None) -> int:
        """Points deducted for making ``n_transfers`` this gameweek.

        Wildcard and Free Hit make transfers unlimited and free.
        """
        if chip in (CHIP_WILDCARD, CHIP_FREE_HIT):
            return 0
        return self.hit_cost * max(0, n_transfers - free_transfers)

    # -- captaincy ---------------------------------------------------------

    def captain_multiplier_for(self, chip: str | None = None) -> int:
        """2x normally, 3x while the Triple Captain chip is active."""
        if chip == CHIP_TRIPLE_CAPTAIN:
            return self.triple_captain_multiplier
        return self.captain_multiplier

    def resolve_captaincy(self, captain_id: int, vice_id: int,
                          played_minutes: dict) -> int:
        """Which player actually receives the multiplier.

        The vice-captain takes over only when the captain plays zero minutes.
        """
        if int(played_minutes.get(captain_id, 0) or 0) > 0:
            return captain_id
        return vice_id

    # -- auto-substitutions ------------------------------------------------

    def apply_auto_subs(self, starting_xi: Sequence[dict], bench: Sequence[dict],
                        played_minutes: dict) -> list[dict]:
        """Apply FPL's automatic substitutions and return the settled XI.

        ``starting_xi`` and ``bench`` are sequences of mappings carrying at least
        ``element`` (player id) and ``element_type``. ``bench`` must be in FPL
        bench order -- first outfield sub, second, third -- with the reserve
        goalkeeper anywhere in the list (it is detected by position, not slot).
        ``played_minutes`` maps player id -> minutes played.

        Rules enforced:

        * a starter who played 0 minutes is eligible for replacement;
        * bench players are tried strictly in order;
        * a bench player who himself played 0 minutes cannot come on;
        * the goalkeeper slot is closed to outfielders -- only the reserve
          keeper may replace the starting keeper, and he may replace nobody
          else;
        * a substitution is only made if the resulting XI is still a legal
          formation, otherwise the next bench player is tried.

        Returns a new list; the inputs are not mutated.
        """
        xi = [dict(p) for p in starting_xi]
        bench_queue = [dict(p) for p in bench]

        def minutes_of(p: dict) -> int:
            return int(played_minutes.get(p.get("element"), 0) or 0)

        # --- goalkeeper first, in its own closed slot ---------------------
        gk_starters = [p for p in xi if int(p["element_type"]) == GKP]
        bench_gks = [p for p in bench_queue if int(p["element_type"]) == GKP]
        for gk in gk_starters:
            if minutes_of(gk) > 0:
                continue
            replacement = next((b for b in bench_gks if minutes_of(b) > 0), None)
            if replacement is None:
                continue
            xi[xi.index(gk)] = replacement
            bench_gks.remove(replacement)
            bench_queue.remove(replacement)

        # --- outfield, in bench order -------------------------------------
        outfield_bench = [b for b in bench_queue if int(b["element_type"]) != GKP]
        for starter in list(xi):
            if int(starter["element_type"]) == GKP:
                continue
            if minutes_of(starter) > 0:
                continue
            for candidate in list(outfield_bench):
                if minutes_of(candidate) <= 0:
                    continue
                trial = list(xi)
                trial[trial.index(starter)] = candidate
                if self.validate_formation(trial):
                    xi = trial
                    outfield_bench.remove(candidate)
                    break

        return xi

    # -- gameweek scoring --------------------------------------------------

    def score_gameweek(self, starting_xi: Sequence[dict], bench: Sequence[dict],
                       points_by_player: dict, captain_id: int, vice_id: int,
                       played_minutes: dict | None = None,
                       chip: str | None = None, n_transfers: int = 0,
                       free_transfers: int = 1) -> dict:
        """Full gameweek score for a squad, chips and hits included.

        Returns a breakdown dict with ``total``, ``xi_points``, ``bench_points``,
        ``captain_points`` (the extra points from the multiplier) and ``hits``.
        """
        played_minutes = played_minutes or {}
        settled_xi = self.apply_auto_subs(starting_xi, bench, played_minutes)
        settled_ids = {p["element"] for p in settled_xi}

        xi_points = sum(int(points_by_player.get(p["element"], 0)) for p in settled_xi)

        effective_captain = self.resolve_captaincy(captain_id, vice_id, played_minutes)
        multiplier = self.captain_multiplier_for(chip)
        captain_extra = 0
        if effective_captain in settled_ids:
            captain_extra = (multiplier - 1) * int(
                points_by_player.get(effective_captain, 0))

        bench_points = 0
        if chip == CHIP_BENCH_BOOST:
            bench_points = sum(int(points_by_player.get(p["element"], 0))
                               for p in bench if p["element"] not in settled_ids)

        hits = self.transfer_cost(n_transfers, free_transfers, chip)

        return {
            "total": xi_points + captain_extra + bench_points - hits,
            "xi_points": xi_points,
            "captain_points": captain_extra,
            "bench_points": bench_points,
            "hits": hits,
            "effective_captain": effective_captain,
            "settled_xi": [p["element"] for p in settled_xi],
        }

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _price_tenths(pick: dict) -> int:
        """Read a price off a pick mapping in whichever form it carries."""
        if "now_cost" in pick and pick["now_cost"] is not None:
            return int(pick["now_cost"])
        if "selling_price" in pick and pick["selling_price"] is not None:
            return int(pick["selling_price"])
        if "price" in pick and pick["price"] is not None:
            return to_tenths(pick["price"])
        raise KeyError("pick has no now_cost / selling_price / price")


def positions_of(picks: Iterable[dict]) -> dict[int, int]:
    """Count picks per position -- a small helper used across modules."""
    counts = {GKP: 0, DEF: 0, MID: 0, FWD: 0}
    for p in picks:
        counts[int(p["element_type"])] += 1
    return counts


#: A module-level default instance, since the rules are stateless.
RULES = FPLRules()
