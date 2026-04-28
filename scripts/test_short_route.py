"""Test short route (Chicago -> Milwaukee) to verify fallback works."""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fuel_route_project.settings')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import django
django.setup()

from services.data_loader import load_fuel_dataset
from services.fuel_optimizer import optimize, haversine

dataset = load_fuel_dataset()

# Chicago -> Milwaukee (~90 miles)
chicago = [41.878, -87.630]
milwaukee = [43.038, -87.906]
n = 20
polyline = [
    [chicago[0] + (milwaukee[0]-chicago[0])*i/(n-1),
     chicago[1] + (milwaukee[1]-chicago[1])*i/(n-1)]
    for i in range(n)
]
dist = haversine(chicago[0], chicago[1], milwaukee[0], milwaukee[1])
print(f"Chicago -> Milwaukee: {dist:.1f} miles")

result = optimize(polyline, dist, dataset)
print(f"Stops: {len(result['fuel_stops'])}, cost: ${result['total_fuel_cost']}")
for s in result['fuel_stops']:
    print(f"  {s['name']}, {s['city']}, {s['state']} @ ${s['retail_price']:.3f}")
