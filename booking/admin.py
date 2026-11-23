from django.contrib import admin

from .models import Appointment, Barber, Shop, ShopPhoto


class ShopPhotoInline(admin.TabularInline):
    model = ShopPhoto
    extra = 1


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("name", "neighborhood", "active")
    list_filter = ("active",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ShopPhotoInline]


@admin.register(Barber)
class BarberAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "account", "active")
    list_filter = ("active", "shop")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("start", "shop", "barber", "customer", "kind", "status")
    list_filter = ("shop", "status", "start")
    list_select_related = ("shop", "barber", "customer")
    date_hierarchy = "start"
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
