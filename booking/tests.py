from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import Appointment, Barber, BarberNotice, Customer, DateHours, Service, Shop, WeeklyHours, WhatsAppJob
from .notifications import DeliveryError, process_due_jobs
from .scheduling import BookingError, LOCAL, available_starts, book, make_offer, move, record

NOW = datetime(2026, 11, 23, 9, tzinfo=LOCAL)
START = NOW.replace(hour=11)


def make_barber(name, shop):
    account = get_user_model().objects.create_user(username=name)
    barber = Barber.objects.create(name=name, shop=shop, account=account, active=True)
    for weekday in range(7):
        WeeklyHours.objects.create(barber=barber, weekday=weekday, windows=[[540, 1080]])
    return Service.objects.create(barber=barber, kind=Service.Kind.HAIR, price=700, duration=30)


def make_shop(name):
    return Shop.objects.create(name=name, slug=name.lower(), neighborhood="Centre", address="Isser")


@override_settings(MESSAGING_BACKEND="console", DEMO_SMS_CODE="123456")
class BookingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.shop = make_shop("Studio")
        cls.service = make_barber("Amine", cls.shop)
        cls.other = make_barber("Yacine", cls.shop)
        cls.customer = Customer.objects.create(name="Karim", phone="+213555000001")

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        self.now = clock.start()
        self.addCleanup(clock.stop)

    def reserve(self, start=START, reminder=False, cancellation=False, service=None):
        return book(self.customer, make_offer(service or self.service, start), "fr", reminder, cancellation)

    def test_time_grid_horizon_and_entire_service(self):
        barber_id = self.service.barber_id
        starts = available_starts(barber_id, NOW.date(), 30)
        self.assertEqual(starts[0], NOW + timedelta(minutes=15))
        self.assertEqual(starts[-1].hour, 17)
        self.assertEqual(starts[-1].minute, 30)
        self.assertEqual(available_starts(barber_id, NOW.date() + timedelta(days=7), 30), [NOW + timedelta(days=7)])
        for start in [NOW + timedelta(minutes=14), START + timedelta(minutes=1), START.replace(hour=17, minute=45), NOW + timedelta(days=7, minutes=15)]:
            with self.subTest(start=start), self.assertRaises(BookingError):
                self.reserve(start)

    def test_overlap_and_boundaries(self):
        first = self.reserve()
        with self.assertRaises(BookingError):
            self.reserve(START + timedelta(minutes=15))
        self.assertEqual(self.reserve(START + timedelta(minutes=30)).start, first.end)
        self.assertEqual(self.reserve(service=self.other).start, first.start)

    def test_override_keeps_booking_and_marks_affected(self):
        item = self.reserve()
        self.client.force_login(self.service.barber.account)
        self.client.post(reverse("barber_schedule"), {
            "action": "override", "date": str(START.date()), "windows": "",
        })
        current = self.client.get(reverse("barber_today"))
        self.assertContains(current, "Rendez-vous hors disponibilités")
        self.assertEqual(current.context["affected"][0], item)
        self.assertEqual(available_starts(item.barber_id, START.date(), 30), [])
        item.refresh_from_db()
        self.assertEqual(item.status, Appointment.Status.CONFIRMED)

    def test_move_preserves_slot_on_failure_and_replaces_reminder_on_success(self):
        first = self.reserve(reminder=True)
        self.reserve(START + timedelta(hours=1))
        reminder = first.whatsapp_jobs.get()
        with self.assertRaises(BookingError):
            move(first, START + timedelta(hours=1))
        first.refresh_from_db()
        self.assertEqual(first.start, START)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, WhatsAppJob.Status.PENDING)
        move(first, START + timedelta(hours=2))
        first.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(first.start, START + timedelta(hours=2))
        self.assertEqual(reminder.status, WhatsAppJob.Status.SKIPPED)
        active = first.whatsapp_jobs.get(status=WhatsAppJob.Status.PENDING)
        self.assertEqual(active.due_at, START + timedelta(hours=1))

    def test_customer_cancellation_releases_slot_and_notifies_barber(self):
        item = self.reserve(reminder=True)
        record(item, "cancel")
        self.assertTrue(BarberNotice.objects.filter(appointment=item).exists())
        self.assertEqual(item.whatsapp_jobs.get().status, WhatsAppJob.Status.SKIPPED)
        self.assertEqual(self.reserve().start, START)

    def test_barber_cancellation_updates_even_when_whatsapp_fails(self):
        item = self.reserve(cancellation=True)
        with patch("booking.notifications.send_whatsapp", side_effect=DeliveryError):
            with self.captureOnCommitCallbacks(execute=True):
                record(item, "cancel", by_barber=True)
        item.refresh_from_db()
        self.assertEqual(item.status, Appointment.Status.BARBER_CANCELED)
        self.assertEqual(item.whatsapp_jobs.get().status, WhatsAppJob.Status.FAILED)

    def test_reminder_opt_in_cutoff_and_delivery(self):
        opted_out = self.reserve()
        short_notice = self.reserve(NOW + timedelta(minutes=30), reminder=True)
        eligible = self.reserve(START + timedelta(minutes=45), reminder=True)
        self.assertFalse(opted_out.whatsapp_jobs.exists())
        self.assertFalse(short_notice.whatsapp_jobs.exists())
        with patch("booking.notifications.send_whatsapp", return_value=WhatsAppJob.Status.SENT) as send:
            self.assertEqual(process_due_jobs(), 0)
            self.now.return_value = eligible.start - timedelta(hours=1)
            self.assertEqual(process_due_jobs(), 1)
            self.assertEqual(process_due_jobs(), 0)
        send.assert_called_once()
        self.assertEqual(eligible.whatsapp_jobs.get().status, WhatsAppJob.Status.SENT)

    def test_staff_scope_and_pilot_count(self):
        item = self.reserve()
        self.client.force_login(self.other.barber.account)
        self.assertEqual(self.client.get(reverse("barber_appointment", args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("report")).status_code, 302)
        self.now.return_value = START + timedelta(minutes=4)
        with self.assertRaises(BookingError):
            record(item, "no_show", by_barber=True)
        record(item, "attended", by_barber=True)
        with self.assertRaises(BookingError):
            record(item, "attended", by_barber=True)
        admin = get_user_model().objects.create_superuser(username="pilot-admin")
        self.client.force_login(admin)
        totals = self.client.get(reverse("report")).context["totals"]
        self.assertEqual(totals, {"booked": 1, "confirmed": 0, "attended": 1, "no_show": 0, "canceled": 0})

    def test_five_minute_no_show_and_customer_cancellation_cutoff(self):
        item = self.reserve()
        self.now.return_value = START
        with self.assertRaises(BookingError):
            record(item, "cancel")
        self.now.return_value = START + timedelta(minutes=5)
        record(item, "no_show", by_barber=True)
        item.refresh_from_db()
        self.assertEqual(item.status, Appointment.Status.NO_SHOW)

    def test_stale_offer_after_price_change(self):
        token = make_offer(self.service, START)
        Service.objects.filter(pk=self.service.pk).update(price=800)
        with self.assertRaises(BookingError):
            book(self.customer, token, "fr", False, False)

    def test_checkout_verification_switch_language_and_ownership(self):
        token = make_offer(self.service, START)
        url = reverse("checkout", args=[token])
        self.assertContains(self.client.get(reverse("search")), "Amine")
        self.assertContains(self.client.get(reverse("shop", args=[self.shop.slug])), "Amine")
        self.client.post(reverse("language"), {"language": "ar", "next": url})
        self.assertContains(self.client.get(url), 'dir="rtl"')
        self.client.post(url, {"action": "send", "name": "سمير", "phone": "0555000002"})
        self.assertContains(self.client.post(url, {"action": "check", "code": "000000"}), "الكود خاطئ")
        self.client.post(url, {"action": "check", "code": "123456"})
        self.assertRedirects(
            self.client.post(url, {"action": "confirm"}),
            reverse("appointment", args=[Appointment.objects.get().pk]),
        )
        self.assertEqual(Appointment.objects.get().customer.phone, "+213555000002")
        other_item = self.reserve(START + timedelta(hours=1))
        self.assertEqual(self.client.get(reverse("appointment", args=[other_item.pk])).status_code, 404)

    def test_qr_response_and_closed_date(self):
        response = self.client.get(reverse("shop_qr", args=[self.shop.slug]))
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)
        DateHours.objects.create(barber_id=self.service.barber_id, date=NOW.date(), windows=[])
        self.assertEqual(available_starts(self.service.barber_id, NOW.date(), self.service.duration), [])


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL for live database concurrency")
class PostgreSQLBookingTests(TransactionTestCase):
    def setUp(self):
        self.service = make_barber("Amine", make_shop("Studio"))
        self.customer = Customer.objects.create(name="Karim", phone="+213555000001")

    def test_two_simultaneous_bookings_and_database_constraint(self):
        tokens = [make_offer(self.service, START + timedelta(minutes=offset)) for offset in (0, 15)]
        ready = Barrier(2)

        def attempt(token):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                book(self.customer, token, "fr", False, False)
                return "confirmed"
            except BookingError:
                return "unavailable"
            finally:
                connections.close_all()

        with patch("django.utils.timezone.now", return_value=NOW), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, tokens)), ["confirmed", "unavailable"])
        item = Appointment.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(
                customer=item.customer, shop=item.shop, barber=item.barber, kind=item.kind,
                price=item.price, duration=item.duration, start=item.start, end=item.end,
            )
