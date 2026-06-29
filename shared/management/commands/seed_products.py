from django.core.management.base import BaseCommand

from shared.models import Product


class Command(BaseCommand):
    help = "Seed Product rows with unique platform_identifiers (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=5000,
            help="Number of products to seed (default: 5000)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        created = 0
        existing = 0

        for i in range(1, count + 1):
            platform_identifier = f"PROD_{i:04d}"
            _, was_created = Product.objects.get_or_create(
                platform_identifier=platform_identifier,
                defaults={"name": f"Product {i}"},
            )
            if was_created:
                created += 1
            else:
                existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {created} created, {existing} already existed "
                f"(target count: {count})"
            )
        )
