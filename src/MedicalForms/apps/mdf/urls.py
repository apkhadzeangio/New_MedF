from django.urls import path

from . import blood, board, cases, consent, duty, medicines, preop, pwa, quotes, roster, views

urlpatterns = [
    path('', views.LoginView.as_view(), name='login'),
    path('manifest.webmanifest', pwa.manifest, name='pwa-manifest'),
    path('sw.js', pwa.service_worker, name='pwa-sw'),
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

    path('medicines/duplicates/', medicines.DuplicatesView.as_view(), name='medicine-duplicates'),

    # Informed consent sheets for operations (NIV-300-12/a)
    path('consent/', consent.ConsentListView.as_view(), name='consent-list'),
    path('consent/new/', consent.ConsentCreateView.as_view(), name='consent-new'),
    path('consent/<int:pk>/', consent.ConsentSheetView.as_view(), name='consent-sheet'),
    path('consent/<int:pk>/save/', consent.ConsentSaveView.as_view(), name='consent-save'),
    path('consent/<int:pk>/delete/', consent.ConsentDeleteView.as_view(), name='consent-delete'),

    # Duty roster (one surgeon per day)
    path('roster/', roster.RosterMonthView.as_view(), name='roster'),
    path('roster/generate/', roster.RosterGenerateView.as_view(), name='roster-generate'),
    path('roster/people/', roster.RosterPeopleView.as_view(), name='roster-people'),
    path('roster/print/', roster.RosterPrintView.as_view(), name='roster-print'),
    path('roster/day/<str:day>/', roster.RosterDayView.as_view(), name='roster-day'),

    path('quotes/', quotes.QuotesView.as_view(), name='quotes'),

    # Blood order blank
    path('blood/', blood.BloodOrderView.as_view(), name='blood-order'),
    path('blood/print/', blood.BloodOrderPrintView.as_view(), name='blood-order-print'),

    # Patients board (ICU + ward) and duty reports
    path('cases/', cases.CaseListView.as_view(), name='cases'),
    path('cases/new/', cases.CaseFormView.as_view(), name='case-new'),
    path('cases/present/', cases.PresentView.as_view(), name='cases-present'),
    path('cases/<int:pk>/', cases.CaseView.as_view(), name='case'),
    path('cases/<int:pk>/edit/', cases.CaseFormView.as_view(), name='case-edit'),
    path('cases/<int:pk>/photos/', cases.PhotoUploadView.as_view(), name='case-photos'),
    path('cases/<int:pk>/decision/', cases.DecisionView.as_view(), name='case-decision'),
    path('cases/<int:pk>/delete/', cases.CaseDeleteView.as_view(), name='case-delete'),
    path('cases/<int:pk>/staging/<str:side>/', cases.StagingView.as_view(), name='case-staging'),
    path('cases/<int:pk>/wifi/<str:side>/', cases.WifiView.as_view(), name='case-wifi'),
    path('cases/<int:pk>/doppler/', cases.DopplerUploadView.as_view(), name='case-doppler'),
    path('cases/doppler/<int:pk>/edit/', cases.DopplerEditView.as_view(), name='doppler-edit'),
    path('cases/doppler/<int:pk>/file/', cases.DopplerFileView.as_view(), name='doppler-file'),
    path('cases/photo/<int:pk>/', cases.PhotoView.as_view(), name='case-photo'),
    path('cases/photo/<int:pk>/thumb/', cases.PhotoView.as_view(), {'size': 'thumb'}, name='case-photo-thumb'),
    path('cases/photo/<int:pk>/edit/', cases.PhotoEditView.as_view(), name='case-photo-edit'),
    path('board/', board.BoardView.as_view(), name='board'),
    path('board/print/', board.BoardPrintView.as_view(), name='board-print'),
    path('board/stats/', board.StatsView.as_view(), name='board-stats'),
    path('board/seen/', board.SeenAllView.as_view(), name='board-seen'),
    path('board/patient/new/', board.PatientFormView.as_view(), name='board-patient-new'),
    path('board/patient/<int:pk>/', board.PatientView.as_view(), name='board-patient'),
    path('board/patient/<int:pk>/edit/', board.PatientFormView.as_view(), name='board-patient-edit'),
    path('board/patient/<int:pk>/write/', board.EntryAddView.as_view(), name='board-entry-add'),
    path('board/patient/<int:pk>/<str:action>/', board.PatientMoveView.as_view(), name='board-patient-move'),
    path('board/entry/<int:pk>/edit/', board.EntryEditView.as_view(), name='board-entry-edit'),
    path('board/task/<int:pk>/done/', board.TaskDoneView.as_view(), name='board-task-done'),
    path('board/discharged/', board.DischargedView.as_view(), name='board-discharged'),
    path('board/report/', board.ReportView.as_view(), name='board-report'),
    path('board/reports/', board.ReportListView.as_view(), name='board-reports'),
    path('board/reports/<int:pk>/', board.SavedReportView.as_view(), name='board-report-saved'),
    path('board/reports/<int:pk>/print/', board.ReportPrintView.as_view(), name='board-report-print'),

    # Old on-duty doctor's notes (kept readable, no longer in the menu)
    path('duty/', duty.DutyIndexView.as_view(), name='duty-index'),
    path('duty/new/', duty.DutyNoteEditView.as_view(), name='duty-new'),
    path('duty/note/<int:pk>/', duty.DutyNoteEditView.as_view(), name='duty-note'),
    path('duty/note/<int:pk>/print/', duty.DutyPrintView.as_view(), name='duty-note-print'),
    path('duty/<str:day>/', duty.DutyDayView.as_view(), name='duty-day'),
    path('duty/<str:day>/print/', duty.DutyPrintView.as_view(), name='duty-day-print'),

    # Preoperative assessment (Tweek-style weekly calendar + nurse POA + cardiology audit)
    path('preop/', preop.PreopWeekView.as_view(), name='preop'),
    path('preop/audit/', preop.PreopAuditView.as_view(), name='preop-audit'),
    path('preop/new/', preop.PreopCreateView.as_view(), name='preop-new'),
    path('preop/<int:pk>/', preop.PreopPatientView.as_view(), name='preop-patient'),
    path('preop/<int:pk>/move/', preop.PreopMoveView.as_view(), name='preop-move'),
    path('preop/<int:pk>/files/', preop.PreopFileUploadView.as_view(), name='preop-files'),
    path('preop/file/<int:pk>/', preop.PreopFileView.as_view(), name='preop-file'),
    path('preop/file/<int:pk>/thumb/', preop.PreopFileView.as_view(), {'size': 'thumb'}, name='preop-file-thumb'),
    path('preop/file/<int:pk>/delete/', preop.PreopFileDeleteView.as_view(), name='preop-file-delete'),

    path('api/medicines/', views.MedicineAPIView.as_view(), name='medicines'),
    path('api/notes/', views.NoteAPIView.as_view(), name='notes'),
    path('backup/download/', views.BackupDownloadView.as_view(), name='backup-download'),
]
