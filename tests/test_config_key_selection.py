from __future__ import annotations

from trader.config import AppConfig


def _clear_binance_env(monkeypatch) -> None:
    keys = [
        "ENV_FILE",
        "BINANCE_ENV",
        "BINANCE_TESTNET",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "BINANCE_MAINNET_API_KEY",
        "BINANCE_MAINNET_API_SECRET",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_testnet_prefers_testnet_specific_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("BINANCE_ENV", "testnet")
    monkeypatch.setenv("BINANCE_API_KEY", "generic_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "generic_secret")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "testnet_key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "testnet_secret")

    cfg = AppConfig.from_env()

    assert cfg.binance_env == "testnet"
    assert cfg.binance_api_key is not None
    assert cfg.binance_api_secret is not None
    assert cfg.binance_api_key.get_secret_value() == "testnet_key"
    assert cfg.binance_api_secret.get_secret_value() == "testnet_secret"


def test_mainnet_prefers_mainnet_specific_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("BINANCE_ENV", "mainnet")
    monkeypatch.setenv("BINANCE_API_KEY", "generic_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "generic_secret")
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", "mainnet_key")
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", "mainnet_secret")

    cfg = AppConfig.from_env()

    assert cfg.binance_env == "mainnet"
    assert cfg.binance_api_key is not None
    assert cfg.binance_api_secret is not None
    assert cfg.binance_api_key.get_secret_value() == "mainnet_key"
    assert cfg.binance_api_secret.get_secret_value() == "mainnet_secret"


def test_specific_key_fallback_to_generic(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("BINANCE_ENV", "testnet")
    monkeypatch.setenv("BINANCE_API_KEY", "generic_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "generic_secret")

    cfg = AppConfig.from_env()

    assert cfg.binance_env == "testnet"
    assert cfg.binance_api_key is not None
    assert cfg.binance_api_secret is not None
    assert cfg.binance_api_key.get_secret_value() == "generic_key"
    assert cfg.binance_api_secret.get_secret_value() == "generic_secret"
