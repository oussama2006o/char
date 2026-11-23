from django.contrib import admin
from django.urls import path

from booking import staff, views

urlpatterns = [
    path("", views.home, name="home"),
    path("shops/", views.shops, name="shops"),
    path("shops/<slug:slug>/", views.shop, name="shop"),
    path("shops/<slug:slug>/qr.svg", views.shop_qr, name="shop_qr"),
    path("search/", views.search, name="search"),
    path("barbers/<int:barber_id>/book/", views.search, name="barber_search"),
    path("language/", views.language, name="language"),
    path("checkout/<str:token>/", views.verification_page, name="checkout"),
    path("customer/login/", views.verification_page, name="customer_login"),
    path("customer/logout/", views.customer_logout, name="customer_logout"),
    path("appointments/", views.mine, name="mine"),
    path("appointments/<uuid:pk>/", views.appointment, name="appointment"),
    path("staff/login/", staff.staff_login, name="staff_login"),
    path("staff/logout/", staff.staff_logout, name="staff_logout"),
    path("barber/", staff.barber_today, name="barber_today"),
    path("barber/notices/<int:pk>/read/", staff.read_notice, name="read_notice"),
    path("barber/appointments/<uuid:pk>/", staff.barber_appointment, name="barber_appointment"),
    path("barber/schedule/", staff.barber_schedule, name="barber_schedule"),
    path("barber/services/", staff.barber_services, name="barber_services"),
    path("pilot/", staff.report, name="report"),
    path("admin/", admin.site.urls),
]
