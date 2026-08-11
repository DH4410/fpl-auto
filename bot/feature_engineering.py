"""
Module 1 -- feature engineering.

Turns raw per-gameweek rows and fixture lists into the model matrix consumed by
``models.py``. Four families of features are built:

1. **EWMA form** (:func:`compute_ewma_stats`) -- exponentially weighted recent
   performance. Recency-weighting beats flat season averages because FPL form is
   strongly autocorrelated over ~6 gameweeks and role changes (a new penalty
   taker, a positional switch) show up fast.
2. **Fixture difficulty** (:func:`fixture_difficulty_features`) -- forward-looking
   opponent strength over the next N gameweeks, blended from FPL's own FDR and
   the fitted team ratings.
3. **Dixon-Coles team strength** (:func:`team_strength_matrix`) -- separate
   attack and defence ratings per team with a home advantage term, fitted by
   maximum likelihood on historical scorelines. These ratings feed the bivariate
   Poisson simulator directly.
4. **Promoted-team priors** (:func:`promoted_team_priors`) -- newly promoted
   sides have no Premier League history, so they are seeded at the
   league-worst-quartile rating rather than the league mean, which would
   systematically over-rate them.

Deliberately *not* implemented here -- see the TODOs at the bottom of the file
for what each would need:

* **Odds de-vigging.** No bookmaker odds API is available to this project.
* **NLP news/press-conference sentiment.** No scraper is available.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .fpl_rules import DEF, FWD, GKP, MID, POSITION_IDS

log = logging.getLogger(__name__)

#: Stats that get an EWMA treatment. Anything absent from the frame is skipped.
EWMA_STATS = (
    "minutes", "goals_scored", "assists", "bonus", "bps", "clean_sheets",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "saves", "goals_conceded", "total_points",
    "influence", "creativity", "threat", "ict_index",
    "clearances_blocks_interceptions", "recoveries", "tackles",
    "defensive_contribution",
    "yellow_cards", "red_cards",
)

DEFAULT_ALPHA = 0.25
DEFAULT_HORIZON = 5

#: FPL's own fixture-difficulty ratings run 2 (easiest) to 5 (hardest).
FDR_MIN, FDR_MAX = 2, 5


# ---------------------------------------------------------------------------
# 1. EWMA form
# ---------------------------------------------------------------------------

def compute_ewma_stats(player_history_df: pd.DataFrame, alpha: float = DEFAULT_ALPHA,
                       stats: Sequence[str] = EWMA_STATS,
                       id_col: str = "element",
                       order_cols: Sequence[str] = ("season", "round"),
                       ) -> pd.DataFrame:
    """Exponentially weighted moving averages of per-gameweek player stats.

    For every player, each stat in ``stats`` gets an ``ewma_<stat>`` column
    computed with smoothing factor ``alpha`` (0.25 gives a ~7-gameweek effective
    window). Values are **shifted by one gameweek** so a row's features only
    describe matches *before* that row -- without the shift the feature set
    leaks the label and every backtest looks brilliant and is worthless.

    Rows are sorted by ``order_cols`` first, so multi-season frames chain
    correctly across the season boundary.

    Also emits:

    * ``games_played`` -- expanding count of prior appearances, a sample-size
      proxy the models use to discount noisy early-season EWMAs;
    * ``ewma_start_rate`` -- share of recent matches started (from ``starts``);
    * ``ewma_p60_rate`` -- share of recent matches with 60+ minutes, the single
      most useful minutes feature.
    """
    df = player_history_df.copy()

    order = [c for c in order_cols if c in df.columns]
    if not order:
        raise ValueError(f"none of {order_cols} present; cannot order history")
    df = df.sort_values([*order, id_col]).reset_index(drop=True)

    # Derived per-match binaries before the grouping, so they get EWMA'd too.
    if "minutes" in df.columns:
        df["played_60"] = (df["minutes"] >= 60).astype(float)
        df["played_any"] = (df["minutes"] > 0).astype(float)
    if "starts" in df.columns:
        df["start_flag"] = pd.to_numeric(df["starts"], errors="coerce").clip(0, 1)

    present = [s for s in stats if s in df.columns]
    missing = [s for s in stats if s not in df.columns]
    if missing:
        log.info("compute_ewma_stats: absent from frame, skipped: %s", missing)

    extra = [c for c in ("played_60", "played_any", "start_flag") if c in df.columns]
    targets = present + extra

    grouped = df.groupby(id_col, sort=False)
    for col in targets:
        numeric = pd.to_numeric(df[col], errors="coerce")
        df[f"_num_{col}"] = numeric

    for col in targets:
        # Shift within player: features must not see the current gameweek.
        df[f"ewma_{col}"] = df.groupby(id_col, sort=False)[
            f"_num_{col}"].transform(
            lambda s: s.ewm(alpha=alpha, adjust=False).mean().shift(1))

    df = df.drop(columns=[f"_num_{c}" for c in targets])

    df["games_played"] = grouped.cumcount()
    if "ewma_played_60" in df.columns:
        df = df.rename(columns={"ewma_played_60": "ewma_p60_rate"})
    if "ewma_start_flag" in df.columns:
        df = df.rename(columns={"ewma_start_flag": "ewma_start_rate"})

    return df


def rolling_window_stats(player_history_df: pd.DataFrame, windows: Iterable[int] = (1, 3, 6, 10),
                         stats: Sequence[str] = ("minutes", "total_points",
                                                 "expected_goals",
                                                 "expected_assists"),
                         id_col: str = "element") -> pd.DataFrame:
    """Simple trailing-window means alongside the EWMAs.

    EWMA and fixed windows disagree usefully after an injury layoff: the window
    mean drops to zero fast, the EWMA decays slowly. Giving the model both lets
    it learn the difference.
    """
    df = player_history_df.copy()
    for w in windows:
        for stat in stats:
            if stat not in df.columns:
                continue
            df[f"roll{w}_{stat}"] = (
                df.groupby(id_col, sort=False)[stat]
                  .transform(lambda s: pd.to_numeric(s, errors="coerce")
                             .rolling(w, min_periods=1).mean().shift(1)))
    return df


# ---------------------------------------------------------------------------
# 2. Fixture difficulty
# ---------------------------------------------------------------------------

def fixture_difficulty_features(fixtures_df: pd.DataFrame, teams_df: pd.DataFrame,
                                horizon: int = DEFAULT_HORIZON,
                                from_gw: int | None = None,
                                strength: pd.DataFrame | None = None,
                                ) -> pd.DataFrame:
    """Per-team forward fixture difficulty over the next ``horizon`` gameweeks.

    Combines three views of each upcoming fixture:

    * FPL's published difficulty (``team_h_difficulty`` / ``team_a_difficulty``),
      normalised from its 2-5 scale to 0-1;
    * the opponent's bootstrap strength ratings, split attack/defence and
      home/away, which are more granular than the 2-5 FDR bucket;
    * fitted Dixon-Coles ratings when a ``strength`` frame from
      :func:`team_strength_matrix` is supplied.

    Returns one row per (team, event) with an ``fdr_norm`` column plus
    aggregates: ``fdr_next{h}_mean`` and ``n_fixtures_next{h}`` (which captures
    double and blank gameweeks -- a blank is worth more than any difficulty
    swing).

    Join it to players on ``team``; a player inherits his club's fixtures.
    """
    fx = fixtures_df.copy()
    if fx.empty:
        return pd.DataFrame(columns=["team", "event", "fdr_norm"])

    if "event" not in fx.columns:
        raise ValueError("fixtures frame needs an 'event' column")
    fx = fx[fx["event"].notna()].copy()
    fx["event"] = fx["event"].astype(int)

    if from_gw is None:
        unfinished = fx[~fx.get("finished", pd.Series(False, index=fx.index))
                        .fillna(False)]
        from_gw = int(unfinished["event"].min()) if not unfinished.empty \
            else int(fx["event"].min())

    window = range(from_gw, from_gw + horizon)
    fx = fx[fx["event"].isin(window)]

    # Long form: one row per team per fixture.
    rows = []
    for _, f in fx.iterrows():
        rows.append({"team": f["team_h"], "opponent": f["team_a"], "is_home": True,
                     "event": f["event"],
                     "fpl_fdr": f.get("team_h_difficulty", np.nan)})
        rows.append({"team": f["team_a"], "opponent": f["team_h"], "is_home": False,
                     "event": f["event"],
                     "fpl_fdr": f.get("team_a_difficulty", np.nan)})
    long = pd.DataFrame(rows)
    if long.empty:
        return pd.DataFrame(columns=["team", "event", "fdr_norm"])

    long["fdr_norm"] = ((long["fpl_fdr"] - FDR_MIN) / (FDR_MAX - FDR_MIN)).clip(0, 1)

    # Bootstrap opponent strengths, if the teams frame carries them.
    strength_cols = ["strength_attack_home", "strength_attack_away",
                     "strength_defence_home", "strength_defence_away",
                     "strength_overall_home", "strength_overall_away"]
    have = [c for c in strength_cols if c in teams_df.columns]
    if have:
        opp = teams_df.set_index("id")[have]
        for col in have:
            long[f"opp_{col}"] = long["opponent"].map(opp[col])
        # Opponent's defence is what an attacker faces; their attack is what a
        # defender faces.
        if {"opp_strength_defence_home", "opp_strength_defence_away"} <= set(long.columns):
            long["opp_defence"] = np.where(long["is_home"],
                                           long["opp_strength_defence_away"],
                                           long["opp_strength_defence_home"])
        if {"opp_strength_attack_home", "opp_strength_attack_away"} <= set(long.columns):
            long["opp_attack"] = np.where(long["is_home"],
                                          long["opp_strength_attack_away"],
                                          long["opp_strength_attack_home"])

    if strength is not None and not strength.empty:
        att = strength["attack"].to_dict()
        dfn = strength["defence"].to_dict()
        long["opp_dc_attack"] = long["opponent"].map(att)
        long["opp_dc_defence"] = long["opponent"].map(dfn)

    long["home_flag"] = long["is_home"].astype(int)

    # Aggregates over the horizon, per team.
    agg = (long.groupby("team")
               .agg(**{f"fdr_next{horizon}_mean": ("fdr_norm", "mean"),
                       f"fdr_next{horizon}_max": ("fdr_norm", "max"),
                       f"n_fixtures_next{horizon}": ("event", "count"),
                       f"home_share_next{horizon}": ("home_flag", "mean")})
               .reset_index())

    out = long.merge(agg, on="team", how="left")
    return out.sort_values(["team", "event"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Dixon-Coles team strength
# ---------------------------------------------------------------------------

def _results_from_gw_frame(history_df: pd.DataFrame) -> pd.DataFrame:
    """Recover one row per fixture from Vaastav's per-player gameweek rows.

    The player rows repeat ``team_h_score`` / ``team_a_score`` for every player
    in the match, so de-duplicating on ``fixture`` recovers the scorelines.
    """
    needed = {"fixture", "team_h_score", "team_a_score", "was_home",
              "opponent_team"}
    if not needed <= set(history_df.columns):
        missing = needed - set(history_df.columns)
        raise ValueError(f"cannot rebuild results, missing columns: {missing}")

    df = history_df.dropna(subset=["team_h_score", "team_a_score"]).copy()
    keys = ["fixture"] + (["season"] if "season" in df.columns else [])

    # A home player's opponent is the away team, and vice versa.
    home_rows = df[df["was_home"] == True]  # noqa: E712 -- may be object dtype
    away_rows = df[df["was_home"] == False]  # noqa: E712

    home = (home_rows.groupby(keys)
            .agg(home_goals=("team_h_score", "first"),
                 away_goals=("team_a_score", "first"),
                 away_team=("opponent_team", "first")).reset_index())
    away = (away_rows.groupby(keys)
            .agg(home_team=("opponent_team", "first")).reset_index())

    results = home.merge(away, on=keys, how="inner")
    results = results.dropna(subset=["home_team", "away_team"])
    for c in ("home_team", "away_team", "home_goals", "away_goals"):
        results[c] = results[c].astype(int)
    return results


def _dixon_coles_tau(home_goals: np.ndarray, away_goals: np.ndarray,
                     lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score correction tau.

    Independent Poisson under-predicts 0-0 and 1-1 and over-predicts 1-0 / 0-1.
    Tau reweights exactly those four cells and leaves everything else at 1.
    """
    tau = np.ones_like(lam, dtype=float)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m11] = 1.0 - rho
    return np.clip(tau, 1e-10, None)


