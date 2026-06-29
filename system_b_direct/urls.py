from django.urls import path

from system_b_direct import views

urlpatterns = [
    path("refresh/", views.refresh, name="system-b-refresh"),
    path("jobs/<str:job_id>/", views.job_detail, name="system-b-job-detail"),
    path("stats/", views.stats, name="system-b-stats"),
]
