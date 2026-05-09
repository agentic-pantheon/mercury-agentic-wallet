"""Typed application settings for Mercury."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MercurySettings(BaseSettings):
    """Settings that store references to secrets, never secret values."""

    model_config = SettingsConfigDict(env_prefix="MERCURY_", env_file=None, extra="ignore")

    app_name: str = "Mercury Wallet Agent"
    default_chain: str = "ethereum"

    graph_node_logging: bool = Field(
        default=True,
        description="Log each LangGraph node completion to stderr when True.",
    )
    log_level: str = Field(
        default="DEBUG",
        description=(
            "Root process log level (e.g. DEBUG, INFO). Env MERCURY_LOG_LEVEL. "
            "Invalid names fall back to DEBUG."
        ),
    )

    ethereum_rpc_secret_path: str = Field(
        default="mercury/rpc/ethereum",
        description="1Claw secret path for Ethereum RPC.",
    )
    base_rpc_secret_path: str = Field(
        default="mercury/rpc/base",
        description="1Claw secret path for Base RPC.",
    )
    arbitrum_rpc_secret_path: str = Field(
        default="mercury/rpc/arbitrum",
        description="1Claw secret path for Arbitrum One RPC.",
    )
    optimism_rpc_secret_path: str = Field(
        default="mercury/rpc/optimism",
        description="1Claw secret path for Optimism RPC.",
    )
    monad_rpc_secret_path: str = Field(
        default="mercury/rpc/monad",
        description="1Claw secret path for Monad RPC.",
    )

    lifi_api_secret_path: str = Field(
        default="mercury/apis/lifi",
        description=(
            "1Claw path for an optional LiFi key; if unset, no x-lifi-api-key (public tier)."
        ),
    )
    cowswap_api_secret_path: str = Field(
        default="mercury/apis/cowswap",
        description="1Claw secret path for CoW Swap API configuration.",
    )
    uniswap_api_secret_path: str = Field(
        default="mercury/apis/uniswap",
        description="1Claw secret path for Uniswap API configuration.",
    )

    alchemy_api_secret_path: str = Field(
        default="mercury/apis/alchemy",
        description="1Claw secret path for the Alchemy API key (REST prices, portfolio, etc.).",
    )
    alchemy_webhook_signing_key_secret_path: str = Field(
        default="mercury/apis/alchemy_webhook_signing_key",
        description=(
            "1Claw secret path for the Alchemy Notify webhook signing key "
            "(HMAC verification; distinct from the REST API key)."
        ),
    )

    oneclaw_base_url: str = Field(
        default="http://localhost:8080",
        description="1Claw API base URL.",
    )
    oneclaw_vault_id: str = Field(
        default="mercury",
        description="1Claw vault ID containing Mercury secret paths.",
    )
    oneclaw_api_key_secret_source: str = Field(
        default="MERCURY_ONECLAW_API_KEY",
        description="Secret source for the 1Claw API key; never the API key value.",
    )
    oneclaw_agent_id: str | None = Field(
        default=None,
        description="Optional 1Claw agent ID used for scoped secret reads.",
    )

    checkpointer_database_url: str = Field(
        default="",
        description=(
            "PostgreSQL connection URL for LangGraph checkpoint persistence; env "
            "``MERCURY_CHECKPOINTER_DATABASE_URL``. When empty or whitespace-only, "
            "Mercury does not attach a checkpoint saver (default behavior). When "
            "set, the FastAPI app lifespan opens ``PostgresSaver`` for the graph "
            "runtime (requires optional ``checkpoint-postgres`` extras)."
        ),
    )


def get_settings() -> MercurySettings:
    """Return application settings from the current process environment.

    Intentionally not cached: hosts (e.g. Telegram bots) may call ``load_dotenv``
    after some imports; a cached singleton would keep pre-dotenv values while
    fresh :class:`MercurySettings` calls would see the updated environment.
    """

    return MercurySettings()
