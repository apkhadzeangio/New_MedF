"""Three more consent sheets, taken from the clinic's Word files (text as written there)."""
from django.db import migrations

SHEETS = [
    [
        "საფეთქლის არტერიის ფრონტალური ტოტის ცრუ ანევრიზმის რეზექცია",
        [
            {
                "title": "გართულებები საფეთქლის არტერიის ფრონტალური ტოტის ცრუ ანევრიზმის რეზექციის დროს",
                "left": [
                    "სისხლდენა",
                    "ინფექცია",
                    "პარესთეზია (კანის მგრძნობელობის მოშლა ჭრილობების მიმდებარე ზონაში)"
                ],
                "right": []
            }
        ]
    ],
    [
        "კრაელის ტიპის ლიმფოდისექცია",
        [
            {
                "title": "გართულებები კრაელის ტიპის ლიმფოდისექციის დროს:",
                "left": [],
                "right": []
            },
            {
                "title": "ადრეული პოსტოპერაციული გართულებები:",
                "left": [
                    "სისხლდენა",
                    "ინფექცია",
                    "პროთეზის (აუტოვენის) თრომბოზი",
                    "ცთომილი, დიაფრაგმული, ენისქვეშა, ხორცის შებრუნებული ნერვის დაზიანება (ხმის ტემბრის შეცვლა, კანის მგრძნობელობის მოშლა ენის ქვედა ყბის და ლოყის არეში, ენის მოზრაობითი ფუნქციის მოშლა)",
                    "თავის ტვინის ინსულტი",
                    "ლეტალობა"
                ],
                "right": []
            },
            {
                "title": "შორეული პოსტოპერაციული გართულებები:",
                "left": [
                    "დაავადების რეციდივი",
                    "სძილე არტერიის თრომბოზი,",
                    "ანასტომოზის უკმარისობა,",
                    "სტენოზი.",
                    "ინსულტი."
                ],
                "right": []
            }
        ]
    ],
    [
        "ფლებექტომია",
        [
            {
                "title": "გართულებები ფლებექტომიის დროს:",
                "left": [
                    "სისხლდენა",
                    "ინფექცია",
                    "ლიმფორეა",
                    "ღრმა ვენური სისტემის თრომბოზი",
                    "ფილტვის არტერიის თრომბოლია",
                    "პარესთეზია (კანის მგრძნობელობის მოშლა ჭრილობების მიმდებარე ზონაში)"
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
    dependencies = [('mdf', '0024_consent_aorta_amputation')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
