import re


def format_csa_display_name(raw_name):
    name = (raw_name or "").strip().replace("_", " ")
    # Drop any trailing numeric sequence, including digits attached to text.
    name = re.sub(r"\d+$", "", name).strip()
    return name or "Unassigned CSA"


def normalize_csa_name_for_join(value):
    text = (value or "").strip().replace("_", " ")
    text = re.sub(r"\s+\d+$", "", text).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text
