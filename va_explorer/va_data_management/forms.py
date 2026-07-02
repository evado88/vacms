from django import forms

from config.settings.base import DATE_FORMATS
from va_explorer.va_data_management.utils.date_parsing import parse_date

from .constants import (
    DEATH_PII_FIELDS,
    FORM_FIELDS,
    HIDDEN_FIELDS,
    HOUSEHOLD_PII_FIELDS,
    PII_FIELDS,
    PREGNANCY_OUTCOME_PII_FIELDS,
    PREGNANCY_PII_FIELDS,
)
from .models import VASA, Death, Household, Pregnancy, PregnancyOutcome, VerbalAutopsy
from .utils.multi_select import MultiSelectFormField


class VerbalAutopsyForm(forms.ModelForm):
    class Meta:
        model = VerbalAutopsy
        exclude = HIDDEN_FIELDS
        widgets = {}
        # Because (the massive amount of) model fields are textarea by default,
        # we are overriding the display logic via here + mappings in .constants
        for form_field in FORM_FIELDS["text"]:
            widgets[form_field] = forms.TextInput()
        for form_field in FORM_FIELDS["radio"]:
            widgets[form_field] = forms.RadioSelect(
                choices=FORM_FIELDS["radio"][form_field], attrs={"class": "va-check"}
            )
        for form_field in FORM_FIELDS["checkbox"]:
            widgets[form_field] = MultiSelectFormField(
                flat_choices=FORM_FIELDS["checkbox"][form_field]
            ).widget
            widgets[form_field].attrs = {"class": "va-check"}
        for form_field in FORM_FIELDS["dropdown"]:
            widgets[form_field] = forms.Select(
                choices=FORM_FIELDS["dropdown"][form_field]
            )
        for form_field in FORM_FIELDS["number"]:
            widgets[form_field] = forms.NumberInput()
        for form_field in FORM_FIELDS["date"]:
            widgets[form_field] = forms.DateInput()
        for form_field in FORM_FIELDS["time"]:
            widgets[form_field] = forms.TimeInput()
        for form_field in FORM_FIELDS["datetime"]:
            widgets[form_field] = forms.DateTimeInput()
        for form_field in FORM_FIELDS["display"]:
            widgets[form_field] = forms.TextInput(attrs={"readonly": "readonly"})

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        # Handle text/textarea input types
        for _, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"

        if not include_pii:
            for field in PII_FIELDS:
                del self.fields[field]

    # TODO: to display the error msgs properly, we need to use crispy forms in
    # the template for now we will just display the errors at the top of the page
    def clean(self, *args, **kwargs):
        """
        Normal cleanup
        """
        cleaned_data = super().clean(*args, **kwargs)

        if "Id10023" in cleaned_data:
            validate_date_format(self, cleaned_data["Id10023"])

        return cleaned_data


class VASAForm(forms.ModelForm):
    """Read/edit form for the WHO columns of a VASA record.

    VASA shares every WHO ``Idxxxxx`` column with VerbalAutopsy (via
    BaseVerbalAutopsy), so the same ``HIDDEN_FIELDS`` are excluded; the extra
    VASA-only administrative fields are hidden too. Social-autopsy data lives on
    a separate model and is intentionally absent here.
    """

    class Meta:
        model = VASA
        # HIDDEN_FIELDS already covers id/location/cluster/instanceid/etc. plus
        # VA-only names Django silently ignores when absent from VASA.
        exclude = list(HIDDEN_FIELDS) + [
            "instrument",
            "source",
            "key",
            "created",
            "updated",
        ]

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        for _name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"
        if not include_pii:
            for field in PII_FIELDS:
                if field in self.fields:
                    del self.fields[field]


def validate_date_format(form, Id10023):
    """
    Custom form validation for field Id10023, date of death
    """
    # TODO add a date picker to the form so we don't have to check the string format
    if Id10023 != "dk":
        try:
            parse_date(Id10023, strict=True)
        except ValueError:
            form._errors["Id10023"] = form.error_class(
                [
                    f'Field Id10023 must be "DK" if unknown or in one of \
                      following date formats: {list(DATE_FORMATS.values())}'
                ]
            )


class HouseholdForm(forms.ModelForm):
    class Meta:
        model = Household
        fields = "__all__"
        widgets = {}

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        for _name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"
        if not include_pii:
            for field in HOUSEHOLD_PII_FIELDS:
                if field in self.fields:
                    del self.fields[field]


class PregnancyForm(forms.ModelForm):
    class Meta:
        model = Pregnancy
        fields = "__all__"
        widgets = {}

        widgets["PE_07A"] = forms.NumberInput(attrs={"class": "form-control"})
        widgets["PE_22"] = forms.NumberInput(attrs={"class": "form-control"})
        widgets["PE_07"] = forms.DateInput(
            attrs={"type": "date", "class": "form-control"}
        )
        widgets["PE_09A"] = forms.DateInput(
            attrs={"type": "date", "class": "form-control"}
        )
        widgets["PE_10A"] = forms.DateInput(
            attrs={"type": "date", "class": "form-control"}
        )
        widgets["PE_21"] = forms.DateInput(
            attrs={"type": "date", "class": "form-control"}
        )

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        for _name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"
        if not include_pii:
            for field in PREGNANCY_PII_FIELDS:
                if field in self.fields:
                    del self.fields[field]


class PregnancyOutcomeForm(forms.ModelForm):
    class Meta:
        model = PregnancyOutcome
        fields = "__all__"
        widgets = {}

        date_fields = [
            "today",
            "start",
            "PO_05",
            "PO_18",
            "PO_19",
            "PO_24",
            "PO_33",
            "PO_41",
            "PO_44A",
            "PO_49D",
            "end",
        ]
        for field in date_fields:
            widgets[field] = forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            )
        widgets["PO_44A"] = forms.NumberInput(attrs={"class": "form-control"})

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        for _name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"
        if not include_pii:
            for field in PREGNANCY_OUTCOME_PII_FIELDS:
                if field in self.fields:
                    del self.fields[field]


class DeathForm(forms.ModelForm):
    class Meta:
        model = Death
        fields = "__all__"
        widgets = {}

        date_fields = ["today", "start", "date_of_death", "submit_time", "end"]
        for field in date_fields:
            widgets[field] = forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            )
        widgets["age_at_death"] = forms.NumberInput(attrs={"class": "form-control"})

    def __init__(self, *args, **kwargs):
        include_pii = kwargs.pop("include_pii", True)
        super().__init__(*args, **kwargs)
        for _name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "form-control"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = "3"
        if not include_pii:
            for field in DEATH_PII_FIELDS:
                if field in self.fields:
                    del self.fields[field]
