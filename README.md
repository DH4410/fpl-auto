# FPL Auto

[![Unit tests](https://github.com/DH4410/fpl-auto/actions/workflows/tests.yml/badge.svg)](https://github.com/DH4410/fpl-auto/actions/workflows/tests.yml)

FPL Auto is a self-hosted Fantasy Premier League assistant for the 2026/27 season. It collects current FPL data, forecasts the next six gameweeks, solves a legal squad and transfer plan with mixed-integer optimisation, can send a deadline digest, and can apply the frozen plan automatically.

All prediction and report generation runs locally. No external AI API or paid data key is required.

> [!CAUTION]
> Live mode can make irreversible transfers and activate chips on your FPL team. Start with `--dry-run`, inspect the generated plan, and monitor the first live runs. The authenticated FPL endpoints are not a stable public API and can change without notice.

## What it does

- Builds six-gameweek expected-points forecasts from official FPL signals, historical models, fixture difficulty, availability, and set-piece information.
- Blends the local ML estimate at 40% with FPL's `ep_next` anchor at 60%; a raw single-GW ML estimate is capped at 12 before blending.
- Uses a MILP planner to enforce budget, squad composition, formations, the three-player club limit, captaincy, transfers, hits, and up to five banked free transfers.
- Reviews injuries, suspensions, price changes, live scoring, post-GW forecast errors, and a deadline-frozen top-100 cohort.
- Produces Markdown/CSV reports and optional email digests and failure alerts.
- Freezes a pre-deadline plan, executes transfers as one atomic batch, updates the XI/captain, and verifies the result by reading the team back from FPL.

## Weekly lifecycle

| Stage | When | Behaviour |
| --- | --- | --- |
| `POST_GW_ANALYSIS` | After the previous GW is fully scored | Compares forecast with actual results and records new issues/ideas. |
| `MONITORING` | Between gameweeks | Refreshes news and availability; no FPL writes. |
| `PRE_DEADLINE_PLAN` | 18–36 hours before the next deadline | Rebuilds forecasts, runs the planner and simulator, emails a digest, and freezes the approved plan. |
| `EXECUTE` | 0.5–18 hours before the deadline | Executes at a pre-selected random time, with a 2.5-hour last-chance guard. |
| `INTERNATIONAL_BREAK` | More than 14 days to the next deadline | Makes no FPL changes. |

GitHub Actions invokes the state machine every two hours. Runs share one concurrency group so state-writing workflows cannot race each other.

## Transfer and chip safety

The automation is deliberately fail-closed:

- Paid transfers are approved only when six-GW gross gain is at least `4 × hits + 2` points.
- A transfer proposal rejected by the simulator is rebuilt as a fresh no-hit/free-transfer plan. Its old transfer list is never reused.
- If a Wildcard is recommended, the planner runs a second, dedicated unlimited-transfer solve. The result must contain 15 valid players, zero hits, a real squad change, and no officially unavailable, unselectable, non-transactable, or removed player.
- A failed Wildcard validation drops both the chip and its transfers, then generates a fresh no-hit plan.
- Wildcard transfers are sent atomically with `chip=wildcard`; banked free transfers are preserved.
- Free Hit execution is currently unsupported and is rejected rather than approximated with permanent transfers.
- Any official availability/news change or live-squad mismatch after planning invalidates the frozen plan and triggers a replan before a write.
- The final squad, captain, vice-captain, bench order, and chip are verified against an exact post-write read-back.

This means a large transfer list is not automatically a paid-hit plan. When `approved_chip` is `wildcard`, it must also be a validated `wildcard_rebuild` with `hit_count: 0`; otherwise execution refuses it.

## Quick start

Python 3.11 is recommended because that is what CI uses.

```bash
git clone https://github.com/DH4410/fpl-auto.git
cd fpl-auto
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pip install tabulate
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Copy the environment template and add your own credentials:

```bash
cp .env.example .env
```

The complete local configuration is:

```dotenv
FPL_REFRESH_TOKEN=replace_me
FPL_EMAIL=you@example.com
FPL_PASSWORD=replace_me
FPL_ENTRY_ID=1234567
FLASK_SECRET=replace_with_a_long_random_value
GMAIL_FROM=you@gmail.com
GMAIL_APP_PASSWORD=replace_with_a_google_app_password
```

`FPL_REFRESH_TOKEN` is tried first; email/password is the fallback. `GMAIL_FROM` and `GMAIL_APP_PASSWORD` are optional and only enable digests/alerts. Never commit `.env`, `.session.json`, passwords, tokens, or cookies.

### Get a refresh token

Install the local browser used by the login helper, then start the dashboard:

```bash
playwright install chromium
python app.py
```

Open [http://localhost:5000](http://localhost:5000), complete the browser sign-in, then visit [http://localhost:5000/api/refresh-token](http://localhost:5000/api/refresh-token). Copy the returned `refresh_token` into `.env` or the `FPL_REFRESH_TOKEN` GitHub secret.

### Run safely first

Let the orchestrator choose the stage from the live FPL calendar, but prohibit all FPL writes:

```bash
python scripts/weekly_orchestrator.py --auto --dry-run
```

To exercise planning specifically:

```bash
python scripts/weekly_orchestrator.py --force-stage PRE_DEADLINE_PLAN --dry-run
```

Only after reviewing the logs, `data/pre_deadline/gwN.json`, and `reports/season_plan_latest.md` should you allow a live run:

```bash
python scripts/weekly_orchestrator.py --auto
```

## GitHub Actions setup

Fork the repository, enable Actions, and add these under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
| --- | --- |
| `FPL_REFRESH_TOKEN` | Primary non-interactive FPL authentication. |
| `FPL_EMAIL` | Fallback FPL login. |
| `FPL_PASSWORD` | Fallback FPL login. |
| `FPL_ENTRY_ID` | Manager entry used for public post-GW and live-team analysis. |
| `GMAIL_FROM` | Optional Gmail sender and recipient for digests/alerts. |
| `GMAIL_APP_PASSWORD` | Optional Gmail app password; do not use the normal account password. |
| `GH_SECRETS_PAT` | Optional token used to rotate `FPL_REFRESH_TOKEN` automatically. |

Set **Workflow permissions** to **Read and write** so the workflows can persist reports and execution state. Then manually dispatch **Weekly Orchestrator** with `dry_run: true` and inspect its log before enabling live automation.

The included workflows are:

| Workflow | Schedule |
| --- | --- |
| `post_gw.yml` | Main state machine every two hours, plus manual dispatch. |
| `top100.yml` | Freezes rankings before the deadline and reads those teams only after lock, every 15 minutes. |
| `token_health.yml` | Checks/rotates the refresh token on the 1st and 15th of each month. |
| `transfer_window.yml` | Checks new signings every six hours while its configured transfer window is open. Update the hard-coded close date for a new window. |
| `apply_gw1.yml` | One-off initial-squad application; manual and dry-run by default. |
| `tests.yml` | Unit tests, Python compilation, and workflow-YAML validation on pushes and pull requests. |

## Outputs

| Path | Contents |
| --- | --- |
| `data/orchestrator_state.json` | Durable stage progress, frozen plan, and execution markers. |
| `data/pre_deadline/gwN.json` | Simulator decision for a gameweek. |
| `data/pre_deadline/forecast_gwN.csv` | Forecast snapshot used for that decision. |
| `data/post_match/gwN.json` | Post-gameweek review data. |
| `reports/season_plan_latest.md` | Latest human-readable six-GW plan. |
| `reports/season_plan_latest.csv` | Tabular version of the plan. |
| `reports/reflect_latest.md` | Latest forecast-error review and lessons. |

The workflows commit `data/` and `reports/` after state changes. Do not delete execution markers during a live gameweek: they prevent duplicate submissions after retries or runner failures.

## Useful commands

```bash
# Run the full test suite
python -m unittest discover -s tests -v

# Compile production Python
python -m compileall -q bot scripts
python -m py_compile fpl_api.py fpl_auth.py app.py

# Run only a read-only live fixture/news check
python scripts/live_check.py

# Run the transfer-window subtask without writes
python scripts/weekly_orchestrator.py --stage transfer_window --dry-run
```

## Project map

- `bot/season_forecaster.py` — six-GW projections and sanity checks.
- `bot/season_planner.py` — multi-gameweek MILP optimisation.
- `bot/chip_planner.py` — chip timing and sequence evaluation.
- `bot/pre_deadline_simulator.py` — transfer/chip approval gates.
- `bot/updater.py` — data, model, planner, and report pipeline.
- `scripts/weekly_orchestrator.py` — production entry point.
- `scripts/weekly_orchestrator_core.py` — persisted state machine and execution guards.
- `app.py` — local single-user dashboard and login helper.
- `docs/FPLrules.md` — rules reference.
- `docs/DATA_SOURCES.md` — source audit and endpoint notes.

## Scope and limitations

- The project is designed for one FPL account, not as a multi-user service.
- Forecasts are estimates, not guarantees; always inspect late team news.
- Free Hit is modelled for strategy but intentionally cannot be executed yet.
- The transfer-window workflow contains a season-specific close date that must be maintained.
- Authentication and authenticated FPL endpoints may break when Premier League changes its login or payloads.
