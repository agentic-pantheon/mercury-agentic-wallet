"""Native (gas) token placeholders used at swap/API boundaries."""

from __future__ import annotations

from web3 import Web3

from mercury.models.addresses import normalize_evm_address
from mercury.models.erc20 import ZERO_ADDRESS

NATIVE_FROM_TOKEN_SENTINEL_ALIAS = Web3.to_checksum_address(
    "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
)


def normalize_swap_input_from_token(value: str) -> str:
    """Normalize swap *sell* token: native sentinels map to canonical zero address."""

    stripped = value.strip()
    lower = stripped.lower()
    if lower == ZERO_ADDRESS.lower() or lower == NATIVE_FROM_TOKEN_SENTINEL_ALIAS.lower():
        return normalize_evm_address(ZERO_ADDRESS)
    return normalize_evm_address(stripped)