def team_strength_matrix(historical_results_df: pd.DataFrame,
                         teams_df: pd.DataFrame | None = None,
                         xi: float = 0.0018,
                         max_iter: int = 300) -> pd.DataFrame:
    """Fit Dixon-Coles attack / defence ratings by maximum likelihood.

    The model is::

        home_goals ~ Poisson(exp(attack[home] - defence[away] + home_adv))
        away_goals ~ Poisson(exp(attack[away] - defence[home]))

    with the Dixon-Coles tau correction on low scores and exponential time decay
    ``exp(-xi * days_ago)`` so old matches count less. ``xi = 0.0018`` halves a
    match's weight after roughly a year, the value Dixon and Coles found optimal.

    Accepts either a fixture-level frame (columns ``home_team``, ``away_team``,
    ``home_goals``, ``away_goals``, optional ``kickoff_time``) or a Vaastav
    per-player gameweek frame, which is collapsed to fixtures automatically.

    Returns a DataFrame indexed by team id with columns ``attack``, ``defence``,
    ``matches``, and attributes ``home_advantage`` / ``rho`` in ``df.attrs``.
    Higher ``attack`` means more goals scored; higher ``defence`` means fewer
    conceded.

    .. warning::
       **FPL re-assigns team ids every season.** They are allocated
       alphabetically over that season's 20 clubs, so id 9 in 2024/25 is not the
       same club as id 9 in 2026/27. Fitting on a historical frame keyed by FPL
       team id and joining the result to the current season silently attributes
       one club's ratings to another. Use :func:`fit_team_strength_by_name`
       instead whenever the ratings will be applied to a different season --
       it keys on club names and maps to current ids explicitly.

    Only attack is constrained to sum to zero. Defence is left free: its mean
    sets the overall goal level, which would otherwise be unidentified.
    """
    df = historical_results_df.copy()
    if "home_goals" not in df.columns:
        df = _results_from_gw_frame(df)

    df = df.dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])
    if df.empty:
        raise ValueError("no usable results to fit team strengths")

    teams = np.sort(pd.unique(np.concatenate([df["home_team"].values,
                                              df["away_team"].values])))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    h = df["home_team"].map(idx).to_numpy()
    a = df["away_team"].map(idx).to_numpy()
    hg = df["home_goals"].to_numpy(dtype=int)
    ag = df["away_goals"].to_numpy(dtype=int)

    # Time decay weights.
    if "kickoff_time" in df.columns and df["kickoff_time"].notna().any():
        kt = pd.to_datetime(df["kickoff_time"], errors="coerce", utc=True)
        days_ago = (kt.max() - kt).dt.total_seconds() / 86400.0
        weights = np.exp(-xi * days_ago.fillna(days_ago.max()).to_numpy())
    else:
        weights = np.ones(len(df))

    from scipy.special import gammaln

    def negloglik(params: np.ndarray) -> float:
        attack = params[:n]
        defence = params[n:2 * n]
        home_adv, rho = params[2 * n], params[2 * n + 1]

        lam = np.exp(attack[h] - defence[a] + home_adv)
        mu = np.exp(attack[a] - defence[h])
        lam = np.clip(lam, 1e-8, 30.0)
        mu = np.clip(mu, 1e-8, 30.0)

        ll = (hg * np.log(lam) - lam - gammaln(hg + 1.0)
              + ag * np.log(mu) - mu - gammaln(ag + 1.0)
              + np.log(_dixon_coles_tau(hg, ag, lam, mu, rho)))
        return -float(np.sum(weights * ll))

    x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])
    # Identifiability: attack ratings must sum to zero, else the whole vector
    # can slide against the home-advantage term.
    constraints = ({"type": "eq", "fun": lambda p: float(np.sum(p[:n]))},)
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.3, 0.3)]

    res = minimize(negloglik, x0, method="SLSQP", bounds=bounds,
                   constraints=constraints, options={"maxiter": max_iter,
                                                     "ftol": 1e-6})
    if not res.success:
        log.warning("Dixon-Coles fit did not fully converge: %s", res.message)

    p = res.x
    counts = pd.Series(np.concatenate([df["home_team"].values,
                                       df["away_team"].values])).value_counts()

    out = pd.DataFrame({
        "attack": p[:n],
        "defence": p[n:2 * n],
    }, index=pd.Index(teams, name="team"))
    out["matches"] = out.index.map(counts).astype(float)
    out["expected_scored"] = np.exp(out["attack"])
    out["expected_conceded"] = np.exp(-out["defence"])

    if teams_df is not None and "id" in teams_df.columns:
        name_map = teams_df.set_index("id")["name"].to_dict()
        out["team_name"] = out.index.map(name_map)

    out.attrs["home_advantage"] = float(p[2 * n])
    out.attrs["rho"] = float(p[2 * n + 1])
    out.attrs["converged"] = bool(res.success)
    out.attrs["xi"] = xi
    return out


