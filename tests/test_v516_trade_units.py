from datetime import datetime, timezone

from src.data.realtime import TencentRealtimeClient


def _line(symbol, fields):
    return f'v_{symbol}="' + "~".join(fields) + '";'


def test_tencent_a_normalizes_lots_and_10k_cny_to_base_units():
    fields = [""] * 39
    fields[0] = "51"
    fields[1] = "安克创新"
    fields[2] = "300866"
    fields[3] = "119.96"
    fields[4] = "118.40"
    fields[5] = "118.40"
    fields[6] = "50345"  # lots / 手
    fields[30] = "20260729161448"
    fields[31] = "1.56"
    fields[32] = "1.32"
    fields[33] = "124.73"
    fields[34] = "117.50"
    fields[35] = "119.96/50345/613010285"  # exact turnover in CNY
    fields[37] = "61301"  # display value in 10k CNY
    parsed = TencentRealtimeClient._parse_line(_line("sz300866", fields), datetime(2026, 7, 29, 8, 15, tzinfo=timezone.utc))
    assert parsed is not None
    _, q = parsed
    assert q["volume"] == 5_034_500
    assert q["amount"] == 613_010_285


def test_tencent_h_keeps_shares_and_hkd_turnover_and_exchange_time():
    fields = [""] * 39
    fields[0] = "100"
    fields[1] = "腾讯控股"
    fields[2] = "00700"
    fields[3] = "466.400"
    fields[4] = "447.200"
    fields[5] = "453.000"
    fields[6] = "36203193.0"  # shares
    fields[30] = "2026/07/29 16:08:22"
    fields[31] = "19.200"
    fields[32] = "4.29"
    fields[33] = "469.400"
    fields[34] = "450.000"
    fields[35] = "466.400"
    fields[37] = "16756065027.552"  # HKD
    parsed = TencentRealtimeClient._parse_line(_line("r_hk00700", fields), datetime(2026, 7, 29, 8, 9, tzinfo=timezone.utc))
    assert parsed is not None
    _, q = parsed
    assert q["volume"] == 36_203_193
    assert q["amount"] == 16_756_065_027.552
    assert q["quote_time"] == "2026-07-29T08:08:22+00:00"
