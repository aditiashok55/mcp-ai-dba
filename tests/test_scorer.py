from agent.schemas import Diagnosis, Finding
from eval.scorer import score_diagnosis


def _diag(text: str) -> Diagnosis:
    return Diagnosis(
        summary=text,
        findings=[
            Finding(
                finding=text,
                evidence=["a"],
                confidence="high",
                recommended_action="do the thing",
            )
        ],
        all_tool_calls=["a"],
    )


def test_all_keywords_matched_scores_1():
    s = score_diagnosis(
        "slow_query",
        ("slow", "sleep"),
        _diag("Detected a slow pg_sleep query"),
    )
    assert s.score == 1.0
    assert s.passed


def test_partial_match_scores_partial():
    s = score_diagnosis(
        "slow_query",
        ("slow", "sleep", "long-running"),
        _diag("Detected a slow query"),
    )
    assert 0 < s.score < 1
    assert s.matched == ["slow"]


def test_missing_diagnosis_scores_zero():
    s = score_diagnosis("slow_query", ("slow",), None)
    assert s.score == 0.0
    assert not s.passed


def test_case_insensitive():
    s = score_diagnosis("x", ("LOCK",), _diag("row-level lock detected"))
    assert s.score == 1.0
