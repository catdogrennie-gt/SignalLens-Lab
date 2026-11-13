# Curated public entrance implementation plan

> Use superpowers:executing-plans in the existing worktree. Do not delegate implementation. Request focused independent review before public publication.

**Goal:** Publish only the verified L1 connection-waterline scene without replacing its workload owner.

**Architecture:** Stateless FastAPI facade, private allowlist and acceptance record, existing controller and B ordinary pipeline unchanged.

**Tech stack:** Python, FastAPI/httpx, existing local-view JavaScript, pytest, existing sealed frontend/gateway deployment.

**Spec:** [Design](../specs/2026-11-13-curated-public-entrance-design.md).

## Task: Curated facade and checked scene label

- [ ] Write failing `tests/test_curated_site.py` covering approval filtering, unknown scene/target denial before forwarding, strict schema and origin, required concurrency, revoked-approval OFF and failure responses.
- [ ] Add `lab/curated_site.py`: fixed upstream requests only, existing controller never started. Keep normal public payloads and current/old report distinction unchanged.
- [ ] Extend the local-view verification label with `analysis_verified`, retaining the old partial-analysis wording.
- [ ] Run focused and full Lab checks, inspect source/forwarding risks through independent review, publish exact reviewed public overlays with secret/link scan.
- [ ] Seal facade/frontend, verify live OFF/current single owner and acceptance proof, start only stateless facade; inspect HTTP/browser without switching ON.
- [ ] Announce one minute before existing gateway switch; verify public B unchanged, A one-scene view, no unauthorized forwarding, rollback on failure. Record actual layer results in status/manual/checkpoint.

## Constraints

No second controller, new fault, manual AI dispatch, changed limits/quotas, runtime source overwrite or private ancestry publication. Unpassed scenes stay hidden. ON requires current explicit acceptance; OFF remains available for the allowlisted current scene after revocation. Failed upstream reads do not become empty healthy data.
