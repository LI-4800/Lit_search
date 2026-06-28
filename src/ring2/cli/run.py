# Copyright 2026 lets-innovate.ch (Michael Hug)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CLI ``ring2 run <project.yaml>`` — drives the orchestrator end-to-end.

Stufe 1.9a Inkrement 4. The CLI is intentionally thin:

* parses arguments,
* invokes :func:`ring2.core.orchestrator.run` with the default null
  callers (real MCP / screening callers will be wired in Stufe 1.10+),
* prints the resulting report path,
* maps known exceptions to non-zero exit codes with a short message.

The CLI does not (yet) inject real callers — a project YAML with a
``search:`` block under 1.9a will fail at the first MCP call with the
:class:`NullMCPCaller`'s loud-failure message. This is by design:
the CLI surface exists for 1.9a so that the end-to-end pipeline can
be exercised by tests (which inject fakes via the library API), and
the production caller wiring is a separate Stufe-1.10+ task.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ring2.core.orchestrator import OrchestratorError
from ring2.core.orchestrator import run as orchestrator_run

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser with the ``run`` sub-command."""
    parser = argparse.ArgumentParser(
        prog="ring2",
        description="RING2 — systematic literature search and evidence appraisal.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run",
        help="Run the end-to-end pipeline for a project YAML.",
        description=(
            "Load a project YAML, run search (if configured), screening, "
            "appraisal, and write the markdown report to the project's "
            "output_dir."
        ),
    )
    run_p.add_argument(
        "project_yaml",
        type=Path,
        help="Path to the project YAML file (see ring2.core.project_config).",
    )
    return run_p.set_defaults(func=_run_cmd) or parser


def _run_cmd(args: argparse.Namespace) -> int:
    """Execute the ``run`` sub-command. Returns the process exit code."""
    try:
        result = orchestrator_run(args.project_yaml)
    except FileNotFoundError as e:
        print(f"ring2: file not found: {e}", file=sys.stderr)
        return 2
    except OrchestratorError as e:
        print(f"ring2: orchestrator error: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ring2: unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"Report written to: {result.report_path}")
    print(f"Screening decisions: {len(result.screening_decisions)}")
    print(f"Eligible records: {result.eligible_records_count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point — parses ``argv`` (defaults to ``sys.argv[1:]``) and dispatches."""
    parser = build_parser()
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
