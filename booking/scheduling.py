from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, Barber, BarberNotice, DateHours, Service, WeeklyHours, WhatsAppJob

LOCAL = ZoneInfo("Africa/Algiers")
STEP = timedelta(minutes=15)
HORIZON = timedelta(days=7)
LATE = timedelta(minutes=5)


class BookingError(Exception):
    pass


def local_now():
    return timezone.now().astimezone(LOCAL)


def midnight(day):
    return datetime.combine(day, datetime.min.time(), LOCAL)


def windows_for(barber_id, day):
    override = DateHours.objects.filter(barber_id=barber_id, date=day).first()
    if override:
        return override.windows
    return WeeklyHours.objects.filter(
        barber_id=barber_id, weekday=day.weekday()
    ).values_list("windows", flat=True).first() or []


def fits(start, end, windows):
    boundary = midnight(start.astimezone(LOCAL).date())
    return any(
        boundary + timedelta(minutes=first) <= start
        and end <= boundary + timedelta(minutes=last)
        for first, last in windows
    )


def eligible(start, now):
    start = start.astimezone(LOCAL)
    return (
        start.minute % 15 == 0 and start.second == start.microsecond == 0
        and now + STEP <= start <= now + HORIZON
    )


def occupied(barber_id, start, end, exclude=None):
    return Appointment.objects.filter(
        barber_id=barber_id, status__in=Appointment.OCCUPIED, start__lt=end, end__gt=start,
    ).exclude(pk=exclude)


def available_starts(barber_id, day, duration, exclude=None, now=None):
    now = now or local_now()
    if day < now.date() or day > (now + HORIZON).date():
        return []
    windows = windows_for(barber_id, day)
    if not windows:
        return []
    boundary = midnight(day)
    conflicts = list(occupied(barber_id, boundary, boundary + timedelta(days=1), exclude).values_list("start", "end"))
    length = timedelta(minutes=duration)
    starts = []
    for first, last in windows:
        start = boundary + timedelta(minutes=((first + 14) // 15) * 15)
        end_of_window = boundary + timedelta(minutes=last)
        while start + length <= end_of_window:
            if eligible(start, now) and all(
                start >= end or start + length <= beginning for beginning, end in conflicts
            ):
                starts.append(start)
            start += STEP
    return starts


def make_offer(service, start):
    return signing.dumps([service.pk, service.price, service.duration, start.isoformat()], salt="appointment-offer")


def read_offer(token):
    try:
        service_id, price, duration, value = signing.loads(token, salt="appointment-offer", max_age=7200)
        start = datetime.fromisoformat(value).astimezone(LOCAL)
        service = Service.objects.select_related("barber__shop", "barber__account").get(
            pk=service_id, active=True, barber__active=True, barber__shop__active=True, barber__account__is_active=True,
        )
    except (signing.BadSignature, ValueError, Service.DoesNotExist):
        raise BookingError("slot_gone") from None
    if (service.price, service.duration) != (price, duration) or not eligible(start, local_now()):
        raise BookingError("slot_gone")
    return service, start


def validate_slot(barber, start, duration, exclude=None):
    end = start + timedelta(minutes=duration)
    if not barber.active or not barber.shop.active or not barber.account.is_active:
        raise BookingError("slot_gone")
    if not eligible(start, local_now()) or not fits(start, end, windows_for(barber.pk, start.date())):
        raise BookingError("slot_gone")
    if occupied(barber.pk, start, end, exclude).exists():
        raise BookingError("slot_gone")
    return end


def schedule_reminder(appointment):
    now = timezone.now()
    clear_reminder(appointment, now)
    due = appointment.start - timedelta(hours=1)
    if appointment.whatsapp_reminder and due >= now:
        appointment.whatsapp_jobs.create(
            kind=WhatsAppJob.Kind.REMINDER, scheduled_start=appointment.start, due_at=due,
        )


def locked_barber(pk):
    return Barber.objects.select_for_update(of=("self",)).select_related("shop", "account").get(pk=pk)


@transaction.atomic
def book(customer, token, language, reminder, cancellation):
    service, start = read_offer(token)
    barber = locked_barber(service.barber_id)
    service, start = read_offer(token)
    end = validate_slot(barber, start, service.duration)
    try:
        with transaction.atomic():
            appointment = Appointment.objects.create(
                customer=customer, barber=barber, shop=barber.shop, kind=service.kind, price=service.price,
                duration=service.duration, start=start, end=end, language=language,
                whatsapp_reminder=reminder, whatsapp_cancellation=cancellation,
            )
    except IntegrityError as error:
        guard_conflict(error)
    schedule_reminder(appointment)
    return appointment


def guard_conflict(error):
    if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "barber_appointment_no_overlap":
        raise BookingError("slot_gone") from error
    raise error


@transaction.atomic
def move(appointment, start):
    barber = locked_barber(appointment.barber_id)
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.status != Appointment.Status.CONFIRMED or appointment.start <= timezone.now():
        raise BookingError("cannot_change")
    if start == appointment.start:
        raise BookingError("slot_gone")
    appointment.end = validate_slot(barber, start, appointment.duration, appointment.pk)
    appointment.start = start
    try:
        with transaction.atomic():
            appointment.save(update_fields=["start", "end"])
    except IntegrityError as error:
        guard_conflict(error)
    schedule_reminder(appointment)


@transaction.atomic
def record(appointment, action, *, by_barber=False):
    locked_barber(appointment.barber_id)
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    now = timezone.now()
    if appointment.status != Appointment.Status.CONFIRMED:
        raise BookingError("cannot_change")
    if action == "cancel" and (by_barber or appointment.start > now):
        appointment.status = Appointment.Status.BARBER_CANCELED if by_barber else Appointment.Status.CUSTOMER_CANCELED
    elif action == "attended" and by_barber and now >= appointment.start:
        appointment.status = Appointment.Status.ATTENDED
    elif action == "no_show" and by_barber and now >= appointment.start + LATE:
        appointment.status = Appointment.Status.NO_SHOW
    else:
        raise BookingError("cannot_change")
    appointment.save(update_fields=["status"])
    clear_reminder(appointment, now)
    if appointment.status == Appointment.Status.CUSTOMER_CANCELED:
        BarberNotice.objects.create(appointment=appointment)
    if appointment.status == Appointment.Status.BARBER_CANCELED and appointment.whatsapp_cancellation:
        from .notifications import process_job

        job = appointment.whatsapp_jobs.create(
            kind=WhatsAppJob.Kind.CANCELLATION, scheduled_start=appointment.start, due_at=now,
        )
        transaction.on_commit(lambda: process_job(job.pk), robust=True)


def clear_reminder(appointment, now):
    appointment.whatsapp_jobs.filter(
        kind=WhatsAppJob.Kind.REMINDER, status=WhatsAppJob.Status.PENDING,
    ).update(status=WhatsAppJob.Status.SKIPPED, processed_at=now)


def appointment_link(appointment):
    return reverse("appointment", args=[appointment.pk])
