# server/tests/unit/domain/test_match_condition.py
import pytest
from types import SimpleNamespace

from app.domain.policies import match_condition

pytestmark = pytest.mark.unit


def TH(**kwargs):
    # Helper: threshold object with expected attrs
    defaults = dict(value_num=None, value_bool=None, value_str=None)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    "cond,sample,th_val,expected",
    [
        ("gt", 10, 5, True),
        ("gt", 5, 10, False),
        ("ge", 10, 10, True),
        ("lt", 4, 5, True),
        ("le", 5, 5, True),
        ("eq", 5, 5, True),
        ("==", 5, 5, True),
        ("ne", 5, 1, True),
        ("!=", 5, 1, True),
        ("unknown", 5, 1, False),
    ],
)
def test_numeric_ops(cond, sample, th_val, expected):
    th = TH(value_num=th_val)
    assert match_condition("numeric", cond, sample, th) is expected


def test_numeric_threshold_none_returns_false():
    th = TH(value_num=None)
    assert match_condition("numeric", "gt", 10, th) is False


def test_numeric_cast_failure_returns_false():
    th = TH(value_num=10)
    assert match_condition("numeric", "gt", "not-a-number", th) is False


@pytest.mark.parametrize(
    "cond,sample,th_val,expected",
    [
        ("eq", True, True, True),
        ("eq", False, True, False),
        ("ne", False, True, True),
        ("!=", True, True, False),
        ("==", True, False, False),
    ],
)
def test_bool_eq_ne(cond, sample, th_val, expected):
    th = TH(value_bool=th_val)
    assert match_condition("bool", cond, sample, th) is expected


def test_bool_threshold_none_returns_false():
    th = TH(value_bool=None)
    assert match_condition("bool", "eq", True, th) is False


@pytest.mark.parametrize(
    "cond,sample,th_val,expected",
    [
        ("eq", "up", "up", True),
        ("ne", "up", "down", True),
        ("contains", "service mysql down", "mysql", True),
        ("contains", "service ok", "mysql", False),
        ("unknown", "x", "x", False),
    ],
)
def test_string_ops(cond, sample, th_val, expected):
    th = TH(value_str=th_val)
    assert match_condition("string", cond, sample, th) is expected


def test_string_contains_with_right_none_returns_false():
    th = TH(value_str=None)
    assert match_condition("string", "mysql is down", "contains", th) is False


def test_condition_is_trimmed_and_case_insensitive():
    th = TH(value_str="OK")
    assert match_condition("string", "  EQ  ", "OK", th) is True
