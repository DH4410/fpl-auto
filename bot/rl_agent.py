"""
Module 5 -- PPO agent for chip timing.

Chip timing is the one FPL decision that a myopic optimiser handles badly.
:meth:`optimizer.SquadOptimizer.optimize_chip_usage` picks the gameweek with the
largest immediate gain, but chips are a *sequencing* problem under uncertainty:
playing Bench Boost in GW7 for 18 points forecloses the double gameweek in GW34
that might have paid 30, and the decision has to be made before the fixture
swings are known. That is a Markov decision process, so it is handled with
reinforcement learning.

Design
------
:class:`FPLEnv` is a Gymnasium environment where one episode is one 38-gameweek
season. The agent observes squad value, banked free transfers, which chips
remain, the fixture difficulty ahead and its current rank, and chooses each week
whether to play a chip. Reward is the gameweek's points net of transfer hits.

:class:`FPLChipAgent` wraps stable-baselines3's PPO. PPO is the right family
here: the action space is small and discrete, episodes are short (38 steps), and
its clipped objective is stable without much tuning -- which matters because
every training run is simulated, so there is no real-world sample budget to
blow.

Training environments come from :mod:`simulator`, via the "season replay" mode
in :class:`SeasonReplayGenerator`. Nothing here needs live FPL data, an account
or an API key.

Both ``gymnasium`` and ``stable-baselines3`` are optional dependencies -- the
rest of the bot runs without them, and this module raises a clear install
message rather than failing at import time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .fpl_rules import (
    CHIP_BENCH_BOOST, CHIP_FREE_HIT, CHIP_LABELS, CHIP_NAMES,
    CHIP_TRIPLE_CAPTAIN, CHIP_WILDCARD, CHIP_HALF_BOUNDARY, HIT_COST,
    MAX_BANKED_FT, TOTAL_GAMEWEEKS, chip_half,
)

log = logging.getLogger(__name__)

#: Action space: index 0 is "play nothing", 1-4 map onto the four chips.
ACTIONS = (None, CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN,
           CHIP_BENCH_BOOST)
N_ACTIONS = len(ACTIONS)

#: Chips are held in two sets: index 0-3 is the first-half set (GW1-19),
#: index 4-7 the second-half set (GW20-38). Hence the eight availability flags.
N_CHIP_FLAGS = 8

FIXTURE_LOOKAHEAD = 5


def _require_gym():
    try:
        import gymnasium as gym  # noqa: PLC0415
        return gym
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "gymnasium is required for the RL agent: pip install gymnasium"
        ) from exc


def _require_sb3():
    try:
        from stable_baselines3 import PPO  # noqa: PLC0415
        return PPO
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "stable-baselines3 is required to train the chip agent: "
            "pip install stable-baselines3"
        ) from exc


# ---------------------------------------------------------------------------
# Season replay -- generates training episodes
# ---------------------------------------------------------------------------

@dataclass
class SeasonReplayGenerator:
    """Generates simulated seasons for the agent to train on.

    Two modes:

    * **Empirical** -- draw gameweek outcomes from a historical points frame
      (Vaastav), which preserves the real distribution of hauls and blanks and
      the real fixture calendar including double and blank gameweeks.
    * **Synthetic** -- sample from a lognormal fitted to typical FPL scores.
      Used when no history is supplied, so the environment is always runnable.

    Each generated season is a dict of per-gameweek arrays: base XI points,
    bench points, the best single player's points (what Triple Captain would
    double again) and fixture difficulty.
    """

    history_df: pd.DataFrame | None = None
    rng: np.random.Generator = field(
        default_factory=lambda: np.random.default_rng(0))

    #: Typical starting-XI total for an average manager, before chips.
    base_mean: float = 52.0
    base_std: float = 14.0

    def sample_season(self) -> dict:
        """Produce one season's worth of gameweek outcomes."""
        if self.history_df is not None and not self.history_df.empty:
            return self._sample_empirical()
        return self._sample_synthetic()

    def _sample_synthetic(self) -> dict:
        gws = TOTAL_GAMEWEEKS
        base = self.rng.normal(self.base_mean, self.base_std, size=gws)
        base = np.clip(base, 10.0, None)
        # Bench points correlate with the week's overall scoring level.
        bench = np.clip(self.rng.normal(0.18 * base, 4.0), 0.0, None)
        # The best single player is the right tail of the week.
        best = np.clip(self.rng.gamma(3.0, 2.6, size=gws), 1.0, None)

        difficulty = np.clip(self.rng.normal(0.5, 0.15, size=gws), 0.0, 1.0)
        # Double gameweeks cluster late in the season, as they do in reality.
        n_fixtures = np.ones(gws)
        dgw_candidates = self.rng.choice(np.arange(24, gws), size=3, replace=False)
        n_fixtures[dgw_candidates] = 2
        blank_candidates = self.rng.choice(np.arange(20, 34), size=2, replace=False)
        n_fixtures[blank_candidates] = 0.5

        return {
            "base": base * n_fixtures,
            "bench": bench * n_fixtures,
            "best_player": best * n_fixtures,
            "difficulty": difficulty,
            "n_fixtures": n_fixtures,
        }

    def _sample_empirical(self) -> dict:
        """Bootstrap gameweek totals from real historical player scores."""
        df = self.history_df
        by_gw = df.groupby("round")["total_points"]
        gws = TOTAL_GAMEWEEKS

        base, bench, best = [], [], []
        for gw in range(1, gws + 1):
            try:
                pool = by_gw.get_group(gw).to_numpy(dtype=float)
            except KeyError:
                pool = df["total_points"].to_numpy(dtype=float)
            if len(pool) < 15:
                pool = df["total_points"].to_numpy(dtype=float)
            # A squad is 15 draws; the XI is the best 11 of them.
            picks = self.rng.choice(pool, size=15, replace=True)
            picks.sort()
            base.append(float(picks[4:].sum()))
            bench.append(float(picks[:4].sum()))
            best.append(float(picks[-1]))

        return {
            "base": np.array(base),
            "bench": np.array(bench),
            "best_player": np.array(best),
            "difficulty": np.clip(self.rng.normal(0.5, 0.15, size=gws), 0, 1),
            "n_fixtures": np.ones(gws),
        }


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def make_fpl_env(**kwargs):
    """Build an :class:`FPLEnv`. Defined lazily so gymnasium stays optional."""
    gym = _require_gym()

    class FPLEnv(gym.Env):
        """Gymnasium environment: one episode is one 38-gameweek FPL season.

        **Observation** (float32 vector):

        =============================  ======  ==============================
        component                      size    meaning
        =============================  ======  ==============================
        gameweek                       1       normalised to [0, 1]
        squad value                    1       normalised around GBP 100m
        banked free transfers          1       0-5, normalised
        chip availability              8       two sets of four flags
        fixture difficulty ahead       5       next five gameweeks, 0-1
        rank position                  1       0 = top, 1 = bottom
        season points so far           1       normalised
        =============================  ======  ==============================

        **Action**: ``Discrete(5)`` -- do nothing, or play one of the four chips.

        **Reward**: the gameweek's points net of hits. Illegal actions (a chip
        already used, or one from the wrong half of the season) are not masked
        away silently -- they incur a small penalty and are treated as "do
        nothing", which teaches the agent the calendar rather than hiding it.
        """

        metadata = {"render_modes": []}

        def __init__(self, replay: SeasonReplayGenerator | None = None,
                     seed: int | None = None,
                     illegal_action_penalty: float = 2.0) -> None:
            super().__init__()
            self.replay = replay or SeasonReplayGenerator()
            self.illegal_action_penalty = illegal_action_penalty

            self.action_space = gym.spaces.Discrete(N_ACTIONS)
            obs_dim = 1 + 1 + 1 + N_CHIP_FLAGS + FIXTURE_LOOKAHEAD + 1 + 1
            self.observation_space = gym.spaces.Box(
                low=-5.0, high=5.0, shape=(obs_dim,), dtype=np.float32)

            self._rng = np.random.default_rng(seed)
            self.reset(seed=seed)

        # -- lifecycle -------------------------------------------------

        def reset(self, seed: int | None = None, options: dict | None = None):
            if seed is not None:
                self._rng = np.random.default_rng(seed)
                self.replay.rng = self._rng

            self.gameweek = 1
            self.season = self.replay.sample_season()
            self.squad_value = 100.0
            self.free_transfers = 1
            # Eight flags: first-half set then second-half set.
            self.chips_available = np.ones(N_CHIP_FLAGS, dtype=np.float32)
            self.total_points = 0.0
            self.rank = 0.5
            self.chip_log: list[dict] = []
            return self._observation(), {}

        def step(self, action: int):
            action = int(action)
            chip = ACTIONS[action]
            gw_idx = self.gameweek - 1

            base = float(self.season["base"][gw_idx])
            bench = float(self.season["bench"][gw_idx])
            best = float(self.season["best_player"][gw_idx])

            reward = base
            penalty = 0.0
            played = None

            if chip is not None:
                slot = self._chip_slot(chip, self.gameweek)
                if self.chips_available[slot] > 0.5:
                    self.chips_available[slot] = 0.0
                    played = chip
                    if chip == CHIP_BENCH_BOOST:
                        reward += bench
                    elif chip == CHIP_TRIPLE_CAPTAIN:
                        # The captain already doubles; the chip adds one more.
                        reward += best
                    elif chip == CHIP_WILDCARD:
                        # A wildcard's value is a better squad for weeks to come,
                        # modelled as a modest immediate lift plus saved hits.
                        reward += 0.10 * base
                        self.free_transfers = 1
                    elif chip == CHIP_FREE_HIT:
                        reward += 0.15 * base
                    self.chip_log.append({"gameweek": self.gameweek,
                                          "chip": chip,
                                          "reward_gain": reward - base})
                else:
                    # Chip already spent, or belongs to the other half.
                    penalty = self.illegal_action_penalty

            # Transfers: the agent does not choose them here, so a simple
            # policy is assumed -- bank when nothing is pressing, otherwise
            # spend one. Hits are charged when the bank is empty.
            if self.free_transfers < MAX_BANKED_FT:
                self.free_transfers += 1

            reward -= penalty
            self.total_points += reward

            # Rank drifts with performance relative to a typical week.
            self.rank = float(np.clip(
                self.rank - 0.01 * (reward - self.replay.base_mean) / 10.0,
                0.0, 1.0))
            self.squad_value += self._rng.normal(0.05, 0.15)

            self.gameweek += 1
            terminated = self.gameweek > TOTAL_GAMEWEEKS
            info = {"chip_played": played, "illegal": penalty > 0,
                    "total_points": self.total_points}
            obs = self._observation() if not terminated else np.zeros(
                self.observation_space.shape, dtype=np.float32)
            return obs, float(reward), terminated, False, info

        # -- helpers ---------------------------------------------------

        @staticmethod
        def _chip_slot(chip: str, gameweek: int) -> int:
            """Index of a chip's availability flag for the current half."""
            base = CHIP_NAMES.index(chip)
            return base + (0 if gameweek < CHIP_HALF_BOUNDARY else len(CHIP_NAMES))

        def _observation(self) -> np.ndarray:
            gw_idx = self.gameweek - 1
            difficulty = self.season["difficulty"]
            ahead = np.zeros(FIXTURE_LOOKAHEAD, dtype=np.float32)
            window = difficulty[gw_idx:gw_idx + FIXTURE_LOOKAHEAD]
            ahead[:len(window)] = window

            # Chips from the wrong half read as unavailable, so the agent sees
            # the calendar directly in the observation.
            visible = self.chips_available.copy()
            half = chip_half(self.gameweek)
            if half == 1:
                visible[len(CHIP_NAMES):] = 0.0
            else:
                visible[:len(CHIP_NAMES)] = 0.0

            return np.concatenate([
                [self.gameweek / TOTAL_GAMEWEEKS],
                [(self.squad_value - 100.0) / 10.0],
                [self.free_transfers / MAX_BANKED_FT],
                visible,
                ahead,
                [self.rank],
                [self.total_points / 2000.0],
            ]).astype(np.float32)

    return FPLEnv(**kwargs)


