from django.contrib import admin

from .models import Medicine, Note, NivForm

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


# @admin.register(Note)
# class NoteAdmin(admin.ModelAdmin):
#     list_display = ['name', 'is_active']
#     list_filter = ['is_active']


@admin.register(NivForm)
class NivFormAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'patient_name', 'department', 'doe']
    list_display_links = ['id', 'title']
    search_fields = ('title', 'patient_name', 'person_id', 'card_number', 'diagnosis')
    ordering = ('-id',)
    list_per_page = 50