def fit_team_strength_by_name(results_df: pd.DataFrame, teams_df: pd.DataFrame,
                              **kwargs) -> pd.DataFrame:
    """Fit Dixon-Coles ratings keyed on club *names*, then map to current FPL ids.

    This is the season-safe entry point. ``results_df`` is a football-data.co.uk
    frame (``HomeTeam`` / ``AwayTeam`` / ``home_goals`` / ``away_goals``), whose
    club names are stable across seasons, unlike FPL's yearly-reshuffled team
    ids.

    Clubs in the historical data that are not in the current Premier League
    (relegated since) are fitted normally and then dropped from the output, and
    currently-promoted clubs simply will not appear -- feed the result through
    :func:`promoted_team_priors` to seed them.
    """
    from .data_collector import match_team_names

    df = results_df.copy()
    if {"HomeTeam", "AwayTeam"} <= set(df.columns):
        df = df.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team"})
    if "kickoff_time" not in df.columns and "date" in df.columns:
        df["kickoff_time"] = df["date"]

    strength = team_strength_matrix(df, **kwargs)

    mapping = match_team_names(strength.index.tolist(), teams_df)
    strength["team_name"] = strength.index
    strength["fpl_id"] = strength.index.map(mapping)

    dropped = strength[strength["fpl_id"].isna()]["team_name"].tolist()
    if dropped:
        log.info("clubs fitted but not in the current PL, dropped: %s", dropped)

    out = (strength[strength["fpl_id"].notna()]
           .set_index(strength.loc[strength["fpl_id"].notna(), "fpl_id"]
                      .astype(int)))
    out.index.name = "team"
    out.attrs.update(strength.attrs)
    return out.drop(columns=["fpl_id"])


# ---------------------------------------------------------------------------
# 4. Promoted-team priors
# ---------------------------------------------------------------------------

