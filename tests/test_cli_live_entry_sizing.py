from io import StringIO

from rich.console import Console

import trader.cli as cli
from trader.runtime import RuntimeConfig


def test_validate_live_entry_sizing_warns_but_allows_fixed_notional_below_floor() -> None:
    runtime_cfg = RuntimeConfig(
        mode="live",
        symbol="BTC/USDT",
        timeframe="1m",
        fixed_notional_usdt=100.0,
        min_entry_notional_usdt=250.0,
    )
    capture = StringIO()
    original_console = cli.console
    cli.console = Console(file=capture, force_terminal=False, color_system=None)
    try:
        cli._validate_live_entry_sizing(runtime_cfg)
    finally:
        cli.console = original_console

    warning = capture.getvalue()
    assert "fixed_notional_usdt=100.00" in warning
    assert "min_entry_notional_usdt=250.00" in warning
    assert "Runtime startup will continue" in warning
    assert "entry_notional_below_floor" in warning


def test_validate_live_entry_sizing_allows_floor_sized_entry() -> None:
    runtime_cfg = RuntimeConfig(
        mode="live",
        symbol="BTC/USDT",
        timeframe="1m",
        fixed_notional_usdt=250.0,
        min_entry_notional_usdt=250.0,
    )
    capture = StringIO()
    original_console = cli.console
    cli.console = Console(file=capture, force_terminal=False, color_system=None)
    try:
        cli._validate_live_entry_sizing(runtime_cfg)
    finally:
        cli.console = original_console

    assert capture.getvalue() == ""
