"""Fakeable runtime boundary for invoking Mercury graphs from services.

LangSmith / LangChain tracing picks up RunnableConfig supplied to ``invoke`` / ``stream`` when
``LANGSMITH_TRACING``, ``LANGSMITH_API_KEY`` (etc.) are configured in the environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast, runtime_checkable

from langchain_core.messages import AIMessage

from mercury.config import MercurySettings, get_settings
from mercury.graph.agent import (
    build_erc20_transaction_graph,
    build_graph,
    build_native_transaction_graph,
    build_swap_transaction_graph,
)
from mercury.graph.intent_validation import validate_invoke_intent
from mercury.graph.logging import log_graph_event
from mercury.graph.nodes_erc20 import ERC20GraphDependencies
from mercury.graph.nodes_native import NativeGraphDependencies
from mercury.graph.nodes_swaps import SwapGraphDependencies
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.graph.responses import format_error_response, format_unsupported_response
from mercury.graph.state import MercuryState
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
    ) -> None:
        self._read_graph = read_graph
        self._erc20_graph = erc20_graph
        self._native_graph = native_graph
        self._swap_graph = swap_graph
        self._runtime_settings = runtime_settings

    def invoke(self, state: MercuryState) -> MercuryState:
        """Invoke the graph that matches the request intent kind."""

        settings = self._runtime_settings if self._runtime_settings is not None else get_settings()
        rid = state.get("request_id")
        request_id = rid if isinstance(rid, str) else ""

        validated_state, validation_error = validate_invoke_intent(state)
        if validation_error is not None:
            log_graph_event(
                "invoke_intent_validation_failed",
                request_id=request_id,
                intent_kind=_intent_kind_from_state(state),
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

        log_graph_event(
            "graph_run_start",
            request_id=request_id,
            graph=graph_label,
            intent_kind=_intent_kind_from_state(working_state),
        )

        if not settings.graph_node_logging or stream_fn is None:
            result = graph.invoke(working_state, config=config)
            return cast(MercuryState, result)

        last_values: MercuryState | None = None
        for mode, payload in stream_fn(
            working_state,
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
                        request_id=request_id,
                        graph=graph_label,
                        node=node_name,
                        state_keys=keys,
                    )
            elif mode == "values" and isinstance(payload, Mapping):
                last_values = cast(MercuryState, dict(payload))

        if last_values is None:
            return cast(MercuryState, graph.invoke(working_state, config=config))
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
) -> MercuryGraphRuntime:
    """Build Mercury's default compiled graphs from injectable dependencies."""

    return MercuryGraphRuntime(
        read_graph=build_graph(registry).compile(),
        erc20_graph=build_erc20_transaction_graph(erc20_deps, transaction_deps).compile(),
        native_graph=build_native_transaction_graph(native_deps, transaction_deps).compile(),
        swap_graph=build_swap_transaction_graph(swap_deps, transaction_deps).compile(),
        runtime_settings=runtime_settings,
    )
