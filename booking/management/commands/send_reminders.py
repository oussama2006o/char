import time

from django.core.management.base import BaseCommand

from booking.notifications import process_due_jobs


class Command(BaseCommand):
    help = "Process due WhatsApp notifications. Use --watch for a persistent worker."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        try:
            while True:
                processed = process_due_jobs()
                if not options["watch"]:
                    self.stdout.write(str(processed))
                    break
                if processed < 100:
                    time.sleep(15)
        except KeyboardInterrupt:
            return
