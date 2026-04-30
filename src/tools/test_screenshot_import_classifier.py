from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from src.tools.screenshot_import.classifier import classify_fingerprint, extract_fingerprint


def _image_with_top_bar(path: Path, rgb: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (200, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 200, 50), fill=rgb)
    draw.rectangle((10, 80, 190, 260), outline=(80, 80, 80), width=2)
    image.save(path)


def test_red_top_bar_prefers_ths(tmp_path: Path) -> None:
    path = tmp_path / "ths.png"
    _image_with_top_bar(path, (210, 30, 30))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="auto")
    assert result.platform == "ths"
    assert "top_red" in result.signals


def test_dark_top_bar_can_prefer_eastmoney(tmp_path: Path) -> None:
    path = tmp_path / "em.png"
    _image_with_top_bar(path, (20, 35, 80))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")
    assert result.platform == "eastmoney"
    assert result.screenshot_type == "holding"


def test_forced_platform_sets_platform_but_keeps_confidence(tmp_path: Path) -> None:
    path = tmp_path / "unknown.png"
    _image_with_top_bar(path, (245, 245, 245))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="hk_panda", forced_type="watchlist")
    assert result.platform == "hk_panda"
    assert result.screenshot_type == "watchlist"
    assert 0 <= result.confidence <= 1
