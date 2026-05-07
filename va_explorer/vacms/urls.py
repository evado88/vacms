# blog/urls.py
from django.urls import path

from va_explorer.vacms.views import (
    EventCreateDeathView,
    EventCreateView,
    EventDetailView,
    EventLinkDataView,
    EventLinkVA,
    EventListCompletedView,
    EventListScheduledView,
    EventListView,
    EventScheduleDataCollectionView,
    EventScheduleVAInterviewView,
    EventUpdateView,
)

urlpatterns = [
    # event
    path("event/list", EventListView.as_view(), name="cms-event-list"),
    path(
        "event/scheduled/list",
        EventListScheduledView.as_view(),
        name="cms-event-scheduled-list",
    ),
    path(
        "event/complete/list",
        EventListCompletedView.as_view(),
        name="cms-event-completed-list",
    ),
    path("event/create", EventCreateView.as_view(), name="cms-event-create"),
    path(
        "event/create-death/<int:death>",
        EventCreateDeathView,
        name="cms-event-death-create",
    ),
    path("event/update/<int:pk>", EventUpdateView.as_view(), name="cms-event-update"),
    path(
        "event/schedule/data-collection/<int:pk>",
        EventScheduleDataCollectionView.as_view(),
        name="cms-event-datacollection-schedule",
    ),
    path(
        "event/schedule/va-interview/<int:pk>",
        EventScheduleVAInterviewView.as_view(),
        name="cms-event-vainterview-schedule",
    ),
    path(
        "event/link-data/<int:pk>",
        EventLinkDataView.as_view(),
        name="cms-event-link",
    ),
    path(
        "event/link-va/<int:pk>",
        EventLinkVA.as_view(),
        name="cms-event-linkva",
    ),
    path("event/detail/<int:pk>", EventDetailView.as_view(), name="cms-event-detail"),
]
