"""``python manage.py seed_demo`` — deterministic demo seed (specs/18).

Writes the fixed demo dataset (4 employers, 20 borrowers/loans/benefit
agreements with full solved schedules and elapsed history, the 8 scripted
scenarios) to Firestore, and provisions the 3 pinned demo users with role custom
claims. Emulator-aware via :mod:`common.firestore` / :mod:`firebase_auth`.

Idempotent and re-runnable (deterministic ids, overwriting writes) so the
nightly ``reset-demo`` job self-heals the public demo (specs/18 §18.1).

Flags:
  --skip-users   seed only Firestore data (skip Firebase Auth provisioning)
  --skip-data    provision only demo users (skip the Firestore dataset)
  --password P   override the shared demo password (default: env SEED_DEMO_PASSWORD)

Third-party imports (google.cloud, firebase_admin) are lazy so this module
``py_compile``s in an offline sandbox.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Seed deterministic demo data + demo users into Firestore (specs/18)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-users", action="store_true",
            help="Skip Firebase Auth demo-user provisioning.",
        )
        parser.add_argument(
            "--skip-data", action="store_true",
            help="Skip the Firestore dataset (provision only demo users).",
        )
        parser.add_argument(
            "--password", default=None,
            help="Override the shared demo-user password.",
        )

    def handle(self, *args, **options):
        from common.firestore import get_client

        try:
            client = get_client()
        except Exception as exc:  # noqa: BLE001
            raise CommandError(
                "Could not obtain a Firestore client. Is the emulator running "
                f"and FIRESTORE_EMULATOR_HOST set? ({exc})"
            )

        if not options["skip_data"]:
            from seed.builder import SeedRunner

            self.stdout.write("Seeding Firestore dataset...")
            runner = SeedRunner(client)
            stats = runner.run()
            for key, value in stats.items():
                self.stdout.write(f"  {key:14s}: {value}")
            self.stdout.write(self.style.SUCCESS("Dataset seeded."))

        if not options["skip_users"]:
            from seed import users as seed_users

            password = options["password"] or seed_users.DEFAULT_PASSWORD
            self.stdout.write("Provisioning demo users...")
            try:
                provisioned = seed_users.provision_demo_users(
                    client, password=password
                )
            except Exception as exc:  # noqa: BLE001
                raise CommandError(
                    "Failed to provision demo users. Is the Auth emulator "
                    "running and FIREBASE_AUTH_EMULATOR_HOST set? "
                    f"({exc})"
                )
            for line in provisioned:
                self.stdout.write(f"  {line}")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Provisioned {len(provisioned)} demo users "
                    "(shared password set; not logged)."
                )
            )

        self.stdout.write(self.style.SUCCESS("seed_demo complete."))
