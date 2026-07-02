# Paper Auto-learning Scheduler

This directory contains the audited deployment contract for the paper/shadow
auto-learning scheduler.

The versioned kill-switch file is a template only. Operational runs may copy it
to `data/runtime/autolearning_scheduler_kill_switch.json`, which is runtime data
and must not be committed.

The deployment contract is intentionally paper-only:

- no live trading
- no order submission
- no private exchange access
- no risk changes
- no model promotion
- no master update
