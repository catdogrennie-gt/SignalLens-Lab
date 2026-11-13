# Curated public demonstration entrance

## Intent and authority

The existing user-approved design exposes only scenes whose reproduction, natural discovery, interpretation, recovery and display have passed. CAPACITY-07 now satisfies the bounded L1 capacity-risk scope. Other candidates remain internal. The public switch must continue controlling the existing single owner, not a second controller or a manual AI dispatch.

## Approaches

1. Reconfigure/restart the controller: fewer hops, but unnecessarily interrupts ownership and still requires catalog/control filtering.
2. Remove the gateway write block only: rejected because it exposes internal candidates.
3. A stateless, fail-closed curated facade over the existing controller: selected. It serves the existing page and forwards only approved scene reads and switches. No workload thread, owner, model tool or database credential is introduced.

## Scope and boundaries

- Private configuration pins one loopback controller origin, public origins, an explicit scene-to-target allowlist and an acceptance record path. No caller chooses an upstream URL.
- Acceptance must explicitly certify analysis as well as the legacy discovery/recovery status. Missing/corrupt records expose no scenes and reject ON. OFF for the allowlisted current scene remains possible after acceptance revocation.
- State/catalog and target reads expose only the allowlist; unknown targets/scenes are rejected before an upstream write. Active nonpublic work is not taken over.
- Strict public switch accepts only boolean on, scene, expected revision and generation; revision and generation are required. Owner/TTL/validation fields are not public inputs. Existing idempotence, ownership and workload resource guards remain in the original controller.
- Same-origin write validation and no-store/security headers apply. Upstream failures are explicit unavailable responses, never synthetic healthy values.
- The catalog labels fully checked analysis separately from historical partial analysis. The acceptance explanation must say this is configured capacity-risk validation, not unique Zabbix detection or proven root cause.
- Publish an immutable facade/frontend candidate, health/read-only/no-fault checks, independent review, one-minute notice, existing gateway switch and rollback. The facade never starts a controller.

## Acceptance

Focused tests prove fail-closed records, exact allowlist, origin/schema protection, no alternate scene write, concurrency conflicts, OFF after revocation, upstream failure, and zero AI dispatch. Actual public checks and browser rendering precede public documentation updates. No new fault is needed to revalidate the already-passed scene's historical evidence.
