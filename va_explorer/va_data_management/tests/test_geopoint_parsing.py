"""Unit tests for the parse_geopoint helper."""

import numpy as np
import pytest

from va_explorer.va_data_management.utils.geopoint import parse_geopoint


def test_parse_full_components():
    result = parse_geopoint("-14.5 28.3 1242 5")
    assert result == {
        "latitude": -14.5,
        "longitude": 28.3,
        "altitude": 1242.0,
        "accuracy": 5.0,
    }


def test_parse_lat_lon_only():
    result = parse_geopoint("-14.5 28.3")
    assert result["latitude"] == -14.5
    assert result["longitude"] == 28.3
    assert result["altitude"] is None
    assert result["accuracy"] is None


def test_parse_lat_lon_alt_only():
    result = parse_geopoint("-14.5 28.3 1200")
    assert result["altitude"] == 1200.0
    assert result["accuracy"] is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "nan",
        "null",
        "<NA>",
        np.nan,
        "-14.5",  # only one token
        "garbage data",
        "abc def",
    ],
)
def test_parse_invalid_returns_none(value):
    assert parse_geopoint(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "999 28.3",  # latitude out of range
        "-14.5 200",  # longitude out of range
        "-91 0",  # latitude just out of range
        "0 181",  # longitude just out of range
    ],
)
def test_parse_out_of_range_returns_none(value):
    assert parse_geopoint(value) is None


def test_parse_handles_extra_whitespace():
    assert parse_geopoint("  -14.5   28.3  ") == {
        "latitude": -14.5,
        "longitude": 28.3,
        "altitude": None,
        "accuracy": None,
    }


def test_parse_zero_coordinates_accepted():
    # 0,0 is in the ocean but technically a valid geopoint and shouldn't be rejected.
    assert parse_geopoint("0 0")["latitude"] == 0.0


def test_parse_infinity_rejected():
    assert parse_geopoint("inf 0") is None
    assert parse_geopoint("-inf 0") is None


def test_parse_garbled_altitude_falls_back_to_none():
    result = parse_geopoint("-14.5 28.3 not-a-number 5")
    assert result["latitude"] == -14.5
    assert result["altitude"] is None
    # Accuracy is at index 3 and should still parse.
    assert result["accuracy"] == 5.0
