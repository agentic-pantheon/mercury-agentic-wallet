"""FastAPI dependency wiring for Mercury's service boundary."""

from __future__ import annotations

import os
from typing import Annotated, cast

from fastapi import Depends, Request

from mercury.config import MercurySettings, get_settings
from mercury.custody import (
    MercuryWalletSigner,
    OneClawHttpClient,
    OneClawSecretStore,
    SecretStore,
)
from mercury.graph.nodes_erc20 import ERC20GraphDependencies
from mercury.graph.nodes_native import NativeGraphDependencies
from mercury.graph.nodes_swaps import SwapGraphDependencies
from mercury.graph.nodes_transaction import TransactionGraphDependencies
from mercury.graph.runtime import GraphRuntime, build_default_runtime
from mercury.models.swaps import SwapProviderName
from mercury.policy.idempotency import InMemoryIdempotencyStore
from mercury.policy.risk import TransactionPolicyEngine
from mercury.providers import Web3EnsAddressResolver, Web3ProviderFactory
from mercury.service.errors import DependencyUnavailableError
from mercury.swaps.base import SwapProviderConfig
from mercury.swaps.cowswap import CowSwapProvider
from mercury.swaps.lifi import LiFiProvider
from mercury.swaps.router import SwapRouter
from mercury.swaps.uniswap import UniswapProvider
from mercury.tools.registry import ReadOnlyToolRegistry
from mercury.tools.portfolio_tokens import AlchemyPortfolioToolDeps
from mercury.tools.token_prices import AlchemyPricesToolDeps
from mercury.tools.transactions import (
    RequestMetadataTransactionApprover,
    Web3TransactionBackend,
)


def get_service_settings(request: Request) -> MercurySettings:
    """Return settings attached to the app or the cached defaults."""

    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, MercurySettings):
        return settings
    return get_settings()


def get_secret_store(
    settings: Annotated[MercurySettings, Depends(get_service_settings)],
) -> SecretStore:
    """Create a 1Claw secret store without resolving any secret values."""

    api_key = os.environ.get(settings.oneclaw_api_key_secret_source)
    if api_key is None or not api_key.strip():
        raise DependencyUnavailableError("1Claw API key source is not configured.")
    client = OneClawHttpClient(base_url=settings.oneclaw_base_url, api_key=api_key)
    return OneClawSecretStore(
        client=client,
        vault_id=settings.oneclaw_vault_id,
        agent_id=settings.oneclaw_agent_id,
    )


def get_provider_factory(
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> Web3ProviderFactory:
    """Create the Web3 provider factory. RPC URLs resolve lazily per graph call."""

    return Web3ProviderFactory(secret_store)


def get_signer(
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> MercuryWalletSigner:
    """Create the custody signer without fetching wallet private keys."""

    return MercuryWalletSigner(secret_store)


def get_swap_router(
    settings: Annotated[MercurySettings, Depends(get_service_settings)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> SwapRouter:
    """Create the normalized swap router with provider API keys resolved lazily."""

    return SwapRouter(
        [
            LiFiProvider(_swap_config(SwapProviderName.LIFI, settings, secret_store)),
            CowSwapProvider(_swap_config(SwapProviderName.COWSWAP, settings, secret_store)),
            UniswapProvider(_swap_config(SwapProviderName.UNISWAP, settings, secret_store)),
        ]
    )


def get_graph_runtime(request: Request) -> GraphRuntime:
    """Return the app runtime or build the default runtime from service dependencies."""

    runtime = getattr(request.app.state, "graph_runtime", None)
    if runtime is not None:
        return cast(GraphRuntime, runtime)

    settings = get_service_settings(request)
    secret_store = get_secret_store(settings)
    provider_factory = get_provider_factory(secret_store)
    signer = get_signer(secret_store)
    ens_resolver = Web3EnsAddressResolver(provider_factory)
    swap_router = get_swap_router(settings, secret_store)
    transaction_deps = TransactionGraphDependencies(
        backend=Web3TransactionBackend(provider_factory),
        signer=signer,
        policy_engine=TransactionPolicyEngine(),
        approver=RequestMetadataTransactionApprover(),
        idempotency_store=InMemoryIdempotencyStore(),
    )
    alchemy_prices: AlchemyPricesToolDeps | None = None
    alchemy_portfolio: AlchemyPortfolioToolDeps | None = None
    alchemy_path = settings.alchemy_api_secret_path.strip()
    if alchemy_path:
        alchemy_prices = AlchemyPricesToolDeps(
            secret_store=secret_store,
            api_key_secret_path=alchemy_path,
        )
        alchemy_portfolio = AlchemyPortfolioToolDeps(
            secret_store=secret_store,
            api_key_secret_path=alchemy_path,
        )

    runtime = build_default_runtime(
        registry=ReadOnlyToolRegistry.from_provider_factory(
            provider_factory,
            alchemy_prices=alchemy_prices,
            alchemy_portfolio=alchemy_portfolio,
        ),
        erc20_deps=ERC20GraphDependencies(
            provider_factory=provider_factory,
            address_resolver=signer,
        ),
        native_deps=NativeGraphDependencies(address_resolver=signer),
        swap_deps=SwapGraphDependencies(
            router=swap_router,
            provider_factory=provider_factory,
            address_resolver=signer,
        ),
        transaction_deps=transaction_deps,
        runtime_settings=settings,
        ens_resolver=ens_resolver,
    )
    request.app.state.graph_runtime = runtime
    return runtime


def _swap_config(
    provider: SwapProviderName,
    settings: MercurySettings,
    secret_store: SecretStore,
) -> SwapProviderConfig:
    api_paths = {
        SwapProviderName.LIFI: settings.lifi_api_secret_path,
        SwapProviderName.COWSWAP: settings.cowswap_api_secret_path,
        SwapProviderName.UNISWAP: settings.uniswap_api_secret_path,
    }
    base_urls = {
        SwapProviderName.LIFI: "https://li.quest/v1",
        SwapProviderName.COWSWAP: "https://api.cow.fi",
        SwapProviderName.UNISWAP: "https://api.uniswap.org",
    }
    return SwapProviderConfig(
        provider=provider,
        base_url=base_urls[provider],
        api_secret_path=api_paths[provider],
        secret_store=secret_store,
    )
