"""Two more consent sheets, taken from the clinic's Word files (text as written there)."""
from django.db import migrations

SHEETS = [
    [
        "არტერიო-ვენური ფისტულის ფორმირება",
        [
            {
                "title": "გართულებები ა.ვ. ფისტულის ფორმირების ოპერაციის დროს",
                "left": [
                    "თრომბოზი",
                    "ჭრილობის ინფექცია",
                    "ლიმფორეა",
                    "კიდურის შეშუპება",
                    "ნევროლოგიური ცვლილებები (ტკივილის სინდრომი ან პარესთეზია)"
                ],
                "right": []
            },
            {
                "title": "შორეული პოსტოპერაციული გართულებები (დიალიზის დაწყების შემდგომ):",
                "left": [
                    "თრომბოზი",
                    "სტენოზი",
                    "ანევრიზმა ან ფსევდოანევრიზმა",
                    "ინფიცირება",
                    "აროზიული სისხლდენა",
                    "ნევროლოგიური ცვლილებები"
                ],
                "right": []
            }
        ]
    ],
    [
        "ზემო კიდურის არტერიების რეკონსტრუქციული ოპერაცია",
        [
            {
                "title": "გართულებები ზემო კიდურის არტერიებზე რეკონსტრუქციული ოპერაციის დროს",
                "left": [
                    "სისხლდენა",
                    "ინფექცია"
                ],
                "right": [
                    "პნევმონია",
                    "არტერიის თრომბოზი"
                ]
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
    dependencies = [('mdf', '0028_consent_femoral_vein_ligation')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
