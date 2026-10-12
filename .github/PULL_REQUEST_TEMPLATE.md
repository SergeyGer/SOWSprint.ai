<!--
Thanks for contributing. The checklist below is short on purpose: each item exists
because its absence has caused a real problem in this repository.
-->

## What this changes

<!-- One paragraph. What behaviour is different after this, from a user's point of view? -->

## Why

<!-- The problem being solved. Link an issue if there is one. -->

---

## Checklist

### Correctness

- [ ] `pytest tests -o addopts= -q` passes — the suite needs no network and no credentials
- [ ] `ruff check src tests app.py scripts` is clean
- [ ] A behaviour change has a test that fails without the fix

### Documentation

- [ ] **If behaviour changed, the matching wiki page under `docs/wiki/` was updated**
      — see [CONTRIBUTING.md](../CONTRIBUTING.md#keeping-the-wiki-honest) for which page
      owns which subsystem
- [ ] Any new screenshot or diagram is committed, not just present locally
      — `python scripts/check_docs.py` verifies this, because the first version of the
      README referenced images that were gitignored
- [ ] A new wiki page is linked from `docs/wiki/_Sidebar.md`
      — `python scripts/check_wiki.py` verifies this

### If this touches something that has bitten before

- [ ] **No credential or credential fragment is logged.** Printing the last four
      characters of an API key counts
- [ ] **A new `.env` setting appears in `.env.example`**, and empty means unset rather
      than empty-string — `base_url=""` broke every OpenAI call for a week
- [ ] **Digests, tokens and secrets contain no `$`** if they live in `.env`, because
      Docker Compose interpolates it and the failure is silent
- [ ] **New container paths are created, not copied**, if the source directory could be
      empty — git does not track empty directories and the build breaks on a fresh clone

### If this changes dependencies

- [ ] The suite was run against the **newly resolved** versions, not the ones already
      installed. A widened range changes what a fresh install gets, and CI is the first
      place that happens otherwise

---

<sub>Reviewers: a change to `docs/wiki/` is published to the GitHub wiki on merge, so it
is worth reading in the diff rather than after the fact.</sub>
