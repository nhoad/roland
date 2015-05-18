from roland import lazy, Mode


home_page = 'https://duckduckgo.com/'
search_page = 'https://www.duckduckgo.com/?q={}'

commands = {
    'i': lazy.set_mode(Mode.Insert),
    ':': lazy.prompt_command(),

    'b': lazy.select_window(),
    'd': lazy.close(),
    'o': lazy.open_or_search(),
    'O': lazy.open_modify(),
    't': lazy.open_or_search(new_window=True),
    'T': lazy.open_modify(new_window=True),

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
        'u': lazy.navigate_up(),
        'U': lazy.navigate_top(),
        'g': lazy.javascript('window.scrollTo(0, 0);'),
    }),
    'u': lazy.undo_close(),
    'G': lazy.javascript('window.scrollBy(0, document.body.scrollHeight);'),
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

# CSS style to use for input/label widgets.
style = '''
    GtkWindow, GtkEntry, GtkLabel {
        background: black;
        color: white;
    }
'''

enable_disk_cache = True
