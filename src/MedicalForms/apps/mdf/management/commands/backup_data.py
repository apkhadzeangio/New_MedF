"""Save a compressed JSON copy of all clinic data (prescriptions, medicines,
templates, users) and keep only the newest copies.

    python manage.py backup_data            # -> ~/medf-backups/medf-YYYY-MM-DD.json.gz
    python manage.py backup_data --keep 30

On PythonAnywhere (free plan) add it as the daily scheduled task (Tasks tab).
Restore with:  python manage.py loaddata <file>.json.gz
"""
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mdf.views import write_backup


class Command(BaseCommand):
    help = 'Write a gzip JSON backup of all data and delete old backups.'

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=str(Path.home() / 'medf-backups'))
        parser.add_argument('--keep', type=int, default=14, help='How many newest backups to keep.')

    def handle(self, *args, **options):
        folder = Path(options['dir'])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"medf-{timezone.localtime():%Y-%m-%d_%H%M}.json.gz"
        with open(path, 'wb') as fh:
            write_backup(fh)
        self.stdout.write(self.style.SUCCESS(f'Saved {path} ({path.stat().st_size // 1024} KB)'))

        from apps.mdf.board import purge_old_reports
        purge_old_reports()                     # duty reports live one month

        backups = sorted(folder.glob('medf-*.json.gz'))
        for old in backups[:-options['keep']] if options['keep'] > 0 else []:
            old.unlink()
            self.stdout.write(f'Deleted old backup {old.name}')