def promoted_team_priors(teams_df: pd.DataFrame,
                         strength: pd.DataFrame | None = None,
                         quantile: float = 0.25) -> pd.DataFrame:
    """Seed newly promoted clubs at the league's worst quartile, not the mean.

    A promoted side has no Premier League history, so a fitted rating either
    does not exist or rests on a handful of matches. Filling with the league
    *mean* systematically over-rates them: promoted teams have finished in the
    bottom quartile for attack and defence in the large majority of recent
    seasons. This function fills any team missing from ``strength`` (or with
    fewer than a handful of matches) with the ``quantile``-th percentile of each
    rating.

    Returns a strength frame covering every team in ``teams_df``, with an
    ``is_prior`` flag marking the imputed rows so downstream code can widen the
    uncertainty on them.
    """
    all_ids = teams_df["id"].tolist() if "id" in teams_df.columns else []

    if strength is None or strength.empty:
        base = pd.DataFrame(index=pd.Index(all_ids, name="team"),
                            columns=["attack", "defence", "matches"], dtype=float)
        base[["attack", "defence"]] = 0.0
        base["matches"] = 0.0
        base["is_prior"] = True
        return base

    out = strength.copy()
    out["is_prior"] = False

    att_prior = float(out["attack"].quantile(quantile))
    def_prior = float(out["defence"].quantile(quantile))

    # Teams with too little history are treated as unknown, not as measured.
    if "matches" in out.columns:
        thin = out["matches"].fillna(0) < 5
        out.loc[thin, ["attack", "defence"]] = [att_prior, def_prior]
        out.loc[thin, "is_prior"] = True

    missing = [t for t in all_ids if t not in out.index]
    if missing:
        add = pd.DataFrame({"attack": att_prior, "defence": def_prior,
                            "matches": 0.0, "is_prior": True},
                           index=pd.Index(missing, name="team"))
        out = pd.concat([out, add])
        log.info("promoted/unseen teams seeded at q%.2f priors: %s",
                 quantile, missing)

    out["expected_scored"] = np.exp(out["attack"])
    out["expected_conceded"] = np.exp(-out["defence"])
    if "id" in teams_df.columns:
        names = teams_df.set_index("id")["name"].to_dict()
        if "team_name" in out.columns:
            out["team_name"] = out["team_name"].fillna(
                pd.Series(out.index.map(names), index=out.index))
        else:
            out["team_name"] = out.index.map(names)

    out.attrs.update(getattr(strength, "attrs", {}))
    return out


def apply_promoted_team_adjustments(
    squad_stats: "pd.DataFrame",
    strength: "pd.DataFrame",
    *,
    xg_discount: float = 0.65,
    minutes_discount: float = 0.92,
    xg_caps: dict | None = None,
    shrinkage_denominator: int = 25,
) -> "pd.DataFrame":
    """Apply league-quality and sample-size corrections to simulation inputs.

    Two corrections are made:

    1. **Promoted-team discount.** Championship xG translates to roughly 65%
       in the Premier League. Any team whose ``is_prior`` flag is set in
       ``strength`` (set by :func:`promoted_team_priors`) is treated as newly
       promoted and gets discounted ``xg_per90``, ``xa_per90`` and
       ``expected_minutes``.

    2. **xG per-90 cap.** Hard upper bounds by position prevent one-off data
       artefacts from dominating the simulation:

       * GKP: 0.05   * DEF: 0.15   * MID: 0.32   * FWD: 0.60

    Returns a copy of ``squad_stats`` with the corrections applied.
    Log lines explain which teams and how many players were adjusted.
    """
    from .fpl_rules import GKP, DEF, MID, FWD

    df = squad_stats.copy()

    # -- 1. Promoted-team discount ----------------------------------------
    if "is_prior" in strength.columns:
        promoted_ids = set(strength.index[strength["is_prior"]].tolist())
    else:
        promoted_ids = set()

    if promoted_ids:
        mask = df["team"].isin(promoted_ids)
        n = int(mask.sum())
        log.info("promoted-team discount (%.0f%%) applied to %d players "
                 "(teams %s)", xg_discount * 100, n, sorted(promoted_ids))
        df.loc[mask, "xg_per90"] *= xg_discount
        df.loc[mask, "xa_per90"] *= xg_discount
        df.loc[mask, "expected_minutes"] *= minutes_discount

    # -- 2. Hard xG/90 caps by position ----------------------------------
    if xg_caps is None:
        xg_caps = {GKP: 0.05, DEF: 0.15, MID: 0.32, FWD: 0.60}

    for pos, cap in xg_caps.items():
        pos_mask = df["element_type"] == pos
        df.loc[pos_mask, "xg_per90"] = df.loc[pos_mask, "xg_per90"].clip(upper=cap)

    # Sanity floor
    df["xg_per90"] = df["xg_per90"].clip(lower=0.0)
    df["xa_per90"] = df["xa_per90"].clip(lower=0.0)
    df["expected_minutes"] = df["expected_minutes"].clip(lower=0.0, upper=90.0)

    return df


# ---------------------------------------------------------------------------
# 5. Position encoding and the combined feature build
# ---------------------------------------------------------------------------

def encode_position(element_type) -> np.ndarray:
    """One-hot encode positions as ``[GKP, DEF, MID, FWD]``.

    Accepts a single id (1-4), a position string ("MID", "GK", "GKP"), or any
    iterable of those; returns shape ``(4,)`` or ``(n, 4)``.
    """
    def _one(value) -> np.ndarray:
        if isinstance(value, str):
            key = value.strip().upper()
            key = {"GK": "GKP", "GOALKEEPER": "GKP", "DEFENDER": "DEF",
                   "MIDFIELDER": "MID", "FORWARD": "FWD"}.get(key, key)
            et = POSITION_IDS.get(key)
            if et is None:
                raise ValueError(f"unknown position {value!r}")
        else:
            et = int(value)
        vec = np.zeros(4, dtype=float)
        if et in (GKP, DEF, MID, FWD):
            vec[et - 1] = 1.0
        else:
            raise ValueError(f"unknown element_type {value!r}")
        return vec

    if isinstance(element_type, (str, int, np.integer)):
        return _one(element_type)
    return np.vstack([_one(v) for v in element_type])


