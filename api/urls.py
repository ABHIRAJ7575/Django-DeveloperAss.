from django.urls import path
from api.views import RouteView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
]
