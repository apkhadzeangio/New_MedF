from django.contrib import admin
from django.utils.html import format_html

from .models import Medicine, NivForm, PrescriptionTemplate, StaffProfile

admin.site.site_header = 'Medical Forms - ადმინისტრირება'
admin.site.site_title = 'Medical Forms'


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ['name', 'note', 'is_active']
    list_editable = ['is_active']
    list_filter = ['is_active']
    search_fields = ('name', 'note')
    ordering = ('name',)
    list_per_page = 100


@admin.register(NivForm)
class NivFormAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'patient_name', 'department', 'doe']
    list_display_links = ['id', 'title']
    search_fields = ('title', 'patient_name', 'person_id', 'card_number', 'diagnosis')
    ordering = ('-id',)
    list_per_page = 50


@admin.register(PrescriptionTemplate)
class PrescriptionTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'medicine_count', 'use_count', 'created_by', 'updated_at']
    search_fields = ('name',)
    readonly_fields = ('created_by', 'created_at', 'updated_at')

    @admin.display(description='მედიკამენტები')
    def medicine_count(self, obj):
        return len(obj.medicines)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ['thumb', 'display_name', 'full_name', 'user', 'order', 'show_on_login']
    list_display_links = ['thumb', 'display_name']
    list_editable = ['full_name', 'order', 'show_on_login']

    @admin.display(description='')
    def thumb(self, obj):
        if obj.photo:
            return format_html('<img src="{}" style="width:40px;height:40px;border-radius:50%;object-fit:cover">', obj.photo.url)
        return obj.initial
