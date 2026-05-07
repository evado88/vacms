from bootstrap_datepicker_plus.widgets import DatePickerInput
from django import forms

from va_explorer.users.models import User
from va_explorer.vacms.cmsmodels.events import Event


class ScheduleDeathForm(forms.Form):
    id = forms.CharField(
        label="Death ID",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    name = forms.CharField(
        label="DE-03 Name of the deceased",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    dob = forms.CharField(
        label="DE-04 Date of Birth of the deceased",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    sex = forms.CharField(
        label="E-05 Sex of the deceased",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    dod = forms.CharField(
        label="DE-06 Date of death of the deceased",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    interview_scheduled_date = forms.DateField(
        label="VA Interview Scheduled Date", required=True, widget=DatePickerInput()
    )
    va_interview_staff = forms.ModelChoiceField(
        label="VA Interview Staff",
        queryset=User.objects.filter(groups__name="Mortality Surveillance Officer"),
        required=True,
    )
    interview_contact_name = forms.CharField(
        max_length=255, label="VA Interview Contact Name", required=False
    )
    interview_contact_tel = forms.CharField(
        max_length=255, label="VA Interview Contact Phone", required=False
    )
    interview_comments = forms.CharField(
        max_length=255, label="VA Interview Comments", required=False
    )

    #
    def clean_id(self):
        id = self.cleaned_data["id"]
        if not id:
            raise forms.ValidationError("Please choose a date to schedule a VA for")


class VAInterviewStatusForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "va_interview_status",
            "va_not_done_reason",
            "va_not_done_other",
        ]
        widgets = {
            "va_interview_status": forms.Select(
                attrs={"class": "form-select form-select-sm"}
            ),
            "va_not_done_reason": forms.Select(
                attrs={"class": "form-select form-select-sm"}
            ),
            "va_not_done_other": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 3}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["va_not_done_other"].label = "Other reasons (Explain)"

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("va_interview_status")
        reason = cleaned_data.get("va_not_done_reason")
        other_comment = cleaned_data.get("va_not_done_other")

        if status == Event.VAInterviewStatus.NOT_DONE:
            if not reason:
                self.add_error(
                    "va_not_done_reason",
                    "Please select a reason when the interview is not done.",
                )
            elif reason == Event.VANotDoneReason.OTHER and not other_comment:
                self.add_error(
                    "va_not_done_other",
                    "Provide a comment when 'Other' is selected.",
                )
        else:
            cleaned_data["va_not_done_reason"] = None
            cleaned_data["va_not_done_other"] = None

        return cleaned_data
