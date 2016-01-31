import os


def get_pretty_size(bytecount):
    size = bytecount

    for suffix in ['b', 'kb', 'mb', 'gb', 'tb', 'pb']:
        if size // 1024 < 1:
            return '%d%s' % (size, suffix)
        size /= 1024
    return '%d%s' % (size, suffix)


def config_path(t, profile=''):
    from gi.repository import GLib
    t = t.format(profile)
    return os.path.join(GLib.get_user_config_dir(), 'roland', t)


def runtime_path(t, profile=''):
    from gi.repository import GLib
    t = t.format(profile)
    return os.path.join(GLib.get_user_runtime_dir(), 'roland', t)


def cache_path(t, profile=''):
    from gi.repository import GLib
    t = t.format(profile)
    return os.path.join(GLib.get_user_cache_dir(), 'roland', t)


def get_keyname(event):
    from gi.repository import Gdk
    if event is None:
        return None

    acceptable_for_shift = ['space']
    keyname = Gdk.keyval_name(event.keyval)
    fields = []
    if event.state & Gdk.ModifierType.CONTROL_MASK:
        fields.append('C')
    if keyname in acceptable_for_shift and event.state & Gdk.ModifierType.SHIFT_MASK:
        fields.append('S')
    if event.state & Gdk.ModifierType.SUPER_MASK:
        fields.append('L')
    if event.state & Gdk.ModifierType.MOD1_MASK:
        fields.append('A')

    keyname = {
        'slash': '/',
        'question': '?',
        'plus': '+',
        'minus': '-',
        'equal': '=',
        'colon': ':',
        'dollar': '$',
        'asciicircum': '^',
    }.get(keyname, keyname)

    fields.append(keyname)
    return '-'.join(fields)


def init_logging():
    import logbook
    import logbook.more

    logbook.set_datetime_format('local')
    logbook.NullHandler(level=0).push_application()
    logbook.more.ColorizedStderrHandler(level='INFO').push_application()
    logbook.RotatingFileHandler(config_path('roland.log'), level='INFO', bubble=True).push_application()


def default_config():
    """Return absolute minimal config for
    a 'functioning' browser.

    Won't let you do much apart from
    quit.
    """
    from roland.api import lazy, Mode
    class config:
        commands = {
            'i': lazy.set_mode(Mode.Insert),
            ':': lazy.prompt_command(),
        }
    return config


class RolandConfigBase:
    def load_config(self):
        self.config = load_config()
        self.extensions = sorted([ext(self) for ext in self.config.extensions], key=lambda ext: ext.sort_order)

        self.make_config_directories()

    def make_config_directories(self):
        for p in cache_path, config_path, runtime_path:
            p = p('')
            try:
                os.makedirs(p)
            except FileExistsError:
                pass

    def is_enabled(self, extension):
        return self.get_extension(extension) is not None

    def get_extension(self, extensiontype):
        if not isinstance(extensiontype, str):
            extensiontype = extensiontype.__name__

        for ext in self.extensions:
            if ext.__class__.__name__ == extensiontype:
                return ext


def load_config():
    import imp
    try:
        config = imp.load_source('roland.config', config_path('config.py'))
    except FileNotFoundError:
        config = default_config()

    from roland.extensions import (
        CookieManager, DBusManager, DownloadManager, HistoryManager,
        SessionManager, TLSErrorByPassExtension, HSTSExtension, UserContentManager,
        PasswordManagerExtension)

    default_extensions = [
        CookieManager, DBusManager, DownloadManager, HistoryManager,
        SessionManager, TLSErrorByPassExtension, HSTSExtension,
        UserContentManager, PasswordManagerExtension]
    config.extensions = getattr(config, 'extensions', default_extensions)

    # DBusManager, as of the WebKit2 port, is essentially required
    if DBusManager not in config.extensions:
        config.extensions.append(DBusManager)

    return config
