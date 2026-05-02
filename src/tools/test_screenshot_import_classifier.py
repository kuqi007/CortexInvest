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
    assert len(result.candidate_platforms) >= 1
    assert result.candidate_platforms[0].platform == result.platform
    assert result.candidate_platforms[0].confidence == result.confidence


def test_red_top_bar_light_theme_with_blue_text_prefers_ths(tmp_path: Path) -> None:
    path = tmp_path / "ths_light_red_blue.png"
    image = Image.new("RGB", (240, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 240, 60), fill=(235, 35, 45))
    for y in range(90, 300, 40):
        draw.rectangle((20, y, 220, y + 10), fill=(35, 115, 190))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "ths"
    assert result.confidence == 0.82
    assert "blue_text" in result.signals


def test_red_top_bar_light_theme_with_green_text_prefers_ths_watchlist(tmp_path: Path) -> None:
    path = tmp_path / "ths_watchlist.png"
    image = Image.new("RGB", (240, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 240, 60), fill=(235, 35, 45))
    for y in range(90, 300, 40):
        draw.rectangle((20, y, 120, y + 10), fill=(40, 40, 40))
        draw.rectangle((145, y, 220, y + 10), fill=(20, 170, 60))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="auto")

    assert result.platform == "ths"
    assert result.screenshot_type == "watchlist"
    assert result.confidence == 0.82
    assert "watchlist_rows" in result.signals


def test_dark_top_bar_can_prefer_eastmoney(tmp_path: Path) -> None:
    path = tmp_path / "em.png"
    _image_with_top_bar(path, (20, 35, 80))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")
    assert result.platform == "eastmoney"
    assert result.screenshot_type == "holding"


def test_orange_red_top_bar_prefers_eastmoney(tmp_path: Path) -> None:
    path = tmp_path / "em_orange.png"
    _image_with_top_bar(path, (245, 80, 10))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")
    assert result.platform == "eastmoney"
    assert result.confidence == 0.82
    assert "top_orange" in result.signals


def test_dark_theme_with_red_blue_text_prefers_ths(tmp_path: Path) -> None:
    path = tmp_path / "ths_dark.png"
    image = Image.new("RGB", (240, 360), (5, 8, 16))
    draw = ImageDraw.Draw(image)
    for y in range(80, 300, 40):
        draw.rectangle((20, y, 90, y + 10), fill=(230, 35, 35))
        draw.rectangle((130, y, 220, y + 10), fill=(35, 95, 230))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "ths"
    assert result.screenshot_type == "holding"
    assert "dark_theme" in result.signals
    assert "red_blue_text" in result.signals


def test_dark_theme_with_only_blue_text_does_not_prefer_ths(tmp_path: Path) -> None:
    path = tmp_path / "blue_only_dark.png"
    image = Image.new("RGB", (240, 360), (5, 8, 16))
    draw = ImageDraw.Draw(image)
    for y in range(80, 300, 40):
        draw.rectangle((20, y, 220, y + 10), fill=(35, 95, 230))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "eastmoney"
    assert "red_blue_text" not in result.signals


def test_dark_theme_with_white_green_red_text_prefers_eastmoney(tmp_path: Path) -> None:
    path = tmp_path / "eastmoney_dark.png"
    image = Image.new("RGB", (240, 360), (5, 8, 16))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 240, 60), fill=(20, 35, 80))
    for y in range(80, 300, 40):
        draw.rectangle((20, y, 105, y + 10), fill=(210, 214, 220))
        draw.rectangle((120, y, 220, y + 10), fill=(15, 220, 45))
    draw.rectangle((160, 310, 220, 320), fill=(230, 35, 35))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "eastmoney"
    assert result.confidence == 0.82
    assert "dark_theme" in result.signals
    assert "white_green_red_text" in result.signals


def test_dark_body_background_is_enough_for_dark_mode(tmp_path: Path) -> None:
    path = tmp_path / "eastmoney_dark_body.png"
    image = Image.new("RGB", (240, 360), (235, 235, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 240, 60), fill=(20, 35, 80))
    draw.rectangle((0, 72, 240, 360), fill=(5, 8, 16))
    for y in range(100, 300, 40):
        draw.rectangle((20, y, 105, y + 10), fill=(210, 214, 220))
        draw.rectangle((120, y, 220, y + 10), fill=(15, 220, 45))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert fp.background_color == "dark_blue_or_black"
    assert result.platform == "eastmoney"
    assert "dark_theme" in result.signals


def test_white_background_with_minimal_top_chrome_prefers_hk_panda(tmp_path: Path) -> None:
    path = tmp_path / "panda_light.png"
    image = Image.new("RGB", (240, 360), "white")
    draw = ImageDraw.Draw(image)
    for y in range(90, 300, 40):
        draw.rectangle((20, y, 120, y + 10), fill=(20, 20, 20))
        draw.rectangle((150, y, 220, y + 10), fill=(240, 90, 20))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "hk_panda"
    assert result.confidence == 0.82
    assert "minimal_top_chrome" in result.signals


