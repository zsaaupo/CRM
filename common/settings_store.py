from django.core.cache import cache


GENERAL_SETTING_CACHE_KEY = 'common:general_setting:v1'
GENERAL_SETTING_CACHE_TIMEOUT = 300


def get_general():
    setting = cache.get(GENERAL_SETTING_CACHE_KEY)
    if setting is not None:
        return setting
    from .models import GeneralSetting

    setting, _ = GeneralSetting.objects.get_or_create(pk=1)
    cache.set(GENERAL_SETTING_CACHE_KEY, setting, GENERAL_SETTING_CACHE_TIMEOUT)
    return setting


def invalidate_general_cache():
    cache.delete(GENERAL_SETTING_CACHE_KEY)
