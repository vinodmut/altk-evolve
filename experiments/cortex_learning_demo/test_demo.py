"""Check the independent answer grader before spending on agent trials."""

import pytest

from .run import decode_answer, grade

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "case,note,count",
    [
        ("train", "note-204", 3),
        ("a", "note-517", 5),
        ("b", "note-831", 3),
        ("c", "note-692", 6),
        ("long_train", "note-938", 29),
        ("long_a", "note-173", 24),
        ("long_b", "note-465", 38),
        ("long_c", "note-726", 30),
    ],
)
def test_grader_requires_correct_note_and_activity_count(case, note, count):
    assert grade(case, {"note_id": note, "completed": count})["passed"]
    assert not grade(case, {"note_id": note, "completed": count + 2})["passed"]
    assert not grade(case, {"note_id": "note-archive", "completed": count})["passed"]
    assert not grade(case, {"note_id": note, "completed": str(count)})["passed"]


def test_answer_decoder_handles_json_in_model_prose():
    assert decode_answer('The result is:\n```json\n{"note_id":"note-517","completed":5}\n```') == {"note_id": "note-517", "completed": 5}
    assert decode_answer("Five activities are done.") is None
