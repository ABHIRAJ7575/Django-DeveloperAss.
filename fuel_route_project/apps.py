import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class FuelRouteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fuel_route_project"

    def ready(self):
        try:
            from services.data_loader import load_fuel_dataset
            from django.conf import settings

            settings.FUEL_DATASET = load_fuel_dataset()
            logger.info("Fuel dataset loaded: %d stops", len(settings.FUEL_DATASET))
        except Exception as exc:
            logger.error("Failed to load fuel dataset at startup: %s", exc)
