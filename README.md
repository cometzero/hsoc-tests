# Apollo FVP Validation

This repository is intended to be used as the `hsoc-stack/tests` submodule of
the Arm Auto Solutions workspace.

The validation model is split into four categories:

- `basic`: one Apollo FVP boot, default 300 second timeout, UART marker checks,
  fatal log scanning, and FVP cleanup.
- `functional`: current Apollo feature checks based on OEQA and sw-ref-stack
  pytest suites.
- `extended`: long-running, image-mutating, or conformance validation such as
  FWU, UEFI secure boot, trusted services, crypto, CPU frequency, and CPU idle.
- `stress`: repeated reset, reboot, poweroff, PFDI, HIPC, CPU power/perf, and
  long-soak validation.

Run commands from the workspace root:

```bash
PYTHONPATH=hsoc-stack/tests python3 -m apollo_validation.cli list \
  --profile apollo-fvp-cfg2-baremetal-demo --format json

PYTHONPATH=hsoc-stack/tests python3 -m apollo_validation.cli context \
  --root . --build-dir build --out build/tests/context.json

PYTHONPATH=hsoc-stack/tests python3 -m apollo_validation.cli run \
  --category basic --root . --build-dir build --timeout 300 \
  --out-dir build/tests/basic-smoke

PYTHONPATH=hsoc-stack/tests python3 -m apollo_validation.cli run \
  --category functional --dry-run --root . --build-dir build \
  --out-dir build/tests/functional-dry-run
```

The top-level `run_test.sh` wrapper delegates through this submodule while the
legacy runner remains available for compatibility.
