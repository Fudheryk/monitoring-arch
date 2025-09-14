import types
from app.application.services.evaluation_service import _match

def T(**kw):  # seuil factice
    return types.SimpleNamespace(**kw)

def test_match_numeric_ops():
    th = T(value_num=2.0, value_bool=None, value_str=None)
    assert _match("gt", "numeric", 3.0, th)
    assert _match("ge", "numeric", 2.0, th)
    assert _match("lt", "numeric", 1.9, th)
    assert _match("le", "numeric", 2.0, th)
    assert _match("eq", "numeric", 2.0, th)
    assert _match("ne", "numeric", 1.0, th)

def test_match_bool_ops():
    th = T(value_num=None, value_bool=True, value_str=None)
    assert _match("eq", "bool", True, th)
    assert _match("ne", "bool", False, th)

def test_match_str_ops():
    th = T(value_num=None, value_bool=None, value_str="ERR")
    assert _match("eq", "string", "ERR", th)
    assert _match("ne", "string", "OK", th)
    assert _match("contains", "string", "SOME ERR MSG", th)
