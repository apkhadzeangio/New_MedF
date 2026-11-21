from django.urls import path

from .views import (
    LoginView,
    LogoutView,
    NivFormCoverView,
    NivFormBackView,
    MedicineAPIView,
    NoteAPIView,
    NivTemplateListView,
    NivTemplateCreateView,
    NivTemplateDeleteView,
    PrintNivFormCoverView,
    PrintNivFormBackView,
    AssignmentSheetView,
    NivDocumentView,
    PasswordChangeView,
)


urlpatterns = [
    path('', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password/change/', PasswordChangeView.as_view(), name='password-change'),
    path('assignment-sheet/', AssignmentSheetView.as_view(), name='assigment-sheet'),
    path('niv-300-9-a/cover/', NivFormCoverView.as_view(), name='niv-cover'),
    # path('niv-300-9-a/cover/print/', PrintNivFormCoverView.as_view(), name='print-niv-cover'),
    path('niv-300-9-a/cover/print/', NivDocumentView.as_view(), name='print-niv-cover'),
    path('niv-300-9-a/back/', NivFormBackView.as_view(), name='niv-back'),
    path('niv-300-9-a/back/print/', PrintNivFormBackView.as_view(), name='print-niv-back'),
    path('niv-300-9-a/create/', NivTemplateCreateView.as_view(), name='niv-create'),
    path('niv-300-9-a/delete/<int:pk>/', NivTemplateDeleteView.as_view(), name='niv-delete'),
    path('niv-300-9-a/templates/', NivTemplateListView.as_view(), name='niv-templates'),
    path('api/medicines/', MedicineAPIView.as_view(), name='medicines'),
    path('api/notes/', NoteAPIView.as_view(), name='notes'),
    path('niv-export/', NivDocumentView.as_view(), name='niv-export'),
]
