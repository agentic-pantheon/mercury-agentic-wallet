"""Fakeable runtime boundary for invoking Mercury graphs from services.

LangSmith / LangChain tracing picks up RunnableConfig supplied to ``invoke`` / ``stream`` when
``LANGSMITH_TRACING``, ``LANGSMITH_API_KEY`` (etc.) are configured in the environment.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, cast, runtime_checkable

from langchain_core.messages import AIMessage

try:
    from langgraph.checkpoint.base import BaseCheckpointSaver
except ImportError:  # pragma: no cover - optional for older langgraph installs
    BaseCheckpointSaver = object  # type: ignore[misc, assignment]

from langgraph.types import Command

from mercury.config import MercurySettings, get_settings
from mercury.graph.agent import (
    build_erc20_transaction_graph,
    build_graph,
    build_native_transaction_graph,
    build_swap_transaction_graph,
)
from mercury.graph.intent_validation import validate_invoke_intent
from mercury.graph.log_summaries import (
    graph_node_finished_summary,
    graph_run_start_summary,
    invoke_intent_validation_failed_summary,
)
from mercury.graph.logging import log_graph_event
from mercury.graph.nodes_erc20 import ERC20GraphDependencies
from mercury.graph.nodes_native import NativeGraphDependencies
from mercury.graph.nodes_swaps import SwapGraphDependencies
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.graph.responses import format_error_response, format_unsupported_response
from mercury.graph.state import MercuryState
from mercury.models.errors import MercuryErrorInfo
from mercury.providers.ens import EVMIdentifierResolver
from mercury.tools.registry import ReadOnlyToolRegistry


@runtime_checkable
class InvokableGraph(Protocol):
    """Compiled LangGraph-like object used behind the service runtime."""

    def invoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke the graph and return its final state."""


@runtime_checkable
class GraphRuntime(Protocol):
    """Service-facing runtime interface that tests can replace."""

    def invoke(self, state: MercuryState) -> MercuryState:
        """Invoke Mercury for an already-normalized graph state."""


class MercuryGraphRuntime:
    """Route service requests to the appropriate Mercury graph."""

    def __init__(
        self,
        *,
        read_graph: InvokableGraph,
        erc20_graph: InvokableGraph,
        native_graph: InvokableGraph,
        swap_graph: InvokableGraph,
        runtime_settings: MercurySettings | None = None,
        ens_resolver: EVMIdentifierResolver | None = None,
    ) -> None:
        self._read_graph = read_graph
        self._erc20_graph = erc20_graph
        self._native_graph = native_graph
        self._swap_graph = swap_graph
        self._runtime_settings = runtime_settings
        self._ens_resolver = ens_resolver

    def invoke(self, state: MercuryState) -> MercuryState:
        """Invoke the graph that matches the request intent kind."""

        settings = self._runtime_settings if self._runtime_settings is not None else get_settings()
        rid = state.get("request_id")
        request_id = rid if isinstance(rid, str) else ""

        validated_state, validation_error = validate_invoke_intent(
            state, ens_resolver=self._ens_resolver
        )
        if validation_error is not None:
            kind = _intent_kind_from_state(state)
            log_graph_event(
                "invoke_intent_validation_failed",
                summary=invoke_intent_validation_failed_summary(
                    kind,
                    code=validation_error.code,
                    message=validation_error.message,
                ),
                request_id=request_id,
                intent_kind=kind,
                error_code=validation_error.code,
            )
            failed = dict(state)
            failed["error"] = validation_error
            if validation_error.code == "unsupported_intent":
                text = format_unsupported_response(validation_error)
            else:
                text = format_error_response(validation_error)
            failed["response_text"] = text
            failed["messages"] = [AIMessage(content=text)]
            return cast(MercuryState, failed)

        working_state = cast(MercuryState, validated_state)
        graph, graph_label = self._graph_selection_for_state(working_state)
        config = self._runnable_config_for_state(working_state, graph_label)
        stream_fn = getattr(graph, "stream", None)

        ik = _intent_kind_from_state(working_state)
        log_graph_event(
            "graph_run_start",
            summary=graph_run_start_summary(graph_label, ik),
            request_id=request_id,
            graph=graph_label,
            intent_kind=ik,
        )

        input_payload: MercuryState | Command[Any] = working_state
        if settings.interrupt_approval:
            _raise_if_interrupt_approval_without_checkpointer(settings, graph, graph_label)
            resume_payload = _approval_response_from_state(working_state)
            has_interrupt = _pending_interrupt(graph, config)
            if has_interrupt:
                if resume_payload is None:
                    failed = dict(working_state)
                    failed["error"] = MercuryErrorInfo(
                        code="interrupt_resume_required",
                        category="validation",
                        message=(
                            "This thread has a pending approval interrupt; repeat the invoke "
                            "with the same request_id and an approval_response payload."
                        ),
                        retryable=False,
                        recoverable=True,
                        details={"stage": "invoke"},
                    )
                    return cast(MercuryState, failed)
                input_payload = Command(resume=resume_payload)

        if not settings.graph_node_logging or stream_fn is None:
            result = graph.invoke(input_payload, config=config)
            return cast(MercuryState, result)

        last_values: MercuryState | None = None
        for mode, payload in stream_fn(
            input_payload,
            stream_mode=["updates", "values"],
            config=config,
        ):
            if mode == "updates" and isinstance(payload, dict):
                for node_name, patch in payload.items():
                    keys: list[str] = []
                    if isinstance(patch, Mapping):
                        keys = sorted(str(k) for k in patch.keys())
                    log_graph_event(
                        "graph_node_finished",
                        summary=graph_node_finished_summary(
                            node_name,
                            patch if isinstance(patch, Mapping) else None,
                        ),
                        request_id=request_id,
                        graph=graph_label,
                        node=node_name,
                        state_keys=keys,
                    )
            elif mode == "values" and isinstance(payload, Mapping):
                last_values = cast(MercuryState, dict(payload))

        if last_values is None:
            return cast(MercuryState, graph.invoke(input_payload, config=config))
        return last_values

    def _graph_selection_for_state(self, state: MercuryState) -> tuple[InvokableGraph, str]:
        raw_input = state.get("raw_input")
        if isinstance(raw_input, dict):
            payload = raw_input.get("intent")
            if not isinstance(payload, dict):
                payload = raw_input
            kind = str(payload.get("kind", "")).strip().lower()
            if kind in {"erc20_transfer", "erc20_approval"}:
                return self._erc20_graph, "erc20_transaction"
            if kind == "native_transfer":
                return self._native_graph, "native_transaction"
            if kind == "swap":
                return self._swap_graph, "swap_transaction"
        return self._read_graph, "read"

    def _runnable_config_for_state(
        self,
        state: MercuryState,
        graph_label: str,
    ) -> dict[str, Any]:
        """LangChain/LangSmith-friendly RunnableConfig additions."""

        raw = state.get("request_id")
        request_id = raw if isinstance(raw, str) and raw else "no-thread"
        return {
            "run_name": f"MercuryGraph.{graph_label}",
            "tags": ["mercury", graph_label],
            "metadata": {
                "request_id": request_id,
                "graph": graph_label,
                "intent_kind": _intent_kind_from_state(state),
            },
            "configurable": {"thread_id": request_id},
        }


