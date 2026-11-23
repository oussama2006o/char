import base64
import json
import logging
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import Appointment, VerificationQuota, WhatsAppJob
from .scheduling import LOCAL, appointment_link, locked_barber

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


def request_json(url, payload, headers):
    request = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def verify_request(path, values):
    if not all((settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_VERIFY_SERVICE)):
        raise DeliveryError
    credentials = f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}"
    headers = {
        "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        return request_json(
            f"https://verify.twilio.com/v2/Services/{settings.TWILIO_VERIFY_SERVICE}/{path}",
            urlencode(values).encode(), headers,
        )
    except HTTPError as error:
        if path == "VerificationCheck" and error.code == 404:
            return {"status": "expired"}
        raise DeliveryError from error
    except (URLError, OSError, ValueError) as error:
        raise DeliveryError from error


@transaction.atomic
def take_quota(category, identifier, limit, seconds):
    now = timezone.now()
    bucket = int(now.timestamp()) // seconds
    digest = salted_hmac("verification-quota", identifier, algorithm="sha256").hexdigest()
    key = f"{category}:{bucket}:{digest}"
    row, _ = VerificationQuota.objects.select_for_update().get_or_create(
        key=key, defaults={"expires_at": now + timedelta(seconds=seconds)},
    )
    if row.count >= limit:
        return False
    row.count += 1
    row.save(update_fields=["count"])
    return True


def start_code(phone, language):
    if settings.MESSAGING_BACKEND == "console":
        code = settings.DEMO_SMS_CODE or f"{secrets.randbelow(1_000_000):06d}"
        print(f"Local SMS verification code: {code}", flush=True)
        return salted_hmac("local-verification", f"{phone}:{code}").hexdigest()
    verify_request("Verifications", {"To": phone, "Channel": "sms", "Locale": language})
    return ""


def check_code(phone, code, digest):
    if settings.MESSAGING_BACKEND == "console":
        expected = salted_hmac("local-verification", f"{phone}:{code}").hexdigest()
        return secrets.compare_digest(expected, digest)
    return verify_request("VerificationCheck", {"To": phone, "Code": code}).get("status") == "approved"


def send_whatsapp(job):
    if settings.MESSAGING_BACKEND == "console":
        logger.info("Local WhatsApp %s for appointment %s", job.kind, job.appointment_id)
        return WhatsAppJob.Status.SKIPPED
    appointment = job.appointment
    template = settings.WHATSAPP_TEMPLATES.get((job.kind, appointment.language))
    if not all((template, settings.PUBLIC_URL, settings.WHATSAPP_TOKEN,
                settings.WHATSAPP_PHONE_ID, settings.WHATSAPP_API_VERSION)):
        raise DeliveryError
    shop_name = appointment.shop.name
    if appointment.language == "ar":
        shop_name = appointment.shop.name_ar or shop_name
    values = [
        shop_name,
        appointment.start.astimezone(LOCAL).strftime("%d/%m/%Y %H:%M"),
        settings.PUBLIC_URL + appointment_link(appointment),
    ]
    payload = {
        "messaging_product": "whatsapp", "to": appointment.customer.phone.lstrip("+"),
        "type": "template", "template": {
            "name": template, "language": {"code": appointment.language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": value} for value in values]}],
        },
    }
    result = request_json(
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages",
        json.dumps(payload).encode(),
        {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}", "Content-Type": "application/json"},
    )
    if not result.get("messages"):
        raise DeliveryError
    return WhatsAppJob.Status.SENT


@transaction.atomic
def process_job(job_id):
    barber_id = WhatsAppJob.objects.values_list("appointment__barber_id", flat=True).get(pk=job_id)
    locked_barber(barber_id)
    job = WhatsAppJob.objects.select_for_update(of=("self",)).select_related(
        "appointment", "appointment__shop", "appointment__customer",
    ).get(pk=job_id)
    now = timezone.now()
    if job.status != WhatsAppJob.Status.PENDING or job.due_at > now:
        return
    appointment = job.appointment
    if job.kind == WhatsAppJob.Kind.REMINDER:
        valid = (
            appointment.status == Appointment.Status.CONFIRMED
            and appointment.whatsapp_reminder and appointment.start == job.scheduled_start
            and now < appointment.start
        )
    else:
        valid = appointment.status == Appointment.Status.BARBER_CANCELED and appointment.whatsapp_cancellation
    job.status = WhatsAppJob.Status.SKIPPED
    if valid:
        try:
            job.status = send_whatsapp(job)
        except (DeliveryError, URLError, OSError, ValueError) as error:
            logger.warning("WhatsApp job %s failed (%s)", job.pk, type(error).__name__)
            job.status = WhatsAppJob.Status.FAILED
    job.processed_at = timezone.now()
    job.save(update_fields=["status", "processed_at"])


def process_due_jobs():
    ids = list(WhatsAppJob.objects.filter(
        status=WhatsAppJob.Status.PENDING, due_at__lte=timezone.now(),
    ).order_by("due_at").values_list("pk", flat=True)[:100])
    for job_id in ids:
        process_job(job_id)
    VerificationQuota.objects.filter(expires_at__lt=timezone.now()).delete()
    return len(ids)
