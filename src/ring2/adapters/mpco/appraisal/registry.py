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
"""Appraisal-lens registry — :func:`register_lens` / :func:`get_lens` / :func:`names`.

Mirrors the :mod:`ring2.core.adapter_base` adapter registry pattern,
restricted to :class:`AppraisalLens` subclasses. Lenses register
themselves under their :attr:`AppraisalLens.name`; the project-YAML
loader (Stufe 1.9+) resolves the configured lens name to a class and
instantiates it.

Auto-registration:
    Stub lens modules in this subpackage (``rob2``, ``grade``,
    ``robins_i``, ``glp_oecd``, ``arrive``, ``astm_iso_material``,
    ``care_caseseries``, ``registry_authoritativeness``) and the
    fully-implemented ``meddev_a6`` (Inkrement 7+) decorate their
    class with :func:`register_lens`. Importing the
    :mod:`ring2.adapters.mpco.appraisal` subpackage triggers all
    module-level decorators and populates the registry.
"""

from __future__ import annotations

from ring2.adapters.mpco.appraisal.base import AppraisalLens

__all__ = [
    "clear",
    "get_lens",
    "names",
    "register_lens",
]


# Module-private registry. Keyed by ``AppraisalLens.name``.
_REGISTRY: dict[str, type[AppraisalLens]] = {}


def register_lens(lens_cls: type[AppraisalLens]) -> type[AppraisalLens]:
    """Register an :class:`AppraisalLens` subclass under its :attr:`name`.

    Usable as a decorator::

        @register_lens
        class Rob2Lens(AppraisalLens):
            name = "rob2"
            ...

    Idempotent: re-registering the same class under its own name is a
    no-op. Re-registering a *different* class under an existing name
    raises — preventing silent override.

    Raises:
        ValueError: if ``name`` is empty or already registered to a
            different class.
    """
    name = lens_cls.name
    if not name:
        raise ValueError(
            f"AppraisalLens {lens_cls.__name__} has an empty .name; set it before registering"
        )
    if name in _REGISTRY:
        existing = _REGISTRY[name]
        if existing is lens_cls:
            return lens_cls  # idempotent
        raise ValueError(
            f"AppraisalLens name {name!r} already registered to "
            f"{existing.__module__}.{existing.__name__}; "
            f"cannot re-register {lens_cls.__module__}.{lens_cls.__name__}"
        )
    _REGISTRY[name] = lens_cls
    return lens_cls


def get_lens(name: str) -> type[AppraisalLens]:
    """Return the lens class registered under ``name``.

    Raises:
        KeyError: if no lens is registered under that name. The error
            message lists currently registered names to aid debugging.
    """
    try:
        return _REGISTRY[name]
    except KeyError as e:
        available = sorted(_REGISTRY)
        raise KeyError(
            f"No AppraisalLens registered as {name!r}. Available: {available or '(none)'}"
        ) from e


def names() -> tuple[str, ...]:
    """Return all currently registered lens names, sorted lexicographically."""
    return tuple(sorted(_REGISTRY))


def clear() -> None:
    """Empty the registry — intended for test isolation only.

    Tests that build their own lens fixtures should call this in a
    fixture's setup/teardown to avoid bleed between cases. Production
    code must never call it.
    """
    _REGISTRY.clear()
