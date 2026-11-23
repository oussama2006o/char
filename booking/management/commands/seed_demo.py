import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from booking.models import Barber, Service, Shop, WeeklyHours


class Command(BaseCommand):
    help = "Create three fictional shops and sample booking availability in a debug database."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo data is for debug databases only.")
        entries = [
            ("studio-28", "Studio 28", "ستوديو 28", "Centre-ville", "وسط المدينة", "Amine", "أمين"),
            ("atelier-nord", "Atelier Nord", "أتيليه نور", "Quartier de la gare", "حي المحطة", "Yacine", "ياسين"),
            ("salon-le-passage", "Salon Le Passage", "صالون الممر", "Route de Bordj Ménaïel", "طريق برج منايل", "Sofiane", "سفيان"),
        ]
        password = os.getenv("DEMO_BARBER_PASSWORD")
        for slug, name, arabic, neighborhood, translated, barber_name, barber_ar in entries:
            shop, _ = Shop.objects.get_or_create(slug=slug, defaults={
                "name": name, "name_ar": arabic, "neighborhood": neighborhood, "neighborhood_ar": translated,
                "address": "Adresse de démonstration", "address_ar": "عنوان تجريبي",
            })
            account, created = get_user_model().objects.get_or_create(username=f"demo-{slug}")
            if created or password:
                account.set_password(password)
                account.save(update_fields=["password"])
            barber, _ = Barber.objects.get_or_create(account=account, defaults={
                "shop": shop, "name": barber_name, "name_ar": barber_ar, "active": True,
            })
            for kind, price, duration in [("hair", 700, 30), ("beard", 400, 15), ("combo", 1000, 45)]:
                Service.objects.get_or_create(barber=barber, kind=kind, defaults={"price": price, "duration": duration})
            for weekday in range(7):
                WeeklyHours.objects.get_or_create(
                    barber=barber, weekday=weekday, defaults={"windows": [[540, 720], [840, 1140]]},
                )
        self.stdout.write(self.style.SUCCESS("Created demo shops and sample availability."))
