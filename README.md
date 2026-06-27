# RING2

**Shared-Core literature search and appraisal tool for medical-device regulatory submissions.**

RING2 implements a reproducible, audit-traceable literature-search workflow for two
domain-specific evidence-gathering tasks:

- **Clinical Evaluation Reports (CERs)** under MEDDEV 2.7/1 Rev. 4 and EU MDR — the PICO adapter
- **Material-evidence justifications** under EU 722/2012 and analogous regulations — the MPCO adapter

The shared core (PubMed connector, deduplication, screening pipeline, study-design
classification, PRISMA 2020 generator, batch persistence, audit trail) is regulation-
and domain-agnostic. Adapters plug in to tailor the workflow per project.

## Status

Pre-alpha. Greenfield development; the legacy Ring-2 codebase (active OsteoGen CER
workflow) is **not** being migrated in this branch — it remains independent until the
MPCO adapter is stable and a regression test suite exists for PICO migration.

See `docs/ARCHITECTURE.md` for the full design.

## Regulatory anchors

The workflow is grounded in (verbatim references — never paraphrased):

- **Regulation (EU) 2017/745 (MDR)** — Annex I, Annex XIV
- **MEDDEV 2.7/1 Rev. 4** — Appendix A4 (Sources), A5 (Protocol), A6 (Appraisal exclusion catalog)
- **MDCG 2020-6** — Confirms MEDDEV §8, §9, §10, A3–A6 are relevant under MDR
- **PRISMA 2020** — BMJ 2021;372:n71
- **EU 722/2012 + EMA/410/01 Rev. 3 + Decision 2007/453/EC** — Animal-tissue framework, activated conditionally
- Citation format: **ICMJE / Vancouver with PMID + DOI** (project convention)

## Quick start

```bash
# Requires Python 3.14+
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests
ruff format --check src tests

# Type-check
mypy src
```

## Repository layout

```
src/ring2/
  core/                    # regulation- and domain-agnostic engine
    adapter_base.py        # Adapter ABC
    persistence.py         # YAML-default write, YAML+JSON read
    (pubmed_client, search, screening, study_classifier, prisma, audit, session — forthcoming)
  adapters/
    mpco/                  # Material / Property / Comparator / Outcome — for 722/2012 and similar
    pico/                  # Population / Intervention / Comparator / Outcome — for CER workflows
  cli/                     # command-line entry points
  ui/                      # Flask UI (strategy builder, batch viewer)
tests/
docs/
```

## License

Apache License, Version 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