def _raise_if_interrupt_approval_without_checkpointer(
    settings: MercurySettings,
    graph: InvokableGraph,
    graph_label: str,
) -> None:
    if not settings.interrupt_approval:
        return
    if graph_label == "read":
        return
    saver = getattr(graph, "checkpointer", None)
    if saver is None:
        raise RuntimeError(
            "Mercury interrupt approval is enabled but transaction graphs were compiled "
            "without a LangGraph checkpointer (configure MERCURY_CHECKPOINTER_DATABASE_URL "
            "or supply an InMemorySaver in tests)."
        )


def _approval_response_from_state(state: MercuryState) -> dict[str, Any] | None:
    raw_input = state.get("raw_input")
    if not isinstance(raw_input, dict):
        return None
    meta = raw_input.get("metadata")
    if not isinstance(meta, dict):
        return None
    nested = meta.get("approval_response")
    return nested if isinstance(nested, dict) else None


def _tasks_have_pending_interrupt(tasks: Iterable[Any] | None) -> bool:
    if not tasks:
        return False
    for task in tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            return True
    return False


def _pending_interrupt(graph: InvokableGraph, config: dict[str, Any]) -> bool:
    get_state = getattr(graph, "get_state", None)
    if get_state is None:
        return False
    snapshot = get_state(config)
    tasks = getattr(snapshot, "tasks", None)
    return _tasks_have_pending_interrupt(tasks if isinstance(tasks, Iterable) else None)


def _intent_kind_from_state(state: MercuryState) -> str:
    raw_input = state.get("raw_input")
    if isinstance(raw_input, dict):
        payload = raw_input.get("intent")
        if not isinstance(payload, dict):
            payload = raw_input
        kind_raw = payload.get("kind")
        kind = str(kind_raw).strip().lower()
        return kind if kind else "unset"
    if isinstance(raw_input, str):
        return "literal_text"
    return "unset"


def build_default_runtime(
    *,
    registry: ReadOnlyToolRegistry,
    erc20_deps: ERC20GraphDependencies,
    native_deps: NativeGraphDependencies,
    swap_deps: SwapGraphDependencies,
    transaction_deps: TransactionGraphDependencies,
    runtime_settings: MercurySettings | None = None,
    ens_resolver: EVMIdentifierResolver | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> MercuryGraphRuntime:
    """Build Mercury's default compiled graphs from injectable dependencies."""

    def _compiled(builder: Any) -> InvokableGraph:
        if checkpointer is None:
            compiled = builder.compile()
        else:
            compiled = builder.compile(checkpointer=checkpointer)
        return cast(InvokableGraph, compiled)

    return MercuryGraphRuntime(
        read_graph=_compiled(build_graph(registry)),
        erc20_graph=_compiled(
            build_erc20_transaction_graph(erc20_deps, transaction_deps),
        ),
        native_graph=_compiled(
            build_native_transaction_graph(native_deps, transaction_deps),
        ),
        swap_graph=_compiled(
            build_swap_transaction_graph(swap_deps, transaction_deps),
        ),
        runtime_settings=runtime_settings,
        ens_resolver=ens_resolver,
    )
