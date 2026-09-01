# Workflows

| Workflow | Trigger | What it guards |
|---|---|---|
| `ci.yml` | push, pull request | lint, offline tests, config integrity, deterministic fixture build, two consecutive releases, diagram drift |

`ci.yml` never touches the network. Nothing here does: acquiring payloads from
the publishers is managed internally and is not part of this repository, so no
workflow can be broken by a publisher outage, and none of them can publish a
release.
