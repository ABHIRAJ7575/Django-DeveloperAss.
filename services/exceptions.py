class LocationOutsideCONUSError(Exception):
    """Raised when a location geocodes outside the continental USA bounding box."""


class RoutingServiceUnavailableError(Exception):
    """Raised when the ORS API is unreachable or returns an error."""


class NoFuelStopInRangeError(Exception):
    """Raised when no fuel stop exists within 500 miles of the current position."""


class DatasetLoadError(Exception):
    """Raised when the fuel prices CSV file is missing or malformed at startup."""
