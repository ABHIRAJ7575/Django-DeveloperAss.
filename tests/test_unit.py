import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock
import polyline as polyline_lib

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuel_route_project.settings")
import django
django.setup()

from django.test import RequestFactory, Client, override_settings
from django.conf import settings as django_settings

# Allow Django test client's default 'testserver' host
django_settings.ALLOWED_HOSTS = ["*"]


class TestEndpointExists:
    def test_route_endpoint_returns_non_404(self):
        """POST /api/route/ should return something other than 404."""
        client = Client()
        response = client.post(
            "/api/route/",
            data=json.dumps({"start_location": "Chicago, IL", "end_location": "LA, CA"}),
            content_type="application/json",
        )
        assert response.status_code != 404

    def test_root_returns_200_with_html(self):
        """GET / should return 200 with HTML content."""
        client = Client()
        response = client.get("/")
        assert response.status_code == 200
        assert b"<!DOCTYPE html>" in response.content or b"<html" in response.content


class TestRoutingService:
    def test_routing_service_makes_correct_ors_calls(self):
        """RoutingService should call ORS geocode and directions endpoints."""
        from services.routing_service import get_route

        encoded = polyline_lib.encode([(41.85, -87.65), (34.05, -118.24)])

        mock_geocode = MagicMock()
        mock_geocode.raise_for_status = MagicMock()
        mock_geocode.json.return_value = {
            "features": [{"geometry": {"coordinates": [-87.65, 41.85]}}]
        }

        mock_directions = MagicMock()
        mock_directions.raise_for_status = MagicMock()
        mock_directions.json.return_value = {
            "routes": [{"summary": {"distance": 3240000.0}, "geometry": encoded}]
        }

        with patch("requests.get", return_value=mock_geocode) as mock_get, \
             patch("requests.post", return_value=mock_directions) as mock_post:
            result = get_route("Chicago, IL", "Los Angeles, CA")

        # Should have made geocode calls (GET) and directions call (POST)
        assert mock_get.call_count >= 1
        assert mock_post.call_count >= 1
        assert "polyline" in result
        assert "distance_miles" in result

    def test_ors_error_raises_routing_service_unavailable(self):
        """ORS HTTP error should raise RoutingServiceUnavailableError."""
        from services.routing_service import get_route
        from services.exceptions import RoutingServiceUnavailableError
        import requests

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(RoutingServiceUnavailableError):
                get_route("Chicago, IL", "Los Angeles, CA")

    def test_ors_error_returns_502_via_view(self):
        """RoutingServiceUnavailableError should map to 502 response."""
        from services.exceptions import RoutingServiceUnavailableError

        with patch("api.views.get_route", side_effect=RoutingServiceUnavailableError("unavailable")):
            client = Client()
            response = client.post(
                "/api/route/",
                data=json.dumps({"start_location": "Chicago, IL", "end_location": "LA, CA"}),
                content_type="application/json",
            )
        assert response.status_code == 502

    def test_no_fuel_stop_in_range_returns_422(self):
        """NoFuelStopInRangeError should map to 422 response."""
        from services.exceptions import NoFuelStopInRangeError, RoutingServiceUnavailableError

        encoded = polyline_lib.encode([(41.85, -87.65), (34.05, -118.24)])
        mock_geocode = MagicMock()
        mock_geocode.raise_for_status = MagicMock()
        mock_geocode.json.return_value = {
            "features": [{"geometry": {"coordinates": [-87.65, 41.85]}}]
        }
        mock_directions = MagicMock()
        mock_directions.raise_for_status = MagicMock()
        mock_directions.json.return_value = {
            "routes": [{"summary": {"distance": 3240000.0}, "geometry": encoded}]
        }

        with patch("requests.get", return_value=mock_geocode), \
             patch("requests.post", return_value=mock_directions), \
             patch("api.views.optimize", side_effect=NoFuelStopInRangeError("no stops")):
            client = Client()
            response = client.post(
                "/api/route/",
                data=json.dumps({"start_location": "Chicago, IL", "end_location": "LA, CA"}),
                content_type="application/json",
            )
        assert response.status_code == 422


class TestDataLoader:
    def test_missing_csv_raises_dataset_load_error(self):
        """Missing CSV file should raise DatasetLoadError."""
        from services.data_loader import load_fuel_dataset
        from services.exceptions import DatasetLoadError

        with patch("services.data_loader.CSV_PATH", "/nonexistent/path/fuel.csv"):
            with pytest.raises(DatasetLoadError):
                load_fuel_dataset()

    def test_geocode_cache_hit_skips_api_call(self):
        """Stop already in geocoded_stops.json should not trigger ORS geocode call."""
        import pandas as pd
        from services.data_loader import load_fuel_dataset

        # Create a minimal CSV
        csv_data = "Truckstop Name,Address,City,State,Retail Price\nTest Stop,123 Rd,Anytown,KS,3.459\n"

        # Create a cache with the stop already geocoded
        cache_data = {
            "version": 1,
            "stops": [{
                "name": "Test Stop",
                "address": "123 Rd",
                "city": "Anytown",
                "state": "KS",
                "latitude": 39.0,
                "longitude": -95.0,
                "retail_price": 3.459,
            }]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            cache_path = os.path.join(tmpdir, "geocoded_stops.json")

            with open(csv_path, "w") as f:
                f.write(csv_data)
            with open(cache_path, "w") as f:
                json.dump(cache_data, f)

            with patch("services.data_loader.CSV_PATH", csv_path), \
                 patch("services.data_loader.CACHE_PATH", cache_path), \
                 patch("requests.get") as mock_get:
                result = load_fuel_dataset()

            # ORS geocode should NOT have been called (cache hit)
            mock_get.assert_not_called()
            assert len(result) == 1
            assert result[0]["name"] == "Test Stop"
            assert result[0]["latitude"] == 39.0
