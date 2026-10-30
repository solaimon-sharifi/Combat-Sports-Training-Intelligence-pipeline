"""Static checks that the Training view and the film room agree. Runs without a browser.

Desktop and phone layouts were checked by hand in the built-in browser; see the Phase 4 notes in README.md.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def element_ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def ids_used(script):
    return set(re.findall(r'\$\("([^"]+)"\)', script))


def test_every_element_the_training_script_looks_up_exists_in_the_page():
    html = read("index.html")
    missing = sorted(ids_used(read("training.js")) - element_ids(html))
    assert missing == []


def test_every_element_the_film_script_looks_up_still_exists_in_the_page():
    html = read("index.html")
    missing = sorted(ids_used(read("app.js")) - element_ids(html))
    assert missing == []


def test_training_script_only_calls_helpers_that_app_script_defines():
    shared = read("app.js")
    training = read("training.js")
    helpers = {"api", "el", "renderReport", "renderLegacy", "loadVideoIntoPlayer", "confirmDialog",
               "deleteReport", "formatClock", "showNotice", "reduceMotion"}
    for name in helpers:
        if re.search(rf"\b{name}\b", training):
            assert re.search(rf"^(async )?function {name}\b|^const {name}\b", shared, re.M), name


def test_training_script_never_writes_markup_from_strings():
    training = read("training.js")
    assert "innerHTML" not in training
    assert "insertAdjacentHTML" not in training
    assert "outerHTML" not in training


def test_both_views_are_wired_into_the_page():
    html = read("index.html")
    assert 'id="trainingView"' in html
    assert 'src="training.js"' in html
    assert 'href="training.css"' in html
    assert 'id="viewFilm"' in html and 'id="viewTraining"' in html


def test_training_script_loads_after_the_film_script():
    html = read("index.html")
    assert html.index('src="app.js"') < html.index('src="training.js"')


def route_pattern(template):
    """Turns a Flask route such as /api/reviews/<int:review_id> into a regex for one concrete path."""
    parts = re.split(r"<[^>]+>", template)
    return re.compile("[^/]+".join(re.escape(part) for part in parts))


def test_every_api_path_the_training_script_calls_is_routed():
    training = read("training.js")
    server = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    declared = [route_pattern(t) for t in re.findall(r'@app\.(?:get|post|delete)\("([^"]+)"', server)]
    # Literal paths and template literals such as `/api/reviews/${reviewId}`; query strings are dropped.
    called = {re.split(r"[?$]", p)[0] for p in re.findall(r'[`"](/api/[^`"]*)', training)}
    assert called
    for path in called:
        probe = path if path.endswith("/") is False else path + "1"
        assert any(pattern.fullmatch(probe) for pattern in declared), path


def test_phone_layout_children_stretch_instead_of_shrinking_to_content():
    """Regression: the capture column was 406px on a 375px phone because the base layout aligned items to start."""
    workspace = read("workspace.css")
    block = workspace[workspace.index(".layout {"):workspace.index("}", workspace.index(".layout {"))]
    assert "align-items: stretch" in block


def test_stored_video_rows_wrap_instead_of_overflowing_a_phone():
    """Regression: stored-video rows and their buttons pushed the page 31px past a 375px phone."""
    player = read("player.css")
    assert "minmax(0, 1fr) auto" in player
    assert "flex-wrap: wrap" in player


def test_training_controls_have_specific_accessible_names():
    """Regression: every Compare checkbox and practice button sounded identical to a screen reader."""
    training = read("training.js")
    assert "Compare the report from" in training
    assert "Log practice day for" in training


def test_training_error_message_is_styled_as_an_error():
    assert '#trainingStatus[data-state="error"]' in read("training.css")
