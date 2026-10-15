# F02 publisher permission boundary

## Publisher and dedicated Treatment boundary

The publisher independently attempts signal invalidation and blocked-report
persistence. Both failures retain their stage, exception type and errno in the
returned report and structured stderr, without exception messages or source
payloads. Invalidating the signal artifact requires a successful empty atomic
replacement and exact readback. An unsuccessful cycle terminates the CLI with
exit code 2, including interval mode. Docker restart policy is not changed.
The success report is written only after the exit-control mirror succeeds.

The **Control A**
`SmartCryptoSignalStrategy._read_first_active_signal_file` and
`_is_signal_active` use the signal's `risk_approved` and `valid_until`, not the
publisher report. If the publisher cannot replace its output, the prior signal
can still be accepted until expiry. A missing `valid_until` is accepted by that
legacy consumer. Its code and protective exits are unchanged.
`depends_on: service_healthy` gates startup only. A healthcheck,
an unhealthy container, stderr or publisher termination does not revoke an
already-readable artifact in a running consumer.

Compose selects `SmartCryptoCanonicalTreatmentStrategy` only for Treatment.
It inherits protective exits, but has a single signal source, strict aware UTC
expiry, publication freshness of at most 20 seconds, an exact signal/decision/
pair/side identity, unique decision identities, an `ok` report and matching
publication UUID/payload hash. No alternate signal file is consulted, and null
expiry is never accepted for entry. Original Control signal objects are preserved.

Every admission additionally needs a synchronous Unix-socket authorization from
the running publisher. A nonce and canonical request hash bind the response to
the exact publication/report/decision; `SO_PEERCRED` verifies publisher UID 10001
and consumer UID 1000. Numeric identities are the deployment contract, not an
implicit group grant. There is no HTTP port, private API, shared credential,
authorization cache or fallback-to-JSON mode. Missing, inaccessible, unresponsive
or unsupported IPC fails closed. The RPC deadline is 500 ms, the server bounds
request size to 8 KiB, and the single worker has a bounded listen backlog.
This is a negative admission fence, not a source of new RiskManager approval;
it never creates a trading signal or sends an order.

The publisher revokes its in-memory commit **before** each build attempt. It
commits a new generation only after all writes succeed. A request rereads source
Control, V3 evidence, output and report using the publisher's own permissions,
checks their committed hashes and expiry, then verifies that the memory commit
has not changed during the reads. Permission loss can therefore deny admission
before the next refresh cycle, even when the consumer can still read old files.
Revocation does not need to persist a blocked report. Publication-in-progress
causes conservative transient denials rather than reusing a prior approval.

`confirm_trade_entry` repeats all checks immediately before entry; prior
`populate_indicators` approval is not reusable. It contains unexpected exceptions
because the pinned engine's safe wrapper otherwise defaults entry confirmation
exceptions to `True`. `check_entry_timeout` requests cancellation of remaining
unfilled entries on revocation, including partially filled entries, without
closing their existing filled position. `adjust_entry_price` maintains the same
price when authorized, or cancels without replacement when denied; replacement
cannot use the engine's `mode != initial` confirmation bypass. Position adjustment
is disabled by the existing config and the dedicated strategy. Exit callbacks,
stoploss, liquidation and regular position/exit management remain inherited.

The previous `continuous_consumer_fence_proven=false` diagnostic remains deliberate:
the publisher alone cannot attest that the effective runtime is using the new
strategy, mounts and callbacks. Passing component tests is not host or Epoch parity.

## Residual revocation window and runtime gate

There is **no claim of atomic revocation at order submission**. Revocation before
or during the synchronous check denies the admission. After the final authority
check there remains socket delivery, local file revalidation, callback return and
the pinned engine's call to `exchange.create_order`. OS descheduling and I/O mean
this interval has no proven hard wall-clock bound. The 500 ms RPC deadline is not
an end-to-end submission bound, and the 20-second JSON TTL is not the revocation
mechanism. An order already submitted/filled cannot be retroactively prevented.
The consumer also rejects if its post-query file revalidation exceeds that RPC
deadline. Independent source reads are not a transaction with other filesystem
writers: a change after the last checked read belongs to the same residual window.

Pending-order cancellation is requested on the next engine management callback.
The effective process loop, scheduling, order-state fetch and cancellation latency
bound that delay, not this strategy. A fill can race cancellation; exchange/fetch
failure can defer the callback. Protective exits and filled-position management
are never gated by publisher availability. Strict atomic submission/cancellation
would require separate execution-engine authorization, outside this change.

