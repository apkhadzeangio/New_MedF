from .base import *  # NOQA

DEBUG = True

ALLOWED_HOSTS = ['medf.pythonanywhere.com']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'medf$MDF',
        'USER': 'medf',
        'PASSWORD': 'REDACTED',
        'HOST': 'medf.mysql.pythonanywhere-services.com',
    }
}
