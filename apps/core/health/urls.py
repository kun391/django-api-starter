from django.urls import path

from . import views

urlpatterns = [
    path("live/", views.live, name="health_live"),
    path("ready/", views.ready, name="health_ready"),
    path("", views.health_check, name="health_check"),
    path("db/", views.database_health, name="database_health"),
    path("celery/", views.celery_health, name="celery_health"),
]
