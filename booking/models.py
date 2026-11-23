import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Shop(models.Model):
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    slug = models.SlugField(unique=True)
    neighborhood = models.CharField(max_length=120)
    neighborhood_ar = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=250)
    address_ar = models.CharField(max_length=250, blank=True)
    image_url = models.URLField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Barber(models.Model):
    account = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="barbers")
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShopPhoto(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="photos")
    image_url = models.URLField()

    def __str__(self):
        return str(self.shop)


class Service(models.Model):
    class Kind(models.TextChoices):
        HAIR = "hair", "Coupe de cheveux"
        BEARD = "beard", "Taille de barbe"
        COMBO = "combo", "Coupe et barbe"

    barber = models.ForeignKey(Barber, on_delete=models.CASCADE, related_name="services")
    kind = models.CharField(max_length=8, choices=Kind)
    price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    duration = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(720)])
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["barber", "kind"], name="one_service_per_kind"),
            models.CheckConstraint(condition=models.Q(price__gte=1, duration__gte=1, duration__lte=720), name="valid_service_terms"),
        ]
        ordering = ["kind"]


class WeeklyHours(models.Model):
    barber = models.ForeignKey(Barber, on_delete=models.CASCADE, related_name="weekly_hours")
    weekday = models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)])
    windows = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["barber", "weekday"], name="one_schedule_per_weekday")]


class DateHours(models.Model):
    barber = models.ForeignKey(Barber, on_delete=models.CASCADE, related_name="date_hours")
    date = models.DateField()
    windows = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["barber", "date"], name="one_schedule_per_date")]


class Customer(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=16, unique=True)

    def __str__(self):
        return self.name


class Appointment(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmé"
        ATTENDED = "attended", "Présent"
        NO_SHOW = "no_show", "Absent"
        CUSTOMER_CANCELED = "customer_canceled", "Annulé par le client"
        BARBER_CANCELED = "barber_canceled", "Annulé par le barbier"

    OCCUPIED = (Status.CONFIRMED, Status.ATTENDED, Status.NO_SHOW)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="appointments")
    barber = models.ForeignKey(Barber, on_delete=models.PROTECT, related_name="appointments")
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="appointments")
    kind = models.CharField(max_length=8, choices=Service.Kind)
    price = models.PositiveIntegerField()
    duration = models.PositiveSmallIntegerField()
    start = models.DateTimeField()
    end = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status, default=Status.CONFIRMED)
    whatsapp_reminder = models.BooleanField(default=False)
    whatsapp_cancellation = models.BooleanField(default=False)
    language = models.CharField(max_length=2, default="fr")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]
        indexes = [
            models.Index(fields=["barber", "start"]),
            models.Index(fields=["shop", "start"]),
        ]
        constraints = [models.CheckConstraint(condition=models.Q(end__gt=models.F("start")), name="appointment_positive_range")]


class BarberNotice(models.Model):
    appointment = models.OneToOneField(Appointment, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)


class WhatsAppJob(models.Model):
    class Kind(models.TextChoices):
        REMINDER = "reminder"
        CANCELLATION = "cancellation"

    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"
        SKIPPED = "skipped"

    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="whatsapp_jobs")
    kind = models.CharField(max_length=12, choices=Kind)
    status = models.CharField(max_length=8, choices=Status, default=Status.PENDING)
    scheduled_start = models.DateTimeField()
    due_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "due_at"])]


class VerificationQuota(models.Model):
    key = models.CharField(max_length=90, unique=True)
    count = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
