#!/usr/bin/env python3


class _Lazy(object):
    def __getattr__(self, name):
        def lazy_command(*args, **kwargs):
            def real_command(browser):
                for_real_this_time = getattr(browser, name)
                return for_real_this_time(*args, **kwargs)
            return real_command
        return lazy_command


def open_window(url, profile='default'):
    import dbus
    bus = dbus.SessionBus()
    roland_service = bus.get_object(
        'com.deschain.roland.{}'.format(profile),
        '/com/deschain/roland/{}'.format(profile))
    open_window = roland_service.get_dbus_method(
        'open_window', 'com.deschain.roland.{}'.format(profile))
    return open_window(url)


lazy = _Lazy()
