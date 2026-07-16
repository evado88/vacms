from datetime import date, timedelta

import pandas as pd
from django.db.models import F
from pandas.tseries.offsets import DateOffset

from va_explorer.va_data_management.constants import REDACTED_STRING
from va_explorer.va_data_management.utils.date_parsing import (
    get_interview_dates,
    parse_date,
    to_dt,
)

NUM_TABLE_ROWS = 5
VA_DF_FIELDS = [
    "id",
    "Id10023",
    "location",
    "Id10011",
    "Id10012",
    "created",
    "Id10010",
    "Id10011",
]
VA_TABLE_FIELDS = [
    "id",
    "location_id",
    "Id10007",
    "Id10010",
    "Id10017",
    "Id10018",
    "Id10012",
    "Id10011",
    "Id10023",
]
VA_TABLE_ROWS = ["collected", "coded", "uncoded"]
VA_TABLE_COLUMNS = ["24", "1 week", "1 month", "Overall"]
VA_GRAPH_TYPES = VA_TABLE_ROWS


def _trend_window():
    """Anchor dates for the trends window, computed per call.

    These were previously module-level constants, but ``date.today()`` captured
    at import time goes stale in long-lived worker processes (the "last 24
    hours" bucket would forever compare against the process start date) and
    breaks time-travelling tests whose import order varies.
    """
    today = pd.to_datetime(date.today())
    start_month = pd.to_datetime(date(today.year - 1, today.month, 1))
    months = [start_month + DateOffset(months=i) for i in range(12)]
    return today, start_month, months


def empty_va_table():
    table = {}
    table_data = {}

    for col in VA_TABLE_COLUMNS:
        table_data[col] = 0
    for row in VA_TABLE_ROWS:
        table[row] = table_data.copy()

    return table


def empty_graph_data():
    graphs = {}
    _today, _start_month, months = _trend_window()
    x_data = [month.strftime("%Y-%m") for month in months]

    for graph_type in VA_GRAPH_TYPES:
        graphs[graph_type] = {}
        graphs[graph_type]["x"] = x_data.copy()
        graphs[graph_type]["y"] = 12 * [0.0]

    return graphs


def get_context_for_va_table(va_list, user):
    context = []
    for va in va_list:
        causes = list(va.causes.all())
        issues = list(va.coding_issues.all())
        context.append(
            {
                "id": va.id,
                "deceased": f"{va.Id10017} {va.Id10018}",
                "interviewer": va.Id10010,
                "interviewed": (
                    parse_date(va.Id10012) if (va.Id10012 != "dk") else "Unknown"
                ),
                "dod": parse_date(va.Id10023) if (va.Id10023 != "dk") else "Unknown",
                "facility": va.location.name if va.location else "Not Provided",
                "cause": causes[0].cause if causes else "Not Coded",
                "warnings": sum(1 for issue in issues if issue.severity == "warning"),
                "errors": sum(1 for issue in issues if issue.severity == "error"),
            }
        )
    # Usually handled by filter, but not in this case
    for item in context:
        if not user.can_view_pii:
            item["deceased"] = REDACTED_STRING
    return context


def _to_cumulative(values):
    running = 0.0
    cumulative = []
    for value in values:
        running += float(value)
        cumulative.append(running)
    return cumulative


# NOTE: using Id10012 (Interview date) to drive stats/views. submissiondate is
# unreliable or inaccurate due to bulk submissions
def _va_stats_dataframe(queryset, source):
    """Build the VA-stats DataFrame for one model, tagged with its source.

    ``uid`` (``source:id``) keeps VA and VASA rows distinct so id collisions
    across the two tables never cross-contaminate coded/uncoded matching.
    """
    frame = pd.DataFrame(
        queryset.only(*VA_DF_FIELDS)
        .select_related("location")
        .values(
            "id",
            "Id10023",
            "created",
            "Id10011",
            "Id10012",
            name=F("Id10010"),
            facility=F("location__name"),
        )
    )
    if frame.empty:
        return frame
    frame["source"] = source
    frame["uid"] = source + ":" + frame["id"].astype(str)
    return frame