**Deployment remains BLOCKED** until filesystem ACL/Unix-socket support, actual
UIDs, mounts, latency and effective callback/config behavior are proved on the host
and causal governance accepts the drift. No new authority is inferred from tests.
The immutable Epoch 2 registration is never rewritten or recertified automatically.

## Permission contract

Obtain paths, numeric identities and mounts from the effective configuration,
not from a historical container name. Check effective and supplementary IDs for
both publisher and consumer. The reported incident used publisher `10001:10001`
and a consumer-owned `1000:1000`, mode `2770`, output directory. A read-write bind
mount does not override Linux DAC or ACLs.

| Role | Required access for publisher |
| --- | --- |
| Control active signal JSON | read file, search exact ancestors |
| V3 evidence JSON | read file, search exact ancestors |
| Control exit JSON, when present | read file, search exact ancestors |
| Treatment runtime output directory | search/write for atomic replace and exit mirror |
| Treatment ledger directory/file | search/write directory and read ledger |
| Treatment report directory/file | search/write directory and read report |
| Publisher authorization socket | publisher-owned live socket; consumer UID 1000 connects only |
| Unrelated runtime, data and research paths | no new access |

Atomic replacement requires directory permissions, not just a writable existing
file. The publisher must read its invalidated artifact back. The actual consumer
must be able to read newly replaced files. Default ACLs must therefore cover the
specific output directories, not `/app/data` or its entire subtree. Source grants
must not permit source writes or directory enumeration when search alone suffices.

Two additional directory mounts are specific and read-only, outside Freqtrade's
userdata initialization: Treatment output at `/run/canonical-treatment-output`,
and only `reports/canonical_treatment` at `/run/canonical-treatment-report`.
Directory rather than individual-file mounts preserve visibility of atomic
replacements. Existing runtime/log mounts remain unchanged. No `group_add`,
privileged flag, host port or writable new mount is added.

The report directory must grant consumer UID 1000 search, not directory write;
its default ACL must grant that UID `rw-` for the socket. The publisher sets the
socket to mode 0660, preserving the named ACL under its mask. The same inherited
entry on report files cannot be used to write through the consumer's read-only
mount. The publisher keeps directory write access. Verify these effective rights,
the regular-file read path and successful IPC through the read-only mount on the
actual filesystem. The consumer cannot replace a socket or forge its peer UID.
Unsupported Unix sockets on a Windows-backed bind are a blocker, not a reason to
fall back to stale JSON, broad mounts, groups or an unauthenticated network service.

Before proposing any repair, inspect the exact paths with `id`, `stat`, `namei -l`,
`findmnt -T` and `getfacl -n` in the same mount/user namespace. Record every ancestor,
mount source/type/options, effective ACL/mask and numeric owner. Check symlinks and
resolve mounts before selecting targets. Do not print `.env` or credentials.
Read-only inspection cannot prove ACL writes or post-replacement inheritance.

Support must be tested on an explicitly authorized, disposable sibling fixture
on the **same filesystem**, not on operational artifacts. `getfacl` output alone
is insufficient: set an ACL on that fixture, test actual access as both effective
UIDs with no incidental supplementary group grant, atomically replace a fixture
file, recheck access and remove only the fixture. If unsupported, denied or
inconsistent across Docker Desktop namespaces, STOP. The isolated tmpfs tests in
this branch do not certify ACL support on a Windows-backed production bind mount.

For an approved repair plan, use only named-user ACLs on enumerated exact paths:
publisher search on required ancestors, read on the three sources, and read/write/
search on the three output directories. Preserve unrelated ACL entries and audit
the mask before/after: broadening a mask may grant other existing named entries
more access. Set a default ACL on only each output directory to preserve the
consumer's required read access after replace; verify actual new-file permissions
under the publisher's real umask. No recursive grant, `chmod 777`, blanket
`chown`, ACL on `/app/data`, automatic `group_add` or automatic repair is allowed.

If POSIX ACLs are unsupported, the minimal alternative is a separately reviewed
identity/mount plan limited to these exact source files and output directories,
with publisher-writable output and consumer-readable output separated from
consumer-writable decision logs. Changing UID/GID, mount layout or adding a
path-specific group is **not** an automatic fallback. It changes identity/parity
and requires explicit authorization, least-privilege negative tests and renewed
causal runtime validation before activation. Never recursively change the whole
shared data root to make the problem disappear.