def player_form_features(bootstrap_df: pd.DataFrame,
                         player_history_df: pd.DataFrame,
                         fixtures_df: pd.DataFrame | None = None,
                         teams_df: pd.DataFrame | None = None,
                         strength: pd.DataFrame | None = None,
                         alpha: float = DEFAULT_ALPHA,
                         horizon: int = DEFAULT_HORIZON) -> pd.DataFrame:
    """Assemble the full model matrix: EWMA form + position + fixtures + strength.

    ``bootstrap_df`` is the current player pool (from
    ``data_collector.build_player_pool``); ``player_history_df`` is the
    per-gameweek history (Vaastav, or ``element-summary`` history rows).

    The returned frame is one row per player-gameweek, carrying:

    * ``ewma_*`` / ``roll*_*`` form features (shifted, no leakage);
    * ``pos_gkp|def|mid|fwd`` one-hot columns;
    * price, ownership and set-piece order from bootstrap;
    * team attack/defence ratings and forward fixture difficulty when the
      fixture and strength inputs are supplied.

    Rows keep ``total_points``, ``minutes`` etc. as *labels* -- callers select
    feature columns via :func:`feature_columns`.
    """
    hist = compute_ewma_stats(player_history_df, alpha=alpha)
    hist = rolling_window_stats(hist)

    # Position one-hots. History frames carry a text 'position'; bootstrap uses
    # the numeric element_type.
    if "element_type" in hist.columns:
        et = pd.to_numeric(hist["element_type"], errors="coerce")
    elif "position" in hist.columns:
        et = hist["position"].map(POSITION_IDS)
    else:
        et = pd.Series(np.nan, index=hist.index)
    hist["element_type"] = et

    for pid, label in ((GKP, "gkp"), (DEF, "def"), (MID, "mid"), (FWD, "fwd")):
        hist[f"pos_{label}"] = (et == pid).astype(float)

    hist["is_home"] = hist["was_home"].astype(float) if "was_home" in hist.columns \
        else np.nan

    # Bootstrap-level attributes (price, ownership, set-piece duties).
    if bootstrap_df is not None and not bootstrap_df.empty:
        boot_cols = [c for c in ("id", "now_cost", "selected_by_percent",
                                 "penalties_order", "corners_and_indirect_freekicks_order",
                                 "direct_freekicks_order", "chance_of_playing_next_round",
                                 "status", "team")
                     if c in bootstrap_df.columns]
        boot = bootstrap_df[boot_cols].copy().rename(columns={"id": "element"})
        # A missing set-piece order means "not on duty", which is genuinely 0
        # priority rather than unknown.
        for col in ("penalties_order", "corners_and_indirect_freekicks_order",
                    "direct_freekicks_order"):
            if col in boot.columns:
                boot[f"{col}_rank"] = pd.to_numeric(boot[col],
                                                    errors="coerce").fillna(9)
        hist = hist.merge(boot, on="element", how="left", suffixes=("", "_boot"))

    # Team strength.
    team_col = "team_boot" if "team_boot" in hist.columns else "team"
    if strength is not None and not strength.empty and team_col in hist.columns:
        team_key = pd.to_numeric(hist[team_col], errors="coerce")
        hist["team_attack"] = team_key.map(strength["attack"].to_dict())
        hist["team_defence"] = team_key.map(strength["defence"].to_dict())
        if "opponent_team" in hist.columns:
            opp = pd.to_numeric(hist["opponent_team"], errors="coerce")
            hist["opp_attack"] = opp.map(strength["attack"].to_dict())
            hist["opp_defence"] = opp.map(strength["defence"].to_dict())

    # Forward fixture difficulty, joined on team.
    if fixtures_df is not None and teams_df is not None and not fixtures_df.empty:
        try:
            fdr = fixture_difficulty_features(fixtures_df, teams_df,
                                              horizon=horizon, strength=strength)
            agg_cols = [c for c in fdr.columns if c.startswith(("fdr_next",
                                                                "n_fixtures_next",
                                                                "home_share_next"))]
            if agg_cols and team_col in hist.columns:
                team_agg = fdr.groupby("team")[agg_cols].first()
                for c in agg_cols:
                    hist[c] = pd.to_numeric(hist[team_col],
                                            errors="coerce").map(team_agg[c])
        except (ValueError, KeyError) as exc:
            log.warning("fixture difficulty features skipped: %s", exc)

    return hist


# ---------------------------------------------------------------------------
# 5. Player vs. specific opponent (head-to-head history)
# ---------------------------------------------------------------------------

