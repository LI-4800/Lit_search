# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository scaffold: `pyproject.toml` (Python 3.14+), `LICENSE` (Apache-2.0),
  `NOTICE`, `README.md`, `.gitignore`, directory layout for `core/`, `adapters/mpco/`,
  `adapters/pico/`, `cli/`, `ui/`, `tests/`, `docs/`.
- `core/adapter_base.py` — Adapter abstract base class and supporting protocols.
- `core/persistence.py` — YAML-default write, dual-format (YAML + legacy JSON) read.
- Test scaffolding for `core/`.

[Unreleased]: https://github.com/LI-4800/Lit_search/compare/HEAD...HEAD
