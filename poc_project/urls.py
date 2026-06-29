from django.contrib import admin
from django.urls import include, path

from poc_project.compare_views import compare
from shared.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health-check"),
    path("api/a/", include("system_a_mq.urls")),
    path("api/b/", include("system_b_direct.urls")),
    path("api/compare/", compare, name="compare"),
]
