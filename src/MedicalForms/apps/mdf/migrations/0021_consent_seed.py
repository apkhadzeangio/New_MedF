from django.db import migrations

AORTO_FEMORAL = [
    {'title': 'გართულებები აორტა-ბარძაყის სეგმენტზე ჩატარებული რეკონსტრუქციული ოპერაციების დროს:',
     'left': ['სისხლდენა', 'კიდურების იშემია', 'ნაწლავის იშემია', 'სპინალური იშემია', 'თირკმლის უკმარისობა',
              'ღვიძლის უკმარისობა', 'პოლიორგანული უკმარიოსბა', 'ინფექცია', 'ლეტალობა'],
     'right': ['პროთეზის თრომბოზი', 'პნევმონია', 'თავის ტვინის ინსულტი', 'მიოკარდიუმის ინფარქტი',
               'შეხორცებითი დაავადება']},
    {'title': 'შორეული პოსტოპერაციული გართულებები:',
     'left': ['პროთეზის ინფიცირება, რეოკლუზია', 'ანასტომოზის ფსევდოანევრიზმა', 'დისტალური ანასტომოზის უკმარისობა',
              'პროქსიმალური ანასტომოზის უკმარისობა', 'ლეტალობა'],
     'right': ['აორტა ნაწლავის შეხორცება', 'მუცლის წინა კედლის თიაქარი', 'სექსუალური დისფუნქცია',
               'შეხორცებითი დაავადება']},
]


def seed(apps, schema_editor):
    ConsentForm = apps.get_model('mdf', 'ConsentForm')
    if not ConsentForm.objects.exists():
        ConsentForm.objects.create(name='აორტა-ბარძაყის სეგმენტის რეკონსტრუქციული ოპერაცია', blocks=AORTO_FEMORAL)


class Migration(migrations.Migration):
    dependencies = [('mdf', '0020_consent_forms')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
