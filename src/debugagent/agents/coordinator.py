"""The Coordinator: the hub side of the P1 delegation seam.

P1 built the seam (TaskSpec, SubAgentResult, the closed registry, authorisation) and P4 built the
first worker. Neither had anything that *dispatched*: a `MemorySpecialist` existed but was only
reachable if a caller reached into the pipeline and called it by hand. This module is that missing
hub, and it is deliberately small.

What it does, in order, for every delegation:

1. `authorize(spec)` — the same seam call every other delegation uses. **Before** the worker is
   resolved or invoked, so an unauthorised task never reaches worker code.
2. resolve the registered worker for `spec.agent`;
3. `worker.execute(spec)`, serially, one task at a time.

What it deliberately does not do:

- **No fan-out, no concurrency, no scheduler, no join barrier.** One task, dispatched, collected. P5's
  parallel phase is a different mechanism and is not started here; the serial shape is what makes the
  current run order deterministic and the trace reproducible.
- **No redesign of the P1 types.** `TaskSpec` in, `SubAgentResult` out. The Coordinator adds no field
  and reinterprets no field.
- **No evidence, no proposals, no decisions.** It never imports the Phase 1 pipeline, so there is no
  path from here into `build_evidence` or `verify`. What a worker returns is an untrusted report.

One decision is load-bearing and easy to get wrong. A worker that *raises* is turned into a failed
`SubAgentResult`, because a worker fault must not take the engineer's session down. But an
**authorisation failure propagates** and is never converted into a failure result: a rejected task
reported as `unavailable` would be indistinguishable from a bank outage, which is the ADR's "failure
is never nothing found" rule applied backwards. Silence about a refusal is the one failure this module
will not commit.
"""

from __future__ import annotations

import threading
from typing import Mapping, Protocol, Sequence

from debugagent.agents.registry import (
    AuthorizationError,
    RegistrationError,
    authorize,
    get_agent,
)
from debugagent.agents.tasks import SubAgentResult, TaskSpec

#: The tools that reach the Hindsight bank. A task requesting any of them can enter the client, so it
#: must be serialised. Derived from the seam's own vocabulary rather than hardcoded per worker, so a
#: memory task is recognised whichever agent it is routed to.
MEMORY_TOOLS = frozenset({"hindsight_recall", "hindsight_get_facts"})

#: Bound on the join barrier. Generous, because a healthy fan-out satisfies it in microseconds; it
#: exists so that a participant which never arrives degrades to `joined=False` instead of hanging a
#: caller that is already holding every result.
DEFAULT_JOIN_TIMEOUT = 30.0


class MemoryLane:
    """The one serial lane every Hindsight-touching operation must pass through.

    `hindsight_client` is not thread-safe, and the P5 concurrency audit established exactly why: its
    synchronous bridge gives each thread its OWN event loop, while the client lazily creates and
    caches a single `aiohttp.ClientSession` that is bound to the loop that created it. A second thread
    reusing that session fails with `RuntimeError: Timeout context manager should be used inside a
    task`. This applies to reads as much as writes - `recall` and `retain` share one client object.

    So the safe architecture is not a better lock, it is **one lane**. Every memory operation -
    delegated or direct - takes it, and a client is only ever touched by one thread at a time.

    Why this is a `Coordinator` concern and not a store concern: the store's `_retain_lock` is keyed on
    the *ledger path* and covers only `retain`. Widening it to a client-global lock is a larger change
    to `hindsight_store.py` than this step authorises, and it would still miss the pipeline's own
    direct `recall()`, which never touches the store's lock at all. The Coordinator already owns every
    dispatched worker, so it is the one place that can see all of them.

    Reentrant, so a caller already holding the lane (the flow wraps its own `recall`/`retain` in it)
    can still dispatch through the Coordinator without deadlocking.

    Deliberately NOT here: fan-out, a join barrier, partial-failure aggregation, or any concurrency
    among NON-memory workers. Those are P5's next step, and nothing in this class presumes them.
    """

    __slots__ = ("_lock", "entered", "name")

    def __init__(self, *, name: str = "memory"):
        self._lock = threading.RLock()
        self.entered = 0
        self.name = name

    def __enter__(self) -> "MemoryLane":
        self._lock.acquire()
        self.entered += 1
        return self

    def __exit__(self, *exc_info) -> bool:
        self._lock.release()
        return False

    def call(self, function, *args, **kwargs):
        """Run `function` inside the lane and return its result."""
        with self:
            return function(*args, **kwargs)

    def is_free(self) -> bool:
        """Whether the lane can be taken right now without blocking. Diagnostics and tests."""
        if self._lock.acquire(blocking=False):
            self._lock.release()
            return True
        return False


