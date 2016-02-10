from urllib.parse import quote_plus
from roland import lazy, Mode

from urllib import parse as urlparse


home_page = 'https://google.com/'
search_page = 'https://www.google.com/search?q={}'


def close_backwards(browser):
    browser.close()
    browser.roland.prev_tab()


def contextual_follow(browser):
    uri = browser.webview.get_uri()

    if not uri:
        return

    domain = urlparse.urlparse(browser.webview.get_uri()).netloc

    if domain.endswith('slack.com'):
        browser.follow(selector='a.channel_name>.overflow_ellipsis, a.im_name>.overflow_ellipsis, #message-input')
    else:
        browser.follow()

commands = {
    'i': lazy.set_mode(Mode.Insert),
    ':': lazy.prompt_command(),
    'Insert': lazy.set_mode(Mode.PassThrough),

    'b': lazy.select_window(),
    'd': lazy.close(),
    'D': close_backwards,
    'o': lazy.open_or_search(),
    'O': lazy.open_modify(),
    't': lazy.open_or_search(new_window=True),
    'T': lazy.open_modify(new_window=True),
    'B': lazy.open_or_search(background=True),

    'r': lazy.reload(),
    'R': lazy.reload_bypass_cache(),

    'C-Up': lazy.zoom_in(),
    'C-Down': lazy.zoom_out(),
    '+': lazy.zoom_in(),
    '-': lazy.zoom_out(),
    '=': lazy.zoom_reset(),

    '/': lazy.search_page(forwards=True),
    '?': lazy.search_page(forwards=False),
    'n': lazy.next_search_result(forwards=True),
    'N': lazy.next_search_result(forwards=False),

    'C-o': lazy.back(),
    'C-i': lazy.forward(),

    'f': lazy.follow(),
    'F': lazy.follow(new_window=True),

    'C-f': lazy.javascript('window.scrollBy(0, window.innerHeight);'),
    'C-b': lazy.javascript('window.scrollBy(0, -window.innerHeight);'),
    'space': lazy.javascript('window.scrollBy(0, window.innerHeight);'),
    'S-space': lazy.javascript('window.scrollBy(0, -window.innerHeight);'),

    'C-c': lazy.stop(),
    'C-w': lazy.shell(),
    'C-q': lazy.quit(),

    'h': lazy.move(x=-1),
    'j': lazy.move(y=1),
    'k': lazy.move(y=-1),
    'l': lazy.move(x=1),

    'y': lambda browser: browser.roland.set_clipboard(browser.webview.get_uri()),
    'g': lazy.set_mode(Mode.SubCommand, 'g', {
        '^': lazy.select_window(1),
        '$': lazy.select_window(-1),
        '0': lazy.select_window(1),
        '1': lazy.select_window(1),
        '2': lazy.select_window(2),
        '3': lazy.select_window(3),
        '4': lazy.select_window(4),
        '5': lazy.select_window(5),
        '6': lazy.select_window(6),
        '7': lazy.select_window(7),
        '8': lazy.select_window(8),
        '9': lazy.select_window(9),
        't': lambda browser: browser.roland.next_tab(),
        'T': lambda browser: browser.roland.prev_tab(),
        'v': lambda browser: browser.toggle_tab_visibility(),
        'n': lazy.tab_bar_position('cycle'),
        'N': lazy.tab_bar_position('reverse-cycle'),
        'u': lazy.navigate_up(),
        'U': lazy.navigate_top(),
        'g': lazy.javascript('window.scrollTo(0, 0);'),
        'f': contextual_follow,
        'y': lazy.yank_links(),
    }),
    'u': lazy.undo_close(),
    'G': lazy.javascript('window.scrollBy(0, document.body.scrollHeight);'),
    'P': lazy.open_from_clipboard(),
}


def should_open_popup(uri):
    print("Yeah I'm going going to open this popup", uri)
    return True


def user_agent_choices():
    return [
        'user-agent 1',
        'user-agent 2',
        'user-agent 3',
    ]

# set this to what you want to use, by default use whatever WebKit uses.
default_user_agent = None

font = 'Anonymous Pro 10'

# put favicons in the tab bar.
show_favicons = True

# CSS style to use for input/label widgets.
style = '''
    GtkNotebook, GtkTab, GtkEntry, GtkLabel {
        background: black;
        color: white;
    }

    .active-page GtkLabel
    {
        color: #01a252;
    }

    #InsertMode {
        color: #01a252;
    }

    #NormalMode {
        color: white;
    }

    #PassThroughMode {
        color: #01a0e4;
    }
'''

enable_disk_cache = True

# enable frame flattening to make pages with frames easier to navigate.
enable_frame_flattening = True

enable_webgl = True
enable_accelerated_2d_canvas = True

from gi.repository import Gdk
dpi = Gdk.Screen.get_default().props.resolution

# scale default zoom to account for the dpi, for my HiDPI monitor. Based on a default of 96 dpi.
default_zoom = dpi / 96.0 * 100

spell_checking_enabled = True
spell_checking_languages = ['en_AU']


def search_url(text):
    """Create custom searches based on what you've entered."""

    if text.startswith('twitter '):
        search = text[len('twitter '):]
        url = 'https://twitter.com/search?q={}&src=typd'
    elif text.startswith('reddit '):
        search = text[len('reddit '):]
        url = 'https://www.reddit.com/search?q={}'
    elif text.startswith('/r/'):
        return 'https://www.reddit.com{}'.format(text)
    elif text.startswith('nyaa '):
        search = text[len('nyaa '):]
        url = 'http://www.nyaa.se/?page=search&cats=1_0&filter=0&term={}'
    else:
        url = None

    if url is None:
        return
    else:
        return url.format(quote_plus(search))
