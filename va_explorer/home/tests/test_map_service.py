from va_explorer.home.services.map import _allocate_proportional_gps_limits


def test_gps_limits_are_proportional():
    assert _allocate_proportional_gps_limits([100, 300], 200) == [50, 150]


def test_gps_limits_do_not_starve_nonempty_sources():
    assert _allocate_proportional_gps_limits([1000, 1, 1, 1], 4) == [1, 1, 1, 1]


def test_gps_limits_do_not_exceed_source_capacity():
    assert _allocate_proportional_gps_limits([1, 2], 50) == [1, 2]
