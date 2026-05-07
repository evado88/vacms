import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from faker import Faker


def _load_location_reference() -> Iterable[Dict[str, str]]:
    data_path = (
        Path(__file__).resolve().parent.parent / "data" / "zambia_locations.json"
    )
    with data_path.open() as handle:
        return json.load(handle)


ZAMBIA_LOCATION_CHOICES = list(_load_location_reference())


class FakeDataGenerator:
    """
    Generates deterministic fake data for sensitive values using cached mappings.
    """

    def __init__(self, seed: Optional[int] = None):
        self.seed = seed
        self.random = random.Random(seed)
        self.faker = Faker()
        if seed is not None:
            Faker.seed(seed)

        self._value_cache: Dict[str, Dict[str, str]] = defaultdict(dict)
        self._location_cache: Dict[Tuple[Optional[str], ...], Dict[str, str]] = {}

    def _truncate(self, value: str, max_length: Optional[int]) -> str:
        if max_length and len(value) > max_length:
            return value[:max_length]
        return value

    def _cached_value(self, cache_key: str, original: str, factory) -> str:
        cache = self._value_cache[cache_key]
        if original in cache:
            return cache[original]
        cache[original] = factory()
        return cache[original]

    def fake_location(self, key_tuple: Tuple[Optional[str], ...]) -> Dict[str, str]:
        if not key_tuple:
            key_tuple = (None,)
        if key_tuple not in self._location_cache:
            choice = (
                self.random.choice(ZAMBIA_LOCATION_CHOICES)
                if ZAMBIA_LOCATION_CHOICES
                else {
                    "province": "Lusaka Province",
                    "district": "Lusaka District",
                    "constituency": "Matero",
                    "ward": "Chaisa",
                }
            )
            self._location_cache[key_tuple] = dict(choice)
        return self._location_cache[key_tuple]

    def fake_value(
        self, kind: str, original: Optional[str], max_length: Optional[int] = None
    ) -> Optional[str]:
        if not original:
            return original

        factories = {
            "name_full": lambda: self._truncate(self.faker.name(), max_length),
            "name_first": lambda: self._truncate(self.faker.first_name(), max_length),
            "name_last": lambda: self._truncate(self.faker.last_name(), max_length),
            "facility": lambda: self._truncate(
                f"{self.faker.last_name()} {self.random.choice(['Clinic', 'Health Post', 'Maternity'])}",
                max_length,
            ),
            "phone": lambda: self._truncate(self._fake_phone_number(), max_length),
            "identifier": lambda: self._truncate(
                self._fake_identifier(original), max_length
            ),
            "gps": lambda: self._truncate(self._fake_gps(), max_length),
        }

        if kind not in factories:
            raise ValueError(f"Unsupported fake data kind '{kind}'")

        return self._cached_value(kind, original, factories[kind])

    def _fake_phone_number(self) -> str:
        digits = "".join(str(self.random.randint(0, 9)) for _ in range(7))
        return f"+2607{digits}"

    def _fake_identifier(self, original: str) -> str:
        pattern = "".join(
            "#" if ch.isdigit() else ("?" if ch.isalpha() else ch) for ch in original
        )
        pattern = pattern or "##########"
        value = self.faker.bothify(
            pattern, letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        ).upper()
        return value

    def _fake_gps(self) -> str:
        lat = self.random.uniform(-18.0, -8.0)
        lon = self.random.uniform(22.0, 33.0)
        return f"{lat:.6f}, {lon:.6f}"
