from django.urls import path

from . import blood, views

urlpatterns = [
    path('', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('me/photo/', views.PhotoView.as_view(), name='my-photo'),

    # Prescription = front side of NIV-300-9/a (the printed state form)
    path('niv-300-9-a/cover/', views.NivFormCoverView.as_view(), name='niv-cover'),
    path('niv-300-9-a/cover/print/', views.NivDocumentView.as_view(), name='print-niv-cover'),
    path('niv-300-9-a/create/', views.NivTemplateCreateView.as_view(), name='niv-create'),
    path('niv-300-9-a/delete/<int:pk>/', views.NivTemplateDeleteView.as_view(), name='niv-delete'),
    path('niv-300-9-a/templates/', views.NivTemplateListView.as_view(), name='niv-templates'),
    path('niv-export/', views.NivDocumentView.as_view(), name='niv-export'),

    # Templates = reusable medicine sets
    path('templates/', views.TemplateListView.as_view(), name='rx-templates'),
    path('analysis/', views.NosologyAnalysisView.as_view(), name='rx-analysis'),
    path('templates/from-analysis/', views.TemplateFromAnalysisView.as_view(), name='rx-template-from-analysis'),
    path('templates/save/', views.TemplateSaveView.as_view(), name='rx-template-save'),
    path('templates/from/<int:pk>/', views.TemplateFromPrescriptionView.as_view(), name='rx-template-from'),
    path('templates/<int:pk>/rename/', views.TemplateRenameView.as_view(), name='rx-template-rename'),
    path('templates/<int:pk>/delete/', views.TemplateDeleteView.as_view(), name='rx-template-delete'),
    path('templates/<int:pk>/used/', views.TemplateUsedView.as_view(), name='rx-template-used'),

    # Blood order blank
    path('blood/', blood.BloodOrderView.as_view(), name='blood-order'),
    path('blood/print/', blood.BloodOrderPrintView.as_view(), name='blood-order-print'),

    path('api/medicines/', views.MedicineAPIView.as_view(), name='medicines'),
    path('api/notes/', views.NoteAPIView.as_view(), name='notes'),
    path('backup/download/', views.BackupDownloadView.as_view(), name='backup-download'),
]
