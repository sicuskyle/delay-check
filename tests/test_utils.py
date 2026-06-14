from delay_check.utils import ms_to_timestamp, ms_to_seconds, sec_to_ms


class TestTimeConversions:
    def test_ms_to_timestamp_positive(self):
        assert ms_to_timestamp(0) == "00:00:00.000"
        assert ms_to_timestamp(1000) == "00:00:01.000"
        assert ms_to_timestamp(60000) == "00:01:00.000"
        assert ms_to_timestamp(3600000) == "01:00:00.000"
        assert ms_to_timestamp(3661000) == "01:01:01.000"
        assert ms_to_timestamp(1500) == "00:00:01.500"
        assert ms_to_timestamp(1234567) == "00:20:34.567"

    def test_ms_to_timestamp_negative(self):
        assert ms_to_timestamp(-1000) == "-00:00:01.000"
        assert ms_to_timestamp(-1500) == "-00:00:01.500"
        assert ms_to_timestamp(-3661000) == "-01:01:01.000"

    def test_ms_to_timestamp_big_values(self):
        assert ms_to_timestamp(90061000) == "25:01:01.000"

    def test_ms_to_seconds(self):
        assert ms_to_seconds(0) == 0.0
        assert ms_to_seconds(1000) == 1.0
        assert ms_to_seconds(1500) == 1.5
        assert ms_to_seconds(100) == 0.1

    def test_sec_to_ms(self):
        assert sec_to_ms(0) == 0
        assert sec_to_ms(1) == 1000
        assert sec_to_ms(1.5) == 1500
        assert sec_to_ms(0.1) == 100

    def test_round_trip(self):
        for val in [0, 1, 100, 1500, 3600000]:
            assert sec_to_ms(ms_to_seconds(val)) == val
