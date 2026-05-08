from typing import Any

from mercury.graph.agent import build_swap_transaction_graph
from mercury.graph.nodes_swaps import SwapGraphDependencies, route_swap_intent
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.models import ExecutionStatus, GasFees, PreparedTransaction, SignedTransactionResult
from mercury.models.addresses import normalize_evm_address
from mercury.models.approval import ApprovalRequest, ApprovalResult, ApprovalStatus
from mercury.models.erc20 import ZERO_ADDRESS
from mercury.models.execution import ExecutableTransaction, TransactionReceipt
from mercury.models.policy import PolicyDecisionStatus
from mercury.models.signing import SignTransactionRequest
from mercury.models.swaps import (
    SwapEVMTransaction,
    SwapExecution,
    SwapExecutionType,
    SwapProviderName,
    SwapQuote,
    SwapQuoteRequest,
    SwapRoute,
    SwapTypedOrder,
)
from mercury.models.wallets import WalletAddressResult
from mercury.policy.idempotency import InMemoryIdempotencyStore
from mercury.policy.risk import TransactionPolicyEngine
from mercury.swaps.router import SwapRouter

from tests.test_evm_read_tools import FakeEth, FakeProviderFactory, FakeWeb3

TOKEN_IN = "0x000000000000000000000000000000000000cafE"
TOKEN_OUT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x000000000000000000000000000000000000bEEF"
SPENDER = "0x0000000000000000000000000000000000000002"
SWAP_TO = "0x0000000000000000000000000000000000000003"


def test_swap_intent_routes_through_quote_allowance_policy_and_pipeline() -> None:
    events: list[str] = []
    signer = FakeSigner(events, expected_to=SWAP_TO)
    graph = _graph(events, signer, allowance=2_000_000)

    result = graph.invoke({"raw_input": _swap_payload("swap-route-1")})

    assert result["execution_result"].status == ExecutionStatus.CONFIRMED
    assert result["prepared_transaction"].metadata["action"] == "swap"
    assert result["prepared_swap"].allowance.allowance_sufficient is True
    assert events == [
        "address",
        "quote",
        "build",
        "address",
        "nonce",
        "gas",
        "simulate",
        "approval",
        "sign",
        "broadcast",
        "monitor",
    ]


def test_swap_graph_prepares_approval_before_swap_when_allowance_is_insufficient() -> None:
    events: list[str] = []
    signer = FakeSigner(events, expected_to=TOKEN_IN)
    graph = _graph(events, signer, allowance=0)

    result = graph.invoke({"raw_input": _swap_payload("swap-approval-1")})

    assert result["execution_result"].status == ExecutionStatus.CONFIRMED
    assert result["prepared_transaction"].metadata["action"] == "erc20_approval"
    assert result["prepared_swap"].approval_transaction is not None
    assert "build" not in events


def test_native_from_token_swap_runs_pipeline_without_erc20_approval_and_keeps_tx_value() -> None:
    """Native *from_token* skips allowance/approval txn and broadcasts with value."""
    events: list[str] = []
    signer = FakeSigner(events, expected_to=SWAP_TO)
    graph = _graph(events, signer, allowance=0, swap_provider_cls=NativeSellFakeSwapProvider)

    result = graph.invoke(
        {"raw_input": _swap_payload("swap-native-from-1", from_token_native=True)}
    )

    assert result["execution_result"].status == ExecutionStatus.CONFIRMED
    prepared_swap = result["prepared_swap"]
    assert prepared_swap.approval_transaction is None
    assert prepared_swap.allowance is None
    assert prepared_swap.quote.request.from_token_is_native is True
    pt = result["prepared_transaction"]
    assert pt.metadata["action"] == "swap"
    assert pt.value_wei == prepared_swap.quote.request.amount_in_raw
    assert pt.value_wei > 0
    assert events == [
        "address",
        "quote",
        "build",
        "address",
        "nonce",
        "gas",
        "simulate",
        "approval",
        "sign",
        "broadcast",
        "monitor",
    ]


def test_route_swap_intent_rejects_non_swap_payload() -> None:
    assert route_swap_intent({"raw_input": {"kind": "erc20_transfer"}}) == "unsupported_response"


