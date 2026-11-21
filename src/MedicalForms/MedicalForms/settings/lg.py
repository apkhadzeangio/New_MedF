from .base import *  # NOQA

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'forms',
        'USER': 'dev',
        'PASSWORD': 'toor',
        'HOST': 'localhost',
        'PORT': 5432,
    },
    # 'default': {
    #     'ENGINE': 'django.db.backends.mysql',
    #     'NAME': 'medf$MDF',
    #     'USER': 'medf',
    #     'PASSWORD': 'REDACTED',
    #     'HOST': 'medf.mysql.pythonanywhere-services.com',
    # }
}
