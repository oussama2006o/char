import re
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.db.models import Exists, OuterRef, Prefetch
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .copy import STRINGS, get_language, phrase
from .forms import parse_date, phone_number
from .models import Appointment, Barber, Customer, Service, Shop
from .notifications import DeliveryError, check_code, start_code, take_quota
from .scheduling import BookingError, LOCAL, available_starts, book, local_now, make_offer, move, read_offer, record


def language_choices(request):
    arabic = get_language(request) == "ar"
    return [(kind, STRINGS[kind][arabic]) for kind in Service.Kind.values]


def public_barbers():
    services = Service.objects.filter(active=True)
    return Barber.objects.filter(
        active=True, account__is_active=True, shop__active=True,
    ).prefetch_related(Prefetch("services", queryset=services))


def public_shops():
    return Shop.objects.filter(active=True)


def featured_shops():
    return public_shops().annotate(has_barbers=Exists(
        Barber.objects.filter(shop_id=OuterRef("pk"), active=True, account__is_active=True),
    )).order_by("-has_barbers", "name")


def search_filters(request, today, kind):
    return {
        "choices": language_choices(request),
        "kind": kind,
        "day": today,
        "today": today,
        "last_day": today + timedelta(days=7),
    }


def home(request):
    return render(request, "booking/home.html", {
        **search_filters(request, local_now().date(), Service.Kind.HAIR),
        "shops": featured_shops()[:3],
    })


def shops(request):
    return render(request, "booking/shops.html", {"shops": featured_shops()})


def shop(request, slug):
    item = get_object_or_404(public_shops(), slug=slug)
    link = reverse("shop", args=[slug])
    share_url = settings.PUBLIC_URL + link if settings.PUBLIC_URL else request.build_absolute_uri(link)
    return render(request, "booking/shop.html", {
        "shop": item, "barbers": public_barbers().filter(shop=item), "share_url": share_url,
    })


def shop_qr(request, slug):
    get_object_or_404(public_shops(), slug=slug)
    link = reverse("shop", args=[slug])
    share_url = settings.PUBLIC_URL + link if settings.PUBLIC_URL else request.build_absolute_uri(link)
    image = qrcode.make(share_url, image_factory=qrcode.image.svg.SvgPathImage)
    output = BytesIO()
    image.save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


@require_POST
def language(request):
    selected = request.POST.get("language")
    if selected in {"ar", "fr"}:
        request.session["language"] = selected
    target = request.POST.get("next")
    if target and target.startswith("/") and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect("home")


def search(request, barber_id=None):
    barber = get_object_or_404(public_barbers(), pk=barber_id) if barber_id else None
    params = request.GET
    kind = params.get("service", Service.Kind.HAIR)
    now = local_now()
    context = {
        **search_filters(request, now.date(), kind),
        "title": "select_time" if barber else "find", "barber": barber, "results": [],
    }
    try:
        day = parse_date(params.get("date", now.date().isoformat()))
        if kind not in Service.Kind.values or day < context["today"] or day > context["last_day"]:
            raise ValueError
    except ValueError:
        context["error"] = "bad_selection"
        return render(request, "booking/search.html", context)
    services = Service.objects.filter(
        kind=kind, active=True, barber__active=True, barber__account__is_active=True, barber__shop__active=True,
    ).select_related("barber__shop")
    if barber:
        services = services.filter(barber=barber)
    for service in services.order_by("barber__shop__name", "barber__name"):
        starts = available_starts(service.barber_id, day, service.duration, now=now)
        if starts:
            context["results"].append((service, [(start, make_offer(service, start)) for start in starts]))
    context["day"] = day
    return render(request, "booking/search.html", context)


def get_customer(request):
    customer_id = request.session.get("customer_id")
    return Customer.objects.filter(pk=customer_id).first() if customer_id else None


def start_verification(request):
    name = request.POST.get("name", "").strip()
    try:
        phone = phone_number(request.POST.get("phone", ""))
        if not 1 <= len(name) <= 100:
            raise ValueError
    except ValueError:
        return "invalid_phone"
    address = request.META.get("REMOTE_ADDR", "unknown")
    if not take_quota("send-ip", address, 30, 900) or not take_quota("send-phone", phone, 4, 900):
        return "slow_down"
    try:
        digest = start_code(phone, get_language(request))
    except DeliveryError:
        return "sms_error"
    request.session["verification"] = {
        "name": name, "phone": phone, "digest": digest,
        "expires": int(timezone.now().timestamp()) + 600,
    }
    return None


