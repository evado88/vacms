import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import SRSClusterLocation

CSV_TEXT = """province,district,constituency,ward,ea,cluster,is_active
Lusaka,Lusaka District,Mandevu,Justine Kabwe,EA 1001,1001,active
"""


def _cluster_csv(tmp_path):
    path = tmp_path / "cluster_map.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


@pytest.mark.django_db
def test_load_repairs_stale_tree_counters_and_is_idempotent(tmp_path):
    root = SRSClusterLocation.add_root(name="Zambia", location_type="country")
    root.add_child(name="Central", location_type="province")
    # Reproduce the production failure: a child occupies slot 0001 while the
    # persisted parent counter incorrectly says the node is a leaf.
    SRSClusterLocation.objects.filter(pk=root.pk).update(numchild=0)
    csv_path = _cluster_csv(tmp_path)

    call_command("load_srs_cluster_locations", str(csv_path))
    count_after_first_load = SRSClusterLocation.objects.count()
    call_command("load_srs_cluster_locations", str(csv_path))

    root.refresh_from_db()
    assert root.numchild == 2
    assert list(root.get_children().values_list("name", flat=True)) == [
        "Central",
        "Lusaka",
    ]
    assert SRSClusterLocation.objects.count() == count_after_first_load
    assert SRSClusterLocation.find_problems() == ([], [], [], [], [])


@pytest.mark.django_db(transaction=True)
def test_load_rolls_back_the_whole_tree_when_an_insert_fails(tmp_path, monkeypatch):
    csv_path = _cluster_csv(tmp_path)
    original_add_child = SRSClusterLocation.add_child

    def fail_on_district(self, **kwargs):
        if kwargs.get("location_type") == "district":
            raise RuntimeError("simulated insert failure")
        return original_add_child(self, **kwargs)

    monkeypatch.setattr(SRSClusterLocation, "add_child", fail_on_district)

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        call_command("load_srs_cluster_locations", str(csv_path))

    assert SRSClusterLocation.objects.count() == 0
