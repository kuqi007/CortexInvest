from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from PIL import Image

from src.tools.screenshot_import.models import (
    ClassificationResult,
    Platform,
    PlatformCandidate,
    ScreenshotType,
)

Layout: TypeAlias = Literal["dense_table", "wide_table"]
ForcedPlatform: TypeAlias = Literal["auto"] | Platform
ForcedType: TypeAlias = Literal["auto"] | ScreenshotType


@dataclass(frozen=True)
class ImageFingerprint:
    width: int
    height: int
    top_bar_color: str
    background_color: str
    red_ratio_top: float
    orange_ratio_top: float
    dark_ratio_top: float
    red_ratio_total: float
    blue_ratio_total: float
    green_ratio_total: float
    dark_ratio_total: float
    light_ratio_total: float
    layout: Layout
    visible_keywords: tuple[str, ...] = ()

    # ── Enhanced color ratios (were missing in original) ──
    green_ratio_top: float = 0.0
    orange_ratio_total: float = 0.0

    # ── Mid-body region (25%-75% height) text color ratios ──
    # The mid-body is where table data/stock rows live.
    # Different platforms use different text color patterns:
    #   THS dark:   red + blue colored text columns
    #   EastMoney:  green/white text columns, some orange accents
    red_ratio_mid: float = 0.0
    green_ratio_mid: float = 0.0
    blue_ratio_mid: float = 0.0
    light_ratio_mid: float = 0.0
    orange_ratio_mid: float = 0.0
    dark_ratio_mid: float = 0.0

    # ── Bottom navigation bar ──
    # EastMoney has a visible bottom nav (行情/自选/交易/我的)
    # THS typically does not have a colored bottom bar
    has_bottom_bar: bool = False
    bottom_bar_color: str = "none"


def _color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    tot = r + g + b
    mx = max(r, g, b)
    mn = min(r, g, b)

    if r >= 235 and g >= 235 and b >= 235:
        return "white"

    if tot < 90 or mx < 45:
        return "dark_blue_or_black"

    if b >= mx - 15 and b >= r + 25 and b >= g + 15 and mx < 200:
        return "dark_blue_or_black"

    if r >= 160 and g >= 70 and b <= g - 15 and r >= b + 40:
        return "orange"

    if r >= 140 and r >= g + 35 and r >= b + 35:
        return "red"

    if mx - mn < 45 and 80 <= tot // 3 <= 200:
        return "mixed"

    if mx >= 220 and mn >= 200:
        return "white"

    return "mixed"


def _ratio(
    pixels: Sequence[tuple[int, int, int]],
    predicate: Callable[[tuple[int, int, int]], bool],
) -> float:
    if not pixels:
        return 0.0
    hit = sum(1 for p in pixels if predicate(p))
    return hit / len(pixels)


def _pixels_rgb(img: Image.Image) -> list[tuple[int, int, int]]:
    return list(img.get_flattened_data())


