"""The card in a real browser: no horizontal scroll, stacked table rows on a phone, no empty tile.

Runs headless Chrome over tests/browser/card-harness.html (synthetic data).  Skipped when no Chrome or
Chromium binary exists.  Set CARD_SCREENSHOT_DIR to also save a screenshot of every layout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


HARNESS = Path(__file__).parent / "browser" / "card-harness.html"
BROWSER = next(
    (path for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if (path := shutil.which(name))),
    None,
)
MODES = ["default", "overview", "usage", "bills", "meter"]
# (window width, window height, width of the view the card sits in); a phone is 390 px wide.
VIEWPORTS = {"desktop": (1280, 900, 1264), "phone": (500, 844, 390)}
TABLE_MODES = {"overview", "bills", "meter"}

pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chrome or Chromium binary on this machine")


def _chrome(url: str, width: int, height: int, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            f"--window-size={width},{height}", "--virtual-time-budget=4000", *extra, url,
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )


def _measure(mode: str, width: int, height: int, view: int, today: str = "2026-10-01") -> dict:
    result = _chrome(f"{HARNESS.as_uri()}?mode={mode}&today={today}&w={view}", width, height, "--dump-dom")
    found = re.search(r'<pre id="measure">(.*?)</pre>', result.stdout, re.S)
    assert found and found.group(1).strip(), f"the harness wrote no measurement ({mode}, {width}px): {result.stderr[-300:]}"
    return json.loads(found.group(1).replace("&quot;", '"').replace("&amp;", "&"))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_a_layout_fits_its_viewport_and_shows_every_tile(mode: str, viewport: str) -> None:
    width, height, view = VIEWPORTS[viewport]
    measured = _measure(mode, width, height, view)
    assert measured["viewWidth"] == view, measured
    assert measured["noHorizontalScroll"], f"{mode} scrolls sideways at {view}px: {measured}"
    assert measured["overflowing"] == 0, f"{mode} has {measured['overflowing']} element(s) past the right edge at {view}px: {measured}"
    assert measured["emptyTiles"] == 0, f"{mode} has an empty tile at {view}px: {measured}"
    if mode == "default":
        assert measured["tileCount"] >= 4
    elif mode != "meter":
        assert measured["tileCount"] >= 4, measured
    if mode in TABLE_MODES:
        assert measured["tableRows"] > 0, measured
        if viewport == "phone":
            assert measured["stackedRows"] == measured["tableRows"], f"{mode} rows are not stacked at {view}px: {measured}"
            assert measured["headerHidden"], measured
        else:
            assert measured["stackedRows"] == 0, f"{mode} rows are stacked on a wide screen: {measured}"


@pytest.mark.parametrize("mode", ["usage", "overview"])
def test_the_first_of_january_layout_still_fits_a_phone(mode: str) -> None:
    measured = _measure(mode, 500, 844, 390, today="2026-01-01")
    assert measured["noHorizontalScroll"] and measured["emptyTiles"] == 0, measured


@pytest.mark.skipif(not os.environ.get("CARD_SCREENSHOT_DIR"), reason="set CARD_SCREENSHOT_DIR to save screenshots")
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_save_screenshots(mode: str, viewport: str) -> None:
    out = Path(os.environ["CARD_SCREENSHOT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    width, height, view = VIEWPORTS[viewport]
    target = out / f"card-{mode}-{viewport}.png"
    _chrome(f"{HARNESS.as_uri()}?mode={mode}&w={view}", width, height, f"--screenshot={target}")
    assert target.exists() and target.stat().st_size > 1000
