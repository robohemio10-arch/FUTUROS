# F03 read-only financial lifecycle boundary

The existing closed-trade adapter now requires `trades` and `orders` from the
same temporary, query-only SQLite snapshot. Source database/WAL/SHM hashes must
remain unchanged. Missing, unreadable or incomplete evidence fails closed.
No deployment, database repair, historical replay or operational quarantine file
is performed. Quarantine means exclusion from the in-memory accepted bundle and
explicit reasons in the existing adapter report, not a new order/risk authority.

`financial_lifecycle.reconcile_financial_lifecycle` checks exact local/order/
parent/pair/side identities, quantities, weighted execution prices, inventory
chronology and the existing financial contract with Decimal and its unchanged
`epsilon_abs_fonte=0.00000001`. Positive fills count even on canceled orders.
Zero fills do not create executions. Pending orders prevent certification of a
closed trade. Equal timestamps cannot prove an entry-before-exit ordering.
Unknown base-asset fee/inventory semantics remain quarantined. Summary quantity
or average incompatible with the full execution history is not silently repaired;
unsupported scale-in/partial-exit summaries require separate evidence/contract.

The financial identity remains signed linear price delta times quantity and
contract size, minus `fee_open_cost * leverage`, minus `fee_close_cost`, plus
`funding_fees`. These are authoritative Paper accounting columns, not independent
observed exchange fees/fills. Nullable per-order funding is explicitly unavailable,
not zero. When all per-order funding values exist their sum is also reconciled.
Missing trade costs/funding or non-USDT fees are not synthesized.

## Historical versus current health

The fixed five-trade forensic classification and the 221/234 recovery allowlist
are unchanged. Existing `quarantine_recovery` alone can replace those two prices
in memory after authoritative validation. They still need the new lifecycle
checks; historical partial recovery never certifies the entire current population.

| Representative isolated case | Expected read-only result |
| --- | --- |
| 141, 258, 561: exit quantity twice entry | Quarantined; historical cause unproven |
| 258: inconsistent times | Additional temporal quarantine |
| 221, 234: missing close_rate | Quarantined without existing validated recovery |
| 221, 234: existing recovery and full valid execution evidence | Eligible in memory only |
| 653, 669: canceled positive-fill exit plus second full exit | Both fills count; quarantined |
| 676: zero-fill timeout cancellation and legitimate retry | Eligible if all other checks pass |

The fixtures represent predicates, not reconstructed historical executions/prices.
Original historical artifacts are neither edited nor recertified. The existing
duplicate-exit, retry, stoploss and liquidation guards remain untouched.

## Installed-engine evidence and limit

The pinned image `sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db`
contains Freqtrade 2026.8. The isolated installed-package regression exercises real
`Trade.update_trade`, `Trade.close` and `FreqtradeBot.update_trade_state` through
`handle_cancel_exit`. A closed fill first sets close_rate to 110; the cancel
handler subsequently assigns None while preserving the filled order. Exchange,
notifications and database commits are isolated fixture boundaries, not network
or operational writes. This proves a current reachable handler behavior under
the supplied invocation, not that the current strategy normally invokes it or
that this was the historical cause of trades 221/234: `ROOT_CAUSE_UNPROVEN`.

The structural correction is at financial admission: such a record cannot enter
the accepted bundle without the existing explicit authoritative recovery. No
monkeypatch, vendored Freqtrade change or strategy lifecycle hook is introduced.
Removing the upstream assignment requires separately authorized execution-engine
work and real call-path evidence; it is not disguised as fixed here.

Run focused F03, adapter/forensic/closeout, exit-lifecycle and Trader Master tests.
Run the installed regression in that image with source mounted read-only, network
disabled, `PYTHONPATH=/freqtrade:/repo`, `PYTHONUSERBASE=/home/ftuser/.local`,
`PYTHONDONTWRITEBYTECODE=1`, `F03_REQUIRE_INSTALLED_FREQTRADE=1` and a disposable
`/tmp`; execute `python -B -m unittest
tests.test_paper_financial_lifecycle_installed_freqtrade_f03_v1 -v`.

Runtime snapshots, CSV/Parquet/Master and immutable Epoch 2 registration are never
modified. Fixture success is not runtime parity or operational authorization.
