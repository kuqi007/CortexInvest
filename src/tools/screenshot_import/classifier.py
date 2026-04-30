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
    light_ratio_total: float
    layout: Layout
    visible_keywords: tuple[str, ...] = ()


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

    if r >= 140 and r >= g + 35 and r >= b + 35:
        return "red"

    if r >= 160 and g >= 70 and b <= g - 15 and r >= b + 40:
        return "orange"

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


def extract_fingerprint(image_path: Path) -> ImageFingerprint:
    with Image.open(image_path) as im:
        rgb = im.convert("RGB")
    w, h = rgb.size
    top_h = max(1, int(h * 0.2))
    top = rgb.crop((0, 0, w, top_h))
    body = rgb.crop((0, top_h, w, h))
    top_pixels = _pixels_rgb(top)
    all_pixels = _pixels_rgb(rgb)
    body_pixels = _pixels_rgb(body) if body.size[1] > 0 else []

    red_ratio_top = _ratio(
        top_pixels, lambda p: _color_name(p) == "red"
    )
    orange_ratio_top = _ratio(
        top_pixels, lambda p: _color_name(p) == "orange"
    )
    dark_ratio_top = _ratio(
        top_pixels, lambda p: _color_name(p) == "dark_blue_or_black"
    )
    light_ratio_total = _ratio(all_pixels, _is_light)

    layout: Layout = "dense_table" if h > w else "wide_table"

    return ImageFingerprint(
        width=w,
        height=h,
        top_bar_color=_color_name(_mean_rgb(top_pixels)),
        background_color=_color_name(_mean_rgb(body_pixels)),
        red_ratio_top=red_ratio_top,
        orange_ratio_top=orange_ratio_top,
        dark_ratio_top=dark_ratio_top,
        light_ratio_total=light_ratio_total,
        layout=layout,
        visible_keywords=(),
    )


def _classify_core(fp: ImageFingerprint) -> tuple[Platform, float, list[str]]:
    signals: list[str] = []
    if fp.layout == "dense_table":
        signals.append("dense_numeric_table")

    if fp.red_ratio_top >= 0.25:
        signals.append("top_red")
        return "ths", 0.78, signals

    if fp.orange_ratio_top >= 0.2:
        signals.append("top_orange")
        return "eastmoney", 0.72, signals

    if fp.dark_ratio_top >= 0.2:
        signals.append("top_dark_bar")
        return "eastmoney", 0.62, signals

    if fp.light_ratio_total >= 0.75:
        signals.append("light_background")
        return "hk_panda", 0.55, signals

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
