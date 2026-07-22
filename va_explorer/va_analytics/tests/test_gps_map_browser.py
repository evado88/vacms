import shutil
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from selenium.webdriver import FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from va_explorer.va_data_management.models import PregnancyOutcome


@unittest.skipUnless(
    shutil.which("firefox") and shutil.which("geckodriver"),
    "Firefox and geckodriver are required for dashboard browser tests.",
)
class GPSMapBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        options = FirefoxOptions()
        options.add_argument("--headless")
        cls.selenium = WebDriver(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.selenium.quit()
        super().tearDownClass()

    def test_gps_point_is_visible_and_choropleth_remains_clickable(self):
        password = "MapTestPassword123!"
        user = get_user_model().objects.create_user(
            email="gps-map-browser@example.com",
            password=password,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            has_valid_password=True,
        )
        PregnancyOutcome.objects.create(
            key="browser-gps-outcome",
            province="Lusaka",
            district="Lusaka",
            gps="-15.4167 28.2833",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )

        self.selenium.get(f"{self.live_server_url}{reverse('account_login')}")
        self.selenium.find_element(By.NAME, "login").send_keys(user.email)
        self.selenium.find_element(By.NAME, "password").send_keys(password)
        self.selenium.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

        dashboard_url = reverse("va_analytics:outcomes-dashboard")
        self.selenium.get(
            f"{self.live_server_url}{dashboard_url}?tab=pregnancy_outcomes"
        )
        wait = WebDriverWait(self.selenium, 20)
        point = wait.until(
            lambda driver: driver.find_element(
                By.CSS_SELECTOR,
                "#poMapContainer .dashboard-gps-pane svg path.leaflet-interactive",
            )
        )
        map_element = self.selenium.find_element(By.ID, "poMapContainer")
        point_rect = point.rect
        map_rect = map_element.rect
        point_center_x = point_rect["x"] + point_rect["width"] / 2
        point_center_y = point_rect["y"] + point_rect["height"] / 2

        assert point_rect["width"] > 0
        assert point_rect["height"] > 0
        assert map_rect["x"] <= point_center_x <= map_rect["x"] + map_rect["width"]
        assert map_rect["y"] <= point_center_y <= map_rect["y"] + map_rect["height"]

        polygon = wait.until(
            lambda driver: driver.find_element(
                By.CSS_SELECTOR,
                "#poMapContainer .leaflet-overlay-pane path.leaflet-interactive",
            )
        )
        polygon.click()
        wait.until(
            lambda driver: "Lusaka"
            in driver.find_element(By.ID, "poMapBreadcrumb").text
        )
