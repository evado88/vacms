"""VASA (Verbal + Social Autopsy) mapping and field-definition artifacts.

The VASA ODK form (``znphi_vasa_who_2016``) bundles four WHO instruments
(Neonate / Child / Adult, with Maternal nested under Adult) plus social-autopsy
questions and three care-seeking repeat groups. Its survey uses VASA pseudo-names
(``Q1xxx`` / ``N2xxx`` / ``C3xxx`` / ``A4xxx`` / helper calcs) in the ``name``
column and moves the original WHO name to a ``WHO_name`` column.

This package holds:

* :mod:`variant_rules` — the hand-authored, explicit rule table that resolves
  every non-standard ``WHO_name`` variant (unit/duration helpers, ``z`` sentinels,
  ``_check`` fields, suffixed splits, combined names, junk). Pure functions, no
  Django dependency, unit-tested against every variant class.
* :mod:`field_map` — GENERATED. The canonical pseudo-name -> WHO-field map,
  instrument-section index, and repeat-group membership consumed by ingestion.
* :mod:`model_fields` — GENERATED. Typed field definitions for the
  ``VASASocialAutopsy`` model and the three care-seeking repeat models.

Regenerate the two generated modules with::

    python manage.py generate_vasa_mapping

The generator is idempotent; ``--check`` verifies the committed artifacts still
match the current XLSForm without writing.
"""