class Worker(Protocol):
    """What the Coordinator needs from a worker. `MemorySpecialist` satisfies it as-is.

    Deliberately declares NO capability of its own. Whether a task reaches the Hindsight client is read
    from the registered `AgentDefinition` (`client_access`) and the task's tools, never from the worker
    object - a worker that could declare itself non-client would be able to opt out of the shared lane by
    writing one attribute, and nothing in authorisation would notice.
    """

    def execute(self, spec: TaskSpec) -> SubAgentResult: ...


class TaskOutcome:
    """What happened to ONE task in a fan-out, kept whole.

    Every task gets one of these, in input order, whether it succeeded, failed or was refused. Nothing
    is merged away: a failed task in a batch of successes is still individually visible, because
    "three of four succeeded" is exactly the information a reader needs and an aggregate count alone
    would hide.

    `result` is None only when the task never ran - a refusal, or a worker that died. `error` carries
    the exception so a refusal stays a refusal instead of being flattened into a generic failure.
    """

    __slots__ = ("index", "spec", "result", "error")

    def __init__(self, index: int, spec: TaskSpec, result: SubAgentResult | None = None,
                 error: BaseException | None = None):
        self.index = index
        self.spec = spec
        self.result = result
        self.error = error

    @property
    def task_id(self) -> str:
        return self.spec.task_id

    @property
    def agent(self) -> str:
        return self.spec.agent

    @property
    def refused(self) -> bool:
        """Refused, as distinct from failed. A refusal is a rule saying "no"; a failure is a thing
        going wrong. Collapsing them is what makes a security rejection look like an outage."""
        return isinstance(self.error, AuthorizationError)

    @property
    def status(self) -> str:
        if self.error is not None:
            return "refused" if self.refused else "error"
        return self.result.status if self.result is not None else "error"

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None and self.result.ok

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "result": None if self.result is None else self.result.to_dict(),
            "error": None if self.error is None else
                      f"{type(self.error).__name__}: {' '.join(str(self.error).split())[:200]}",
        }


class FanOutResult:
    """The joined outcome of a fan-out: one `TaskOutcome` per dispatched task, in input order.

    Ordering is positional, not completion order. Threads finish in whatever order the scheduler
    chooses, so results are written into a pre-sized slot by index and nothing depends on timing.

    `joined` is False if the join barrier broke - a worker thread died without reporting - so a caller
    can tell "everything reported" from "something vanished", rather than reading a short tuple as
    complete.
    """

    __slots__ = ("outcomes", "joined")

    def __init__(self, outcomes: tuple[TaskOutcome, ...], joined: bool = True):
        self.outcomes = outcomes
        self.joined = joined

    def __len__(self) -> int:
        return len(self.outcomes)

    def __iter__(self):
        return iter(self.outcomes)

    def __getitem__(self, index):
        return self.outcomes[index]

    @property
    def results(self) -> tuple[SubAgentResult, ...]:
        """Only the tasks that produced a result. Refusals are NOT in here - they have no result."""
        return tuple(o.result for o in self.outcomes if o.result is not None)

    @property
    def refusals(self) -> tuple[TaskOutcome, ...]:
        return tuple(o for o in self.outcomes if o.refused)

    @property
    def succeeded(self) -> tuple[TaskOutcome, ...]:
        return tuple(o for o in self.outcomes if o.ok)

    @property
    def failed(self) -> tuple[TaskOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.ok)

    def raise_for_refusals(self) -> None:
        """Raise the first refusal, if any.

        Offered rather than automatic: a fan-out should not abandon three good results because one task
        was malformed, but a caller that requires authorisation to hold everywhere can demand it.
        """
        for outcome in self.outcomes:
            if outcome.error is not None:
                raise outcome.error

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return {"total": len(self.outcomes), "joined": self.joined,
                "by_status": counts, "order": [o.task_id for o in self.outcomes]}

    def to_dict(self) -> dict:
        return {**self.summary(), "outcomes": [o.to_dict() for o in self.outcomes]}