def get_trends_data(user):
    # VASA records are folded into the verbal-autopsy statistics: they are coded
    # through the same pipeline, so collected/coded/uncoded counts and graphs
    # count VA + VASA as one unified population.
    user_vas = user.verbal_autopsies()
    user_vasa = user.vasa_records()
    today, start_month, months = _trend_window()
    va_table = empty_va_table()
    graphs = empty_graph_data()
    issue_list = []
    indeterminate_cod_list = []
    additional_issues = 0
    additional_indeterminate_cods = 0

    if user_vas.count() + user_vasa.count() > 0:
        # Fetch VA base data WITHOUT joining causes — causes__cause JOIN multiplies
        # rows (one per cause per VA), which inflates the DataFrame by 3–5× and
        # causes runaway memory growth on large datasets.
        va_df = pd.concat(
            [
                _va_stats_dataframe(user_vas, "VA"),
                _va_stats_dataframe(user_vasa, "VASA"),
            ],
            ignore_index=True,
        )

        # Fetch coded record UIDs separately (cheap single-column queries, no
        # JOIN blowup), keyed by source so VA/VASA ids never collide.
        coded_uids = {
            f"VA:{pk}"
            for pk in user_vas.filter(causes__isnull=False).values_list("id", flat=True)
        } | {
            f"VASA:{pk}"
            for pk in user_vasa.filter(causes__isnull=False).values_list(
                "id", flat=True
            )
        }
        va_df["is_coded"] = va_df["uid"].isin(coded_uids)

        va_df["date"] = get_interview_dates(va_df)

        # clean date fields - strip timezones from Id10012 (Interview date
        # created dates
        va_df["date"] = to_dt(va_df["date"])
        va_df["created"] = to_dt(va_df["created"])
        va_df["Id10023"] = to_dt(va_df["Id10023"])
        va_df["yearmonth"] = va_df["date"].dt.strftime("%Y-%m")

        # Load the VAs that are collected over various periods of time
        vas_24_hours = va_df[va_df["date"] == today].index
        vas_1_week = va_df[va_df["date"] >= (today - timedelta(days=7))].index
        vas_1_month = va_df[va_df["date"] >= (today - DateOffset(months=1))].index
        vas_overall = va_df.sort_values(by="id").index

        # Graphs of the past 12 months, not including this month
        # (current month will almost always show month with artificially low numbers)
        x = [month.strftime("%b") for month in months]
        yearmonths = [month.strftime("%Y-%m") for month in months]
        plot_df = pd.DataFrame({"yearmonth": yearmonths, "x": x})

        # Collected; total VAs by month
        plot_df = plot_df.merge(
            va_df.query("date >= @start_month")
            .groupby("yearmonth")["id"]
            .count()
            .rename("y_collected")
            .reset_index(),
            how="left",
        ).fillna(0)

        collected_monthly = plot_df["y_collected"].astype(float).values.tolist()
        graphs["collected"]["x"] = plot_df["x"].values.tolist()
        graphs["collected"]["y"] = _to_cumulative(collected_monthly)

        # Coded; same query as above, just filtered by whether the va has been coded
        plot_df = plot_df.merge(
            va_df.query("date >= @start_month & is_coded")
            .groupby("yearmonth")["id"]
            .count()
            .rename("y_coded")
            .reset_index(),
            how="left",
        ).fillna(0)

        coded_monthly = plot_df["y_coded"].astype(float).values.tolist()
        graphs["coded"]["x"] = plot_df["x"].values.tolist()
        graphs["coded"]["y"] = _to_cumulative(coded_monthly)

        # Uncoded; just take the difference between the two previous queries
        uncoded_monthly = (
            (plot_df["y_collected"] - plot_df["y_coded"]).astype(float).values.tolist()
        )
        graphs["uncoded"]["x"] = plot_df["x"].values.tolist()
        graphs["uncoded"]["y"] = _to_cumulative(uncoded_monthly)

        # VAs successfully coded in the past 24 hours, 1 week, and 1 month
        vas_coded_24_hours = int(va_df.loc[vas_24_hours, "is_coded"].sum())
        vas_coded_1_week = int(va_df.loc[vas_1_week, "is_coded"].sum())
        vas_coded_1_month = int(va_df.loc[vas_1_month, "is_coded"].sum())
        vas_coded_overall = int(va_df.loc[vas_overall, "is_coded"].sum())

        va_table["collected"]["24"] = len(vas_24_hours)
        va_table["collected"]["1 week"] = len(vas_1_week)
        va_table["collected"]["1 month"] = len(vas_1_month)
        va_table["collected"]["Overall"] = len(vas_overall)
        va_table["coded"]["24"] = vas_coded_24_hours
        va_table["coded"]["1 week"] = vas_coded_1_week
        va_table["coded"]["1 month"] = vas_coded_1_month
        va_table["coded"]["Overall"] = vas_coded_overall
        va_table["uncoded"]["24"] = len(vas_24_hours) - vas_coded_24_hours
        va_table["uncoded"]["1 week"] = len(vas_1_week) - vas_coded_1_week
        va_table["uncoded"]["1 month"] = len(vas_1_month) - vas_coded_1_month
        va_table["uncoded"]["Overall"] = len(vas_overall) - vas_coded_overall

        # Use a local scope copy of VA_TABLE_FIELDS to avoid modifying the
        # original which would impact all future requests made by any user and
        # cause a ValueError: list.remove(x): x not in list
        user_va_table_fields = list(VA_TABLE_FIELDS)
        if user.is_fieldworker():
            user_va_table_fields.remove("Id10007")

        # List the VAs (and VASA records) that need attention; requesting
        # certain fields and refetching makes this more efficient.
        def _attention_list(filter_kwargs):
            combined = list(
                user_vas.only(*user_va_table_fields)
                .filter(**filter_kwargs)[:NUM_TABLE_ROWS]
                .prefetch_related("causes", "coding_issues", "location")
            ) + list(
                user_vasa.only(*user_va_table_fields)
                .filter(**filter_kwargs)[:NUM_TABLE_ROWS]
                .prefetch_related("causes", "coding_issues", "location")
            )
            return combined[:NUM_TABLE_ROWS]

        issue_list = get_context_for_va_table(
            _attention_list({"causes__isnull": True}), user
        )
        indeterminate_cod_list = get_context_for_va_table(
            _attention_list({"causes__cause": "Indeterminate"}), user
        )

        # If there are more than NUM_TABLE_ROWS show a link to where
        # the rest can be seen
        additional_issues = max(
            (len(vas_overall) - vas_coded_overall) - NUM_TABLE_ROWS, 0
        )
        indeterminate_total = (
            user_vas.only("id").filter(causes__cause="Indeterminate").count()
            + user_vasa.only("id").filter(causes__cause="Indeterminate").count()
        )
        additional_indeterminate_cods = max(
            indeterminate_total - NUM_TABLE_ROWS,
            0,
        )

    return (
        va_table,
        graphs,
        issue_list,
        indeterminate_cod_list,
        additional_issues,
        additional_indeterminate_cods,
    )
