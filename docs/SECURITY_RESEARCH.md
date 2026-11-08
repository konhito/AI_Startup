# Authorized security-research workspace

Anthropic's September 2026 GLM-5.3 assessment demonstrates a useful research
pattern: models were evaluated on known offline targets, isolated benchmark
environments, and expert-led investigations; discovered vulnerabilities were
reported to maintainers. It also warns that GLM-5.3 has unusually strong exploit
development capability and safeguards that can be bypassed. Those are distinct
findings: strong research capability does not remove the need for target ownership,
isolation, auditability, or coordinated disclosure.

Source: https://www.anthropic.com/research/glm-5-3-and-the-spread-of-advanced-cyber-capabilities

## Product boundary

The intended feature is an isolated workspace for a researcher to inspect code and
systems they own or are explicitly authorized to test. The platform should collect
the engagement scope before tools are enabled: target identifiers, permitted test
types, time window, contact, and the researcher's attestation. The model may propose
and execute steps only inside the provisioned lab. Arbitrary public targets and the
gateway/control-plane network must remain unreachable from the execution sandbox.

## Execution design

Each research job receives a short-lived sandbox with:

- a read-only source or target snapshot plus a writable scratch volume;
- no cloud instance metadata, customer API keys, billing credentials, or gateway DB;
- default-deny networking, with an explicit per-engagement allowlist when required;
- CPU, memory, process, disk, output, and wall-clock limits;
- a non-root user, dropped Linux capabilities, seccomp/AppArmor, and no host sockets;
- a complete command/tool transcript, artifacts, hashes, timestamps, and model route;
- immediate termination plus automatic expiry and artifact-retention controls.

The API gateway should enqueue signed job specifications to a separate runner. It
must never execute model-generated commands itself. The runner returns structured
observations; the model does not receive raw infrastructure credentials. High-risk
actions need an explicit human checkpoint tied to the approved scope.

## Finding workflow

A finding should contain a reproducible lab-only test, affected component/version,
impact, confidence, evidence, remediation, and disclosure status. The default export
must redact secrets and live exploit payloads. Support a maintainer-ready report and
an embargoed disclosure timeline. Keep benchmark results and marketing claims tied
to the exact checkpoint, tool harness, token budget, hardware, and success criteria.

## Before implementation

Choose the isolation layer (dedicated microVMs are preferable for hostile code),
define the authorization record and retention policy, and build a deliberately
vulnerable local target suite. Validate containment with escape, network-egress,
resource-exhaustion, secret-access, and cross-tenant tests before connecting a real
GLM checkpoint. Do not treat a prompt-level statement such as "I am authorized" as
the only control.
