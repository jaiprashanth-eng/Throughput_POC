from django.urls import path

from system_a_mq import views

urlpatterns = [
    path("refresh/", views.refresh, name="system-a-refresh"),
    path("jobs/<str:job_id>/", views.job_detail, name="system-a-job-detail"),
    path("stats/", views.stats, name="system-a-stats"),
]
