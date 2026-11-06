# Management DB Connectivity Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent profile/tests work. Git publication requires explicit approval of the final diff; this plan is not apply authorization.

**Goal:** Prepare the management API's moimroom DB route and canonical catalog consistently for [#98](https://github.com/2026-Softbank-Hackathon-Charlie/infra/issues/98).

**Architecture:** Reuse ADR0048's manual management Tailscale operator/proxy chart. The API uses local `db-gke`/`db-eks` Service DNS; only the proxies use the observed workload ingress FQDNs.

**Tech Stack:** Existing Helm chart, YAML/JSON configuration and offline Python unittest contracts. No new dependency or chart version.

## Global Constraints

- No AWS/GCP deployment, Terraform apply, Argo sync, RBAC grant, DB execution or strict apply.
- Keep the management profile disabled and outside GitOps registration.
- Keep existing CNPG identities, password references, buckets, onprem settings and system policies.
- Extend the DSN allowlist only with the DB network namespace suffix.
- Separate code readiness, merge-triggered API rollout and separately approved infrastructure application.

## Feature Commits

1. Management profile and its render regression: disabled emits 0 objects; explicit enable emits only ProxyClass 1, Services 2 and NetworkPolicies 5.
2. Canonical catalog and API allowlist with their regression checks: local Service addresses agree and existing workload identity/storage contracts remain intact.
3. Existing ADR0048 explanation, rollout runbook, this plan and README inventory. No new architecture decision is needed because the selected design is unchanged.

Tests belong to the feature commit they verify. Stage the profile test hunk separately from the catalog/allowlist test hunk.

## Verification

- [ ] Observe profile tests fail before adding the profile and pass afterward.
- [ ] Observe catalog/allowlist regressions fail on the old values and pass with the new ones.
- [ ] Run the affected asset and DB chart tests, targeted Helm lint/render and platform allowlist rendering.
- [ ] Run inventory and diff checks; obtain one independent review with the recorded test evidence.
- [ ] Request approval for the three described commits and `codex/moimroom-db-connectivity` push, then create the requested Draft PR and observe CI.

## Deployment Boundary

The existing platform GitOps app can restart the API after the allowlist change is merged. Approve merge timing against the runbook: operator/OAuth and management proxy readiness, actual API SQL connectivity, canonical SSM update, API allowlist rollout/restart, then a new Plan. Proxy installation remains a manual operation. Actual DNS/TCP/TLS/SQL and migration verification are pending until that separately approved rollout.
