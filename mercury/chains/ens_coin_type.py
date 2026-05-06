"""ENSIP-11 coin types for EVM chain address records."""


def evm_chain_id_to_ens_coin_type(chain_id: int) -> int:
    """Map an EVM ``chain_id`` to the ENS ``addr`` record coin type.

    Ethereum mainnet uses SLIP-44 coin type **60**. Other EVM chains use
    ENSIP-11: ``0x80000000 | chain_id``.
    """

    if chain_id == 1:
        return 60
    return 0x80000000 | chain_id
