from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "administration"
    # 'administration' (not 'admin') to avoid clashing with django.contrib.admin.
    label = "administration"
