# Contributing

Use a virtual environment, install `requirements.txt`, then run `python -m pytest -q` and `python cad_doctor.py --render`. Ordinary tests must be offline and must not require API credentials. Add mocked wire-format tests for provider changes and preserve existing budget/audit behavior.

Do not commit `.env`, tokens, local databases, raw CAD assets or generated outputs. Document protocol-tested vs live-tested support separately. Do not revise historical responses or convert predictions into ground truth. New evaluation claims require explicit denominators, data provenance and human-label status.

Use new experiment roots for model/provider comparisons. Never restart or mutate the sealed v0.1.0 release. Changes to taxonomy, prompt or acceptance policy need versioned experimental configurations and must not be mixed silently into historical measurements.
