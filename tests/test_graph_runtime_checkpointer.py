"""Optional checkpointer wiring for `build_default_runtime`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mercury.graph.nodes_erc20 import ERC20GraphDependencies
from mercury.graph.nodes_native import NativeGraphDependencies
from mercury.graph.nodes_swaps import SwapGraphDependencies
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.graph.runtime import build_default_runtime
from mercury.tools.registry import ReadOnlyToolRegistry


def _fake_deps() -> tuple[
    ReadOnlyToolRegistry,
    ERC20GraphDependencies,
    NativeGraphDependencies,
    SwapGraphDependencies,
    TransactionGraphDependencies,
]:
    registry = MagicMock(spec=ReadOnlyToolRegistry)
    erc20 = MagicMock(spec=ERC20GraphDependencies)
    native = MagicMock(spec=NativeGraphDependencies)
    swap = MagicMock(spec=SwapGraphDependencies)
    tx = MagicMock(spec=TransactionGraphDependencies)
    return registry, erc20, native, swap, tx


def test_build_default_runtime_without_checkpointer_invokes_plain_compile() -> None:
    registry, erc20_deps, native_deps, swap_deps, tx_deps = _fake_deps()

    graphs: dict[str, MagicMock] = {}

    def capture(name: str) -> MagicMock:
        g = MagicMock()
        g.compile.return_value = MagicMock(name=f"compiled_{name}")
        graphs[name] = g
        return g

    with (
        patch("mercury.graph.runtime.build_graph", return_value=capture("read")),
        patch(
            "mercury.graph.runtime.build_erc20_transaction_graph",
            return_value=capture("erc20"),
        ),
        patch(
            "mercury.graph.runtime.build_native_transaction_graph",
            return_value=capture("native"),
        ),
        patch(
            "mercury.graph.runtime.build_swap_transaction_graph",
            return_value=capture("swap"),
        ),
    ):
        build_default_runtime(
            registry=registry,
            erc20_deps=erc20_deps,
            native_deps=native_deps,
            swap_deps=swap_deps,
            transaction_deps=tx_deps,
        )

    for g in graphs.values():
        g.compile.assert_called_once_with()


def test_build_default_runtime_forwards_checkpointer_to_all_compile_calls() -> None:
    registry, erc20_deps, native_deps, swap_deps, tx_deps = _fake_deps()
    checkpointer = MagicMock(name="checkpointer")

    graphs: dict[str, MagicMock] = {}

    def capture(name: str) -> MagicMock:
        g = MagicMock()
        g.compile.return_value = MagicMock(name=f"compiled_{name}")
        graphs[name] = g
        return g

    with (
        patch("mercury.graph.runtime.build_graph", return_value=capture("read")),
        patch(
            "mercury.graph.runtime.build_erc20_transaction_graph",
            return_value=capture("erc20"),
        ),
        patch(
            "mercury.graph.runtime.build_native_transaction_graph",
            return_value=capture("native"),
        ),
        patch(
            "mercury.graph.runtime.build_swap_transaction_graph",
            return_value=capture("swap"),
        ),
    ):
        build_default_runtime(
            registry=registry,
            erc20_deps=erc20_deps,
            native_deps=native_deps,
            swap_deps=swap_deps,
            transaction_deps=tx_deps,
            checkpointer=checkpointer,
        )

    for g in graphs.values():
        g.compile.assert_called_once_with(checkpointer=checkpointer)
