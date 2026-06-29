from django.contrib import admin

from shared.models import JobStatus, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("platform_identifier", "name", "price", "mrp", "last_refreshed_at")
    search_fields = ("platform_identifier", "name")


@admin.register(JobStatus)
class JobStatusAdmin(admin.ModelAdmin):
    list_display = ("job_id", "system", "status", "total", "completed", "failed_count", "wall_time_ms")
    list_filter = ("system", "status")