def _mean_rgb(pixels: Sequence[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not pixels:
        return (0, 0, 0)
    n = len(pixels)
    sr = sum(p[0] for p in pixels)
    sg = sum(p[1] for p in pixels)
    sb = sum(p[2] for p in pixels)
    return (sr // n, sg // n, sb // n)


def _is_light(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    if _color_name(rgb) == "white":
        return True
    return (r + g + b) / 3.0 >= 220.0


def _is_red_text(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 140 and r >= g + 35 and r >= b + 35


def _is_blue_text(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return b >= 120 and b >= r + 30 and b >= g + 15


def _is_green_text(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return g >= 120 and g >= r + 35 and g >= b + 35


def _is_dark(rgb: tuple[int, int, int]) -> bool:
    return _color_name(rgb) == "dark_blue_or_black"


def _is_dark_mode(fp: ImageFingerprint) -> bool:
    return fp.background_color == "dark_blue_or_black" or fp.dark_ratio_total >= 0.75


def extract_fingerprint(image_path: Path) -> ImageFingerprint:
    with Image.open(image_path) as im:
        rgb = im.convert("RGB")
    w, h = rgb.size

    # ── Standard top-20% + body (legacy) ──
    top_h = max(1, int(h * 0.2))
    top = rgb.crop((0, 0, w, top_h))
    body = rgb.crop((0, top_h, w, h))
    top_pixels = _pixels_rgb(top)
    all_pixels = _pixels_rgb(rgb)
    body_pixels = _pixels_rgb(body) if body.size[1] > 0 else []

    red_ratio_top = _ratio(top_pixels, lambda p: _color_name(p) == "red")
    orange_ratio_top = _ratio(top_pixels, lambda p: _color_name(p) == "orange")
    dark_ratio_top = _ratio(top_pixels, _is_dark)
    red_ratio_total = _ratio(all_pixels, _is_red_text)
    blue_ratio_total = _ratio(all_pixels, _is_blue_text)
    green_ratio_total = _ratio(all_pixels, _is_green_text)
    dark_ratio_total = _ratio(all_pixels, _is_dark)
    light_ratio_total = _ratio(all_pixels, _is_light)
    orange_ratio_total = _ratio(all_pixels, lambda p: _color_name(p) == "orange")
    green_ratio_top = _ratio(top_pixels, _is_green_text)

    layout: Layout = "dense_table" if h > w else "wide_table"

    # ── Mid-body region (25%-75%) where table data lives ──
    mid_band_start = int(h * 0.25)
    mid_band_end = int(h * 0.75)
    if mid_band_end > mid_band_start:
        mid_pixels = _pixels_rgb(rgb.crop((0, mid_band_start, w, mid_band_end)))
    else:
        mid_pixels = []

    red_ratio_mid = _ratio(mid_pixels, _is_red_text) if mid_pixels else 0.0
    green_ratio_mid = _ratio(mid_pixels, _is_green_text) if mid_pixels else 0.0
    blue_ratio_mid = _ratio(mid_pixels, _is_blue_text) if mid_pixels else 0.0
    light_ratio_mid = _ratio(mid_pixels, _is_light) if mid_pixels else 0.0
    orange_ratio_mid = (
        _ratio(mid_pixels, lambda p: _color_name(p) == "orange") if mid_pixels else 0.0
    )
    dark_ratio_mid = _ratio(mid_pixels, _is_dark) if mid_pixels else 0.0

    # ── Bottom navigation bar detection (bottom 8%) ──
    bottom_band_h = max(1, int(h * 0.08))
    if h > bottom_band_h:
        bottom_pixels = _pixels_rgb(rgb.crop((0, h - bottom_band_h, w, h)))
    else:
        bottom_pixels = []
    bottom_mean = _mean_rgb(bottom_pixels) if bottom_pixels else (0, 0, 0)
    bottom_cname = _color_name(bottom_mean)
    has_bottom_bar = bool(bottom_pixels) and bottom_cname not in (
        "white",
        "mixed",
        "unknown",
    )
    bottom_bar_color = bottom_cname if has_bottom_bar else "none"

    return ImageFingerprint(
        width=w,
        height=h,
        top_bar_color=_color_name(_mean_rgb(top_pixels)),
        background_color=_color_name(_mean_rgb(body_pixels)),
        red_ratio_top=red_ratio_top,
        orange_ratio_top=orange_ratio_top,
        dark_ratio_top=dark_ratio_top,
        red_ratio_total=red_ratio_total,
        blue_ratio_total=blue_ratio_total,
        green_ratio_total=green_ratio_total,
        dark_ratio_total=dark_ratio_total,
        light_ratio_total=light_ratio_total,
        layout=layout,
        visible_keywords=(),
        green_ratio_top=green_ratio_top,
        orange_ratio_total=orange_ratio_total,
        red_ratio_mid=red_ratio_mid,
        green_ratio_mid=green_ratio_mid,
        blue_ratio_mid=blue_ratio_mid,
        light_ratio_mid=light_ratio_mid,
        orange_ratio_mid=orange_ratio_mid,
        dark_ratio_mid=dark_ratio_mid,
        has_bottom_bar=has_bottom_bar,
        bottom_bar_color=bottom_bar_color,
    )


def _classify_core(fp: ImageFingerprint) -> tuple[Platform, float, list[str]]:
    signals: list[str] = []
    if fp.layout == "dense_table":
        signals.append("dense_numeric_table")

    is_dark = _is_dark_mode(fp)

    # ──────────────────────────────────────────────────
    # Stage 1: Theme-aware platform classification
    # ──────────────────────────────────────────────────

    # ── LIGHT THEME: Red top bar + white body → THS ──
    if (
        not is_dark
        and fp.red_ratio_top >= 0.25
        and (fp.background_color == "white" or fp.light_ratio_total >= 0.75)
    ):
        if fp.green_ratio_total >= 0.005 and fp.blue_ratio_total < 0.005:
            signals.extend(["top_red", "light_theme", "green_text", "watchlist_rows"])
            return "ths", 0.82, signals
        if fp.blue_ratio_total >= 0.02:
            signals.extend(["top_red", "light_theme", "blue_text"])
            return "ths", 0.82, signals
        signals.extend(["top_red", "light_theme"])
        return "ths", 0.78, signals

    # ── Red top bar (any theme, including dark) → THS ──
    if fp.red_ratio_top >= 0.25:
        body_colored = fp.red_ratio_mid + fp.blue_ratio_mid + fp.green_ratio_mid
        if body_colored >= 0.01:
            signals.extend(["top_red", "body_colored_text"])
            return "ths", 0.82, signals
        signals.append("top_red")
        return "ths", 0.78, signals

    # ── Orange top bar → EastMoney ──
    if fp.orange_ratio_top >= 0.2:
        signals.append("top_orange")
        return "eastmoney", 0.82, signals

    # ── Hybrid red+orange top bar → EastMoney ──
    if fp.orange_ratio_top >= 0.1 and fp.red_ratio_top >= 0.1:
        signals.append("top_red_orange")
        return "eastmoney", 0.82, signals

    # ── DARK THEME: Platform disambiguation ──
    if is_dark:
        # THS dark: both red AND blue colored text in mid-body data region
        if fp.red_ratio_mid >= 0.005 and fp.blue_ratio_mid >= 0.005:
            signals.extend(["dark_theme", "red_blue_text", "mid_body_signals"])
            return "ths", 0.85, signals

        # THS dark (fallback to total-image ratios — legacy compatibility)
        if (
            fp.red_ratio_total + fp.blue_ratio_total >= 0.02
            and fp.red_ratio_total >= 0.005
            and fp.blue_ratio_total >= 0.005
        ):
            signals.extend(["dark_theme", "red_blue_text"])
            return "ths", 0.82, signals

        # EastMoney dark: green text columns + light body text in mid-body
        if fp.green_ratio_mid >= 0.003 and fp.light_ratio_mid >= 0.01:
            signals.extend(["dark_theme", "white_green_red_text", "mid_body_signals"])
            return "eastmoney", 0.82, signals

        # EastMoney dark (legacy — total-image ratios)
        if (
            fp.dark_ratio_top >= 0.2
            and fp.green_ratio_total >= 0.005
            and fp.light_ratio_total >= 0.015
        ):
            signals.extend(["dark_theme", "white_green_red_text"])
            return "eastmoney", 0.82, signals

        # EastMoney dark fallback: dark top bar with body text evidence
        if fp.dark_ratio_top >= 0.2:
            body_text = fp.red_ratio_total + fp.blue_ratio_total + fp.green_ratio_total
            if body_text >= 0.005:
                signals.extend(["top_dark_bar", "body_text_detected"])
                return "eastmoney", 0.75, signals
            signals.append("top_dark_bar")
            return "eastmoney", 0.65, signals

    # ── LIGHT THEME: Non-red top bar ──
    # EastMoney light: dark top bar with light body
    if fp.dark_ratio_top >= 0.2:
        body_colored = fp.green_ratio_mid + fp.blue_ratio_mid + fp.red_ratio_mid
        if body_colored >= 0.005:
            signals.extend(["top_dark_bar", "body_colored_text"])
            return "eastmoney", 0.75, signals
        signals.append("top_dark_bar")
        return "eastmoney", 0.70, signals

    # EastMoney light: dark top bar + light background (already handled above since
    # dark_ratio_top >= 0.2 would have matched; this is a narrower match for
    # light-background cases)
    if (
        fp.top_bar_color == "dark_blue_or_black"
        and (fp.background_color == "white" or fp.light_ratio_total >= 0.75)
        and fp.orange_ratio_top < 0.1
        and fp.red_ratio_top < 0.25
    ):
        signals.append("top_dark_bar_light_body")
        return "eastmoney", 0.70, signals

    # HK Panda: white/minimal chrome
    if (
        fp.top_bar_color == "white"
        and fp.background_color == "white"
        and fp.light_ratio_total >= 0.85
        and fp.dark_ratio_top < 0.05
        and fp.red_ratio_top < 0.05
        and fp.orange_ratio_top < 0.05
    ):
        signals.extend(["light_background", "minimal_top_chrome"])
        return "hk_panda", 0.82, signals

    if fp.light_ratio_total >= 0.75:
        signals.append("light_background")
        return "hk_panda", 0.55, signals

    # ── Unknown ──
    signals.append("no_strong_classifier_signal")
    return "unknown", 0.35, signals


def _candidate_list_for_unknown() -> list[PlatformCandidate]:
    return [
        PlatformCandidate(platform="ths", confidence=0.40),
        PlatformCandidate(platform="eastmoney", confidence=0.38),
        PlatformCandidate(platform="hk_panda", confidence=0.36),
        PlatformCandidate(platform="other", confidence=0.30),
    ]


def classify_fingerprint(
    fingerprint: ImageFingerprint,
    forced_platform: ForcedPlatform = "auto",
    forced_type: ForcedType = "auto",
) -> ClassificationResult:
    platform, confidence, signals = _classify_core(fingerprint)
    screenshot_type: ScreenshotType = (
        "holding" if fingerprint.layout == "dense_table" else "watchlist"
    )
    if "watchlist_rows" in signals:
        screenshot_type = "watchlist"
    if forced_type != "auto":
        screenshot_type = forced_type
    if forced_platform != "auto":
        platform = forced_platform
        candidates = [PlatformCandidate(platform=platform, confidence=1.0)]
    elif platform == "unknown":
        candidates = _candidate_list_for_unknown()
    else:
        candidates = [PlatformCandidate(platform=platform, confidence=confidence)]

    return ClassificationResult(
        platform=platform,
        screenshot_type=screenshot_type,
        confidence=confidence,
        signals=signals,
        candidate_platforms=candidates,
    )
