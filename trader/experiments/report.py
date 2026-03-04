from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any

import pandas as pd


Color = tuple[int, int, int]


def _chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def _write_png(path: Path, pixels: list[list[Color]]) -> None:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    if width <= 0 or height <= 0:
        raise ValueError("PNG canvas must be non-empty")

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw.extend((r & 255, g & 255, b & 255))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
    iend = _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(signature + ihdr + idat + iend)


def _canvas(width: int, height: int, color: Color = (255, 255, 255)) -> list[list[Color]]:
    return [[color for _ in range(width)] for _ in range(height)]


def _set_px(img: list[list[Color]], x: int, y: int, color: Color) -> None:
    if 0 <= y < len(img) and 0 <= x < len(img[0]):
        img[y][x] = color


def _line(img: list[list[Color]], x0: int, y0: int, x1: int, y1: int, color: Color, thickness: int = 1) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        for tx in range(-thickness + 1, thickness):
            for ty in range(-thickness + 1, thickness):
                _set_px(img, x0 + tx, y0 + ty, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _rect(img: list[list[Color]], x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    for y in range(ya, yb + 1):
        for x in range(xa, xb + 1):
            _set_px(img, x, y, color)


def _plot_frame(width: int = 900, height: int = 500) -> tuple[list[list[Color]], int, int, int, int]:
    img = _canvas(width, height, (252, 252, 252))
    left = 70
    right = width - 30
    top = 30
    bottom = height - 45
    _rect(img, left, top, right, bottom, (245, 247, 250))
    _line(img, left, bottom, right, bottom, (70, 70, 70), thickness=1)
    _line(img, left, top, left, bottom, (70, 70, 70), thickness=1)
    return img, left, right, top, bottom


def _normalize(values: list[float]) -> tuple[list[float], float, float]:
    if not values:
        return [], 0.0, 1.0
    lo = min(values)
    hi = max(values)
    if math.isclose(lo, hi):
        hi = lo + 1.0
    norm = [(v - lo) / (hi - lo) for v in values]
    return norm, lo, hi


def save_line_chart(path: Path, values: list[float], color: Color = (37, 99, 235)) -> None:
    img, left, right, top, bottom = _plot_frame()
    if not values:
        _write_png(path, img)
        return
    norm, _, _ = _normalize(values)
    n = len(values)
    if n == 1:
        x = (left + right) // 2
        y = int(bottom - norm[0] * (bottom - top))
        _rect(img, x - 3, y - 3, x + 3, y + 3, color)
    else:
        for i in range(n - 1):
            x0 = int(left + i * (right - left) / (n - 1))
            x1 = int(left + (i + 1) * (right - left) / (n - 1))
            y0 = int(bottom - norm[i] * (bottom - top))
            y1 = int(bottom - norm[i + 1] * (bottom - top))
            _line(img, x0, y0, x1, y1, color, thickness=2)
    _write_png(path, img)


def save_dual_line_chart(
    path: Path,
    values_a: list[float],
    values_b: list[float],
    color_a: Color = (37, 99, 235),
    color_b: Color = (220, 38, 38),
) -> None:
    img, left, right, top, bottom = _plot_frame()
    n = min(len(values_a), len(values_b))
    if n <= 0:
        _write_png(path, img)
        return
    merged = [float(values_a[i]) for i in range(n)] + [float(values_b[i]) for i in range(n)]
    norm_all, _, _ = _normalize(merged)
    norm_a = norm_all[:n]
    norm_b = norm_all[n:]
    if n == 1:
        x = (left + right) // 2
        y_a = int(bottom - norm_a[0] * (bottom - top))
        y_b = int(bottom - norm_b[0] * (bottom - top))
        _rect(img, x - 3, y_a - 3, x + 3, y_a + 3, color_a)
        _rect(img, x - 3, y_b - 3, x + 3, y_b + 3, color_b)
    else:
        for i in range(n - 1):
            x0 = int(left + i * (right - left) / (n - 1))
            x1 = int(left + (i + 1) * (right - left) / (n - 1))
            y0a = int(bottom - norm_a[i] * (bottom - top))
            y1a = int(bottom - norm_a[i + 1] * (bottom - top))
            y0b = int(bottom - norm_b[i] * (bottom - top))
            y1b = int(bottom - norm_b[i + 1] * (bottom - top))
            _line(img, x0, y0a, x1, y1a, color_a, thickness=2)
            _line(img, x0, y0b, x1, y1b, color_b, thickness=2)
    _write_png(path, img)


def save_bar_chart(path: Path, values: list[float], color: Color = (14, 165, 233)) -> None:
    img, left, right, top, bottom = _plot_frame()
    if not values:
        _write_png(path, img)
        return
    norm, _, _ = _normalize(values)
    n = len(values)
    bar_w = max((right - left) // max(n * 2, 1), 4)
    for i, v in enumerate(norm):
        cx = int(left + (i + 0.5) * (right - left) / max(n, 1))
        y = int(bottom - v * (bottom - top))
        _rect(img, cx - bar_w, y, cx + bar_w, bottom - 1, color)
    _write_png(path, img)


def save_histogram(path: Path, values: list[float], bins: int = 14, color: Color = (22, 163, 74)) -> None:
    img, left, right, top, bottom = _plot_frame()
    if not values:
        _write_png(path, img)
        return
    lo = min(values)
    hi = max(values)
    if math.isclose(lo, hi):
        hi = lo + 1.0
    span = hi - lo
    counts = [0 for _ in range(max(bins, 1))]
    for v in values:
        idx = int((v - lo) / span * len(counts))
        idx = min(max(idx, 0), len(counts) - 1)
        counts[idx] += 1
    cmax = max(counts) if counts else 1
    bw = max((right - left) // max(len(counts), 1), 3)
    for i, count in enumerate(counts):
        h = int((count / max(cmax, 1)) * (bottom - top))
        x0 = left + i * bw
        x1 = min(x0 + bw - 1, right)
        _rect(img, x0, bottom - h, x1, bottom - 1, color)
    _write_png(path, img)


def save_dataframe_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True, default=str), encoding="utf-8")


def _md_table(df: pd.DataFrame, max_rows: int = 12) -> str:
    if df.empty:
        return "_(no rows)_"
    shown = df.head(max_rows).copy()
    headers = list(shown.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---" for _ in headers]) + " |",
    ]
    for _, row in shown.iterrows():
        cells: list[str] = []
        for h in headers:
            v = row[h]
            if isinstance(v, float):
                cells.append(f"{v:.6f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_markdown_report(
    *,
    path: Path,
    run_id: str,
    config: dict[str, Any],
    summary: dict[str, Any],
    cost_df: pd.DataFrame,
    wfo_df: pd.DataFrame,
    regime_df: pd.DataFrame,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Edge Validation Report ({run_id})")
    lines.append("")
    lines.append("## Verdict")
    lines.append(f"- verdict: **{summary.get('verdict', 'UNKNOWN')}**")
    lines.append(f"- robustness_score: `{float(summary.get('robustness_score', 0.0)):.4f}`")
    lines.append("")
    lines.append("## Core Summary")
    for k, v in summary.items():
        if k in {"verdict", "robustness_score"}:
            continue
        if isinstance(v, float):
            lines.append(f"- {k}: `{v:.6f}`")
        else:
            lines.append(f"- {k}: `{v}`")
    lines.append("")
    lines.append("## Config")
    lines.append("```json")
    lines.append(json.dumps(config, indent=2, ensure_ascii=True, default=str))
    lines.append("```")
    lines.append("")
    lines.append("## Cost Stress (head)")
    lines.append(_md_table(cost_df))
    lines.append("")
    lines.append("## Walk-forward Windows (head)")
    lines.append(_md_table(wfo_df))
    lines.append("")
    lines.append("## Regime Performance (head)")
    lines.append(_md_table(regime_df))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


class ExperimentReporter:
    """Backward-compatible reporter facade used by older experiment modules."""

    @staticmethod
    def save_json(data: dict[str, Any], path: str | Path) -> None:
        save_json(data, Path(path))

    @staticmethod
    def save_csv(df: pd.DataFrame, path: str | Path) -> None:
        save_dataframe_csv(df, Path(path))

    @staticmethod
    def save_line(values: list[float], path: str | Path) -> None:
        save_line_chart(Path(path), values)

    @staticmethod
    def save_hist(values: list[float], path: str | Path) -> None:
        save_histogram(Path(path), values)

    @staticmethod
    def save_bar(values: list[float], path: str | Path) -> None:
        save_bar_chart(Path(path), values)
