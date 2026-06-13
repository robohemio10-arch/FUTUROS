# Docker Compose Read-Only Volume Tightening V1

## Machine-Readable Policy

```text
policy_status: active
paper_only: true
shadow_only: true
live_trading_enabled: false
order_submission_enabled: false
real_order_submission_enabled: false
exchange_private_access: false
sends_orders: false
changes_risk: false
writable_exception: docker-compose.live.example.yml|freqtrade-live|/freqtrade/user_data
```

## Objective

Read-only bind mounts reduce the chance that a compromised or defective process modifies source code, scripts, configuration, or strategies on the host. This branch applies that protection only where the runtime has no legitimate write requirement.

## Read-Only Mounts

The following container paths are immutable inputs and should use `:ro` or long-syntax `read_only: true`:

- `/app/config`
- `/app/scripts`
- `/app/smartcrypto`
- `/app/docs`
- `/freqtrade/user_data/config.paper.json`
- `/freqtrade/user_data/strategies`

The paper bot and Qlib worker mounts for code/scripts/config were tightened accordingly. Dashboard, supervisors, feedback sync, and notification services already used read-only mounts for immutable inputs.

## Writable Mounts

These paths remain writable because services generate operational state there:

- `/app/data`, including reports, predictions, runtime state, and feature outputs;
- `/app/logs`;
- `/freqtrade/user_data/logs`;
- `/freqtrade/user_data/data` in paper execution;
- `/freqtrade/user_data/db` and named SQLite volumes.

Making these paths read-only would break paper operation, evidence generation, feedback synchronization, or database persistence. Their writable classification is intentional, not a security bypass.

## Temporary Mixed-Purpose Exception

`docker-compose.live.example.yml` still mounts `./freqtrade/user_data` broadly and writable at `/freqtrade/user_data`. The example combines immutable strategy/config files with state that Freqtrade may write. Splitting that bind safely requires a dedicated compatibility review and is outside this narrow branch.

The auditor classifies this exact file/service/target tuple as `unknown_requires_review` and returns `warning`. The exception does not approve other writable code/config mounts and does not enable live trading: the example remains dry-run with live and order flags disabled.

## Audit

Run the offline static audit:

```powershell
python scripts/audit_docker_compose_readonly_volumes.py --project-root . --json
```

Validate Compose rendering separately:

```powershell
docker compose -f docker-compose.paper.yml config
```

Unit tests never invoke Docker or network services. The auditor reads versioned Compose text only and does not import trading modules, access an exchange, send notifications, or write runtime artifacts.

## Scope Boundary

This branch does not alter commands, entrypoints, environments, ports, networks, images, healthchecks, trading, RiskManager, Freqtrade strategy, Qlib, AI Shadow, OCR, datasets, active signals, readiness, canary, or live behavior. Image digest, lockfile hashes, and notification root bootstrap remain governed by their dedicated policies.
