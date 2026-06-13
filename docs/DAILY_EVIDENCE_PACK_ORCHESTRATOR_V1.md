# Daily Evidence Pack Orchestrator V1

## Objective

The orchestrator runs a fixed allowlist of paper/shadow evidence checks and consolidates their results into one daily report. It is an operator-invoked CLI, not a scheduler. This branch creates no cron job, systemd timer, Windows task, heartbeat, or external automation.

## Allowlisted Steps

The default pack executes:

- runtime evidence pack v2 in `--no-write` mode;
- paper runtime health/freshness without `--write`;
- notification runtime permission audit without `--write`;
- operational exception swallowing audit;
- Freqtrade image pin/digest policy audit;
- lockfile hash integrity audit;
- Docker Compose read-only volume audit;
- deterministic manifest check;
- versioned secret scan.

`build_dashboard_snapshots.py` is intentionally excluded because it has no no-write mode. Subordinate steps never write their own reports. With normal pack writing enabled, only the dated daily report and `latest` report are created.

Docker/container collection is disabled by default. `--include-container-snapshot` only adds the existing read-only container collection flag to the two allowlisted evidence steps that support it.

## Conservative Status

- any blocked step makes the pack `blocked`;
- otherwise any warning makes the pack `warning`;
- only all-ok steps produce `ok`;
- timeout, execution failure, invalid JSON, missing script, or an unsafe command produces `blocked`.

The orchestrator never rewrites warning/blocked to ok. It does not modify readiness, canary, live, risk gates, thresholds, models, signals, datasets, or trading behavior.

## Concurrency And Atomic Writes

`data/runtime/daily_evidence_pack.lock` is created atomically. A recent lock returns a controlled `daily_evidence_pack_already_running` result. A stale lock older than one hour is removed and reported through `lock_recovered=true`. The lock is removed in `finally`.

Reports are rendered to a temporary file in the destination directory and replaced atomically:

- `data/reports/daily_evidence_pack_YYYYMMDD.json`
- `data/reports/daily_evidence_pack_latest.json`

Runtime reports and lockfiles remain ignored by Git.

## Commands

Safe no-write run:

```powershell
python scripts/run_daily_evidence_pack_orchestrator.py --project-root . --no-write --json
```

Write the daily pack:

```powershell
python scripts/run_daily_evidence_pack_orchestrator.py --project-root . --output-dir data/reports --json
```

Optional read-only container evidence:

```powershell
python scripts/run_daily_evidence_pack_orchestrator.py --project-root . --no-write --include-container-snapshot --json
```

The default per-step timeout is 120 seconds and can be adjusted with `--timeout-seconds`. `--date YYYY-MM-DD` exists for deterministic replay/tests and only controls report naming/date.

## Security

Commands are fixed `StepDefinition` entries. The CLI accepts no script path. Every script is resolved under ProjectRoot and checked against the allowlist. Subprocess calls use `sys.executable`, argument lists, `shell=False`, a timeout, and controlled JSON parsing.

Stdout/stderr excerpts are truncated and redact token, secret, password, API-key, bearer, Telegram, NTFY, and `.env` markers. The report stores only a safe summary of each step's JSON.

The orchestrator does not call an exchange, CommandBus, NotificationDispatcher, Telegram, NTFY, or Docker by default. It sends no orders, changes no risk, and preserves live/canary release as false.
