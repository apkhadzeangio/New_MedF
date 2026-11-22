from django.db import migrations


def misha_random(apps, schema_editor):
    """Misha: any two days of the month (chosen at random), at least 10 days apart."""
    Person = apps.get_model('mdf', 'RosterPerson')
    Person.objects.filter(name='მიშა', in_rotation=False).update(
        fixed_weekday=None, fixed_weeks='', per_month=2, min_gap=10)


class Migration(migrations.Migration):
    dependencies = [('mdf', '0032_roster_random_days')]
    operations = [migrations.RunPython(misha_random, migrations.RunPython.noop)]
