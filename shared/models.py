from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=255)
    platform_identifier = models.CharField(max_length=255, unique=True, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    last_refreshed_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["platform_identifier"])]

    def __str__(self):
        return self.platform_identifier


class JobStatus(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("processing", "processing"),
        ("done", "done"),
        ("failed", "failed"),
    ]

    job_id = models.CharField(max_length=64, unique=True, db_index=True)
    system = models.CharField(max_length=8)
    total = models.IntegerField(default=0)
    completed = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    enqueued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    wall_time_ms = models.FloatField(null=True)

    def __str__(self):
        return f"{self.job_id} ({self.status})"
