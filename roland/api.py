#!/usr/bin/env python3


class _Lazy:
    def __getattr__(self, name):
        class lazy_command:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def __call__(self, browser):
                for_real_this_time = getattr(browser, name)
                return for_real_this_time(*self.args, **self.kwargs)

            def __str__(self):
                return '{}({}, {})'.format(name, self.args, self.kwargs)

            __repr__ = __str__

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
