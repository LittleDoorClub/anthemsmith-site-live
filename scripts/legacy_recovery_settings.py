"""Private deployment settings for operator-dispatched legacy recovery jobs."""
import os
import re


def required_setting(name, pattern):
    value = os.environ.get(name, '')
    if not re.fullmatch(pattern, value):
        raise SystemExit('Missing or invalid private setting: ' + name)
    return value


def required_order_id():
    return required_setting('ANTHEMSMITH_LEGACY_ORDER_ID', r'AS-[A-Z0-9-]{1,64}')
