import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fuel_route_project.settings')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import django
django.setup()

from services.data_loader import load_fuel_dataset
from services.fuel_optimizer import filter_corridor, optimize, haversine

dataset = load_fuel_dataset()
print(f"Dataset: {len(dataset)} stops")

# Chicago -> LA straight-line polyline
chicago = [41.878, -87.630]
la = [34.052, -118.244]
n = 50
polyline = [
    [chicago[0] + (la[0]-chicago[0])*i/(n-1),
     chicago[1] + (la[1]-chicago[1])*i/(n-1)]
    for i in range(n)
]

for radius in [100, 200, 500]:
    corridor = filter_corridor(polyline, dataset, corridor_miles=radius)
    print(f"Corridor {radius} miles: {len(corridor)} stops")
    if corridor:
        print(f"  First: {corridor[0]['city']}, {corridor[0]['state']}")
        break

# Try full optimization
try:
    dist = haversine(chicago[0], chicago[1], la[0], la[1])
    result = optimize(polyline, dist, dataset)
    print(f"\nOptimization SUCCESS: {len(result['fuel_stops'])} stops, cost ${result['total_fuel_cost']}")
    for s in result['fuel_stops'][:3]:
        print(f"  {s['name']}, {s['city']}, {s['state']} @ ${s['retail_price']:.3f}")
except Exception as e:
    print(f"\nOptimization FAILED: {e}")
