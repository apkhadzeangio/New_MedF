"""Domain-organized Django ORM models for the `mdf` application.

`PrivateStorage` and `private_storage` live directly in this package namespace so
historical migrations referencing `apps.mdf.models.private_storage` stay 100%
migration-neutral.
"""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Patient photos/PDFs: a folder no web server publishes (served via login views only).

    The folder is read from settings on every use, so tests can point it elsewhere.
    """

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def private_storage():
    return PrivateStorage()


from .board import BoardEntry, BoardPatient, BoardSeen, DutyNote, DutyReport, SiteState  # noqa: E402
from .cases import Case, CasePhoto, DopplerStudy, _delete_private_files  # noqa: E402
from .operations import ConsentForm, Quote, RosterDay, RosterLog, RosterPerson  # noqa: E402
from .preop import PreopFile, PreopPatient, PreopRuleSignoff  # noqa: E402
from .prescriptions import (  # noqa: E402
    ROW_COUNT,
    Medicine,
    NivForm,
    Note,
    PrescriptionTemplate,
    StaffProfile,
    medicine_key,
)

__all__ = [
    'ROW_COUNT',
    'BoardEntry',
    'BoardPatient',
    'BoardSeen',
    'Case',
    'CasePhoto',
    'ConsentForm',
    'DopplerStudy',
    'DutyNote',
    'DutyReport',
    'Medicine',
    'NivForm',
    'Note',
    'PreopFile',
    'PreopPatient',
    'PreopRuleSignoff',
    'PrescriptionTemplate',
    'PrivateStorage',
    'Quote',
    'RosterDay',
    'RosterLog',
    'RosterPerson',
    'SiteState',
    'StaffProfile',
    '_delete_private_files',
    'medicine_key',
    'private_storage',
]