def test_forced_platform_sets_platform_but_keeps_confidence(tmp_path: Path) -> None:
    path = tmp_path / "unknown.png"
    _image_with_top_bar(path, (245, 245, 245))
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="hk_panda", forced_type="watchlist")
    assert result.platform == "hk_panda"
    assert result.screenshot_type == "watchlist"
    assert 0 <= result.confidence <= 1
    assert len(result.candidate_platforms) == 1
    assert result.candidate_platforms[0].platform == "hk_panda"
    assert result.candidate_platforms[0].confidence == 1.0


def test_unknown_signal_yields_multiple_ranked_candidates(tmp_path: Path) -> None:
    path = tmp_path / "unk.png"
    image = Image.new("RGB", (320, 300), (52, 54, 58))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 320, 48), fill=(105, 106, 110))
    draw.rectangle((12, 70, 308, 270), outline=(78, 79, 82), width=2)
    image.save(path)
    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="auto")
    assert result.platform == "unknown"
    assert len(result.candidate_platforms) >= 2
    assert result.candidate_platforms[0].confidence >= result.candidate_platforms[1].confidence


def test_eastmoney_dark_top_bar_with_body_text_boosted_confidence(tmp_path: Path) -> None:
    """EastMoney dark top bar with visible body text should get boosted from 0.62 to 0.75."""
    path = tmp_path / "em_body_text.png"
    image = Image.new("RGB", (240, 360), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    # Dark top bar (eastmoney style)
    draw.rectangle((0, 0, 240, 60), fill=(20, 35, 80))
    # Body: white text rows simulating stock data
    for y in range(100, 300, 35):
        draw.rectangle((15, y, 225, y + 8), fill=(210, 214, 220))
    # Some green and red text (price change columns)
    draw.rectangle((160, 100, 225, 108), fill=(15, 220, 45))
    draw.rectangle((160, 135, 225, 143), fill=(230, 35, 35))
    draw.rectangle((160, 170, 225, 178), fill=(15, 220, 45))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="auto")

    assert result.platform == "eastmoney"
    assert result.confidence >= 0.75
    assert "body_colored_text" in result.signals
    assert "dense_numeric_table" in result.signals


def test_ths_dark_mid_body_red_blue_text_detected(tmp_path: Path) -> None:
    """THS dark mode with both red and blue text in the mid-body table
    region should detect body_colored_text via the red top bar rule."""
    path = tmp_path / "ths_dark_mid.png"
    image = Image.new("RGB", (240, 360), (5, 8, 16))
    draw = ImageDraw.Draw(image)
    # Red top bar (THS characteristic)
    draw.rectangle((0, 0, 240, 55), fill=(210, 30, 30))
    # Mid-body: alternating red and blue text columns (stock data)
    for y in range(100, 300, 35):
        draw.rectangle((20, y, 100, y + 10), fill=(220, 50, 50))
        draw.rectangle((120, y, 220, y + 10), fill=(40, 100, 240))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "ths"
    assert "body_colored_text" in result.signals
    assert "dense_numeric_table" in result.signals
    # Should get higher confidence with body text evidence
    assert result.confidence >= 0.78


def test_eastmoney_dark_mid_body_green_text(tmp_path: Path) -> None:
    """EastMoney dark mode with green text columns in mid-body should
    get 0.82 and mid_body_signals."""
    path = tmp_path / "em_dark_mid.png"
    image = Image.new("RGB", (240, 360), (5, 8, 16))
    draw = ImageDraw.Draw(image)
    # Dark top bar
    draw.rectangle((0, 0, 240, 55), fill=(20, 35, 80))
    # Mid-body: green and white text columns (eastmoney characteristic)
    for y in range(100, 300, 35):
        draw.rectangle((20, y, 130, y + 10), fill=(210, 214, 220))
        draw.rectangle((145, y, 225, y + 10), fill=(15, 220, 45))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "eastmoney"
    assert result.confidence == 0.82
    assert "mid_body_signals" in result.signals
    assert "dense_numeric_table" in result.signals


def test_eastmoney_light_dark_bar_with_colored_body_text(tmp_path: Path) -> None:
    """Light mode EastMoney with dark top bar and colored body text
    should be detected at 0.75 with body_colored_text signal."""
    path = tmp_path / "em_light_dark_bar.png"
    image = Image.new("RGB", (240, 360), "white")
    draw = ImageDraw.Draw(image)
    # Dark top bar
    draw.rectangle((0, 0, 240, 55), fill=(20, 35, 80))
    # Body: green and red price text
    for y in range(100, 300, 35):
        draw.rectangle((20, y, 110, y + 10), fill=(40, 40, 40))
        draw.rectangle((130, y, 225, y + 10), fill=(15, 220, 45))
    draw.rectangle((130, 170, 225, 178), fill=(230, 35, 35))
    image.save(path)

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="watchlist")

    assert result.platform == "eastmoney"
    assert result.confidence >= 0.75
    assert "body_colored_text" in result.signals