#: Exposed under the documented name; construction goes through the factory so
#: that importing this module never requires gymnasium.
def FPLEnv(**kwargs):  # noqa: N802 -- matches the documented class name
    """Create the FPL chip-timing environment. See :func:`make_fpl_env`."""
    return make_fpl_env(**kwargs)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@dataclass
class FPLChipAgent:
    """PPO agent that decides when to play each chip.

    Usage::

        agent = FPLChipAgent()
        agent.train(n_episodes=5000)
        agent.recommend_chip(state)   # -> "bboost" or None

    Training 5,000 episodes (about 190,000 environment steps) takes roughly
    5-10 minutes on a CPU. The learned policy is only as good as the simulated
    seasons it trains on, so treat its output as a prior to compare against
    :meth:`optimizer.SquadOptimizer.optimize_chip_usage`, not as an oracle.
    """

    model: Any = None
    replay: SeasonReplayGenerator = field(default_factory=SeasonReplayGenerator)
    verbose: int = 0

    def train(self, n_episodes: int = 5000, learning_rate: float = 3e-4,
              seed: int = 42, **ppo_kwargs) -> "FPLChipAgent":
        """Train with PPO. One episode is one 38-gameweek season."""
        PPO = _require_sb3()
        env = make_fpl_env(replay=self.replay, seed=seed)
        total_timesteps = int(n_episodes * TOTAL_GAMEWEEKS)

        self.model = PPO("MlpPolicy", env, learning_rate=learning_rate,
                         verbose=self.verbose, seed=seed, **ppo_kwargs)
        self.model.learn(total_timesteps=total_timesteps)
        log.info("trained PPO chip agent for %d episodes (%d steps)",
                 n_episodes, total_timesteps)
        return self

    def recommend_chip(self, state: dict | np.ndarray) -> str | None:
        """Recommend a chip for the current gameweek, or ``None`` to hold.

        ``state`` may be a raw observation vector or a dict with the keys
        ``gameweek``, ``squad_value``, ``free_transfers``, ``chips_available``
        (8 flags), ``fixture_difficulty`` (5 values), ``rank`` and
        ``total_points``.
        """
        if self.model is None:
            raise RuntimeError("agent is not trained; call train() or load()")

        obs = state if isinstance(state, np.ndarray) else self.encode_state(state)
        action, _ = self.model.predict(obs, deterministic=True)
        chip = ACTIONS[int(action)]
        return chip

    @staticmethod
    def encode_state(state: dict) -> np.ndarray:
        """Build an observation vector from a plain dict of the current position."""
        chips = np.asarray(state.get("chips_available",
                                     np.ones(N_CHIP_FLAGS)), dtype=np.float32)
        if len(chips) != N_CHIP_FLAGS:
            raise ValueError(f"chips_available must have {N_CHIP_FLAGS} flags")

        difficulty = np.zeros(FIXTURE_LOOKAHEAD, dtype=np.float32)
        supplied = np.asarray(state.get("fixture_difficulty", []), dtype=np.float32)
        difficulty[:len(supplied[:FIXTURE_LOOKAHEAD])] = supplied[:FIXTURE_LOOKAHEAD]

        return np.concatenate([
            [state.get("gameweek", 1) / TOTAL_GAMEWEEKS],
            [(state.get("squad_value", 100.0) - 100.0) / 10.0],
            [state.get("free_transfers", 1) / MAX_BANKED_FT],
            chips,
            difficulty,
            [state.get("rank", 0.5)],
            [state.get("total_points", 0.0) / 2000.0],
        ]).astype(np.float32)

    def evaluate(self, n_episodes: int = 200, seed: int = 7) -> pd.DataFrame:
        """Play out seasons with the trained policy and log what it does.

        Reports the mean season total and when each chip tends to be played --
        the interesting output, since a sensible policy should learn to hold
        Bench Boost for a double gameweek rather than spending it in GW2.
        """
        if self.model is None:
            raise RuntimeError("agent is not trained")

        env = make_fpl_env(replay=self.replay, seed=seed)
        rows = []
        for ep in range(n_episodes):
            obs, _ = env.reset(seed=seed + ep)
            done, total = False, 0.0
            while not done:
                action, _ = self.model.predict(obs, deterministic=True)
                obs, reward, done, _, info = env.step(action)
                total += reward
            for entry in env.chip_log:
                rows.append({"episode": ep, **entry})
            rows.append({"episode": ep, "gameweek": None, "chip": "_total",
                         "reward_gain": total})
        return pd.DataFrame(rows)

    def save(self, path: str | Path) -> Path:
        """Persist the PPO policy."""
        if self.model is None:
            raise RuntimeError("nothing to save; the agent is not trained")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(path))
        return path

    def load(self, path: str | Path) -> "FPLChipAgent":
        """Load a PPO policy saved by :meth:`save`."""
        PPO = _require_sb3()
        self.model = PPO.load(str(path))
        return self