class Dispatch:
    """One completed delegation, recorded so a run can be audited after the fact.

    Kept as a plain record rather than folded into the result so that the Coordinator never edits a
    worker's own output. The `spec` and `result` are the two things that actually happened.
    """

    __slots__ = ("task_id", "agent", "spec", "result")

    def __init__(self, spec: TaskSpec, result: SubAgentResult):
        self.spec = spec
        self.result = result
        self.task_id = spec.task_id
        self.agent = spec.agent


class Coordinator:
    """Resolves, authorises and dispatches one task at a time.

    `workers` maps a registered agent id to its worker instance. The registry is closed, so a worker
    for an unregistered id cannot be registered here in the first place: `delegate()` authorises
    against the roster, not against this mapping.

    Construction never authorises anything, because there is no task yet. Authorisation happens per
    delegation, in `delegate()`.
    """

    def __init__(self, workers: Mapping[str, Worker] | None = None, *, lane: MemoryLane | None = None):
        self._workers: dict[str, Worker] = dict(workers or {})
        # One lane per Coordinator unless a caller supplies a shared one. Sharing is how two
        # Coordinators serialise against each other; NOT sharing is a deliberate choice and means
        # each is its own boundary.
        #
        # CONSEQUENCE, and the one usage rule that matters: build ONE Coordinator per client and pass
        # it to every `investigate()` call. A Coordinator per session means a lane per session, and a
        # client shared across two lanes is unprotected - the boundary would be per-session while the
        # hazard is per-client. That mistake is asserted against in
        # tests/test_p5_serial_memory_lane.py so it cannot be reintroduced quietly.
        self._lane = lane if lane is not None else MemoryLane()
        self._dispatches: list[Dispatch] = []

    @classmethod
    def with_memory_specialist(cls, worker: Worker, *, lane: MemoryLane | None = None) -> "Coordinator":
        """The only roster P4 provides. Named after the worker so adding P6 workers is a visible
        change here rather than an invisible one at a call site."""
        from debugagent.agents.memory_specialist import MEMORY_SPECIALIST

        return cls({MEMORY_SPECIALIST: worker}, lane=lane)

    @property
    def lane(self) -> MemoryLane:
        """The serial lane guarding this Coordinator's client access.

        The flow takes this for its OWN memory calls - the direct `recall()` and the retention write -
        because those bypass the workers. A boundary that only covered delegation would not be a
        boundary at all: the same client would still be reachable from two threads.
        """
        return self._lane

    def register(self, agent_id: str, worker: Worker) -> None:
        """Attach a worker for a REGISTERED agent id.

        For the composition root, which is the only place that knows what is wired up. The agent must
        already be on the roster: this refuses an unknown id rather than inventing one, so registering
        cannot quietly widen the closed set of identities the seam authorises.

        Registration never replaces a live worker silently - a caller that means to swap must say so
        explicitly, because a swap under a running fan-out would change which code executes mid-batch.
        """
        definition = get_agent(agent_id)  # refuses an unregistered id
        if agent_id in self._workers and self._workers[agent_id] is not worker:
            raise RegistrationError(
                f"{agent_id!r} is already served by {type(self._workers[agent_id]).__name__}; "
                f"unregister it first if the swap is intended")
        self._workers[agent_id] = worker
        del definition

    def unregister(self, agent_id: str) -> Worker | None:
        """Detach the worker for `agent_id`, returning it, or None if there was none.

        The counterpart to `register()`, and what that method's error message points at. A worker is
        held for the lifetime of the Coordinator in normal use, so this exists for deliberate
        reconfiguration - swapping a port or a scope - and for tests.
        """
        return self._workers.pop(agent_id, None)

    @property
    def agent_ids(self) -> tuple[str, ...]:
        """The registered agents that currently have a worker instance. Diagnostics."""
        return tuple(sorted(self._workers))


    @property
    def dispatches(self) -> tuple[Dispatch, ...]:
        """Completed delegations, in the order they finished."""
        return tuple(self._dispatches)

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(dispatch.task_id for dispatch in self._dispatches)

    def worker_for(self, agent_id: str) -> Worker:
        """The registered worker for `agent_id`, or an explicit failure explaining its absence.

        The roster check is `get_agent`, not membership of this mapping: an unknown agent id is
        rejected by the closed registry before it can be looked up here.
        """
        get_agent(agent_id)
        worker = self._workers.get(agent_id)
        if worker is None:
            return _Absent(agent_id)
        return worker

    def delegate(self, spec: TaskSpec) -> SubAgentResult:
        """Authorise, then dispatch. Returns the worker's own result.

        Raises `AuthorizationError` if `spec` is not permitted. That is the security boundary and it
        is loud on purpose; see the module docstring.
        """
        # Authorisation FIRST, unconditionally, before the worker object is even resolved. A test
        # asserts the worker is never invoked for a rejected spec, so this ordering cannot be
        # quietly inverted later.
        #
        # Authorisation is deliberately OUTSIDE the memory lane: it is pure, cheap, and holds no
        # client. Serialising it would make every refusal queue behind an in-flight memory call for no
        # safety gain.
        authorize(spec)
        result = self._execute_authorized(spec)
        self._dispatches.append(Dispatch(spec, result))
        return result

    def _execute_authorized(self, spec: TaskSpec) -> SubAgentResult:
        """Run an already-authorised spec, always inside the lane.

        A single dispatch is the whole operation, so it takes the lane unconditionally: serialising one
        memory call costs nothing and keeps the safe path the default. `fan_out` is where the
        conditional lives, because that is the only place with something to overlap.
        """
        worker = self.worker_for(spec.agent)
        try:
            # Inside the lane: `execute` is where a client-touching worker reaches the client, and the
            # client is what is not thread-safe. Everything else about a dispatch stays outside.
            with self._lane:
                result = worker.execute(spec)
        except AuthorizationError:
            # A refusal raised by the worker during execution is a refusal, not an outage.
            raise
        except Exception as exc:  # noqa: BLE001 - see docstring: a worker fault is not a session fault
            result = self._worker_fault(spec, exc)
        return result

    def _worker_fault(self, spec: TaskSpec, exc: BaseException) -> SubAgentResult:
        return SubAgentResult(
            task_id=spec.task_id,
            agent=spec.agent,
            status="failed",
            observations=(),
            failure_kind="unavailable",
            failure_detail=(
                f"memory specialist could not be dispatched ({type(exc).__name__})"
                if spec.agent.endswith("memory_specialist")
                else f"worker could not be dispatched ({type(exc).__name__})"),
        )

    def touches_client(self, spec: TaskSpec) -> bool:
        """Whether this TASK enters the Hindsight client, and so must take the lane.

        Two independent reasons, either of which is sufficient:

        1. **The registered agent has the capability.** `client_access` on the `AgentDefinition` is the
           authority. A client-access agent is serialised for EVERY task it runs, whatever that task
           asks for - so omitting a memory tool from the tool list cannot buy unsynchronised client
           access. This is the reason the capability lives in the roster rather than on the worker.
        2. **The task requests a memory tool.** Defence in depth for the reverse mistake: a task naming
           `hindsight_*` is serialised even if the roster somehow said otherwise. `authorize()` already
           refuses a memory tool to an agent not granted it, so this is a second net, not the only one.

        A task that is neither - a registered non-client agent, asking for nothing that reaches the
        bank - runs outside the lane, which is what makes fan-out worth having.
        """
        if get_agent(spec.agent).client_access:
            return True
        return any(tool in MEMORY_TOOLS for tool in spec.allowed_tools)

    def fan_out(self, specs: Sequence[TaskSpec], *, timeout: float | None = None) -> FanOutResult:
        """Dispatch several authorised tasks in parallel, then join them.

        The shape of the whole thing, and the reason it is safe:

        - **Authorisation first, in input order, outside the lane.** A task that cannot be authorised
          never starts, and its refusal is recorded in its own slot rather than aborting its siblings.
        - **Only client-touching tasks take the lane.** Memory work therefore still enters the client
          one thread at a time, while everything else overlaps freely. This is the whole point: the
          client is the constraint, not the CPU.
        - **One shared Coordinator, one shared lane.** No worker, thread or task constructs either. A
          per-worker lane would be per-worker serialisation of a shared client, i.e. none at all.
        - **A join barrier before aggregation.** No result is read until every dispatched thread has
          reported, and a barrier that breaks is reported as `joined=False` rather than silently
          shortening the result set.
        - **Positional results.** Each thread writes to a slot indexed by its position in `specs`, so
          ordering is deterministic regardless of completion order.

        `timeout` bounds the JOIN BARRIER only, not any worker's execution, and defaults to
        `DEFAULT_JOIN_TIMEOUT`. The bound matters because the barrier is advisory: every outcome is
        written BEFORE the barrier wait, so a barrier that cannot be satisfied costs the `joined` flag,
        not a result. Without a bound, one participant that never arrives would hang the whole
        fan-out with the other results already in hand.

        Deliberately absent: retries, partial-failure retry, timeouts per task beyond the join, and any
        escalation of a failure. `partial` and `failed` stay distinguishable, and no failure is hidden
        by a sibling's success.
        """
        ordered = tuple(specs)
        if not ordered:
            return FanOutResult((), joined=True)

        # Authorise everything up front, in order, so refusals are known before any thread starts and
        # a malformed task cannot surprise a sibling mid-flight.
        outcomes: list[TaskOutcome | None] = [None] * len(ordered)
        runnable: list[tuple[int, TaskSpec]] = []
        for index, spec in enumerate(ordered):
            try:
                authorize(spec)
            except AuthorizationError as exc:
                outcomes[index] = TaskOutcome(index, spec, error=exc)
                continue
            runnable.append((index, spec))

        if not runnable:
            return FanOutResult(tuple(o for o in outcomes if o is not None), joined=True)

        lane_plan = {index: self.touches_client(spec) for index, spec in runnable}
        # `timeout` is a KEYWORD here, and that is not a style preference: `Barrier`'s signature is
        # `Barrier(parties, action=None, timeout=None)`, so a positional second argument is the release
        # callback. Passing the number positionally installs a float as the action and raises
        # `TypeError: 'float' object is not callable` the moment the barrier releases.
        join = threading.Barrier(len(runnable), timeout=timeout or DEFAULT_JOIN_TIMEOUT)
        broken = threading.Event()

        def run_one(index: int, spec: TaskSpec) -> None:
            worker = self.worker_for(spec.agent)
            try:
                if lane_plan[index]:
                    with self._lane:
                        result = worker.execute(spec)
                else:
                    result = worker.execute(spec)
                outcomes[index] = TaskOutcome(index, spec, result=result)
                self._dispatches.append(Dispatch(spec, result))
            except AuthorizationError as exc:
                # Raised by the worker mid-task: still a refusal, never a generic failure.
                outcomes[index] = TaskOutcome(index, spec, error=exc)
            except Exception as exc:  # noqa: BLE001 - one worker's fault must not hide the others
                result = self._worker_fault(spec, exc)
                outcomes[index] = TaskOutcome(index, spec, result=result)
                self._dispatches.append(Dispatch(spec, result))
            finally:
                try:
                    join.wait()
                except threading.BrokenBarrierError:
                    # A thread died without reporting, so the rest can never rendezvous. Recorded, not
                    # raised: a short result set is visible rather than silently short.
                    broken.set()

        threads = [threading.Thread(target=run_one, args=(index, spec), daemon=True)
                   for index, spec in runnable]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        return FanOutResult(tuple(o for o in outcomes if o is not None), joined=not broken.is_set())

    def delegate_memory(self, *, task_id: str, case_signature: str,
                        symptoms: tuple[str, ...] = ()) -> SubAgentResult:
        """Build the memory task through the seam and dispatch it.

        The spec is built by `build_memory_specialist_task`, which routes through `build_task_spec`
        (validate + authorise), so this method does not get a second way in. `delegate()` authorises
        again before execution, which is cheap and means a hand-constructed `TaskSpec` cannot skip
        the roster either.
        """
        from debugagent.agents.memory_specialist import build_memory_specialist_task

        spec = build_memory_specialist_task(
            task_id=task_id, case_signature=case_signature, symptoms=symptoms)
        return self.delegate(spec)


class _Absent:
    """Stands in for a registered agent that has no worker instance wired up.

    A distinct object rather than `None` so `delegate()` has one code path. It reports the absence as
    an explicit `unavailable` failure: a roster entry with no instance is a deployment gap, and saying
    "unavailable" keeps it distinguishable from a clean recall that found nothing.
    """

    def __init__(self, agent_id: str):
        self._agent_id = agent_id

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        return SubAgentResult(
            task_id=spec.task_id,
            agent=self._agent_id,
            status="failed",
            observations=(),
            failure_kind="unavailable",
            failure_detail=f"no worker instance is wired up for {self._agent_id!r}",
        )
