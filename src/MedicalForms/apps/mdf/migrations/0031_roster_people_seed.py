from django.db import migrations

ROTATION = ['რამაზი', 'ია', 'თეონა', 'გურამი', 'გიგა', 'დიმიტრი', 'თორნიკე']


def seed(apps, schema_editor):
    Person = apps.get_model('mdf', 'RosterPerson')
    if Person.objects.exists():
        return
    for i, name in enumerate(ROTATION, start=1):
        Person.objects.create(name=name, order=i, in_rotation=True)
    # Misha: every 2nd and 4th Friday of the month, outside the rotation
    Person.objects.create(name='მიშა', order=len(ROTATION) + 1, in_rotation=False, fixed_weekday=4, fixed_weeks='2,4')


class Migration(migrations.Migration):
    dependencies = [('mdf', '0030_duty_roster')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
