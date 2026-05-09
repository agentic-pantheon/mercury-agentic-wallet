import pytest
from mercury.config import MercurySettings


def test_default_settings_load_without_secrets() -> None:
    settings = MercurySettings()

    assert settings.app_name == "Mercury Wallet Agent"
    assert settings.checkpointer_database_url == ""
    assert settings.ethereum_rpc_secret_path == "mercury/rpc/ethereum"
    assert settings.base_rpc_secret_path == "mercury/rpc/base"
    assert settings.oneclaw_vault_id == "mercury"
    assert settings.oneclaw_api_key_secret_source == "MERCURY_ONECLAW_API_KEY"
    assert settings.alchemy_webhook_signing_key_secret_path.startswith("mercury/")


def test_alchemy_signing_key_path_distinct_from_rest_api_path() -> None:
    settings = MercurySettings()

    assert settings.alchemy_api_secret_path != settings.alchemy_webhook_signing_key_secret_path


def test_default_chain_is_ethereum() -> None:
    settings = MercurySettings()

    assert settings.default_chain == "ethereum"


def test_interrupt_approval_defaults_false() -> None:
    settings = MercurySettings()

    assert settings.interrupt_approval is False


def test_interrupt_approval_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERCURY_INTERRUPT_APPROVAL", "true")
    settings = MercurySettings()

    assert settings.interrupt_approval is True


def test_rpc_values_are_references_not_secret_values() -> None:
    settings = MercurySettings()

    paths = [
        settings.ethereum_rpc_secret_path,
        settings.base_rpc_secret_path,
        settings.arbitrum_rpc_secret_path,
        settings.optimism_rpc_secret_path,
        settings.monad_rpc_secret_path,
    ]
    assert all(path.startswith("mercury/rpc/") for path in paths)
    assert all("://" not in path for path in paths)
