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
    assert cfg.binance_api_key_source == "BINANCE_TESTNET_API_KEY"
    assert cfg.binance_api_secret_source == "BINANCE_TESTNET_API_SECRET"
    assert cfg.binance_api_key_source_origin == "process_env"
    assert cfg.binance_api_secret_source_origin == "process_env"
    assert cfg.binance_api_key_len == len("testnet_key")
    assert cfg.binance_api_key_prefix == "test"


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
    assert cfg.binance_api_key_source == "BINANCE_MAINNET_API_KEY"
    assert cfg.binance_api_secret_source == "BINANCE_MAINNET_API_SECRET"
    assert cfg.binance_api_key_source_origin == "process_env"
    assert cfg.binance_api_secret_source_origin == "process_env"


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
    assert cfg.binance_api_key_source == "BINANCE_API_KEY"
    assert cfg.binance_api_secret_source == "BINANCE_API_SECRET"
    assert cfg.binance_api_key_source_origin == "process_env"
    assert cfg.binance_api_secret_source_origin == "process_env"


def test_binance_env_override_changes_selected_key_source(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("BINANCE_ENV", "mainnet")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "testnet_key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "testnet_secret")
    monkeypatch.setenv("BINANCE_MAINNET_API_KEY", "mainnet_key")
    monkeypatch.setenv("BINANCE_MAINNET_API_SECRET", "mainnet_secret")

    cfg = AppConfig.from_env(binance_env_override="testnet")

    assert cfg.binance_env == "testnet"
    assert cfg.binance_api_key is not None
    assert cfg.binance_api_secret is not None
    assert cfg.binance_api_key.get_secret_value() == "testnet_key"
    assert cfg.binance_api_secret.get_secret_value() == "testnet_secret"
    assert cfg.binance_api_key_source == "BINANCE_TESTNET_API_KEY"
    assert cfg.binance_api_secret_source == "BINANCE_TESTNET_API_SECRET"
    assert cfg.binance_api_key_source_origin == "process_env"
    assert cfg.binance_api_secret_source_origin == "process_env"


def test_dotenv_source_origin_when_process_env_not_set(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "BINANCE_ENV=testnet\nBINANCE_TESTNET_API_KEY=abc123abc123abc123\nBINANCE_TESTNET_API_SECRET=def456def456def456\n",
        encoding="utf-8",
    )

    cfg = AppConfig.from_env()

    assert cfg.binance_api_key_source == "BINANCE_TESTNET_API_KEY"
    assert cfg.binance_api_secret_source == "BINANCE_TESTNET_API_SECRET"
    assert cfg.binance_api_key_source_origin == "merged_defaults"
    assert cfg.binance_api_secret_source_origin == "merged_defaults"
    assert cfg.env_file_used is not None
    assert str(dotenv.resolve()) == cfg.env_file_used


def test_budget_usdt_auto_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("BUDGET_USDT", "auto")

    cfg = AppConfig.from_env()

    assert cfg.budget_usdt_mode == "auto"
    assert cfg.budget_usdt_value is None


def test_min_entry_notional_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")
    monkeypatch.setenv("MIN_ENTRY_NOTIONAL_USDT", "333.0")

    cfg = AppConfig.from_env()

    assert cfg.min_entry_notional_usdt == 333.0


def test_sleep_mode_default_caps(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_binance_env(monkeypatch)
    monkeypatch.setenv("ENV_FILE", "__missing__.env")

    cfg = AppConfig.from_env()

    assert cfg.account_allocation_pct == 0.4
    assert cfg.max_position_notional_usdt == 4000.0
    assert cfg.max_position_notional == 4000.0
    assert cfg.min_entry_notional_usdt == 250.0
