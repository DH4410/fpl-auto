# FPL Multi-Account Platform

## Goal

Scale the existing single-manager FPL automation into a platform that can run dozens of authorised FPL teams from one shared forecasting/model stack while keeping each bot strategically distinct.

The platform should:

- manage many email/password FPL logins without committing credentials;
- build expensive public data, features and forecasts once per cycle;
- give every bot a stable strategy personality instead of cloning one optimiser 76 times;
- isolate account state so one failure cannot corrupt another bot;
- execute transfers, picks, captaincy and chips safely for each account;
- evaluate every bot after each gameweek and feed bounded lessons into future decisions;
- publish a credential-free leaderboard/dashboard of bot teams and results.

## Architecture

### 1. Shared intelligence layer

Run once per orchestration cycle:

- FPL bootstrap, fixtures, live data and player summaries;
- news/injury/transfer-window collectors;
- feature engineering;
- trained prediction models and xPts forecasts;
- top-manager/cohort signals;
- common candidate pools and fixture horizon data.

This layer is deliberately account-independent. Training or rebuilding the same model 76 times would be slow, expensive and create unnecessary load on FPL endpoints.

### 2. Account registry and credential boundary

Credentials are runtime-only secrets. The repository stores only an account ID, optional bot display name and public strategy metadata.

Preferred CI secret format:

```json
{
  "accounts": [
    {"email": "...", "password": "...", "display_name": "Bot 01"}
  ]
}
```

The JSON is supplied through `FPL_ACCOUNTS_JSON` (or an equivalent encrypted secret-store integration). Passwords and emails must never be written to reports, dashboard files, logs or committed state.

Each email is mapped to a stable opaque account ID using a one-way hash. FPL entry IDs may be stored because they are public identifiers.

### 3. Strategy personality layer

All bots share the same underlying forecasts, but optimise them differently. A deterministic strategy profile is generated for each opaque account ID and then persisted so the bot keeps a recognisable personality across gameweeks.

Personality dimensions include:

- template vs differential preference;
- transfer patience / hit aversion;
- short vs long fixture horizon;
- captain safety vs upside;
- bench investment;
- price-change/value sensitivity;
- injury/minutes-risk tolerance;
- chip aggressiveness;
- form vs underlying-data weighting.

Profiles are bounded: creativity should produce different teams and decisions without allowing deliberately irrational play.

Initial archetypes include Template Anchor, Differential Hunter, Fixture Planner, Form Chaser, Value Builder, Captain Maverick, Patient Planner, Aggressive Rebuilder, Attack Heavy, Defence First, Minutes Purist and Balanced Analyst.

### 4. Per-account decision/execution layer

For each account:

1. authenticate using the existing OAuth2 PKCE email/password flow;
2. discover its current `entry_id` through `/api/me/`;
3. fetch current squad, bank, free transfers and chip state;
4. combine shared forecasts with that account's strategy profile;
5. build a per-account transfer/captain/chip plan;
6. run the existing safety simulation/validation rules;
7. execute only inside the permitted deadline stage;
8. record a structured decision receipt.

A failure for one account must be captured and the remaining accounts must continue unless the failure indicates a global FPL/API outage.

### 5. State model

Per-account state must be isolated conceptually as:

```text
data/accounts/<account_id>/
  profile.json
  orchestrator_state.json
  pre_deadline/
  post_match/
  decisions/
  history/
```

Shared data remains under the existing common data/cache locations.

Longer term, account state and leaderboard snapshots should move to a small database rather than generating dozens of competing Git commits from CI workers.

### 6. Post-gameweek learning

The existing post-match analyzer already compares actual output with pre-GW forecasts and creates an idea list. The multi-account version should add a bot-specific scorecard:

- GW points and total points;
- points hit;
- transfer net gain vs hold;
- captain result vs best reasonable alternative;
- bench points / benching regret;
- model prediction error for owned players;
- injury/minutes misses;
- differential wins/losses;
- chip value when used;
- rank movement.

Learning is bounded and gradual. A single lucky/unlucky GW must not radically rewrite a strategy. Use rolling/EWMA metrics over several GWs and adjust only small profile deltas within safe limits.

Examples:

- repeated failed punts -> slightly lower differential weight;
- consistently strong patient holds -> increase transfer patience;
- repeated captain volatility losses -> reduce captain variance;
- excessive bench regret -> slightly increase XI certainty / reduce bench spend.

The original profile remains stored so we can compare current behaviour with the bot's starting identity.

### 7. Dashboard / leaderboard

The dashboard must contain no private login information. It can use public entry IDs after initial discovery.

Core leaderboard columns:

- internal rank;
- bot name;
- strategy/archetype;
- GW points;
- total points;
- overall rank;
- rank change;
- transfers this GW;
- transfer cost/hits;
- captain;
- chip;
- team value;
- current record/status.

Bot detail pages should show current squad, recent transfers, captain history, chips, GW score history, strategy profile and post-GW reflections.

### 8. Scheduling for ~76 accounts

Do not run 76 full independent model pipelines.

Recommended workflow:

1. `prepare-shared-data` builds the common forecast snapshot once;
2. split account IDs into stable shards (for example 8 shards of about 9-10 accounts);
3. process shards with conservative parallelism and jitter between authenticated actions;
4. aggregate receipts/results into the leaderboard snapshot;
5. render/publish the dashboard.

Deadline execution should be idempotent. Every write requires an account/GW action key so retries cannot submit the same transfer twice.

## Security requirements

- Never commit the supplied spreadsheet or derived plaintext credentials.
- Redact credentials from exceptions and logs.
- Do not include emails in public dashboard data.
- Store the full account JSON only in an encrypted runtime secret.
- Prefer one-way opaque account IDs in filenames and state.
- Rotate credentials if any secret is ever printed to a public CI log.
- Only automate accounts whose owners have authorised this project.

## Implementation phases

### Phase 0 — platform skeleton

- account loader and opaque IDs;
- deterministic strategy profiles;
- credential-safe logging;
- tests for validation/profile stability.

### Phase 1 — isolated multi-account runtime

- refactor orchestrator paths into an account context;
- auto-discover `entry_id` after login;
- per-account state and decision receipts;
- serial dry-run across a small test subset.

### Phase 2 — real strategy divergence

- thread `StrategyProfile` into optimiser, captaincy, transfer-hit and chip scoring;
- add profile-aware explanations;
- backtest the archetypes to eliminate destructive parameter ranges.

### Phase 3 — post-GW learning

- account scorecards;
- rolling regret/performance metrics;
- bounded profile adaptation;
- comparison against original profile and cohort.

### Phase 4 — dashboard

- leaderboard snapshot builder;
- team/bot detail views;
- strategy and learning visualisations;
- failure/health view for orchestration.

### Phase 5 — CI scaling

- shared forecast artifact;
- sharded account workers;
- conservative concurrency/rate limiting;
- idempotency and retry testing;
- end-to-end dry-run before enabling writes.

## Definition of done

The platform is ready for all accounts only when a complete dry-run can process every account, produce 76 independent plans and dashboard rows, preserve isolated state, survive individual login failures, and prove that retries cannot duplicate transfers or chip actions.
