import re
from typing import Dict, Tuple, List

from apps.mdf.enums import Symbols

TM_KEY_REGEX = re.compile(r'^(r\d+|gk|p|ta|tc|fs|sp2s|giv|per0s|di|de|dg|gl)-d\d+')


def validate_take_medicine(data: Dict) -> Tuple[Dict, List[str]]:
    result, keys = dict(), list()
    if not data:
        return result, keys

    for key, _ in data.items():
        if not TM_KEY_REGEX.match(key):
            continue

        keys.append(key)
        value = data.getlist(key)
        medicine, day = key.split('-')
        if medicine not in result:
            result[medicine] = dict()

        result[medicine][day] = {h: Symbols.PLUS.value for h in value if 'del' not in h}

    return result, keys


def jsonfield_default_value():
    return dict(d1=dict(), d2=dict(), d3=dict())
