# Academic Research: FPL Optimisation & Football Prediction

*Compiled 2026-08-13 — verified sources only*

---

## 1. Competing with Humans at Fantasy Football (Matthews et al., AAAI 2012)

**Authors:** Tim Matthews, Sarvapali D. Ramchurn, Georgios Chalkiadakis  
**Venue:** AAAI Conference on Artificial Intelligence, pp. 1394–1400  
**Links:** [Semantic Scholar](https://www.semanticscholar.org/paper/Competing-with-Humans-at-Fantasy-Football:-Team-in-Matthews-Ramchurn/5aaaa7c18b7703021ed959dbe4dba15b35fd0d8a) | [CORE](https://core.ac.uk/outputs/8749245/)

Models FPL team selection as a Bayesian RL problem with an exponentially large action space. Finished top-percentile against 2.5 million managers.

**Key takeaway for fpl-auto:** Rolling-horizon MILP is the right architecture. The Bayesian RL framing motivates stochastic/robust variants of the MILP objective for chip-timing uncertainty.

---

## 2. Modelling Association Football Scores and Inefficiencies in the Football Betting Market (Dixon & Coles, 1997)

**Authors:** Mark J. Dixon, Stuart G. Coles  
**Venue:** JRSS: Series C (Applied Statistics), Vol. 46, No. 2, pp. 265–280  
**Link:** [Wiley](https://rss.onlinelibrary.wiley.com/doi/10.1111/1467-9876.00065)

The canonical bivariate Poisson model for football scorelines with low-score diagonal inflation and exponential time-decay weighting (optimal ξ ≈ 0.0065 per day).

**Key takeaway for fpl-auto:** The literature optimal decay ξ = 0.0065 vs our current ξ = 0.0018 (in feature_engineering.py). Our decay is ~3.6× slower — team strengths are updated less responsively to recent form. Consider calibrating ξ against EPL historical data. Also: the sample-weight approach of time-decay is the academic basis for the `sample_weight` fix already committed.

---

## 3. Analysis of Sports Data by Using Bivariate Poisson Models (Karlis & Ntzoufras, 2003)

**Authors:** Dimitris Karlis, Ioannis Ntzoufras  
**Venue:** JRSS: Series D (The Statistician), Vol. 52, No. 3, pp. 381–393  
**Links:** [Semantic Scholar](https://www.semanticscholar.org/paper/Analysis-of-sports-data-by-using-bivariate-Poisson-Karlis-Ntzoufras/2b7290f6fe92dc43b57f27c863384e7e3faffb3c) | [PDF](http://www2.stat-athens.aueb.gr/~jbn/papers2/08_Karlis_Ntzoufras_2003_RSSD.pdf)

Replaces independent Poisson (Dixon-Coles) with a true bivariate Poisson that models goal correlation between teams; extends with diagonal inflation for draws. Improves scoreline probability calibration.

**Key takeaway for fpl-auto:** Better scoreline probabilities → better CS and bonus-point estimates for defenders/GKPs. `feature_engineering.team_strength_matrix` currently uses independent Poisson; upgrading to bivariate Poisson would improve xG-against features.

---

## 4. Bayesian Hierarchical Model for Football Results (Baio & Blangiardo, 2010)

**Authors:** Gianluca Baio, Marta Blangiardo  
**Venue:** Journal of Applied Statistics, Vol. 37, No. 2, pp. 253–264  
**Link:** [Tandfonline](https://www.tandfonline.com/doi/full/10.1080/02664760802684177)

Fully Bayesian hierarchical Poisson model with MCMC. Produces full posterior distributions over team strengths, not point estimates.

**Key takeaway for fpl-auto:** Full posteriors over team strength → captain selection can maximise *P(captain scores > alternatives)* rather than just highest mean xPts. The posterior variance is informative for DGW/TC chip decisions.

---

## 5. Bayesian Inference for Player Abilities in Football (Whitaker et al., 2017/2020)

**Authors:** Gavin A. Whitaker, Ricardo Silva, Daniel Edwards, Ioannis Kosmidis  
**Venue:** arXiv:1710.00001 (stat.AP)  
**Link:** [arXiv](https://arxiv.org/abs/1710.00001)

Extends team-level Bayesian models to individual players via variational inference. A player's goal-scoring ability is separated from their team's strength.

**Key takeaway for fpl-auto:** Principled promoted-team player handling. A player from Sunderland (promoted) retains an informative individual ability prior even when team strength is unknown. This is a formal replacement for `promoted_team_priors` in feature_engineering.py.

---

## 6. Multi-stream Data Analytics for FPL Performance Prediction (Bonello et al., 2019)

**Authors:** Nicholas Bonello, Joeran Beel, Seamus Lawless, Jeremy Debattista  
**Venue:** 27th AIAI Irish Conference | arXiv:1912.07441  
**Link:** [arXiv](https://arxiv.org/abs/1912.07441)

Integrates historical stats, FDR, betting odds, and social media signals. Finished top 0.5% of 6.5 million players in 2018/19 using all four streams.

**Key takeaway for fpl-auto:** Betting market odds and FDR are documented to add signal beyond historical stats. The `devig_odds` + `implied_goal_rates` code already exists in `feature_engineering.py` but is NOT wired into the live pipeline. Connecting odds features to the ML model training is a low-cost accuracy improvement.

---

## 7. Adaptive Bayesian Weighted Models for Football Prediction (Macrì-Demartino et al., 2025)

**Authors:** Roberto Macrì-Demartino, Leonardo Egidi, Nicola Torelli  
**Venue:** JRSS: Series C, 2026 | arXiv:2508.05891  
**Links:** [arXiv](https://arxiv.org/abs/2508.05891) | [Oxford Academic](https://academic.oup.com/jrsssc/advance-article/doi/10.1093/jrsssc/qlag032/8704597)

Bayesian spike-and-slab priors learn the optimal recency decay from data — effectively an adaptive ξ that accelerates after managerial changes or transfer windows.

**Key takeaway for fpl-auto:** The fixed ξ = 0.0018 in `fit_team_strength_by_name` should be learned from data, not hand-set. The spike-and-slab approach is complex but the simpler takeaway is: *cross-validate ξ on holdout seasons* and update it seasonally.

---

## 8. Data-Driven MILP Framework for FPL Team Selection (Ramezani & Dinh, 2025)

**Authors:** Danial Ramezani, Tai Dinh  
**Venue:** arXiv:2505.02170 (cs.CE)  
**Link:** [arXiv](https://arxiv.org/abs/2505.02170)

Deterministic and robust MILP formulations for FPL squad selection, bench ordering, captaincy, chip deployment, and multi-week rolling-horizon transfer planning. ARIMA with constrained budget and rolling window gave the best out-of-sample performance in 2023/24.

**Key takeaway for fpl-auto:** The **robust MILP variant** (hedging against forecast scenarios) is a direct upgrade to the current deterministic formulation in `season_planner.py`. Also provides a formal chip-deployment MILP that could replace the current heuristic chip planner.

---

## Implementation Priority (from papers above)

| Priority | Change | Paper | Status |
|----------|--------|-------|--------|
| 1 | Fix double-decay in forecaster | — | ✅ Committed |
| 2 | Sample weight for per-90 cameo bias | Dixon & Coles decay logic | ✅ Committed |
| 3 | Wire ML into weekly updater | Bonello et al. (multi-stream) | ✅ Committed |
| 4 | Calibrate Dixon-Coles ξ decay (0.0018 → ~0.0065) | Dixon & Coles 1997 | TODO |
| 5 | Wire betting odds features into model training | Bonello et al. 2019 | TODO |
| 6 | Robust MILP with scenario sets | Ramezani & Dinh 2025 | TODO |
| 7 | Bivariate Poisson for scoreline distribution | Karlis & Ntzoufras 2003 | TODO |
| 8 | Posterior-variance-aware captain selection | Baio & Blangiardo 2010 | TODO |