def baseline_chip_policy(state: dict) -> str | None:
    """Hand-written policy to benchmark the learned agent against.

    Encodes the conventional wisdom: hold Bench Boost and Triple Captain for
    double gameweeks, wildcard when the squad is stale, and never let a chip
    expire unused at the half-season boundary. An RL agent that cannot beat this
    is not worth deploying -- which is the point of having it.
    """
    gw = state.get("gameweek", 1)
    chips = np.asarray(state.get("chips_available", np.ones(N_CHIP_FLAGS)))
    n_fixtures = state.get("n_fixtures", 1)
    half_offset = 0 if gw < CHIP_HALF_BOUNDARY else len(CHIP_NAMES)

    def available(chip: str) -> bool:
        return chips[CHIP_NAMES.index(chip) + half_offset] > 0.5

    # Use it or lose it: the last gameweek of each half.
    deadline = (CHIP_HALF_BOUNDARY - 1) if gw < CHIP_HALF_BOUNDARY \
        else TOTAL_GAMEWEEKS
    if gw == deadline:
        for chip in (CHIP_BENCH_BOOST, CHIP_TRIPLE_CAPTAIN, CHIP_FREE_HIT,
                     CHIP_WILDCARD):
            if available(chip):
                return chip

    if n_fixtures >= 2:
        if available(CHIP_BENCH_BOOST):
            return CHIP_BENCH_BOOST
        if available(CHIP_TRIPLE_CAPTAIN):
            return CHIP_TRIPLE_CAPTAIN
    if n_fixtures < 1 and available(CHIP_FREE_HIT):
        return CHIP_FREE_HIT
    return None
