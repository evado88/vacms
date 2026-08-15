from va_explorer.va_data_management.utils.geopoint import allocate_proportional_limits


def test_gps_limits_are_proportional():
    assert allocate_proportional_limits([100, 300], 200) == [50, 150]


def test_gps_limits_do_not_starve_nonempty_sources():
    assert allocate_proportional_limits([1000, 1, 1, 1], 4) == [1, 1, 1, 1]


def test_gps_limits_do_not_exceed_source_capacity():
    assert allocate_proportional_limits([1, 2], 50) == [1, 2]
