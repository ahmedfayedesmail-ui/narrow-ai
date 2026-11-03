# Contributing

Thanks for helping make AI-assisted work safer and more dependable.

## Ground rules

- Every safety-related change needs automated tests.
- Do not weaken a fail-closed default without updating the safety model and threat model.
- Do not introduce network calls, telemetry, source uploads, or mandatory API keys without an explicit public design decision.
- Do not add provider-specific permission bypasses.
- Prefer standard-library dependencies in the core.
- Keep provider integrations behind small, testable adapters.

## Development

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Pull requests

Include:

- The user problem and expected behavior.
- Safety impact and new trust assumptions.
- Tests, including a failure-path test.
- Documentation updates where behavior, commands, or guarantees changed.

Never include real credentials, private repository contents, or customer data in tests or screenshots.
