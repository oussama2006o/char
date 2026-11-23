from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .copy import DAYS, get_language, phrase
from .forms import ServiceForm, display_windows, parse_date, parse_windows
from .models import Appointment, Barber, BarberNotice, DateHours, Service, Shop, WeeklyHours
from .notifications import take_quota
from .scheduling import BookingError, LATE, LOCAL, fits, local_now, locked_barber, midnight, record, windows_for


def staff_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("report")
        if Barber.objects.filter(account=request.user, active=True).exists():
            return redirect("barber_today")
    error = None
    if request.method == "POST":
        address = request.META.get("REMOTE_ADDR", "unknown")
        if take_quota("staff-ip", address, 50, 900):
            user = authenticate(
                request, username=request.POST.get("username", ""), password=request.POST.get("password", ""),
            )
            if user and (user.is_staff or Barber.objects.filter(account=user, active=True).exists()):
                login(request, user)
                return redirect("report" if user.is_staff else "barber_today")
            error = "invalid_staff"
        else:
            error = "slow_down"
    return render(request, "booking/staff_login.html", {"error": error})


@require_POST
def staff_logout(request):
    logout(request)
    return redirect("home")


def current_barber(request):
    return get_object_or_404(Barber, account=request.user, active=True)


@login_required
def barber_today(request):
    barber = current_barber(request)
    today = local_now().date()
    error = None
    try:
        day = parse_date(request.GET.get("date", today.isoformat()))
        if day >= date.max:
            raise ValueError
    except ValueError:
        day = today
        error = "bad_range"
    boundary = midnight(day)
    items = barber.appointments.filter(start__gte=boundary, start__lt=boundary + timedelta(days=1)).select_related("customer")
    notices = BarberNotice.objects.filter(
        appointment__barber=barber, read_at__isnull=True,
    ).select_related("appointment__customer").order_by("-created_at")
    return render(request, "booking/barber_today.html", {
        "barber": barber, "items": items, "notices": notices,
        "affected": affected_appointments(barber), "day": day, "today": today, "error": error,
    })


@require_POST
@login_required
def read_notice(request, pk):
    barber = current_barber(request)
    BarberNotice.objects.filter(pk=pk, appointment__barber=barber, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("barber_today")


@login_required
def barber_appointment(request, pk):
    barber = current_barber(request)
    item = get_object_or_404(barber.appointments.select_related("customer", "shop"), pk=pk)
    if request.method == "POST":
        try:
            record(item, request.POST.get("action"), by_barber=True)
            messages.success(request, phrase(request, "recorded"))
            return redirect("barber_appointment", pk=pk)
        except BookingError as issue:
            messages.error(request, phrase(request, str(issue)))
    now = timezone.now()
    return render(request, "booking/barber_appointment.html", {
        "barber": barber, "item": item, "can_attend": now >= item.start, "can_no_show": now >= item.start + LATE,
    })


def affected_appointments(barber):
    return [
        item for item in barber.appointments.filter(
            start__gte=timezone.now(), status=Appointment.Status.CONFIRMED,
        ).select_related("customer")
        if not fits(item.start, item.end, windows_for(barber.pk, item.start.astimezone(LOCAL).date()))
    ]


@login_required
def barber_schedule(request):
    barber = current_barber(request)
    error = None
    if request.method == "POST":
        try:
            update_schedule(barber, request.POST)
            messages.success(request, phrase(request, "saved"))
            return redirect("barber_schedule")
        except ValueError:
            error = "bad_hours"
    raw_week = dict(barber.weekly_hours.values_list("weekday", "windows"))
    arabic = get_language(request) == "ar"
    week = [
        (index, names[arabic], request.POST.get(f"day{index}", display_windows(raw_week.get(index, []))))
        for index, names in enumerate(DAYS)
    ]
    exceptions = [(override, display_windows(override.windows)) for override in barber.date_hours.order_by("date")]
    return render(request, "booking/barber_schedule.html", {
        "barber": barber, "week": week, "exceptions": exceptions, "error": error,
        "affected": affected_appointments(barber),
    })


@transaction.atomic
def update_schedule(barber, data):
    locked_barber(barber.pk)
    action = data.get("action")
    if action == "week":
        for index in range(7):
            WeeklyHours.objects.update_or_create(
                barber=barber, weekday=index, defaults={"windows": parse_windows(data.get(f"day{index}", ""))},
            )
        return
    when = parse_date(data.get("date"))
    if action == "override":
        DateHours.objects.update_or_create(
            barber=barber, date=when, defaults={"windows": parse_windows(data.get("windows", ""))},
        )
    elif action == "restore":
        DateHours.objects.filter(barber=barber, date=when).delete()
    else:
        raise ValueError


@login_required
def barber_services(request):
    barber = current_barber(request)
    error = None
    if request.method == "POST":
        kind = request.POST.get("kind")
        if kind not in Service.Kind.values or not save_service(barber, kind, request.POST):
            error = "bad_service"
        else:
            messages.success(request, phrase(request, "saved"))
            return redirect("barber_services")
    services = {item.kind: item for item in barber.services.all()}
    return render(request, "booking/barber_services.html", {
        "barber": barber, "services": [(kind, services.get(kind)) for kind in Service.Kind.values], "error": error,
    })


@transaction.atomic
def save_service(barber, kind, data):
    locked_barber(barber.pk)
    item = barber.services.filter(kind=kind).first() or Service(barber=barber, kind=kind)
    form = ServiceForm(data, instance=item)
    if not form.is_valid():
        return False
    form.save()
    return True


@user_passes_test(lambda user: user.is_active and user.is_staff)
def report(request):
    today = local_now().date()
    first = today.replace(day=1)
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = next_month - timedelta(days=1)
    error = None
    try:
        first = parse_date(request.GET.get("from", first.isoformat()))
        last = parse_date(request.GET.get("to", last.isoformat()))
        if last < first or last >= date.max:
            raise ValueError
    except ValueError:
        first, last = today.replace(day=1), next_month - timedelta(days=1)
        error = "bad_range"
    period = Q(appointments__start__gte=midnight(first), appointments__start__lt=midnight(last + timedelta(days=1)))

    def count(*statuses):
        filters = period & Q(appointments__status__in=statuses) if statuses else period
        return Count("appointments", filter=filters)

    rows = list(Shop.objects.annotate(
        booked=count(), confirmed=count(Appointment.Status.CONFIRMED),
        attended=count(Appointment.Status.ATTENDED), no_show=count(Appointment.Status.NO_SHOW),
        canceled=count(Appointment.Status.CUSTOMER_CANCELED, Appointment.Status.BARBER_CANCELED),
    ))
    totals = {key: sum(getattr(row, key) for row in rows) for key in ("booked", "confirmed", "attended", "no_show", "canceled")}
    return render(request, "booking/report.html", {
        "shops": rows, "totals": totals, "first": first, "last": last, "error": error,
    })
