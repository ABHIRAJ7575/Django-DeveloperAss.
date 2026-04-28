import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from django.conf import settings

from api.serializers import RouteRequestSerializer
from services.routing_service import get_route
from services.fuel_optimizer import optimize
from services.exceptions import (
    LocationOutsideCONUSError,
    RoutingServiceUnavailableError,
    NoFuelStopInRangeError,
)

logger = logging.getLogger(__name__)


def _get_dataset():
    """Return FUEL_DATASET, loading it on demand if AppConfig.ready() didn't run."""
    dataset = getattr(settings, "FUEL_DATASET", [])
    if not dataset:
        logger.warning("FUEL_DATASET empty at request time — loading now")
        from services.data_loader import load_fuel_dataset
        dataset = load_fuel_dataset()
        settings.FUEL_DATASET = dataset
        logger.info("Lazy-loaded %d fuel stops", len(dataset))
    return dataset


class RouteView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start = serializer.validated_data["start_location"]
        end   = serializer.validated_data["end_location"]

        try:
            route   = get_route(start, end)
            dataset = _get_dataset()
            logger.info("Calling optimize with %d stops, route %.1f miles",
                        len(dataset), route["distance_miles"])
            result  = optimize(route["polyline"], route["distance_miles"], dataset)
        except LocationOutsideCONUSError:
            return Response(
                {"error": "Location must be within the continental USA"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RoutingServiceUnavailableError:
            return Response(
                {"error": "Routing service unavailable"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except NoFuelStopInRangeError:
            return Response(
                {"error": "No fuel stops found within range for this route segment"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        logger.info("Optimizer returned %d stops, cost $%.2f",
                    len(result["fuel_stops"]), result["total_fuel_cost"])

        fuel_stops = [
            {
                "name":         s.get("name", "Unknown"),
                "address":      s.get("address", ""),
                "city":         s.get("city", ""),
                "state":        s.get("state", ""),
                "latitude":     float(s.get("latitude", 0)),
                "longitude":    float(s.get("longitude", 0)),
                "retail_price": float(s.get("retail_price", 0)),
            }
            for s in result["fuel_stops"]
        ]

        return Response({
            "route_polyline":      route["polyline"],
            "fuel_stops":          fuel_stops,
            "total_distance_miles": round(route["distance_miles"], 2),
            "total_fuel_cost":     round(result["total_fuel_cost"], 2),
        }, status=status.HTTP_200_OK)
