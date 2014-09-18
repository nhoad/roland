from roland import lazy, Mode


home_page = 'https://www.google.com'
search_page = 'https://www.google.com/search?q=%s'

commands = {
    'i': lazy.set_mode(Mode.Insert),
    'colon': lazy.set_mode(Mode.Command),

    'b': lambda browser: browser.roland.select_window(),
    'c': lazy.close(),
    'o': lazy.open_or_search(),
    'O': lazy.open_modify(),
    't': lazy.open_or_search(new_window=True),
    'T': lazy.open_modify(new_window=True),

    'r': lazy.reload(),
    'R': lazy.reload_bypass_cache(),

    'plus': lazy.zoom_in(),
    'minus': lazy.zoom_out(),
    'equal': lazy.zoom_reset(),

    'slash': lazy.search_page(forwards=True),
    'question': lazy.search_page(forwards=False),
    'n': lazy.next_search_result(forwards=True),
    'N': lazy.next_search_result(forwards=False),

    'C-o': lazy.back(),
    'C-i': lazy.forward(),

    'f': lazy.follow(),
    'F': lazy.follow(new_window=True),

    'C-f': lazy.run_javascript('window.scrollBy(0, window.innerHeight);'),
    'C-b': lazy.run_javascript('window.scrollBy(0, -window.innerHeight);'),

    'C-c': lazy.stop(),
    'C-w': lazy.shell(),
    'C-q': lazy.quit(),

    'h': lazy.move(x=-1),
    'j': lazy.move(y=1),
    'k': lazy.move(y=-1),
    'l': lazy.move(x=1),

    'y': lambda browser: browser.roland.set_clipboard(browser.get_uri()),
    'g': lazy.set_mode(Mode.SubCommand, 'g', {
        'g': lazy.run_javascript('window.scrollTo(0, 0);'),
    }),
    'd': lazy.set_mode(Mode.SubCommand, 'd', {
        'l': lazy.list_downloads(),
        'c': lazy.cancel_download(),
    }),

    'G': lazy.run_javascript('window.scrollBy(0, document.body.scrollHeight);'),
}
