# Workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push, pull request | lint, offline tests (including the `site/` JavaScript under node), config integrity, deterministic fixture build, two consecutive releases, diagram drift |
| `pages.yml` | push to `main` touching `site/**`, or manual | publishes the static map in `site/` to GitHub Pages |

Neither touches a publisher. Acquiring payloads is managed internally and is not
part of this repository, so no workflow can be broken by a publisher outage, and
none of them builds or publishes a data release.

`pages.yml` deploys `site/` as committed — it does not rebuild the map's data,
because that data is derived from boundary files that are not in the repository
(`docs/POLICY.md` §3.2). Regenerate it locally with `tools/build_mesh_table.py`
and `tools/build_site_geo.py`, then commit `site/data/`. The first deployment
needs Settings → Pages → Source set to "GitHub Actions".
