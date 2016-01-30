#!/usr/bin/env python3

import enum


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
    return dbus_execute('open_window', url=url, profile=profile)


def dbus_execute(method, *args, profile, **kwargs):
    import dbus
    bus = dbus.SessionBus()
    roland_service = bus.get_object(
        'com.deschain.roland.{}'.format(profile),
        '/com/deschain/roland/{}'.format(profile))
    func = roland_service.get_dbus_method(
        method, 'com.deschain.roland.{}'.format(profile))
    return func(*args, **kwargs)


lazy = _Lazy()

Mode = enum.Enum('Mode', 'Insert Normal Motion SubCommand Prompt PassThrough')
