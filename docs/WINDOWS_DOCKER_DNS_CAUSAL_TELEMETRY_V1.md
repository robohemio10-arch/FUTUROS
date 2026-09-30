# Windows/Docker DNS Causal Telemetry V1

This is a host-side, non-authoritative observation tool. It resolves only
`fapi.binance.com`; it does not send an HTTP request or use exchange credentials.
It neither changes Paper/V3 behavior nor repairs a network outage.

## Execution

Run manually on Windows with an exact container name obtained from the current
deployment configuration. There is no built-in historic container name, scheduler,
service installation, or automatic retry.

```powershell
python scripts/capture_windows_docker_dns_causal_telemetry_v1.py --once --container <exact-name> --json
```

`--interval-seconds N` enables foreground sampling until Ctrl+C. Each external
probe has an independent timeout (default 8 seconds; maximum 30). Omitting the
container records Docker as unavailable; it never guesses a container.
Output selection is `--output-dir` first, then
`FUTUROS_DNS_CAUSAL_TELEMETRY_OUTPUT_DIR`, then
`Path.home() / "FUTUROS_LOCAL_CHECKPOINTS" / "DNS_CAUSAL_TELEMETRY_V1"`.
The selected directory must be absolute and outside a Git worktree. An invalid
configured path blocks the run; it does not fall back to another destination.
For this project's Windows deployment, use the approved external path explicitly:

```powershell
python scripts/capture_windows_docker_dns_causal_telemetry_v1.py --once --container <exact-name> --output-dir E:\FUTUROS_LOCAL_CHECKPOINTS\DNS_CAUSAL_TELEMETRY_V1 --json
```

## Evidence

Each sample is appended and fsynced to `dns_causal_telemetry_v1.jsonl` under a
process lock on that file. `dns_causal_telemetry_v1.latest.json` is replaced atomically.
The sample contains `schema_version`, UTC sample time, target, Windows
`getaddrinfo`, active default-route adapters, network profile category and ID hash, configured
DNS servers, default routes, per-adapter DNS-config hashes and a network-state SHA-256. Each configured server
gets one explicit A lookup via `Resolve-DnsName -Server`. It also records Docker
engine/container availability, exact container identity, network names,
`/etc/resolv.conf` SHA-256 and sanitized lines, and an in-container `getaddrinfo`.
Recent DNS Client, System DNS, and NetworkProfile events are recorded by
provider, ID, level and UTC time only, without raw messages or network names.

Missing commands, disabled event channels, permission errors and timeouts are
`unavailable`/`timeout`, not evidence of DNS failure. Output never includes
environment variables, proxy credentials, secrets or raw event messages.
The tool does not elevate privileges or modify network state.

## Classification

`HOST_AND_DIRECT_DNS_OK`, `HOST_RESOLUTION_FAILED_DIRECT_DNS_OK`,
`HOST_AND_DIRECT_DNS_FAILED`, `HOST_OK_CONTAINER_FAILED`,
`HOST_AND_CONTAINER_FAILED`, `CONTAINER_OK`, and `INSUFFICIENT_EVIDENCE` are
sample-level observations, never definitive root-cause labels. Direct DNS
failure requires every configured server probe to fail; success requires every
configured server probe to succeed. Invalid configured server literals are
redacted and counted as `unavailable`, so partial evidence cannot appear complete.
Correlate consecutive samples with event-time logs before assigning any
infrastructure cause. Do not infer the historical resolver from current state.

`source_reads_only=true` describes the probes; only external evidence is
persisted. `writes_runtime=false` and `classification_is_root_cause=false`
remain explicit. No write under `data/`, no replay/backfill, no Paper or V3
runtime authority.
