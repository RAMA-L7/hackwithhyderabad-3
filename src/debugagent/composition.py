"""The composition root: one Hindsight client -> one Coordinator -> one MemoryLane.

The P5 concurrency audit established that `hindsight_client` is not thread-safe - it caches a single
`aiohttp.ClientSession` bound to whichever event loop created it, while its synchronous bridge gives
each thread its own loop. The serial memory lane that mitigates this is owned by a `Coordinator`, which
means the lane is only as good as the Coordinator's reach: **one client must have exactly one
Coordinator, or the boundary is per-Coordinator while the hazard is per-client.**

That invariant cannot be enforced from inside the Coordinator, because a Coordinator cannot see the
others. It has to be enforced where objects are assembled. That is this module, and the only place in
the codebase that should construct a `Coordinator` for production use.

What it guarantees:

- `bind()` is idempotent for the same (port, coordinator) pair, so passing one Coordinator to many
  sessions is the normal, cheap case.
- `bind()` **raises** if a port's client is already bound to a *different* Coordinator. A second
  Coordinator over one client is therefore not discouraged, it is impossible through this path - which
  is stronger than a comment, and is what stops a future fan-out from quietly losing serialisation by
  building one Coordinator per worker.

Two properties of the registry are worth stating plainly, because both are deliberate:

- **The VALUE is held strongly, and that is the feature.** Sharing has to work without every host
  remembering to retain its own Runtime; a registry that dropped the Coordinator when the caller let
  go would make reuse accidental. The cost is one Coordinator retained per client, which is bounded by
  the number of clients.
- **The KEY is weak, but its entry cannot die on its own**, because the Coordinator references the
  port, which references the store, which references the client - the value keeps the key alive
  through a cycle. A `WeakKeyDictionary` alone would therefore leak. Discarding a runtime explicitly
  is the release path: `unbind(port)` or `Runtime.close()`. Teardown in tests uses it, and so does the
  CLI's exit path.

Deliberately not here: thread pools, join barriers, partial-failure aggregation, or any scheduling.
This module assembles and refuses. It does not run work concurrently.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass
from typing import Any

from debugagent.agents.coordinator import Coordinator, MemoryLane

__all__ = [
    "CompositionError",
    "RepositoryRuntime",
    "Runtime",
    "build_repository_runtime",
    "bind",
    "build_runtime",
    "bound_count",
    "coordinator_for",
    "identity_of",
    "unbind",
]


class CompositionError(RuntimeError):
    """Two Coordinators were bound to one client, or a binding was asked for and did not exist."""


# Identity -> Coordinator. The key is weak; the value is strong ON PURPOSE (see the module docstring:
# sharing must not depend on a host remembering to retain its own Runtime). The value keeps the key
# alive through Coordinator -> worker -> port -> store -> client, so `unbind` is the release path.
_BINDINGS: "weakref.WeakKeyDictionary[Any, Coordinator]" = weakref.WeakKeyDictionary()
_GUARD = threading.Lock()


def identity_of(port: Any) -> Any:
    """The object whose client is actually shared: the backend client if the port exposes one.

    Keying on the port alone would be a weaker invariant than the one that matters. Two `MemoryPort`
    objects wrapping ONE client - which is what "a worker per bank" or "a port per session" produces -
    are two keys, and serialisation would silently not apply. So the client is preferred wherever the
    port can name it, and the port is the fallback for ports that own no client (offline, fakes).
    """
    for attribute in ("client_identity", "memory_client"):
        candidate = getattr(port, attribute, None)
        if candidate is None:
            continue
        try:
            resolved = candidate() if callable(candidate) else candidate
        except Exception:  # noqa: BLE001 - a port that cannot name its client falls back to itself
            resolved = None
        if resolved is not None and _is_weak_referenceable(resolved):
            return resolved
    return port


def _is_weak_referenceable(value: Any) -> bool:
    try:
        weakref.ref(value)
    except TypeError:
        return False
    return True


def bind(port: Any, coordinator: Coordinator) -> Coordinator:
    """Associate `coordinator` with `port`'s client, and return it.

    Idempotent for a repeat of the same pairing. Raises `CompositionError` for a second, different
    Coordinator over the same client - the whole point of this module.
    """
    identity = identity_of(port)
    with _GUARD:
        existing = _BINDINGS.get(identity)
        if existing is None:
            _BINDINGS[identity] = coordinator
            return coordinator
        if existing is coordinator:
            return coordinator
        raise CompositionError(
            f"{type(coordinator).__name__} {id(coordinator)} cannot also be bound to a client already "
            f"served by {type(existing).__name__} {id(existing)}. One client, one Coordinator, one "
            f"MemoryLane: a second Coordinator would give the same non-thread-safe Hindsight client a "
            f"second lane, and nothing would be serialised. Reuse the existing Coordinator, or pass a "
            f"shared MemoryLane when constructing it."
        )


def coordinator_for(port: Any) -> Coordinator | None:
    """The Coordinator bound to this port's client, or `None`. Never constructs one."""
    with _GUARD:
        return _BINDINGS.get(identity_of(port))


