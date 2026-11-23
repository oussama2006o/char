# Le fauteuil

Mobile-first barbershop booking for Isser, Boumerdès. The customer and barber screens and the pilot report support French and Arabic. See the [product requirements](PRD-Isser-Barber-Booking.md).

## Run locally

Install Python 3.12 or newer, then run these commands in PowerShell from the project folder.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Open [the customer site](http://127.0.0.1:8000/), [staff sign-in](http://127.0.0.1:8000/staff/login/), or [Django administration](http://127.0.0.1:8000/admin/). Debug mode defaults to SQLite and prints SMS codes to the server console. For repeatable local testing, set `DEMO_SMS_CODE` before starting the server.

To populate the local site with three fictional shops, run `manage.py seed_demo`. Optionally set `DEMO_BARBER_PASSWORD` before seeding to enable the sample staff accounts `demo-studio-28`, `demo-atelier-nord`, and `demo-salon-le-passage`. Running the command again with a password updates those demo passwords. Use tomorrow's date if today's booking hours have passed.

## Set up the pilot

1. Set `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` (comma-separated), `PUBLIC_URL` (the public HTTPS origin), and `DATABASE_URL` (a PostgreSQL connection URL). Set `DJANGO_CSRF_TRUSTED_ORIGINS` if you use a separate trusted origin. Behind an HTTPS-terminating reverse proxy that replaces incoming forwarding headers, set `DJANGO_TRUST_PROXY=1`. Use a production WSGI server with `isser.wsgi:application` and run `manage.py migrate` and `manage.py collectstatic --noinput` before serving traffic. The database must have the `btree_gist` extension available. If the migration account cannot install it, ask the database administrator to enable it first.
2. Create the first shop in Django administration, with its French and Arabic details and HTTPS photo URLs. Create non-staff Django users for barbers, assign each user a barber profile and shop, and activate the profile. Each barber signs in to configure their prices, durations, and booking windows. A dated schedule replaces the usual hours for that day. To close only part of a day, enter the remaining bookable windows. Download the QR from the shop page after setting the public URL.
3. Configure Twilio Verify with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_VERIFY_SERVICE`. Enable verification for Algeria. Confirm delivery to an Algerian test number and check sender setup in the provider account. [Twilio Verify API](https://www.twilio.com/docs/verify/api/verification) · [Algeria SMS guidelines](https://www.twilio.com/en-us/guidelines/dz/sms)
4. Configure Meta Cloud API with `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, and `WHATSAPP_API_VERSION` (including the leading `v`). Set the names of four approved utility templates in `WHATSAPP_REMINDER_FR`, `WHATSAPP_REMINDER_AR`, `WHATSAPP_CANCELLATION_FR`, and `WHATSAPP_CANCELLATION_AR`. Each needs three body text parameters in this order: shop name, local appointment date and time, and appointment URL. Checkout lets customers opt in separately to WhatsApp reminders and cancellation notices. [Meta WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/)
5. Run `manage.py send_reminders --watch` as a persistent worker with the same environment and database as the web process. The worker polls every 15 seconds. The web process attempts a barber cancellation notice after committing the cancellation. The worker also processes any pending notices left behind if that process stops. A provider error is recorded without automatically retrying delivery. The cancellation remains visible on the website.
6. Run the two-day test with one shop and verify real bookings, changes, cancellations, attendance, and both messaging channels. Add the other two shops for the free month. The [pilot report](http://127.0.0.1:8000/pilot/) filters by the inclusive local appointment-date range and counts each attended appointment once.

## Tests

Run `python manage.py test` in the environment with the requirements installed. Set `DATABASE_URL` to a development PostgreSQL server to also run the simultaneous-booking and exclusion-constraint test. The PostgreSQL test user needs permission to create the test database and enable `btree_gist` there.

Landing photograph by Tim Mossholder [on Unsplash](https://unsplash.com/photos/man-cutting-the-hair-okvy6zpdifw).
