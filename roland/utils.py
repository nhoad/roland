import os

from gi.repository import Gdk


def get_pretty_size(bytecount):
    size = bytecount

    for suffix in ['b', 'kb', 'mb', 'gb', 'tb', 'pb']:
        if size // 1024 < 1:
            return '%d%s' % (size, suffix)
        size /= 1024
    return '%d%s' % (size, suffix)


def config_path(t, profile=''):
    t = t.format(profile)
    return os.path.expanduser('~/.config/roland/{}'.format(t))


def get_keyname(event):
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

    fields.append(keyname)
    return '-'.join(fields)
