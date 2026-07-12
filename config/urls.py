from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


from apps.core.health import get_health_status


def health_check(request):
    status = get_health_status()
    all_healthy = all(status.values())
    return JsonResponse(
        {"status": "ok" if all_healthy else "degraded", "checks": status},
        status=200 if all_healthy else 503,
    )


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.core.urls')),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/docs/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/learning/', include('apps.learning.urls')),
    path('api/health/', health_check, name='health-check'),
]
