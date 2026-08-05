"""
FPL Auto — bot package.

An advisory Fantasy Premier League engine. It produces *recommendations only*:
nothing in this package calls a write endpoint, and no module here ever executes
a transfer. Squad changes remain a manual, human-confirmed action in the web app.

Modules
-------
fpl_rules          Exact encoding of the 2026/27 FPL ruleset (scoring, squad,
                   chips, transfers, selling price, auto-substitutions).
data_collector     Cached ingest of the official FPL API, Vaastav historical
                   CSVs and FPL-Core-Insights.
feature_engineering  Module 1 — EWMA form, fixture difficulty, Dixon-Coles team
                   strength, promoted-team priors.
models             Module 2 — four ML sub-models (minutes, attack, defense,
                   bonus) plus the FPLPointsPredictor orchestrator.
simulator          Module 3 — bivariate-Poisson match simulation and player
                   attribution, vectorised over Monte Carlo draws.
optimizer          Module 4 — MIP squad/XI/transfer/chip optimisation via PuLP
                   with a HiGHS-or-CBC solver (never Gurobi).
rl_agent           Module 5 — PPO chip-timing agent over a Gymnasium env.
season_forecaster  Module 6 — lightweight per-player, per-GW xPts projection.
season_planner     Module 7 — rolling-horizon MILP season planner.
chip_planner       Module 8 — marginal chip value and timing.
updater            Module 9 — post-matchday automated pipeline orchestrator.
"""

__version__ = "0.2.0"

__all__ = [
    "fpl_rules",
    "data_collector",
    "feature_engineering",
    "models",
    "simulator",
    "optimizer",
    "rl_agent",
    "news_collector",
    "season_forecaster",
    "season_planner",
    "chip_planner",
    "updater",
]
