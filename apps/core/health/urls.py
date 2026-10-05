from django.urls import path

from . import views

urlpatterns = [
    path("", views.health_check, name="health_check"),
    path("db/", views.database_health, name="database_health"),
    path("celery/", views.celery_health, name="celery_health"),
]