def resolve_opponent_team_name(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'opponent_team_name' string column via within-fixture cross-referencing.

    Vaastav's ``opponent_team`` is a per-season integer FPL team ID that resets
    every year. Within any single fixture both teams' player-rows appear in the
    same DataFrame: team A's players have ``opponent_team = B_int``, team B's
    players have ``opponent_team = A_int``. Cross-referencing pairs resolves
    ``B_int → 'B'`` and ``A_int → 'A'`` without needing an external teams CSV.

    Requires ``team`` (player's own team name string), ``opponent_team`` (int),
    and ``fixture`` (fixture ID) columns. Operates per season when ``season``
    is present.
    """
    need = {"opponent_team", "team", "fixture"}
    if not need.issubset(df.columns):
        return df

    out = df.copy()
    season_cols = ["season"] if "season" in df.columns else []
    join_keys = season_cols + ["fixture"]

    pairs = (
        out[join_keys + ["team", "opponent_team"]]
        .dropna(subset=["opponent_team"])
        .drop_duplicates(subset=join_keys + ["team"])
        .copy()
    )
    pairs["opponent_team"] = pd.to_numeric(pairs["opponent_team"],
                                           errors="coerce").astype("Int64")
    pairs = pairs.dropna(subset=["opponent_team"])

    # Self-join within fixture: left side "opp_int" = right side team name.
    left = pairs.rename(columns={"team": "team_l", "opponent_team": "opp_int"})
    right = pairs.rename(columns={"team": "opp_name", "opponent_team": "opp_int_r"})
    cross = left.merge(right, on=join_keys)
    cross = cross[cross["team_l"] != cross["opp_name"]]

    lookup = (
        cross[season_cols + ["opp_int", "opp_name"]]
        .drop_duplicates(subset=season_cols + ["opp_int"])
        .rename(columns={"opp_int": "_opp_int"})
    )

    out["_opp_int"] = pd.to_numeric(out["opponent_team"],
                                    errors="coerce").astype("Int64")
    out = out.merge(lookup, on=season_cols + ["_opp_int"], how="left")
    out = out.rename(columns={"opp_name": "opponent_team_name"})
    out = out.drop(columns=["_opp_int"])
    return out


def player_vs_opponent_features(
    df: pd.DataFrame,
    name_col: str = "name",
    stats: tuple[str, ...] = ("goals_scored", "assists", "total_points"),
    min_appearances: int = 2,
) -> pd.DataFrame:
    """Per-player rolling head-to-head stats vs each specific opponent team.

    For each row ``(player P, opponent O)``, computes P's expanding mean of
    ``goals_scored`` / ``assists`` / ``total_points`` in all *prior* appearances
    vs O, shifted by one GW to prevent leakage. Players with fewer than
    ``min_appearances`` h2h games fall back to their overall player-level
    expanding mean for that stat.

    Requires ``resolve_opponent_team_name`` to have been called first so that
    ``opponent_team_name`` is a string column.

    New columns: ``h2h_goals_scored_vs_opp``, ``h2h_assists_vs_opp``,
    ``h2h_total_points_vs_opp``, ``h2h_appearances_vs_opp``.

    Fully vectorised (no Python-level lambda per group) so scales to 200k+ rows.
    """
    if "opponent_team_name" not in df.columns or name_col not in df.columns:
        return df

    out = df.copy()
    order_cols = [c for c in ("season", "round") if c in out.columns]
    if not order_cols:
        return out

    out = out.sort_values(order_cols).reset_index(drop=True)
    group_key = [name_col, "opponent_team_name"]
    has_opp = out["opponent_team_name"].notna()

    # Prior h2h appearance count: cumcount within each (player, opponent) group
    # cumcount() gives 0 for the 1st row in a group, 1 for the 2nd, etc.
    h2h_cumcnt = (
        out.groupby(group_key, sort=False, dropna=True).cumcount()
        .reindex(out.index)
        .where(has_opp, 0)
        .fillna(0)
        .astype(float)
    )
    out["h2h_appearances_vs_opp"] = h2h_cumcnt

    for stat in stats:
        if stat not in out.columns:
            continue
        col_name = f"h2h_{stat}_vs_opp"
        tmp = f"_h2h_{stat}"
        out[tmp] = pd.to_numeric(out[stat], errors="coerce").fillna(0.0)

        # Vectorised: cumulative sum per (player, opponent) group
        cumsum = (
            out.groupby(group_key, sort=False, dropna=True)[tmp]
            .cumsum()
            .reindex(out.index)
            .where(has_opp, np.nan)
        )
        # Prior sum = cumsum_i - current_val_i = sum of all rows before row i
        prior_sum = (cumsum - out[tmp]).where(has_opp)
        # prior count = h2h_cumcnt (0 on first appearance)
        prior_cnt = h2h_cumcnt.replace(0.0, np.nan)
        out[col_name] = prior_sum / prior_cnt

        out.drop(columns=[tmp], inplace=True)

    # Fallback: overall player expanding mean when h2h history is thin
    for stat in stats:
        col_name = f"h2h_{stat}_vs_opp"
        if col_name not in out.columns:
            continue
        tmp = f"_ov_{stat}"
        out[tmp] = pd.to_numeric(out[stat], errors="coerce").fillna(0.0)

        # Overall cumsum per player (all opponents combined)
        ov_cumsum = out.groupby(name_col, sort=False)[tmp].cumsum()
        ov_cumcnt = out.groupby(name_col, sort=False).cumcount().astype(float)
        ov_prior_cnt = ov_cumcnt.replace(0.0, np.nan)
        ov_mean = (ov_cumsum - out[tmp]) / ov_prior_cnt

        thin = (h2h_cumcnt < min_appearances) | out[col_name].isna()
        out[col_name] = out[col_name].where(~thin, ov_mean)
        out.drop(columns=[tmp], inplace=True)

    return out


def feature_columns(df: pd.DataFrame, extra: Sequence[str] = ()) -> list[str]:
    """Model-safe feature columns: EWMA/rolling/position/fixture, no labels.

    Deliberately excludes every same-gameweek outcome column (``total_points``,
    ``minutes``, ``goals_scored``, ...) so a caller cannot accidentally train on
    the label.
    """
    banned = {
        "total_points", "minutes", "goals_scored", "assists", "bonus", "bps",
        "clean_sheets", "goals_conceded", "saves", "own_goals", "yellow_cards",
        "red_cards", "penalties_saved", "penalties_missed", "starts",
        "expected_goals", "expected_assists", "expected_goal_involvements",
        "expected_goals_conceded", "influence", "creativity", "threat",
        "ict_index", "xP", "played_60", "played_any", "start_flag", "value",
        "clearances_blocks_interceptions", "recoveries", "tackles",
        "defensive_contribution", "team_h_score", "team_a_score",
        "transfers_in", "transfers_out", "transfers_balance", "selected",
    }
    prefixes = ("ewma_", "roll1_", "roll3_", "roll6_", "roll10_", "pos_", "fdr_next",
                "n_fixtures_next", "home_share_next", "h2h_")
    cols = [c for c in df.columns
            if (c.startswith(prefixes) or c in {
                "games_played", "is_home", "now_cost", "selected_by_percent",
                "team_attack", "team_defence", "opp_attack", "opp_defence",
                "penalties_order_rank", "corners_and_indirect_freekicks_order_rank",
                "direct_freekicks_order_rank", "chance_of_playing_next_round",
            })
            and c not in banned]
    cols += [c for c in extra if c in df.columns and c not in cols]
    # Keep only numeric columns -- the tree models cannot take objects.
    return [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]


# ---------------------------------------------------------------------------
# 6. Odds de-vigging (free source: football-data.co.uk historical CSVs)
# ---------------------------------------------------------------------------

def devig_odds(odds: np.ndarray | Sequence[float], method: str = "shin"
               ) -> np.ndarray:
    """Strip the bookmaker margin from decimal odds to get fair probabilities.

    Raw implied probabilities ``1/odds`` sum to more than 1 -- the overround.
    Two removal methods:

    * ``"proportional"`` -- divide by the overround. Simple, but it assumes the
      margin is spread evenly, which it is not: bookmakers load more margin onto
      longshots (the favourite-longshot bias).
    * ``"shin"`` -- Shin's model, which assumes the margin arises from a
      proportion ``z`` of insider money and solves for it. It shifts probability
      mass back toward favourites and is the better default for football 1X2.

    Works on a single market (1-D array of odds for mutually exclusive outcomes)
    or a batch (2-D, one market per row). Returns probabilities summing to 1
    along the last axis. Rows with any missing odds come back as NaN.

    No API key and no paid feed: odds come from football-data.co.uk's free
    historical CSVs via ``data_collector.fetch_football_data_odds``.
    """
    arr = np.asarray(odds, dtype=float)
    single = arr.ndim == 1
    if single:
        arr = arr[None, :]

    out = np.full(arr.shape, np.nan, dtype=float)
    valid = np.isfinite(arr).all(axis=1) & (arr > 1.0).all(axis=1)

    q = np.zeros_like(arr)
    q[valid] = 1.0 / arr[valid]

    if method == "proportional":
        totals = q[valid].sum(axis=1, keepdims=True)
        out[valid] = q[valid] / totals
    elif method == "shin":
        for i in np.flatnonzero(valid):
            out[i] = _shin_probabilities(q[i])
    else:
        raise ValueError(f"unknown de-vig method {method!r}")

    return out[0] if single else out


def _shin_probabilities(q: np.ndarray) -> np.ndarray:
    """Shin's de-vigging for one market. ``q`` are the raw 1/odds."""
    from scipy.optimize import brentq

    total = float(q.sum())
    if total <= 1.0:  # no margin to remove (or an arbitrage quote)
        return q / total

    def implied(z: float) -> np.ndarray:
        root = np.sqrt(z ** 2 + 4.0 * (1.0 - z) * (q ** 2) / total)
        return (root - z) / (2.0 * (1.0 - z))

    try:
        z = brentq(lambda zz: implied(zz).sum() - 1.0, 1e-9, 0.5, xtol=1e-10)
    except (ValueError, RuntimeError):
        # No sign change -- fall back to proportional rather than failing.
        return q / total

    p = implied(z)
    return p / p.sum()


def implied_goal_rates(p_home: float, p_draw: float, p_away: float,
                       p_over25: float | None = None,
                       max_goals: int = 12) -> tuple[float, float]:
    """Solve for the Poisson goal rates that reproduce de-vigged market prices.

    Finds ``(lambda_home, lambda_away)`` whose independent-Poisson scoreline
    grid best matches the market's home/draw/away probabilities and, when
    supplied, its over-2.5-goals probability. The over/under market is what pins
    down the *total*, so including it materially improves the split; without it
    the 1X2 prices alone leave total goals weakly identified.

    Returns the fitted rates. These can be handed straight to
    :meth:`simulator.BivariatePoissonSimulator.simulate_fixture` via its
    ``lambdas`` argument, bypassing the Dixon-Coles fit for fixtures where a
    market price exists.
    """
    from scipy.optimize import minimize as _minimize
    from scipy.stats import poisson

    goals = np.arange(max_goals + 1)

    def market_probs(log_lams: np.ndarray) -> tuple[float, float, float, float]:
        lh, la = np.exp(log_lams)
        ph = poisson.pmf(goals, lh)
        pa = poisson.pmf(goals, la)
        grid = np.outer(ph, pa)
        home = float(np.tril(grid, -1).sum())
        draw = float(np.trace(grid))
        away = float(np.triu(grid, 1).sum())
        totals = goals[:, None] + goals[None, :]
        over = float(grid[totals > 2.5].sum())
        return home, draw, away, over

    def loss(log_lams: np.ndarray) -> float:
        home, draw, away, over = market_probs(log_lams)
        err = (home - p_home) ** 2 + (draw - p_draw) ** 2 + (away - p_away) ** 2
        if p_over25 is not None and np.isfinite(p_over25):
            err += (over - p_over25) ** 2
        return err

    res = _minimize(loss, np.log([1.4, 1.1]), method="Nelder-Mead",
                    options={"xatol": 1e-4, "fatol": 1e-9, "maxiter": 500})
    lh, la = np.exp(res.x)
    return float(lh), float(la)


def odds_features(odds_df: pd.DataFrame, teams_df: pd.DataFrame | None = None,
                  method: str = "shin", use_over_under: bool = True
                  ) -> pd.DataFrame:
    """Per-fixture market features from a football-data.co.uk frame.

    Input is the output of ``data_collector.fetch_football_data_odds``, which
    normalises whichever bookmaker columns a season happens to carry into
    ``odds_home`` / ``odds_draw`` / ``odds_away`` / ``odds_over25`` /
    ``odds_under25``.

    Produces, per fixture: de-vigged ``p_home``/``p_draw``/``p_away``, the
    de-vigged ``p_over25``, the market-implied ``lambda_home``/``lambda_away``,
    and ``mkt_clean_sheet_home``/``_away`` (the Poisson probability the opponent
    fails to score) -- which is directly the quantity the defence model wants.

    Solving the Poisson inversion per fixture costs roughly 20-40 ms, so a full
    380-fixture season takes about 10-15 seconds.
    """
    df = odds_df.copy()
    required = {"odds_home", "odds_draw", "odds_away"}
    if not required <= set(df.columns):
        raise ValueError(f"odds frame is missing {required - set(df.columns)}; "
                         "use data_collector.fetch_football_data_odds")

    probs = devig_odds(df[["odds_home", "odds_draw", "odds_away"]].to_numpy(),
                       method=method)
    df["p_home"], df["p_draw"], df["p_away"] = probs[:, 0], probs[:, 1], probs[:, 2]

    if use_over_under and {"odds_over25", "odds_under25"} <= set(df.columns):
        ou = devig_odds(df[["odds_over25", "odds_under25"]].to_numpy(),
                        method=method)
        df["p_over25"] = ou[:, 0]
    else:
        df["p_over25"] = np.nan

    lam_h, lam_a = [], []
    for row in df.itertuples(index=False):
        ph, pd_, pa = row.p_home, row.p_draw, row.p_away
        if not np.isfinite([ph, pd_, pa]).all():
            lam_h.append(np.nan)
            lam_a.append(np.nan)
            continue
        over = getattr(row, "p_over25", np.nan)
        h, a = implied_goal_rates(ph, pd_, pa,
                                  over if np.isfinite(over) else None)
        lam_h.append(h)
        lam_a.append(a)

    df["lambda_home"] = lam_h
    df["lambda_away"] = lam_a
    # P(opponent scores zero) -- the market's clean-sheet price.
    df["mkt_clean_sheet_home"] = np.exp(-df["lambda_away"])
    df["mkt_clean_sheet_away"] = np.exp(-df["lambda_home"])
    df["mkt_total_goals"] = df["lambda_home"] + df["lambda_away"]

    if teams_df is not None:
        from .data_collector import match_team_names
        names = pd.unique(pd.concat([df["HomeTeam"], df["AwayTeam"]]).dropna())
        mapping = match_team_names(names, teams_df)
        df["home_team_id"] = df["HomeTeam"].map(mapping)
        df["away_team_id"] = df["AwayTeam"].map(mapping)

    return df


# ---------------------------------------------------------------------------
# 7. Free news / availability sentiment (no API key, no LLM service)
# ---------------------------------------------------------------------------

#: Keyword weights for scoring FPL injury-news text. Negative means the player
#: is less likely to play or to play well; positive means reassurance. Weights
#: are deliberately coarse -- this is a signal, not a diagnosis. Phrases are
#: matched as substrings on lowercased text, so multi-word keys work.
NEWS_KEYWORD_WEIGHTS = {
    # --- strongly negative: the player is definitely out ---
    "suspended": -1.0, "red card": -0.9, "season": -1.0, "surgery": -1.0,
    "operation": -0.9, "ruled out": -1.0, "long-term": -0.9, "acl": -1.0,
    "cruciate": -1.0, "fracture": -0.9, "broken": -0.9, "torn": -0.9,
    "rupture": -1.0, "transferred": -1.0, "loan": -0.7, "left the club": -1.0,
    # --- moderately negative: doubt or a live injury ---
    "injury": -0.6, "injured": -0.6, "knock": -0.5, "strain": -0.5,
    "hamstring": -0.6, "groin": -0.5, "calf": -0.5, "ankle": -0.5,
    "knee": -0.6, "back": -0.4, "thigh": -0.5, "illness": -0.4, "ill": -0.3,
    "doubt": -0.6, "doubtful": -0.6, "assessed": -0.4, "scan": -0.5,
    "misses": -0.8, "miss": -0.6, "unavailable": -0.9, "out": -0.7,
    "concern": -0.5, "lacks match fitness": -0.6, "rotation": -0.4,
    "rested": -0.4, "benched": -0.4, "sidelined": -0.9,
    # --- positive: reassurance about availability ---
    "expected back": 0.4, "returned": 0.6, "return": 0.4, "available": 0.7,
    "fit": 0.6, "fully fit": 0.9, "training": 0.5, "in contention": 0.6,
    "no problem": 0.7, "recovered": 0.8, "sharp": 0.4, "confident": 0.3,
    "starting": 0.6, "starts": 0.5, "cleared": 0.7,
}

#: Words that flip the sign of a following keyword within a short window.
NEGATION_TOKENS = ("no ", "not ", "never ", "without ", "denied ")


def score_news_text(text: str | float | None) -> float:
    """Keyword sentiment for one FPL news string, in roughly [-1, 1].

    A free, dependency-light substitute for LLM-based press-conference
    sentiment: no API key, no model download, no network call. It scores the
    ``news`` field FPL already publishes on every player ("Knee injury - 75%
    chance of playing", "Suspended until 12 Sep").

    Simple negation handling flips a keyword's sign when it is directly preceded
    by "no", "not" etc., so "no injury concerns" does not read as negative.
    An empty or missing string scores 0.0 -- no news is genuinely neutral in
    FPL, since the field is only populated when something is wrong.

    >>> round(score_news_text("Knee injury - 50% chance of playing"), 2) < 0
    True
    >>> score_news_text("") == 0.0
    True
    """
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return 0.0
    low = str(text).strip().lower()
    if not low:
        return 0.0

    score, hits = 0.0, 0
    for phrase, weight in NEWS_KEYWORD_WEIGHTS.items():
        idx = low.find(phrase)
        if idx < 0:
            continue
        window = low[max(0, idx - 12):idx]
        negated = any(tok in window for tok in NEGATION_TOKENS)
        score += -weight if negated else weight
        hits += 1

    if hits == 0:
        # Text exists but matched nothing known -- FPL only writes news when
        # something is up, so treat it as mildly negative rather than neutral.
        return -0.2
    return float(np.clip(score / max(1, hits ** 0.5), -1.0, 1.0))


def news_sentiment_features(players_df: pd.DataFrame,
                            set_piece_notes: dict | None = None
                            ) -> pd.DataFrame:
    """Availability features from free FPL fields plus keyword news sentiment.

    Uses only unauthenticated FPL data:

    * ``news`` -- free text, scored by :func:`score_news_text`;
    * ``chance_of_playing_this_round`` / ``chance_of_playing_next_round`` --
      FPL's own 0-100 availability percentage. Note that ``None`` here means
      "no doubt", i.e. 100, **not** missing; conflating the two would mark every
      healthy player as unknown;
    * ``status`` -- a=available, d=doubtful, i=injured, s=suspended,
      u=unavailable, n=not in squad;
    * optionally the ``/team/set-piece-notes/`` endpoint for penalty duty.

    Returns the input frame with ``news_sentiment``, ``avail_this``,
    ``avail_next``, ``status_score`` and ``availability_index`` appended.
    ``availability_index`` in [0, 1] is the composite the minutes model uses.
    """
    df = players_df.copy()

    df["news_sentiment"] = df["news"].apply(score_news_text) \
        if "news" in df.columns else 0.0

    for src, dest in (("chance_of_playing_this_round", "avail_this"),
                      ("chance_of_playing_next_round", "avail_next")):
        if src in df.columns:
            # None/NaN means "no doubt registered" -> 100%.
            df[dest] = pd.to_numeric(df[src], errors="coerce").fillna(100.0) / 100.0
        else:
            df[dest] = 1.0

    status_scores = {"a": 1.0, "d": 0.5, "i": 0.0, "s": 0.0, "u": 0.0, "n": 0.0}
    df["status_score"] = (df["status"].map(status_scores).fillna(0.5)
                          if "status" in df.columns else 1.0)

    # Composite: the FPL percentage dominates when present, the news text and
    # status flag adjust it. Clipped to [0, 1].
    df["availability_index"] = np.clip(
        0.6 * df["avail_next"] + 0.3 * df["status_score"]
        + 0.1 * (df["news_sentiment"] + 1.0) / 2.0, 0.0, 1.0)

    if set_piece_notes:
        df = df.merge(set_piece_features(set_piece_notes), on="team", how="left")

    return df


def set_piece_features(set_piece_notes: dict) -> pd.DataFrame:
    """Per-team set-piece note text, scored for penalty-duty clarity.

    The ``/team/set-piece-notes/`` endpoint is free and unauthenticated. Its
    notes are free text ("Bruno Fernandes is the first-choice penalty taker"),
    so this returns a per-team summary plus a boolean for whether penalties are
    explicitly assigned. Mapping the named player to an FPL id is left to the
    caller -- bootstrap's ``penalties_order`` column already gives the
    structured version of the same fact and is preferred where present.
    """
    rows = []
    for team in set_piece_notes.get("teams", []):
        notes = team.get("notes", [])
        text = " ".join(str(n.get("info_message", "")) for n in notes).strip()
        low = text.lower()
        rows.append({
            "team": team.get("id"),
            "set_piece_note": text,
            "has_penalty_note": bool("penalt" in low),
            "has_corner_note": bool("corner" in low),
            "has_fk_note": bool("free kick" in low or "free-kick" in low),
            "n_set_piece_notes": len(notes),
        })
    return pd.DataFrame(rows)


# TODO(local transformer sentiment, optional): for richer text than FPL's
# one-line `news` field, a locally downloaded HuggingFace model such as
# `distilbert-base-uncased-finetuned-sst-2-english` can be run through
# `transformers.pipeline("sentiment-analysis")`. It downloads weights once over
# plain HTTPS and needs no API key or account. It is not wired in because
# (a) it adds a ~250 MB torch/transformers dependency for a feature that only
# refines an already-weak signal, and (b) general-purpose sentiment is a poor
# fit for football injury text -- "returns to full training after surgery" is
# positive for FPL and negative to SST-2. The keyword scorer above is
# domain-specific and free. If richer text is wanted, scrape BBC Sport match
# reports or club sites (both plain HTTP, no key) and feed them to
# `score_news_text`, extending NEWS_KEYWORD_WEIGHTS as needed.

# TODO(effective ownership): EO drives rank-differential strategy rather than
# raw points, so it belongs in the optimiser's objective, not here. It needs
# top-10k pick data, which is scrapeable from the free
# `/leagues-classic/314/standings/` + `/entry/{id}/event/{gw}/picks/` endpoints
# by sampling entries -- no key required, just a lot of polite requests.
# `selected_by_percent` is the overall-ownership proxy currently used.
