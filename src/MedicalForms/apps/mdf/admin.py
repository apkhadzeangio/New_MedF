from django.contrib import admin
from django.utils.html import format_html

from .models import (BoardEntry, BoardPatient, DutyNote, DutyReport, Medicine, NivForm,
                     PrescriptionTemplate, StaffProfile)

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


@admin.register(DutyNote)
class DutyNoteAdmin(admin.ModelAdmin):
    list_display = ['duty_date', 'patient', 'created_at', 'created_by']
    list_filter = ['duty_date']
    search_fields = ('patient', 'body')
    date_hierarchy = 'duty_date'
    readonly_fields = ('duty_date', 'created_at', 'updated_at', 'created_by', 'updated_by')


class BoardEntryInline(admin.TabularInline):
    model = BoardEntry
    extra = 0
    fields = ('created_at', 'kind', 'text', 'created_by', 'done_at', 'done_by')
    readonly_fields = ('created_at', 'created_by', 'done_at', 'done_by')


@admin.register(BoardPatient)
class BoardPatientAdmin(admin.ModelAdmin):
    list_display = ['name', 'bed', 'unit', 'diagnosis', 'doctor', 'admitted_at', 'discharged_at']
    list_filter = ['unit', ('discharged_at', admin.EmptyFieldListFilter)]
    search_fields = ('name', 'diagnosis', 'bed')
    inlines = [BoardEntryInline]


@admin.register(DutyReport)
class DutyReportAdmin(admin.ModelAdmin):
    list_display = ['duty_date', 'created_at', 'created_by']
    readonly_fields = ('duty_date', 'data', 'created_at', 'created_by')
