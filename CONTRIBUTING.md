# Contributing to Sympraxi

Thanks for helping. Sympraxi holds student records, so a few rules are stricter than in most projects.

## Sign your commits (DCO)

Every commit must carry a `Signed-off-by` line certifying the [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change, or otherwise have the right to submit it under the project's license.

```bash
git commit -s -m "Describe the change"
```

CI rejects pull requests with unsigned commits. We use the DCO instead of a copyright assignment on purpose: copyright stays with each contributor, so no single party can relicense the project.

## Licenses

- Code: AGPL-3.0-or-later (see `LICENSE`).
- Topic vocabulary and catalog metadata in `catalog/`: CC0.
- Don't paste text from Purdue OWL, WCONLINE, or any other source that isn't openly licensed. That includes WCONLINE's help text and screens: Sympraxi is a clean-room build.

## Development setup

See [docs/development.md](docs/development.md).

## Before you open a pull request

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy sympraxi
uv run pytest
uv run pytest -m e2e   # needs `npm run build` and `uv run playwright install chromium`
```

## Rules for code that touches student data

1. **Every permission decision goes through `sympraxi.core.policy`.** Don't check roles inline in a view. Add an `Action`, grant it to roles in the policy table, and add its expected results to `tests/test_policy_matrix.py`.
2. **Every new URL is classified** in `tests/test_url_inventory.py` as public, login-required, or a specific policy action. The test fails until you do this, on purpose.
3. **Querysets are scoped.** Views fetch objects through the policy helpers (`get_for_action`, `centers_for`), never with a bare `Model.objects.get(pk=...)` on a value from the URL.
4. **Reads and changes of sensitive records are audited** with `sympraxi.privacy.audit.record()`.
5. **No student data in logs, error messages, or URLs** beyond opaque IDs.
6. **Accessibility is a requirement, not a polish step.** New pages need an entry in the axe test (`tests/e2e/test_accessibility.py`) and must work by keyboard alone.
