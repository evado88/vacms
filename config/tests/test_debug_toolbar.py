from unittest.mock import patch

from django.test import RequestFactory, override_settings

from config.settings.base import _show_debug_toolbar


@override_settings(DEBUG=True)
def test_show_debug_toolbar_skips_post_requests():
    request = RequestFactory().post("/users/update/25/")

    with patch("debug_toolbar.middleware.show_toolbar", return_value=True) as mock_show:
        assert _show_debug_toolbar(request) is False

    mock_show.assert_not_called()


@override_settings(DEBUG=True)
def test_show_debug_toolbar_allows_get_requests():
    request = RequestFactory().get("/users/update/25/")

    with patch("debug_toolbar.middleware.show_toolbar", return_value=True) as mock_show:
        assert _show_debug_toolbar(request) is True

    mock_show.assert_called_once_with(request)
