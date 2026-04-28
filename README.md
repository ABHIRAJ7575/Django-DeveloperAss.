# Fuel Route Optimizer

A Django REST Framework API that computes an optimal driving route between two US locations and identifies the most cost-effective fuel stops along the way. The vehicle is assumed to have a 500-mile range and 10 MPG efficiency.

## Prerequisites

- Python 3.11+
- pip
- An OpenRouteService API key ([register here](https://openrouteservice.org/dev/#/signup))

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy the example environment file and fill in your values:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```
   ORS_API_KEY=your_openrouteservice_api_key
   DJANGO_SECRET_KEY=your_django_secret_key
   ```

3. Apply database migrations:
   ```bash
   python manage.py migrate
   ```

4. Place the fuel prices CSV in the `data/` directory:
   ```
   data/fuel-prices-for-be-assessment.csv
   ```

5. Start the development server:
   ```bash
   python manage.py runserver
   ```

The UI is available at `http://localhost:8000/`.

## API Usage

**POST** `/api/route/`

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start_location": "New York, NY", "end_location": "Los Angeles, CA"}'
```

Response:
```json
{
  "route_polyline": [[40.7128, -74.0060], ...],
  "fuel_stops": [
    {
      "name": "Pilot Travel Center",
      "address": "123 Main St",
      "city": "Columbus",
      "state": "OH",
      "latitude": 39.9612,
      "longitude": -82.9988,
      "retail_price": 3.45
    }
  ],
  "total_distance_miles": 2790.5,
  "total_fuel_cost": 963.22
}
```