## Controlled recovery procedure (not executed by this change)

1. Obtain separate maintenance authorization and validate the effective dedicated
   consumer, synchronous authority and residual submission/cancellation risk.
   Do not proceed without accepting those execution limits. Preserve existing positions'
   risk/exit protections; this document does not authorize stopping trading or
   restarting any service.
2. Seal the effective mount/identity/ancestor/ACL observations and preserve the
   previous report, ledger and signals as evidence outside Git. Inspect consumer
   startup directory-management behavior: recursive ownership changes in a shared
   writable mount can invalidate the plan after startup. Historical actor
   attribution is not established merely by discovering this possible writer.
3. After filesystem support and least-privilege tests pass, obtain approval for
   the enumerated ACL plan. Apply only that approved plan in the correct namespace.
   Revalidate as publisher and consumer, including source-write/unrelated-path
   denial, atomic output replacement, report persistence and startup ownership
   stability. Failure at any point leaves the system blocked; do not retry storm.
4. In an independently fenced maintenance window, validate one prospective cycle,
   exact lineage and current freshness. Confirm empty invalidation on injected
   fixture failures and rejection of blocked, stale or unreadable reports. No
   replay, backfill, manual retry of historical decisions or identity repair.
5. Preserve the immutable Epoch 2 registration. Code/Compose/mount/container
   fingerprint changes are drift until `validate_registration` and
   `validate_current_epoch2_runtime` plus canonical governance validate the actual
   runtime. Passing fixture tests is not runtime parity. Do not rewrite the
   registration/hash, introduce a new T0 or register another epoch as a workaround.
6. Only separately authorized rollout may follow complete causal revalidation.
   A stale socket from a crashed publisher is not removed automatically: prove
   that no live writer owns it before an operator-authorized cleanup. A live second
   publisher, unknown node or changed inode must remain blocked. No cleanup runs here.

## Isolated validation

`tests/test_canonical_treatment_publisher_permission_boundary_v1.py` uses only
stdlib, so it can also run via `python -B -m unittest
tests.test_canonical_treatment_publisher_permission_boundary_v1 -v` in an isolated
Linux/Python container. Mount source read-only, disable network, keep the root
filesystem read-only and use a disposable `/tmp` tmpfs. The root fixture setup
needs only CHOWN/FOWNER/FSETID/DAC_OVERRIDE/SETUID/SETGID; FSETID is required to
reproduce mode `2770` rather than silently losing its setgid bit. Actual probes run as UID/GID
10001 or 1000 with no supplementary groups. No production mounts or sockets are
passed into the container. `libacl` must exist and ACL application must succeed;
the Linux ACL gate must not be claimed passed from Windows skips.

Tests cover explicit denial, source-only permissions, ancestor traversal,
restricted output/default ACLs, consumer readback, unrelated-directory denial,
output ACL revocation, independent secondary failures, stderr sanitization,
nonzero loop exit and blocked/stale/unreadable health reports. The known stale
consumer acceptance is tested and reported as a blocker, not hidden by mocks or
by weakening the legacy Control expiry/approval predicates; Treatment has its own
mandatory rejection test and cannot use that characterization as authorization.

`tests/test_canonical_treatment_consumer_fence_v1.py` adds the dedicated Treatment
integration: actual publisher UID 10001 and consumer UID 1000, a disposable volume
mounted once writable for fixtures and once read-only for consumption, and the
actual pinned Freqtrade package. Run with `PYTHONUSERBASE=/home/ftuser/.local`,
`F02_FREQTRADE_SOURCE_ROOT=/freqtrade`, `F02_REQUIRE_REAL_FREQTRADE=1`,
`TMPDIR=/fixture-rw` and `F02_TEST_READONLY_ROOT=/fixture-ro`. The source root avoids
the repository's `freqtrade/` namespace shadowing the installed editable package;
the required-real flag forbids fixture fallback in this isolated integration run.
Neither operational data nor a Docker socket is mounted, and network is disabled.

The decisive negative case denies both invalidation and blocked-report writes,
proves that the original `ok` report and approved payload remain byte-identical
and readable, then requires Treatment confirmation to reject via live authority.
Other cases cover before-cycle source permission revocation, memory revocation,
publisher shutdown, RPC timeout, spoofed socket-node ownership versus peer UID,
populate/confirm synchronization without sleeps, pending-entry cancellation,
read-only write denial and preservation of Control/protective callbacks.
