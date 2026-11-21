from django.contrib import admin

from .models import Medicine, Note, NivForm


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ['name', 'note', 'is_active']
    list_filter = ['is_active']
    search_fields = ('name',)


# @admin.register(Note)
# class NoteAdmin(admin.ModelAdmin):
#     list_display = ['name', 'is_active']
#     list_filter = ['is_active']


@admin.register(NivForm)
class NivFormAdmin(admin.ModelAdmin):
    pass
