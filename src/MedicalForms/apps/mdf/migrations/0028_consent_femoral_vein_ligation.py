"""One more consent sheet, taken from the clinic's Word file (text as written there)."""
from django.db import migrations

SHEETS = [
    [
        "ბარძაყის ვენის ლიგირება",
        [
            {
                "title": "შესაძლო გართულებები ბარძაყის ვენის ლიგირების დროს:",
                "left": [
                    "ფილტვის არტერიის თრომბოემბოლია",
                    "რეთრომბოზი",
                    "ღრმა ვენური თრომბოზი",
                    "სისხლდენა",
                    "ინფექცია",
                    "ლიმფორეა",
                    "კიდურის ლიმფედემა",
                    "ლეტალობა"
                ],
                "right": []
            }
        ]
    ]
]


def add(apps, schema_editor):
    ConsentForm = apps.get_model('mdf', 'ConsentForm')
    for name, blocks in SHEETS:
        if not ConsentForm.objects.filter(name=name).exists():
            ConsentForm.objects.create(name=name, blocks=blocks)


class Migration(migrations.Migration):
    dependencies = [('mdf', '0027_consent_sympathectomy_tracheostomy')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
