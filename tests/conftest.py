from hypothesis import settings, HealthCheck

settings.register_profile("ci", max_examples=100)
settings.load_profile("ci")
