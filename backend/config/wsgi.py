"""WSGI config for the BenefitServicing Workbench backend (Cloud Run / gunicorn)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
