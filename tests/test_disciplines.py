import pytest

import disciplines
from disciplines import DISCIPLINES, build_prompt, config_payload, live_cue_prompt, prompt_version, validate_discipline

# Terms from other sports that must not appear in a discipline's own focus or
# "things not to claim" text. Each list is what that sport is NOT about.
FOREIGN_TERMS = {
    "boxing": ["takedown", "clinch", "kick", "knee", "closed guard", "guard retention", "escape", "sweep"],
    "kickboxing": ["takedown", "clinch", "knee", "closed guard", "guard retention", "escape", "sweep", "submission"],
    "muay_thai": ["takedown", "closed guard", "half guard", "cage", "fence", "guard retention", "escape", "sweep"],
    "mma": ["closed guard", "half guard", "bridging", "guard retention", "jiu-jitsu"],
    "bjj": ["punch", "kick", "jab", "uppercut", "cage", "fence", "knee", "combination", "uppercut"],
}


@pytest.mark.parametrize("discipline", list(DISCIPLINES))
def test_prompt_names_its_own_sport_and_its_focus(discipline):
    prompt = build_prompt(discipline, 4, "")
    spec = DISCIPLINES[discipline]
    assert spec["label"] in prompt
    assert spec["focus"] in prompt
    assert spec["avoid"] in prompt


@pytest.mark.parametrize("discipline", list(DISCIPLINES))
def test_discipline_text_contains_no_other_sport_vocabulary(discipline):
    own_text = (DISCIPLINES[discipline]["focus"] + " " + DISCIPLINES[discipline]["avoid"]).lower()
    leaked = [term for term in FOREIGN_TERMS[discipline] if term in own_text]
    assert leaked == [], f"{discipline} text mentions another sport: {leaked}"


@pytest.mark.parametrize("discipline", list(DISCIPLINES))
def test_each_discipline_has_its_own_areas_and_a_live_cue_focus(discipline):
    spec = DISCIPLINES[discipline]
    assert len(spec["areas"]) >= 5
    assert spec["cue_focus"]
    assert discipline in disciplines.DISCIPLINES


def test_all_five_disciplines_are_registered():
    assert set(DISCIPLINES) == {"boxing", "kickboxing", "muay_thai", "mma", "bjj"}


def test_prompt_versions_differ_between_disciplines():
    versions = {prompt_version(d) for d in DISCIPLINES}
    assert len(versions) == len(DISCIPLINES)


def test_prompt_version_is_stable_for_the_same_text():
    assert prompt_version("boxing") == prompt_version("boxing")


def test_editing_a_discipline_changes_its_prompt_version(monkeypatch):
    before = prompt_version("kickboxing")
    monkeypatch.setitem(DISCIPLINES["kickboxing"], "focus", DISCIPLINES["kickboxing"]["focus"] + " Extra.")
    assert prompt_version("kickboxing") != before


def test_prompt_states_frame_labels_athlete_note_and_no_speed_claims():
    prompt = build_prompt("muay_thai", 16, "clinch from the front")
    assert "F16" in prompt
    assert "clinch from the front" in prompt
    assert "speed, power, timing" in prompt
    assert "insufficient" in prompt
    assert "(none given)" in build_prompt("muay_thai", 4, "")


def test_live_cue_prompt_names_the_sport():
    prompt = live_cue_prompt("boxing")
    assert "Boxing coach" in prompt
    assert DISCIPLINES["boxing"]["cue_focus"] in prompt


def test_config_payload_lists_labels_and_areas_for_the_page():
    payload = config_payload()
    assert set(payload) == set(DISCIPLINES)
    assert payload["bjj"]["label"] == "Brazilian Jiu-Jitsu"
    assert "guard_retention" in payload["bjj"]["areas"]


def test_unknown_discipline_is_rejected():
    with pytest.raises(ValueError):
        validate_discipline("wrestling")
    with pytest.raises(ValueError):
        validate_discipline(None)