def test_swap_graph_resolves_typed_cow_order_without_evm_pipeline() -> None:
    events: list[str] = []
    signer = FakeSigner(events, expected_to=SWAP_TO)
    graph = _cow_graph(events, signer, allowance=2_000_000)

    result = graph.invoke({"raw_input": _swap_payload("cow-typed-1", provider="cowswap")})

    assert result.get("prepared_transaction") is None
    assert result["prepared_swap"].execution.execution_type == SwapExecutionType.EIP712_ORDER
    pd = result.get("policy_decision")
    assert pd is not None
    assert pd.status == PolicyDecisionStatus.NEEDS_APPROVAL
    assert "sign" not in events and "broadcast" not in events


class FakeSwapProvider:
    name = SwapProviderName.LIFI

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def get_quote(self, request: SwapQuoteRequest) -> SwapQuote:
        self._events.append("quote")
        return SwapQuote(
            provider=self.name,
            request=request,
            route=SwapRoute(
                provider=self.name,
                route_id="route-1",
                from_chain_id=request.chain_id,
                to_chain_id=request.chain_id,
                from_token=request.from_token,
                to_token=request.to_token,
                spender_address=SPENDER,
            ),
            amount_in_raw=request.amount_in_raw,
            expected_amount_out_raw=1_000_000,
            min_amount_out_raw=990_000,
            slippage_bps=request.max_slippage_bps,
            recipient_address=request.effective_recipient,
        )

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        self._events.append("build")
        return SwapExecution(
            provider=self.name,
            execution_type=SwapExecutionType.EVM_TRANSACTION,
            quote=quote,
            transaction=SwapEVMTransaction(
                chain_id=quote.request.chain_id,
                to=SWAP_TO,
                data="0x1234",
            ),
        )


class NativeSellFakeSwapProvider(FakeSwapProvider):
    """LiFi-shaped fake that attaches value for native sells."""

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        self._events.append("build")
        value_wei = quote.request.amount_in_raw if quote.request.from_token_is_native else 0
        return SwapExecution(
            provider=self.name,
            execution_type=SwapExecutionType.EVM_TRANSACTION,
            quote=quote,
            transaction=SwapEVMTransaction(
                chain_id=quote.request.chain_id,
                to=SWAP_TO,
                data="0x1234",
                value_wei=value_wei,
            ),
        )


class FakeSigner:
    def __init__(self, events: list[str], *, expected_to: str) -> None:
        self._events = events
        self._expected_to = expected_to

    def get_wallet_address(self, wallet_id: str) -> WalletAddressResult:
        self._events.append("address")
        return WalletAddressResult(wallet_id=wallet_id, address=WALLET)

    def sign_transaction(self, request: SignTransactionRequest) -> SignedTransactionResult:
        self._events.append("sign")
        assert request.prepared_transaction.transaction["to"] == self._expected_to
        return SignedTransactionResult(
            wallet_id=request.wallet.wallet_id,
            chain_id=request.chain_id,
            signer_address=WALLET,
            raw_transaction_hex="0x02",
            tx_hash="0xabcd",
        )


class FakeApprover:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        self._events.append("approval")
        assert request.metadata["action"] in {"swap", "erc20_approval"}
        return ApprovalResult(status=ApprovalStatus.APPROVED, reason="approved", approved_by="test")


class FakeBackend:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def resolve_chain_id(self, transaction: PreparedTransaction) -> int:
        return transaction.chain_id or 1

    def lookup_nonce(self, transaction: PreparedTransaction, wallet_address: str) -> int:
        self._events.append("nonce")
        assert wallet_address == WALLET
        return 9

    def populate_gas(self, transaction: PreparedTransaction | ExecutableTransaction) -> GasFees:
        self._events.append("gas")
        return GasFees(gas_limit=80_000, gas_price=1_000_000_000)

    def simulate(self, transaction: ExecutableTransaction) -> Any:
        self._events.append("simulate")
        from mercury.models.simulation import SimulationResult, SimulationStatus

        return SimulationResult(status=SimulationStatus.PASSED, gas_estimate=80_000)

    def broadcast(self, signed_transaction: SignedTransactionResult) -> str:
        self._events.append("broadcast")
        return "0xbeef"

    def wait_for_receipt(
        self,
        *,
        chain: str,
        tx_hash: str,
        timeout_seconds: float,
        confirmations: int,
    ) -> TransactionReceipt:
        self._events.append("monitor")
        return TransactionReceipt(tx_hash=tx_hash, status=ExecutionStatus.CONFIRMED        )


