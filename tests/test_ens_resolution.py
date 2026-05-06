"""ENS resolution helpers and invoke pre-validation wiring."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from mercury.chains import evm_chain_id_to_ens_coin_type
from mercury.graph.intent_validation import validate_invoke_intent
from mercury.providers.ens import (
    Web3EnsAddressResolver,
    looks_like_potential_ens_name,
)
from web3 import Web3


@dataclass
class FakeEnsResolver:
    """Deterministic stub for ``EVMIdentifierResolver``."""

    name_to_address: dict[str, str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def resolve_evm_identifier(self, raw: str, chain_name: str) -> str:
        self.calls.append((raw, chain_name))
        key = raw.strip().lower()
        if key not in self.name_to_address:
            msg = f"unknown name {raw!r}"
            raise AssertionError(msg)
        return self.name_to_address[key]


def test_evm_chain_id_to_ens_coin_type() -> None:
    assert evm_chain_id_to_ens_coin_type(1) == 60
    assert evm_chain_id_to_ens_coin_type(8453) == (0x80000000 | 8453)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("vitalik.eth", True),
        ("team.example.xyz", True),
        ("0xdead000000000000000000000000000000dEaD", False),
        ("USDC", False),
        ("not-a-name", False),
    ],
)
def test_looks_like_potential_ens_name(value: str, expected: bool) -> None:
    assert looks_like_potential_ens_name(value) is expected


def test_invoke_resolves_native_transfer_recipient() -> None:
    resolved = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")
    fake = FakeEnsResolver({"alice.eth": resolved})
    state, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "native_transfer",
                "chain": "base",
                "wallet_id": "primary",
                "recipient_address": "alice.eth",
                "amount": "0.01",
                "idempotency_key": "k1",
            }
        },
        ens_resolver=fake,
    )
    assert err is None
    assert state is not None
    raw_in = state["raw_input"]
    assert isinstance(raw_in, dict)
    assert raw_in["recipient_address"] == resolved
    assert fake.calls == [("alice.eth", "base")]


def test_invoke_without_resolver_rejects_dot_name() -> None:
    _, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "native_transfer",
                "chain": "base",
                "wallet_id": "primary",
                "recipient_address": "alice.eth",
                "amount": "0.01",
                "idempotency_key": "k1",
            }
        },
        ens_resolver=None,
    )
    assert err is not None
    assert err.code == "validation_failed"


def test_invoke_does_not_resolve_token_address_symbols() -> None:
    resolved = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")
    fake = FakeEnsResolver({"vitalik.eth": resolved})

    _, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "erc20_transfer",
                "chain": "base",
                "wallet_id": "primary",
                "token_address": "vitalik.eth",
                "recipient_address": resolved,
                "amount": "1",
                "idempotency_key": "k2",
            }
        },
        ens_resolver=fake,
    )

    assert err is not None
    assert fake.calls == [], "ENS must not touch token_address"


def test_web3_resolver_invokes_ens_with_coin_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERCURY_DEFAULT_CHAIN", raising=False)

    ens_addr = MagicMock(
        side_effect=[
            Web3.to_checksum_address("0x1111111111111111111111111111111111111111"),
            Web3.to_checksum_address("0x2222222222222222222222222222222222222222"),
        ]
    )

    mocked_w3 = MagicMock()
    mocked_w3.ens.address = ens_addr

    factory = MagicMock()
    factory.create.return_value.client = mocked_w3

    resolver = Web3EnsAddressResolver(factory)

    r1 = resolver.resolve_evm_identifier("foo.eth", "ethereum")
    r2 = resolver.resolve_evm_identifier("foo.eth", "base")

    assert r1 == Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
    assert r2 == Web3.to_checksum_address("0x2222222222222222222222222222222222222222")

    assert ens_addr.call_count == 2
    assert ens_addr.call_args_list[0].kwargs["coin_type"] == 60
    assert ens_addr.call_args_list[1].kwargs["coin_type"] == evm_chain_id_to_ens_coin_type(8453)


def test_readonly_wallet_address_resolution() -> None:
    resolved = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    fake = FakeEnsResolver({"foo.eth": Web3.to_checksum_address(resolved)})

    state, err = validate_invoke_intent(
        {
            "raw_input": {
                "kind": "native_balance",
                "wallet_address": "foo.eth",
            }
        },
        ens_resolver=fake,
    )

    assert err is None
    assert isinstance(state, dict)
    inp = state["raw_input"]
    assert isinstance(inp, dict)
    assert inp["wallet_address"] == Web3.to_checksum_address(resolved)

