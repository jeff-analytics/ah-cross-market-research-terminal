from src.data.realtime import EastmoneyRealtimeClient


def test_realtime_numeric_parser():
    assert EastmoneyRealtimeClient._num("12.34") == 12.34
    assert EastmoneyRealtimeClient._num("-") is None
    assert EastmoneyRealtimeClient._num(None) is None