class FakeCowSwapProvider:
    name = SwapProviderName.COWSWAP

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def get_quote(self, request: SwapQuoteRequest) -> SwapQuote:
        self._events.append("quote")
        return SwapQuote(
            provider=self.name,
            request=request,
            route=SwapRoute(
                provider=self.name,
                route_id="route-cow-1",
                from_chain_id=request.chain_id,
                to_chain_id=request.chain_id,
                from_token=request.from_token,
                to_token=request.to_token,
                spender_address=SPENDER,
            ),
            amount_in_raw=request.amount_in_raw,
            expected_amount_out_raw=1_000_000,
            min_amount_out_raw=990_000,
            slippage_bps=request.max_slippage_bps,
            recipient_address=request.effective_recipient,
        )

    def build_execution(self, quote: SwapQuote) -> SwapExecution:
        self._events.append("build")
        return SwapExecution(
            provider=self.name,
            execution_type=SwapExecutionType.EIP712_ORDER,
            quote=quote,
            order=SwapTypedOrder(
                chain_id=quote.request.chain_id,
                typed_data={"domain": {"chainId": quote.request.chain_id}, "message": {}},
                submit_url="https://api.cow.fi/base/api/v1/orders",
            ),
        )


def _graph(
    events: list[str],
    signer: FakeSigner,
    *,
    allowance: int,
    swap_provider_cls: type[FakeSwapProvider] = FakeSwapProvider,
) -> Any:
    factory = FakeProviderFactory(
        FakeWeb3(
            FakeEth(
                contract_responses={
                    ("decimals", ()): 6,
                    ("symbol", ()): "USDC",
                    ("allowance", (WALLET, SPENDER)): allowance,
                }
            )
        )
    )
    return build_swap_transaction_graph(
        SwapGraphDependencies(
            router=SwapRouter([swap_provider_cls(events)]),
            provider_factory=factory,
            address_resolver=signer,
        ),
        TransactionGraphDependencies(
            backend=FakeBackend(events),
            signer=signer,
            policy_engine=TransactionPolicyEngine(),
            approver=FakeApprover(events),
            idempotency_store=InMemoryIdempotencyStore(),
        ),
    ).compile()


def _cow_graph(events: list[str], signer: FakeSigner, *, allowance: int) -> Any:
    factory = FakeProviderFactory(
        FakeWeb3(
            FakeEth(
                contract_responses={
                    ("decimals", ()): 6,
                    ("symbol", ()): "USDC",
                    ("allowance", (WALLET, SPENDER)): allowance,
                }
            )
        )
    )
    return build_swap_transaction_graph(
        SwapGraphDependencies(
            router=SwapRouter([FakeCowSwapProvider(events)]),
            provider_factory=factory,
            address_resolver=signer,
        ),
        TransactionGraphDependencies(
            backend=FakeBackend(events),
            signer=signer,
            policy_engine=TransactionPolicyEngine(),
            approver=FakeApprover(events),
            idempotency_store=InMemoryIdempotencyStore(),
        ),
    ).compile()


def _swap_payload(
    idempotency_key: str,
    *,
    provider: str = "lifi",
    from_token_native: bool = False,
) -> dict[str, object]:
    from_tok = normalize_evm_address(ZERO_ADDRESS) if from_token_native else TOKEN_IN
    return {
        "kind": "swap",
        "chain": "base",
        "wallet_id": "primary",
        "from_token": from_tok,
        "to_token": TOKEN_OUT,
        "amount_in": "1.5",
        "max_slippage_bps": 50,
        "provider_preference": provider,
        "idempotency_key": idempotency_key,
    }