def finish_verification(request):
    challenge = request.session.get("verification", {})
    code = request.POST.get("code", "").strip()
    if not challenge or challenge["expires"] <= timezone.now().timestamp():
        request.session.pop("verification", None)
        return "invalid_code"
    if not take_quota("check-phone", challenge["phone"], 8, 900):
        request.session.pop("verification", None)
        return "slow_down"
    try:
        if not re.fullmatch(r"[0-9]{4,10}", code) or not check_code(challenge["phone"], code, challenge["digest"]):
            return "invalid_code"
    except DeliveryError:
        return "sms_error"
    customer, _ = Customer.objects.get_or_create(
        phone=challenge["phone"], defaults={"name": challenge["name"]},
    )
    request.session.pop("verification", None)
    request.session.cycle_key()
    request.session["customer_id"] = customer.pk
    return None


def verification_page(request, token=None):
    service = start = error = None
    if token:
        try:
            service, start = read_offer(token)
        except BookingError as issue:
            messages.error(request, phrase(request, str(issue)))
            return redirect("search")
    customer = get_customer(request)
    next_path = request.GET.get("next", "")
    if not next_path.startswith("/appointments/") or not url_has_allowed_host_and_scheme(
        next_path, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        next_path = ""
    if customer and not token:
        return redirect(next_path or "mine")
    current_page = reverse("checkout", args=[token]) if token else reverse("customer_login")
    if next_path and not token:
        current_page += "?" + urlencode({"next": next_path})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm" and customer and token:
            try:
                item = book(
                    customer, token, get_language(request), "reminder" in request.POST,
                    "cancellation" in request.POST,
                )
            except BookingError as issue:
                messages.error(request, phrase(request, str(issue)))
                return redirect(
                    reverse("barber_search", args=[service.barber_id])
                    + "?" + urlencode({"service": service.kind, "date": start.date().isoformat()})
                )
            messages.success(request, phrase(request, "booked"))
            return redirect("appointment", pk=item.pk)
        if not customer:
            if action == "send":
                error = start_verification(request)
            elif action == "check":
                error = finish_verification(request)
            elif action == "reset":
                request.session.pop("verification", None)
            if not error:
                return redirect(current_page)
    return render(request, "booking/verify.html", {
        "item": service, "start": start, "customer": customer,
        "challenge": request.session.get("verification"), "error": error,
    })


def mine(request):
    customer = get_customer(request)
    if not customer:
        return render(request, "booking/mine.html")
    return render(request, "booking/mine.html", {
        "customer": customer, "items": customer.appointments.select_related("shop", "barber").order_by("-start"),
    })


@require_POST
def customer_logout(request):
    request.session.pop("customer_id", None)
    request.session.pop("verification", None)
    request.session.cycle_key()
    return redirect("home")


def appointment(request, pk):
    customer = get_customer(request)
    if not customer:
        return render(request, "booking/mine.html", {"login_next": request.path})
    item = get_object_or_404(Appointment.objects.select_related("shop", "barber"), customer=customer, pk=pk)
    editable = item.status == Appointment.Status.CONFIRMED and timezone.now() < item.start
    moving = editable and request.GET.get("move") == "1"
    now = local_now()
    last_day = (now + timedelta(days=7)).date()
    day = item.start.astimezone(LOCAL).date()
    error = None
    choices = []
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "cancel":
                record(item, "cancel")
                messages.success(request, phrase(request, "canceled"))
                return redirect("appointment", pk=pk)
            if action == "move":
                moving = editable
                start = datetime.fromisoformat(request.POST.get("start", ""))
                if start.tzinfo is None:
                    raise ValueError
                move(item, start.astimezone(LOCAL))
                messages.success(request, phrase(request, "moved"))
                return redirect("appointment", pk=pk)
        except BookingError as issue:
            error = str(issue)
        except (ValueError, OverflowError):
            error = "slot_gone"
    if moving:
        try:
            day = parse_date(request.GET.get("date", day.isoformat()))
            if not now.date() <= day <= last_day:
                raise ValueError
            choices = [start for start in available_starts(item.barber_id, day, item.duration, exclude=item.pk) if start != item.start]
        except ValueError:
            day = now.date()
            error = "bad_selection"
    return render(request, "booking/appointment.html", {
        "item": item, "editable": editable, "moving": moving, "day": day, "options": choices,
        "today": now.date(), "last_day": last_day, "error": error,
    })
