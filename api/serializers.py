from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start_location = serializers.CharField()
    end_location = serializers.CharField()


class FuelStopSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    retail_price = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    route_polyline = serializers.ListField(child=serializers.ListField())
    fuel_stops = FuelStopSerializer(many=True)
    total_distance_miles = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