def unbind(port: Any) -> Coordinator | None:
    """Drop the binding for this port's client. For teardown and tests, not for reassignment."""
    with _GUARD:
        return _BINDINGS.pop(identity_of(port), None)


def bound_count() -> int:
    """How many clients currently have a Coordinator. Diagnostics and leak checks in tests."""
    with _GUARD:
        return len(_BINDINGS)


@dataclass(frozen=True)
class RepositoryRuntime:
    """The P6 workers wired to a real repository, ready for `Coordinator.fan_out`.

    Holds the `RepositoryScope` because it is the security boundary: anything that hands these workers a
    path must go through it, and the object that enforces it should be visible to whoever composed them
    rather than buried inside an adapter.
    """

    scope: Any
    coordinator: Coordinator
    source_port: Any
    patch_port: Any

    @property
    def root(self) -> Any:
        return self.scope.root


def build_repository_runtime(root: Any, *, coordinator: Coordinator | None = None,
                             lane: MemoryLane | None = None) -> RepositoryRuntime:
    """Compose the Code/Log Verifier and Patch Generator against a real directory.

    This is the composition root for the P6 workers, mirroring `build_runtime` for the memory side: the
    adapters are constructed here, once, and the workers are attached to a Coordinator - the existing one
    when a caller already has a memory runtime, or a fresh one otherwise. The path boundary is a
    `RepositoryScope` built here too, so a caller cannot construct a worker pointing anywhere it likes
    without going through this function.

    `lane` is accepted for symmetry with `build_runtime` and ignored when a Coordinator already exists -
    a memory lane is meaningless for a repository-only runtime, and silently replacing a shared lane
    would break the one-Coordinator-per-client invariant.
    """
    from debugagent.agents.code_log_verifier import CODE_LOG_VERIFIER, CodeLogVerifier
    from debugagent.agents.patch_generator import PATCH_GENERATOR, PatchGenerator
    from debugagent.agents.sdc_analyzer_worker import SDC_ANALYZER, SdcAnalyzerWorker
    from debugagent.agents.source_files import FileSourcePort, RepoPatchSourcePort, RepositoryScope
    from debugagent.domains.vlsi.sdc_worker import analyze_sdc_text

    scope = RepositoryScope(root)
    source_port = FileSourcePort(scope)
    patch_port = RepoPatchSourcePort(scope)
    hub = coordinator if coordinator is not None else Coordinator(lane=lane)
    hub.register(CODE_LOG_VERIFIER, CodeLogVerifier(source_port))
    hub.register(PATCH_GENERATOR, PatchGenerator(patch_port))
    # The one place in the wiring that names a domain. The worker takes `analyze` as a callable and
    # imports no domain module, so `agents/` stays domain-blind and VLSI-2's STA analysis arrives as a
    # second callable here rather than a fifth roster entry. This is the same composition root that
    # already builds the ports and the two P6 workers, for the same reason: a caller cannot construct a
    # worker pointing anywhere it likes without going through this function.
    hub.register(SDC_ANALYZER, SdcAnalyzerWorker(source_port, analyze=analyze_sdc_text))
    return RepositoryRuntime(scope=scope, coordinator=hub, source_port=source_port,
                             patch_port=patch_port)


@dataclass(frozen=True)
class Runtime:
    """The assembled objects one client needs: its port, its Coordinator, and so its lane."""

    port: Any
    coordinator: Coordinator

    @property
    def lane(self) -> MemoryLane:
        return self.coordinator.lane

    def close(self) -> None:
        from debugagent.pipeline.memory_adapter import close_port

        unbind(self.port)
        close_port(self.port)


def build_runtime(port: Any, *, coordinator: Coordinator | None = None,
                  memory_specialist: Any = None, lane: MemoryLane | None = None) -> Runtime:
    """Compose the one Coordinator for `port`, or return the one already composed.

    The binding always wins. If this client already has a Coordinator, that is the one every session
    must share, and a `memory_specialist` passed alongside is simply a construction hint that is no
    longer needed - reusing the bound Coordinator is the whole point, and refusing here would break
    the second run of any host that composes per invocation (the CLI included).

    What IS refused is passing a *different, explicit* Coordinator for a client that already has one:
    that is the silent-serialisation-loss this module exists to prevent, and it routes through `bind()`
    so the error is the single canonical one.

    `memory_specialist` is the ergonomic path - a host knows its port, not necessarily its worker - and
    builds through `Coordinator.with_memory_specialist`, so there remains one construction site.
    """
    existing = coordinator_for(port)
    if existing is not None:
        if coordinator is not None and coordinator is not existing:
            bind(port, coordinator)  # raises; kept separate for the canonical message
        return Runtime(port=port, coordinator=existing)

    if coordinator is None:
        if memory_specialist is None:
            raise CompositionError(
                "build_runtime needs either a coordinator or a memory_specialist to build one from")
        coordinator = Coordinator.with_memory_specialist(memory_specialist, lane=lane)
    elif memory_specialist is not None:
        raise CompositionError("pass `coordinator` or `memory_specialist`, not both")

    return Runtime(port=port, coordinator=bind(port, coordinator))
