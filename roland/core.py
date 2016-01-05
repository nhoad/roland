#!/usr/bin/env python3

import code
import collections
import datetime
import enum
import faulthandler
import fnmatch
import html
import imp
import itertools
import os
import pathlib
import random
import shlex
import threading
import traceback
from urllib import parse as urlparse

import logbook
import msgpack
import gi

gi.require_version('WebKit2', '4.0')

from gi.repository import GObject, Gdk, Gio, Gtk, Notify, Pango, GLib, WebKit2, GdkPixbuf

from .extensions import (
    CookieManager, DBusManager, DownloadManager, HistoryManager,
    SessionManager, TLSErrorByPassExtension, HSTSExtension, UserContentManager,
    PasswordManagerExtension)
from .utils import (
    cache_path, config_path, runtime_path, get_keyname, get_pretty_size)


faulthandler.enable()
log = logbook.Logger('roland')

Mode = enum.Enum('Mode', 'Insert Normal Motion SubCommand Prompt PassThrough')
HTMLNotification = collections.namedtuple('HTMLNotification', 'id title body')

DEFAULT_STYLE = b'''
    GtkEntry, GtkLabel {
        background: black;
        color: white;
    }
'''


def default_config():
    """Return absolute minimal config for
    a 'functioning' browser.

    Won't let you do much apart from
    quit.
    """
    from roland.api import lazy
    class config:
        commands = {
            'i': lazy.set_mode(Mode.Insert),
            ':': lazy.prompt_command(),
        }
    return config


def rename(name):
    def callable(func):
        func.__name__ = name
        return func
    return callable


def requires(*extensions):
    def inner(func):
        func.extensions = extensions
        return func
    return inner


def private(func):
    """Decorator for methods on BrowserCommands that shouldn't be displayed in
    the command suggestions.
    """
    func.private = True
    return func


request_counter = itertools.count(1)


def message_webprocess(command, *, page_id, profile, callback, **kwargs):
    request_id = next(request_counter)
    p = runtime_path('webprocess.{{}}.{}'.format(page_id), profile)
    addr = Gio.UnixSocketAddress.new(p)
    client = Gio.SocketClient.new()

    unpacker = msgpack.Unpacker()

    def connect_callback(obj, result, user_data):
        conn = client.connect_finish(result)
        ostream = conn.get_output_stream()

        # FIXME: make write async
        r = msgpack.dumps([request_id, command, kwargs])
        ostream.write_bytes(GLib.Bytes(r))

        istream = conn.get_input_stream()
        istream.read_bytes_async(8192, 1, None, read_callback, conn)

    def read_callback(obj, result, conn):
        istream = conn.get_input_stream()
        bytes = istream.read_bytes_finish(result)
        if not bytes.get_data():
            conn.close(None)

            response_id, notes = unpacker.unpack()
            if callback is not None:
                callback(notes)
        else:
            unpacker.feed(bytes.get_data())
            istream.read_bytes_async(8192, 1, None, read_callback, conn)

    client.connect_async(addr, None, connect_callback, None)


class BrowserCommands:
    @rename('tab-bar-width')
    def tab_bar_width(self, width):
        width = int(width)
        for b in self.roland.get_browsers():
            b.tab_title.set_width_chars(width)
            b.tab_title.set_max_width_chars(width)
        self.roland.config.tab_width = width

    @rename('toggle-tab-bar-visibility')
    def toggle_tab_visibility(self):
        notebook = self.roland.window.notebook
        notebook.set_show_tabs(not notebook.get_show_tabs())

    @rename('tab-bar-position')
    def tab_bar_position(self, position):
        self.roland.set_tab_position(position)

    @requires(PasswordManagerExtension)
    @rename('generate-password')
    def generate_password(self, *params):
        ext = self.roland.get_extension(PasswordManagerExtension)
        if not params:
            params = ['len=24', 'chars=all', 'mixed=yes']

        def parse_params():
            nonlocal params

            try:
                params = dict(kv.split('=') for kv in params)
            except Exception as e:
                self.roland.notify("Could not parse generation parameters: {}".format(e))
                params = {}

            params.setdefault('len', '24')
            params.setdefault('chars', 'all')
            params.setdefault('mixed', 'yes')
            return params

        def generate_password():
            params = parse_params()
            # FIXME: lookup domain in db and prompt to use that one or generate another
            allowable_chars = params['chars']
            if allowable_chars == 'all':
                allowable_chars = 'special,alpha'

            available_chars = ''
            if 'special' in allowable_chars:
                available_chars += '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
            if 'numbers' in allowable_chars:
                available_chars += '0123456789'
            if 'alpha' in allowable_chars:
                available_chars += 'abcdefghijklmnopqrstuvwxyz'
            if params['mixed'].lower() == 'yes':
                available_chars += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

            password = ''.join(random.sample(available_chars, int(params['len'])))
            return password

        password = generate_password()

        try:
            ext.unlock(self)
        except ValueError:
            self.roland.notify('Could not generate password')
        else:
            domain = urlparse.urlparse(self.webview.get_uri()).netloc
            description = 'Generated password for {}'.format(domain)
            form = {
                'input[type=password]': password,
            }
            ext.save_form(domain, form, description=description)

            message_webprocess(
                'form_fill',
                profile=self.roland.profile,
                page_id=self.webview.get_page_id(),
                callback=None,
                **{k.decode('utf8'): v for (k, v) in form.items()}
            )

    @requires(PasswordManagerExtension)
    @rename('form-save')
    def form_save(self):
        forms = {}
        ext = self.roland.get_extension(PasswordManagerExtension)

        def serialised_form(form):
            self.remove_overlay()

            domain = urlparse.urlparse(self.webview.get_uri()).netloc

            try:
                ext.unlock(self)
            except ValueError:
                self.roland.notify('Could not save form')
            else:
                ext.save_form(domain, form)

        def display_choices(choices):
            forms.update(choices)
            result = self.entry_line.blocking_prompt(
                prompt='Select form to save',
                suggestions=sorted(k.decode('utf8') for k in forms.keys()),
                force_match=True,
            )
            form_id = forms[result.encode('utf8')].decode('utf8')

            message_webprocess(
                'serialise_form',
                form_id=form_id,
                profile=self.roland.profile,
                page_id=self.webview.get_page_id(),
                callback=serialised_form,
            )

        message_webprocess(
            'highlight',
            selector='form',
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=display_choices,
        )

    @requires(PasswordManagerExtension)
    @rename('form-fill')
    def form_fill(self):
        ext = self.roland.get_extension(PasswordManagerExtension)

        domain = urlparse.urlparse(self.webview.get_uri()).netloc
        try:
            ext.unlock(self)
        except ValueError:
            return
        else:
            choices = ext.get_for_domain(domain.encode('utf8'))

        if not choices:
            self.roland.notify("No form fills for {}".format(domain))
            return

        suggestions = ['{}: {} (last used {})'.format(i, choice.description.decode('utf8'), choice.last_used)
                       for (i, choice) in enumerate(choices)]

        result = self.entry_line.blocking_prompt(
            prompt='Select form fill for {}'.format(domain),
            suggestions=suggestions,
            force_match=True,
        )

        if result is None:
            return

        index, ignore = result.split(':', 1)
        choice = choices[int(index)]

        form_data = choice.form_data

        ext.update_last_used(choice.id)

        message_webprocess(
            'form_fill',
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=None,
            **{k.decode('utf8'): v for (k, v) in form_data.items()}
        )

    @private
    def select_window(self, selected=None):
        def present_window(selected):
            try:
                win_id = name_to_id[selected]
                win = id_to_window[win_id]
            except KeyError:
                pass
            else:
                win.present()

        browsers = self.roland.get_browsers()
        name_to_id = {'%d: %s' % (i, w.title.title): i for (i, w) in enumerate(browsers, 1)}
        id_to_window = {i: w for (i, w) in enumerate(browsers, 1)}

        if selected is not None:
            if selected == -1:
                selected = max(id_to_window)

            try:
                win = id_to_window[selected]
            except KeyError:
                pass
            else:
                win.present()
            return True
        else:
            self.entry_line.prompt(
                present_window, prompt="Window", force_match=True, glob=True,
                suggestions=sorted(name_to_id))
        return True

    @private
    def open(self, url=None, new_window=False, background=False):
        def open_window(url):
            if background or new_window:
                if new_window:
                    log.info("Loading {} in a new window", url)
                elif background:
                    log.info("Loading {} in a background window", url)
                self.roland.new_window(url, background=background)
            else:
                log.info("Loading {}", url)
                self.webview.load_uri(url)

        if not url:
            prompt = 'open'
            if background:
                prompt += ' (new background window)'
            elif new_window:
                prompt += ' (new window)'
            self.entry_line.prompt(
                open_window, prompt=prompt, glob=True,
                suggestions=self.roland.most_popular_urls())
        else:
            open_window(url)
        return True

    @requires(SessionManager)
    @rename('save-session')
    def save_session(self):
        """Save the current session."""
        self.roland.get_extension(SessionManager).save_session()

    @private
    def open_or_search(self, text=None, new_window=False, background=False):
        def callback(obj, result, text):
            resolver = Gio.Resolver.get_default()

            try:
                resolver.lookup_by_name_finish(result)
            except Exception:
                self.search(text, new_window=new_window)
            else:
                self.open('http://'+text, new_window=new_window)

        def open_or_search(text):
            if urlparse.urlparse(text).scheme:
                self.open(text, new_window=new_window, background=background)
            else:
                if '://' not in text:
                    maybe_url = 'http://{}'.format(text)
                else:
                    maybe_url = text

                maybe_hostname = urlparse.urlparse(maybe_url).hostname

                if ' ' in maybe_hostname or '_' in maybe_hostname:
                    self.search(text, new_window=new_window)
                else:
                    resolver = Gio.Resolver.get_default()
                    resolver.lookup_by_name_async(
                        text.split('/')[0].split(':')[0], None, callback, text)

        if text is None:
            prompt = 'open/search'
            if background:
                prompt += ' (new background window)'
            elif new_window:
                prompt += ' (new window)'

            self.entry_line.prompt(
                open_or_search, prompt=prompt, glob=True,
                suggestions=self.roland.most_popular_urls())
        else:
            open_or_search(text)
        return True

    @private
    def open_modify(self, new_window=False):
        def open_window(url):
            self.open(url, new_window=new_window)

        prompt = 'open'
        if new_window:
            prompt += ' (new window)'

        self.entry_line.prompt(
            open_window, prompt=prompt, initial=self.webview.get_uri() or '')
        return True

    @private
    def navigate_up(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)
        if url.path not in ('', '/'):
            parent_path = str(pathlib.Path(url.path).parent)
            url = url._replace(path=parent_path).geturl()
            self.open(url)

    @private
    def navigate_top(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)._replace(path='').geturl()
        self.open(url)

    def close(self):
        """Close the current window. Quits if there's only one window."""
        # explicitly trigger quitting in case downloads are in progress
        if len(self.roland.get_browsers()) == 1:
            self.roland.quit()
            return

        self.roland.add_close_history(self.webview.get_uri())

    @private
    def change_user_agent(self, user_agent=None):
        def change_user_agent(user_agent):
            if not user_agent:
                return
            for browser in self.roland.get_browsers():
                browser.web_view.get_settings().props.user_agent = user_agent

        if user_agent is None:
            user_agents = [self.roland.config.default_user_agent] + self.roland.hooks('user_agent_choices', default=[])
            self.entry_line.prompt(change_user_agent, prompt="User Agent", suggestions=user_agents)
        else:
            change_user_agent(user_agent)
        return True

    @private
    def search(self, text=None, new_window=False):
        def search(text):
            search_url = self.roland.config.search_page.format(text)
            url = self.roland.hooks('search_url', text, default=None) or search_url
            self.open(url, new_window=new_window)

        if text is None:
            self.entry_line.prompt(search, prompt='Search')
        else:
            search(text)
        return True

    def back(self):
        """Go backward in navigation history."""
        self.webview.go_back()

    def forward(self):
        """Go forward in navigation history."""
        self.webview.go_forward()

    def javascript(self, script):
        """Execute given JavaScript."""
        self.webview.run_javascript(script, None, None, None)

    @rename('view-source')
    def view_source(self):
        def have_source(source):
            html = source[b'html'].decode('utf8')

            uri = self.webview.get_uri()

            try:
                import pygments
                import pygments.lexers
                import pygments.formatters
            except ImportError:
                self.roland.new_window(uri, text=html)
            else:
                lexer = pygments.lexers.HtmlLexer()
                formatter = pygments.formatters.HtmlFormatter(
                    full=True, linenos='table')
                highlighted = pygments.highlight(html, lexer, formatter)
                self.roland.new_window(uri, html=highlighted)

        message_webprocess(
            'get_source',
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=have_source
        )

    @private
    def remove_overlay(self):
        message_webprocess(
            'remove_overlay',
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=None,
        )

    @private
    def set_log_level(self, level):
        level = int(level)

        message_webprocess(
            'set_log_level',
            log_level=str(level),
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=None,
        )


    @private
    def follow(self, new_window=False):
        def open_link(key):
            try:
                click_id = click_map[key.encode('utf8')].decode('utf8')
            except KeyError:
                self.remove_overlay()
            else:
                message_webprocess(
                    'click',
                    click_id=click_id,
                    new_window=str(new_window),
                    profile=self.roland.profile,
                    page_id=self.webview.get_page_id(),
                    callback=None
                )

        def display_choices(choices):
            click_map.update(choices)

            prompt = 'Follow'
            if new_window:
                prompt += ' (new window)'
            suggestions = sorted([
                s.decode('utf8').replace('\n', ' ')
                for s in click_map.keys()], key=lambda s: int(s.split(':')[0]))
            self.entry_line.prompt(
                open_link, prompt=prompt, cancel=self.remove_overlay,
                suggestions=suggestions, force_match=True, beginning=False)

        click_map = {}

        if new_window:
            selector = 'a'
        else:
            selector = "a, input:not([type=hidden]), textarea, select, button"

        message_webprocess(
            'highlight',
            selector=selector,
            profile=self.roland.profile,
            page_id=self.webview.get_page_id(),
            callback=display_choices,
        )

        return True

    @private
    def search_page(self, text=None, forwards=True, case_insensitive=None):
        def search_page(text):
            nonlocal case_insensitive
            if case_insensitive is None:
                case_insensitive = text.lower() == text

            finder = self.webview.get_find_controller()

            if text == '':
                finder.search_finish()
                return

            self.search_forwards = forwards

            options = WebKit2.FindOptions.WRAP_AROUND
            if not forwards:
                options |= WebKit2.FindOptions.BACKWARDS

            if case_insensitive:
                options |= WebKit2.FindOptions.CASE_INSENSITIVE

            max_count = 1000  # FIXME: configurable?
            finder.search(text, options, max_count)

        if text is None:
            self.entry_line.prompt(search_page, prompt='Search page')
        else:
            search_page(text)
        return True

    @private
    def next_search_result(self, forwards=True):
        finder = self.webview.get_find_controller()

        if forwards == self.search_forwards:
            finder.search_next()
        else:
            finder.search_previous()

    @private
    def zoom_in(self):
        self.webview.set_zoom_level(self.webview.get_zoom_level() + 0.1)
        # binding for C-Up scrolls, this stops that
        return True

    @private
    def zoom_out(self):
        zoom_level = self.webview.get_zoom_level() - 0.1
        if zoom_level >= 0.1:
            self.webview.set_zoom_level(zoom_level)
        # binding for C-Down scrolls, this stops that
        return True

    def zoom(self, level):
        """Set zoom to given level, e.g. 'zoom 200' for 200%."""
        self.webview.set_zoom_level(float(level)/100)

    @private
    def zoom_reset(self):
        self.zoom(getattr(self.roland.config, 'default_zoom', 100))

    def stop(self):
        """Stop loading the current page."""
        self.webview.stop_loading()

    @private
    def move(self, x=0, y=0):
        self.javascript('window.scrollBy(%d, %d);' % (x*30, y*30))

    def shell(self):
        """Open a Python REPL on stdout."""
        self.roland.notify('Starting shell...')
        t = threading.Thread(target=code.interact, kwargs={'local': {'roland': self.roland}})
        t.daemon = True
        t.start()

    def quit(self):
        """Close the browser."""
        self.roland.quit()

    def reload(self):
        """Reload the page."""
        self.webview.reload()

    @rename('reload-bypass-cache')
    def reload_bypass_cache(self):
        """Reload the page, ignoring the disk cache."""
        self.webview.reload_bypass_cache()

    @rename('clear-cache')
    def clear_cache(self):
        """Clear the disk cache."""
        context = WebKit2.WebContext.get_default()
        context.clear_cache()

    def help(self):
        """This very informative help."""
        available_commands = sorted(self.roland.get_commands())
        help_info = '\t' + '\n\t'.join('{} - {}'.format(cmd, self.roland.get_help(cmd)) for cmd in available_commands)

        config = self.roland.config

        command_info = '\t' + '\n\t'.join(sorted([
            '{}: {}'.format(key, command)
            for (key, command) in config.commands.items()
        ]))

        misc_info = '\t' + '\n\t'.join([
            'home page: {}'.format(config.home_page),
            'search page: {}'.format(config.search_page),
            'font: {}'.format(config.font),
        ])

        page_info = '\n'.join([
            'Commands',
            help_info,
            '\nBindings:',
            command_info,
            '\nMisc:',
            misc_info
        ])
        self.roland.new_window(None, page_info)

    # FIXME: make host optional - if the current page has an invalid
    # certificate, bypass that instead.
    @requires(TLSErrorByPassExtension)
    def bypass(self, host):
        """Set up a certificate exclusion for a given domain."""
        manager = self.roland.get_extension(TLSErrorByPassExtension)
        manager.bypass(host)

    @requires(DownloadManager)
    @private
    def cancel_download(self):
        if not self.roland.downloads:
            self.roland.notify("No downloads in progress")
            return

        def cancel_download(key):
            try:
                download = self.roland.downloads[key]
            except KeyError:
                self.roland.notify("No download by that name")
            else:
                download.cancel()

        self.entry_line.prompt(
            cancel_download, prompt="Cancel download", force_match=True,
            glob=True, suggestions=list(self.roland.downloads.keys()))

        return True

    @rename('inspector-show')
    def inspector_show(self):
        self.webview.get_inspector().show()

    @rename('inspector-hide')
    def inspector_hide(self):
        self.webview.get_inspector().close()

    @private
    def undo_close(self):
        self.roland.undo_close()

    @requires(DownloadManager)
    @private
    def list_downloads(self):
        if not self.roland.downloads:
            self.roland.notify("No downloads in progress")
            return

        for location, download in self.roland.downloads.items():
            if download.get_progress() == 1.0:
                continue  # completed while we were doing this
            progress = get_pretty_size(download.get_current_size())
            total = get_pretty_size(download.get_total_size())
            self.roland.notify('%s - %s out of %s' % (location, progress, total))

    @private
    def get_certificate_info(self, certificate=None):
        certificate = certificate or self.pem_certificate

        if not certificate:
            return ''
        else:
            from OpenSSL import crypto

            x509 = crypto.load_certificate(
                crypto.FILETYPE_PEM, certificate)

            extensions = [
                x509.get_extension(i) for i in
                range(x509.get_extension_count())
            ]

            keyed_extensions = {}
            for ext in extensions:
                name = ext.get_short_name().decode('utf8')
                try:
                    value = str(ext)
                except Exception:
                    value = 'Value unavailable'
                keyed_extensions[name] = value

            buf = certificate
            buf += '\nsubject:'
            buf += '/'.join(
                b'='.join(kv).decode('utf8') for kv in x509.get_subject().get_components())

            buf += '\nissuer:'
            buf += '/'.join(
                b'='.join(kv).decode('utf8') for kv in x509.get_issuer().get_components())

            buf += '\nsignature algorithm: {}'.format(x509.get_signature_algorithm())

            def time_parse(raw):
                raw = raw.decode('utf8').replace('Z', '')
                return datetime.datetime.strptime(raw, '%Y%m%d%H%M%S')

            start = time_parse(x509.get_notBefore())
            end = time_parse(x509.get_notAfter())
            buf += '\nvalid time range: {} - {}'.format(start, end)

            buf += '\nextensions:'
            buf += ', '.join(sorted(keyed_extensions))

            buf += '\nsubjectAltName:\n\t'
            buf += keyed_extensions.get('subjectAltName', '').replace(', ', '\n\t')
            return buf

    def certificate(self):
        """Display certificate information for the given website, if available."""
        cert_info = self.get_certificate_info()

        if cert_info:
            self.roland.new_window(None, cert_info)
        else:
            self.roland.notify("No certificate information available")


class EntryLine(Gtk.VBox):
    def __init__(self, status_line, browser, font):
        Gtk.VBox.__init__(self)

        self.status_line = status_line
        self.browser = browser
        self.font = font

        self.label = Gtk.Label()
        self.label.modify_font(font)
        self.label.set_alignment(0.0, 0.5)

        self.input = Gtk.Entry()
        self.input.set_has_frame(False)
        self.input.modify_font(font)

        self.input.connect('key-release-event', self.on_key_release_event)
        self.input.connect('backspace', self.on_key_release_event, None)

        self.input_container = Gtk.HBox()
        self.input_container.pack_start(self.label, False, False, 0)
        self.input_container.pack_start(self.input, True, True, 0)

        self.pack_end(self.input_container, False, False, 0)

    def completion(self, forward=True):
        if not self.lock_suggestions:
            self.lock_suggestions = True
            self.position = -1

        labels = [l.get_text() for l in self.get_children() if isinstance(l, Gtk.Label)]

        if forward:
            self.position = self.position + 1
            if self.position == len(labels):
                self.position = 0
        else:
            self.position = self.position - 1
            if self.position <= -1:
                self.position = len(labels) - 1

        if labels:
            self.input.set_text(labels[self.position])
            self.input.set_position(-1)

    def on_key_release_event(self, widget, event):
        keyname = get_keyname(event)
        if keyname in ('ISO_Left_Tab', 'Tab'):
            return
        self.lock_suggestions = False

        self.remove_completions()
        self.add_completions()

        return False

    def blocking_prompt(self, **kwargs):
        result = None

        def callback(value):
            nonlocal result
            result = value
            loop.stop()

        def cancel():
            loop.stop()

        self.browser.present()
        self.prompt(callback, cancel=cancel, **kwargs)
        import gbulb
        loop = gbulb.get_event_loop()
        loop.run()

        return result

    def prompt(self, callback, suggestions=None, force_match=False,
               glob=False, prompt='', initial='', cancel=None,
               case_sensitive=True, beginning=True, private=False):
        self.callback = callback
        self.suggestions = suggestions or []
        self.force_match = force_match
        self.glob = glob
        self.lock_suggestions = False
        self.cancel = cancel
        self.case_sensitive = case_sensitive
        self.beginning = beginning
        self.input.set_visibility(not private)

        self.label.set_markup('{}:'.format(prompt))
        self.label.show()
        self.show()
        self.input.set_text(initial)
        if initial:
            self.input.set_position(-1)
        self.input.show()
        self.status_line.hide()
        self.get_toplevel().set_focus(self.input)
        self.input.select_region(-1, -1)

        self.remove_completions()
        self.add_completions()
        self.browser.set_mode(Mode.Prompt)

    def fire_cancel_callback(self):
        if self.cancel:
            cancel, self.cancel = self.cancel, None
            cancel()

    def fire_callback(self):
        t = self.input.get_text()
        if self.force_match:
            labels = [l.get_text() for l in self.get_children() if isinstance(l, Gtk.Label)]
            if labels and t not in labels:
                t = labels[0]

        assert self.callback is not None

        callback, self.callback = self.callback, None
        callback(t)

    def hide_input(self):
        self.hide()
        self.status_line.show()
        self.get_toplevel().set_focus(None)

    def add_completions(self):
        t = self.input.get_text()
        if self.glob:
            entries = fnmatch.filter(self.suggestions, '*{}*'.format(t))
        else:
            if self.case_sensitive:
                f = str.casefold
            else:
                f = lambda a: a

            if self.beginning:
                condition = str.startswith
            else:
                condition = str.__contains__
            entries = [e for e in self.suggestions if condition(f(e), f(t))]

        for entry in reversed(entries[:20]):
            # FIXME: highlight matching portion
            l = Gtk.Label()
            l.set_alignment(0.0, 0.5)
            l.set_text(entry)
            l.modify_font(self.font)
            self.pack_end(l, False, False, 0)
            l.show()

    def remove_completions(self):
        for child in self.get_children():
            if child != self.input_container:
                self.remove(child)


class StatusLine(Gtk.HBox):
    def __init__(self, font):
        Gtk.HBox.__init__(self)

        self.left = Gtk.Label()
        self.middle = Gtk.Label()
        self.right = Gtk.Label()

        self.left.set_alignment(0.0, 0.5)
        self.right.set_alignment(1.0, 0.5)

        self.left.set_name('NormalMode')

        for i in [self.left, self.middle, self.right]:
            i.modify_font(font)
            self.add(i)

        self.buffered_command = ''
        self.uri = ''
        self.trusted = True

    def set_uri(self, uri):
        self.uri = uri
        self.update_right()

    def set_mode(self, text, name=None):
        self.left.set_markup(text)
        if name is not None:
            self.left.set_name(name)

    def set_trust(self, trusted):
        self.trusted = trusted
        self.update_right()

    def set_buffered_command(self, text):
        self.buffered_command = text
        self.update_right()

    def update_right(self):
        text = []
        if self.buffered_command:
            text.append('<b><span foreground="#01a0e4">{}</span></b>'.format(self.buffered_command))

        if self.uri:
            uri = ''.join([
                '<span foreground="{color}"><b>'.format(color='limegreen' if self.trusted else 'red'),
                html.escape(self.uri),
                '</b></span>',
            ])
            text.append(uri)

        self.right.set_markup(' '.join(text))


class BrowserTitle:
    title = ''
    progress = 0

    def __str__(self):
        if self.progress < 100:
            return '[{}%] Loading... {}'.format(self.progress, self.title)
        return self.title or 'No title'


class BrowserView(BrowserCommands):
    pem_certificate = None

    def on_decide_policy(self, webview, decision, decision_type):
        if decision_type != WebKit2.PolicyDecisionType.RESPONSE:
            return False  # let default action take place

        if decision.is_mime_type_supported():
            decision.use()

            if self.webview == webview:
                self.status_line.set_uri(webview.get_uri())
                self.status_line.set_trust(True)  # assume trust until told otherwise
            return False

        download_manager = self.roland.get_extension(DownloadManager)

        uri = webview.get_uri()

        if download_manager is None:
            self.roland.notify("Cannot display {}, and download manager is not enabled.".format(uri))
            decision.ignore()
        else:
            decision.download()

        return False

    def __init__(self, roland):
        super().__init__()
        self.roland = roland
        self.search_forwards = True
        self.title = BrowserTitle()
        self.webview = None
        self.sub_commands = None

    @classmethod
    def from_webview(cls, browser, roland):
        self = cls(roland)
        self.webview = browser
        self.webview.connect('web-view-ready', lambda *args: self.start(None))
        return self

    def start(self, url):
        self.connect('key-press-event', self.on_key_press_event)

        # will already be initialised for popups
        if self.webview is None:
            self.webview = self.roland.new_webview()

        settings = self.webview.get_settings()
        settings.props.user_agent = self.roland.config.default_user_agent
        settings.props.enable_frame_flattening = getattr(self.roland.config, 'enable_frame_flattening', False)
        settings.props.enable_webgl = getattr(self.roland.config, 'enable_webgl', False)
        settings.props.enable_accelerated_2d_canvas = getattr(self.roland.config, 'enable_accelerated_2d_canvas', False)
        settings.props.enable_developer_extras = True

        self.status_line = StatusLine(self.roland.font)
        self.entry_line = EntryLine(self.status_line, self, self.roland.font)

        self.set_mode(Mode.Normal)

        self.zoom_reset()

        self.webview.connect('notify::favicon', self.update_window_icon)
        self.webview.connect('notify::title', self.update_title_from_event)
        self.webview.connect('notify::estimated-load-progress', self.update_title_from_event)
        self.webview.connect('authenticate', self.on_authenticate)
        self.webview.connect('load-changed', self.on_load_status)
        self.webview.connect('load-failed-with-tls-errors', self.on_load_failed_with_tls_errors)
        self.webview.connect('close', lambda *args: self.destroy())
        self.webview.connect('create', self.on_create_web_view)
        self.webview.connect('show-notification', self.on_show_notification)
        self.webview.connect('permission-request', self.on_permission_request)
        self.webview.connect('web-process-crashed', self.on_web_process_crashed)
        self.webview.connect('resource-load-started', self.on_resource_load_started)
        self.webview.connect('script-dialog', self.on_script_dialog)

        # I never want context menus.
        self.webview.connect('context-menu', lambda *args: True)

        finder = self.webview.get_find_controller()
        finder.connect('failed-to-find-text', self.failed_to_find_text)

        self.webview.connect('decide-policy', self.on_decide_policy)

        main_ui_box = Gtk.VBox()
        scrollable = Gtk.ScrolledWindow()
        scrollable.add(self.webview)
        main_ui_box.pack_start(scrollable, True, True, 0)

        main_ui_box.pack_end(self.status_line, False, False, 0)
        main_ui_box.pack_end(self.entry_line, False, False, 0)

        self.add(main_ui_box)
        self.show_all()
        self.entry_line.hide_input()

        # will be None for popups
        if url is not None:
            self.open_or_search(url)

    def on_load_failed_with_tls_errors(self, webview, failing_uri, certificate, error):
        if self.webview == webview:
            self.pem_certificate = certificate.props.certificate_pem

        certificate_info = self.get_certificate_info(certificate.props.certificate_pem)

        reasons = []
        if error & Gio.TlsCertificateFlags.UNKNOWN_CA:
            reasons.append('Unknown CA')
        if error & Gio.TlsCertificateFlags.BAD_IDENTITY:
            reasons.append('Bad Identity')
        if error & Gio.TlsCertificateFlags.NOT_ACTIVATED:
            reasons.append('Certificate not activated yet')
        if error & Gio.TlsCertificateFlags.NOT_ACTIVATED:
            reasons.append('Certificate has expired')
        if error & Gio.TlsCertificateFlags.REVOKED:
            reasons.append('Certificate has been revoked')
        if error & Gio.TlsCertificateFlags.REVOKED:
            reasons.append('Certificate algorithm is insecure')
        if error & Gio.TlsCertificateFlags.GENERIC_ERROR:
            reasons.append('Unknown generic error occurred')

        domain = urlparse.urlparse(failing_uri).netloc

        cert_error_path = cache_path(
            '{}/tls/error/{}'.format(self.roland.profile, domain))

        with open(cert_error_path, 'w') as f:
            f.write(certificate.props.certificate_pem)

        help = "To attempt to bypass this error, run `:bypass {}` and reload the page".format(domain)
        html = '<pre>Error going to {}: {}\n{}\n\n{}</pre>'.format(
            failing_uri, ', '.join(reasons), help, certificate_info)
        webview.load_alternate_html(html, failing_uri)

        self.title.title = 'An Error Occurred loading {}'.format(failing_uri)
        self.title.progress = 100
        self.set_title(str(self.title))
        return True

    def on_authenticate(self, webview, request):
        def ask_user():
            prompt = 'Enter username for {}:{} ({})'.format(request.get_host(), request.get_port(), request.get_realm())

            if request.is_retry():
                prompt += ' <span color="red">(retry)</span>'
            username = self.entry_line.blocking_prompt(prompt=prompt)
            if username is None:
                request.cancel()
                return None, None

            prompt = 'Enter password for {}:{} ({})'.format(request.get_host(), request.get_port(), request.get_realm())
            password = self.entry_line.blocking_prompt(
                prompt=prompt,
                private=True
            )
            if password is None:
                request.cancel()
                return None, None

            return username, password

        ext = self.roland.get_extension(PasswordManagerExtension)
        if ext is None:
            username, password = ask_user()
            if username is not None and password is not None:
                cred = WebKit2.Credential.new(
                    username, password, WebKit2.CredentialPersistence.FOR_SESSION)
                request.authenticate(cred)
            return True

        try:
            ext.unlock(self)
        except ValueError:
            self.roland.notify('Could not generate password')
        else:
            domain = ':'.join(map(str, [
                request.get_host(), request.get_port(), request.get_realm()
            ]))

            choices = ext.get_for_domain(domain.encode('utf8'))

            # FIXME: display choices prompt instead of assuming first.
            if choices and not request.is_retry():
                choice = choices[0]

                username = choice.form_data[b'username'].decode('utf8')
                password = choice.form_data[b'password'].decode('utf8')
            else:
                username, password = ask_user()

                if username is not None and password is not None:
                    ext.save_form(domain, dict(username=username, password=password))

            if username is not None and password is not None:
                cred = WebKit2.Credential.new(
                    username, password, WebKit2.CredentialPersistence.FOR_SESSION)
                request.authenticate(cred)
        return True

    def on_load_status(self, webview, load_status):
        if self.webview != webview:
            return
        if load_status == WebKit2.LoadEvent.COMMITTED:
            is_https, certificate, flags = webview.get_tls_info()

            if is_https and certificate is not None:
                self.pem_certificate = certificate.props.certificate_pem
            else:
                self.pem_certificate = None

            if is_https and (certificate is None or int(flags) != 0):
                self.status_line.set_trust(False)
            else:
                self.status_line.set_trust(True)

    def failed_to_find_text(self, finder):
        text = finder.get_search_text()
        if text is None:
            return
        self.roland.notify(
            'No match found for "{}"'.format(text))

    def on_permission_request(self, webview, permission):
        # FIXME: config hook for this.
        if isinstance(permission, WebKit2.NotificationPermissionRequest):
            permission.allow()
        else:
            # FIXME: config hook for this.
            permission.deny()
        return True

    def on_web_process_crashed(self, webview):
        self.roland.notify("Web process for {} crashed.".format(webview.get_uri()), critical=True)

    def on_resource_load_started(self, webview, resource, request):
        def finished(resource, *ignored):
            response = resource.get_response()

            if response is None:
                return
            headers = response.get_http_headers()

            if headers is None:
                return
            hsts = headers.get_one('Strict-Transport-Security')

            if hsts is not None and self.roland.is_enabled(HSTSExtension):
                ext = self.roland.get_extension(HSTSExtension)
                ext.add_entry(response.get_uri(), hsts)

        resource.connect('finished', finished)

    def on_script_dialog(self, webview, dialog):
        if dialog.get_dialog_type() == WebKit2.ScriptDialogType.ALERT:
            self.roland.notify(dialog.get_message())
            return True
        elif dialog.get_dialog_type() == WebKit2.ScriptDialogType.PROMPT:
            result = self.entry_line.blocking_prompt(
                initial=dialog.prompt_get_default_text(),
                prompt=dialog.get_message(),
            )
            if result is not None:
                dialog.prompt_set_text(result)
            return True
        elif dialog.get_dialog_type() == WebKit2.ScriptDialogType.CONFIRM:
            result = self.entry_line.blocking_prompt(
                prompt=dialog.get_message(),
                suggestions=['ok', 'cancel'],
                force_match=True,
            )

            dialog.confirm_set_confirmed(result == 'ok')
            return True
        return False

    def update_window_icon(self, widget, event):
        icon = self.webview.get_favicon()
        if icon is not None:
            pixbuf = Gdk.pixbuf_get_from_surface(
                icon, 0, 0, icon.get_width(), icon.get_height())
            self.set_icon(pixbuf)
        else:
            self.set_icon(None)

    def update_title_from_event(self, widget, event):
        if event.name == 'title':
            title = self.webview.get_title()
            self.title.title = title
        elif event.name == 'estimated-load-progress':
            self.title.progress = int(self.webview.get_estimated_load_progress() * 100)

        self.set_title(str(self.title))

    def on_show_notification(self, webview, notification):
        notification = HTMLNotification(
            notification.get_id,
            notification.get_title(),
            notification.get_body(),
        )
        if self.roland.hooks('should_display_notification', notification, default=True):
            self.roland.notify(notification.body, header=notification.title)

    def on_create_web_view(self, webview, webframe):
        if self.roland.hooks('should_open_popup', webframe.get_uri(), default=True):
            v = self.roland.new_webview()
            self.roland.add_window(self.roland.browser_view.from_webview(v, self.roland))
            return v

    def on_key_press_event(self, widget, event):
        keyname = get_keyname(event)
        if keyname in ('Shift_L', 'Shift_R'):
            return

        if self.mode in (Mode.Normal, Mode.SubCommand):
            available_commands = {
                Mode.Normal: self.roland.config.commands,
                Mode.SubCommand: self.sub_commands,
            }[self.mode]

            orig_mode = self.mode

            try:
                command = available_commands[keyname]
            except KeyError:
                pass
            else:
                log.info('running command "{}"', command)
                try:
                    return command(self)
                except Exception as e:
                    self.roland.notify("Error invoking command '{}': {}'".format(keyname, e))
                    traceback.print_exc()
            finally:
                if orig_mode == Mode.SubCommand and self.mode != Mode.Prompt:
                    self.set_mode(Mode.Normal)
                    self.sub_commands = None
        elif self.mode == Mode.Prompt:
            if keyname in ('Escape', 'C-c'):
                self.set_mode(Mode.Normal)
                self.entry_line.hide_input()
                self.entry_line.fire_cancel_callback()
            elif keyname == 'Return':
                self.set_mode(Mode.Normal)
                self.entry_line.hide_input()
                try:
                    self.entry_line.fire_callback()
                except Exception as e:
                    self.roland.notify("Error invoking callback: {}'".format(e))
                    traceback.print_exc()
            elif keyname == 'ISO_Left_Tab':
                self.entry_line.completion(forward=False)
                return True
            elif keyname == 'Tab':
                self.entry_line.completion(forward=True)
                return True
            return False
        elif self.mode == Mode.PassThrough:
            # FIXME: would be great if this was configurable :/
            if keyname == 'Insert':
                self.set_mode(Mode.Normal)
        else:
            assert self.mode == Mode.Insert

            if keyname == 'Escape':
                self.set_mode(Mode.Normal)

    def set_mode(self, mode, *args):
        assert mode in Mode
        self.mode = mode

        log.info("Setting mode to {}", mode)

        if mode == Mode.Normal:
            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>NORMAL</b>', name='NormalMode')
            self.status_line.set_buffered_command('')
        elif mode == Mode.SubCommand:
            command, self.sub_commands = args

            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>COMMAND</b>', name='CommandMode')
            self.status_line.set_buffered_command(command)
        elif mode == Mode.Prompt:
            pass
        elif mode == Mode.PassThrough:
            self.webview.set_can_focus(True)
            self.webview.grab_focus()
            self.status_line.set_mode('<b>PASSTHROUGH</b> (press insert to return to normal mode)', name='PassThroughMode')
            self.status_line.set_buffered_command('')
            # stop event propagation to prevent dumping 'Insert' into webpage
            return True
        else:
            assert mode == Mode.Insert, "Unknown Mode %s" % mode
            self.webview.set_can_focus(True)
            self.webview.grab_focus()
            self.status_line.set_mode('<b>INSERT</b>', name='InsertMode')
            self.status_line.set_buffered_command('')
            # stop event propagation to prevent dumping 'i' into webpage
            return True

    def prompt_command(self):
        def run_command(text):
            if not text.strip():
                return
            command = list(shlex.split(text))
            command_name, args = command[0], command[1:]
            self.run_command(command_name, *args)

        self.entry_line.prompt(
            run_command, prompt='command', force_match=True,
            suggestions=self.roland.get_commands(), beginning=False)
        return True

    def run_command(self, name, *args):
        log.info('Running "{}" command', name)
        try:
            command = getattr(self, name)
        except AttributeError:
            for command in dir(BrowserCommands):
                func = getattr(self, command)
                if getattr(func, '__name__', None) == name:
                    command = func
                    break
            else:
                self.roland.notify("No such command '{}'".format(name))
                return

        try:
            command(*args)
        except Exception as e:
            self.roland.notify("Error calling '{}': {}".format(name, str(e)))
            traceback.print_exc()


class BrowserWindow(BrowserView, Gtk.Window):
    def start(self, url):
        self.set_default_size(1000, 800)
        super().start(url)

    def close(self):
        super().close()
        Gtk.Window.close(self)
        Gtk.Window.destroy(self)


class MultiTabBrowserWindow(BrowserWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_show_border(False)
        self.add(self.notebook)
        self.connect('key-press-event', self.on_key_press_event)

    def on_key_press_event(self, widget, event):
        i = self.notebook.get_current_page()
        page = self.notebook.get_nth_page(i)
        assert isinstance(page, BrowserTab), type(page)
        return page.on_key_press_event(widget, event)

    def add(self, widget):
        if widget is self.notebook:
            super().add(widget)
        else:
            assert isinstance(widget, BrowserTab), type(widget)
            tab_widget = Gtk.HBox()
            if getattr(self.roland.config, 'show_favicons', True):
                tab_widget.pack_start(widget.tab_icon, False, False, 5)
            tab_widget.pack_start(widget.tab_title, True, True, 0)
            tab_widget.show_all()
            self.notebook.append_page(widget, tab_widget)


class BrowserTab(BrowserView, Gtk.VBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tab_title = Gtk.Label('empty tab')
        self.tab_icon = Gtk.Image()
        self.tab_title.modify_font(self.roland.font)
        self.tab_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.tab_title.set_line_wrap(False)

        tab_width = getattr(self.roland.config, 'tab_width', 30)
        self.tab_title.set_width_chars(tab_width)
        self.tab_title.set_max_width_chars(tab_width)

    def set_title(self, text):
        self.tab_title.set_text(text)
        self.tab_title.set_tooltip_text(text)

    def set_icon(self, icon):
        if icon is None:
            self.tab_icon.set_from_pixbuf(None)
        else:
            self.tab_icon.set_from_pixbuf(
                icon.scale_simple(32, 32, GdkPixbuf.InterpType.HYPER))

    def set_focus(self, widget):
        win = self.get_ancestor(Gtk.Window)
        if win is not None:
            win.set_focus(widget)

    def present(self):
        notebook = self.roland.window.notebook
        notebook.set_current_page(notebook.page_num(self))

    def close(self):
        super().close()
        notebook = self.roland.window.notebook
        notebook.remove_page(notebook.page_num(self))
        self.destroy()


class Roland(Gtk.Application):
    __gsignals__ = {
        'new_browser': (GObject.SIGNAL_RUN_LAST, None, (str, str, str, bool)),
        'profile_set': (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    browser_view = BrowserTab

    def __init__(self):
        Gtk.Application.__init__(
            self, application_id='deschain.roland',
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.setup_run = False
        self.connect('command-line', self.on_command_line)
        logbook.set_datetime_format('local')
        logbook.NullHandler(level=0).push_application()
        logbook.StderrHandler(level='INFO').push_application()
        logbook.RotatingFileHandler(config_path('roland.log'), level='INFO', bubble=True).push_application()

        self.previous_uris = []
        self.load_config()
        self.before_run()

    def get_browsers(self):
        if self.browser_view == BrowserTab:
            notebook = self.window.notebook
            return [notebook.get_nth_page(i) for i in range(notebook.get_n_pages())]
        else:
            return self.get_windows()

    def next_tab(self):
        if self.browser_view is not BrowserTab:
            return
        notebook = self.window.notebook
        if notebook.get_current_page() + 1 == notebook.get_n_pages():
            notebook.set_current_page(0)
        else:
            self.window.notebook.next_page()

    def prev_tab(self):
        if self.browser_view is not BrowserTab:
            return
        notebook = self.window.notebook
        if notebook.get_current_page() == 0:
            notebook.set_current_page(-1)
        else:
            self.window.notebook.prev_page()

    def set_tab_position(self, position='cycle'):
        """Set the position of the tab bar. Can be one of top, bottom, left or
        right. Use cycle or reverse-cycle to go through them.
        """
        notebook = self.window.notebook
        if position == 'cycle':
            pos = notebook.get_tab_pos()
            if pos == Gtk.PositionType.LEFT:
                self.set_tab_position('top')
            elif pos == Gtk.PositionType.TOP:
                self.set_tab_position('right')
            elif pos == Gtk.PositionType.RIGHT:
                self.set_tab_position('bottom')
            elif pos == Gtk.PositionType.BOTTOM:
                self.set_tab_position('left')
        elif position == 'reverse-cycle':
            pos = notebook.get_tab_pos()
            if pos == Gtk.PositionType.LEFT:
                self.set_tab_position('bottom')
            elif pos == Gtk.PositionType.BOTTOM:
                self.set_tab_position('right')
            elif pos == Gtk.PositionType.RIGHT:
                self.set_tab_position('top')
            elif pos == Gtk.PositionType.TOP:
                self.set_tab_position('left')
        elif position == 'left':
            notebook.set_tab_pos(Gtk.PositionType.LEFT)
        elif position == 'top':
            notebook.set_tab_pos(Gtk.PositionType.TOP)
        elif position == 'right':
            notebook.set_tab_pos(Gtk.PositionType.RIGHT)
        elif position == 'bottom':
            notebook.set_tab_pos(Gtk.PositionType.BOTTOM)
        elif position == 'hidden':
            notebook.set_show_tabs(False)
        elif position == 'visible':
            notebook.set_show_tabs(True)

    def new_webview(self):
        user_content_manager = self.get_extension(UserContentManager)

        if user_content_manager is not None:
            webview = WebKit2.WebView.new_with_user_content_manager(user_content_manager.manager)
        else:
            webview = WebKit2.WebView()
        return webview

    def before_run(self):
        for ext in self.extensions:
            ext.before_run()

    def find_browser(self, page_id):
        for browser in self.get_browsers():
            if browser.webview.get_page_id() == page_id:
                return browser

    def do_new_browser(self, uri, text, html, background):
        window = self.browser_view(self)
        if text:
            window.start('about:blank')
            window.webview.load_plain_text(text)
        elif html:
            window.start('about:blank')
            window.webview.load_html(html, uri)
        else:
            window.start(uri)
        self.add_window(window)

        if not background:
            window.present()

    def add_window(self, window):
        if isinstance(window, BrowserTab):
            self.window.add(window)
        else:
            super().add_window(window)

    def add_close_history(self, uri):
        if uri == 'about:blank':
            return
        self.previous_uris.append(uri)

    def undo_close(self):
        try:
            previous_uri = self.previous_uris.pop()
        except IndexError:
            pass
        else:
            if previous_uri != 'about:blank':
                self.new_window(previous_uri)

    def set_profile(self, profile):
        self.profile = profile
        self.set_application_id('{}.{}'.format('deschain.roland', profile))
        self.emit('profile-set', profile)

    def load_config(self):
        try:
            self.config = imp.load_source('roland.config', config_path('config.py'))
        except FileNotFoundError:
            self.config = default_config()

        if not hasattr(self.config, 'default_user_agent') or self.config.default_user_agent is None:
            self.config.default_user_agent = WebKit2.Settings().props.user_agent
        if not hasattr(self.config, 'enable_disk_cache'):
            self.config.enable_disk_cache = False

        self.browser_view = getattr(self.config, 'browser_view', self.browser_view)

        self.connect('profile-set', self.make_config_directories)
        if self.config.enable_disk_cache:
            self.connect('profile-set', self.set_disk_cache)

        font = getattr(self.config, 'font', '')

        self.font = Pango.FontDescription.from_string(font)

        style_text = getattr(self.config, 'style', DEFAULT_STYLE)
        if not isinstance(style_text, bytes):
            style_text = style_text.encode('utf8')
        self.style_provider = Gtk.CssProvider()
        self.style_provider.load_from_data(style_text)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        default_extensions = [
            CookieManager, DBusManager, DownloadManager, HistoryManager,
            SessionManager, TLSErrorByPassExtension, HSTSExtension,
            UserContentManager, PasswordManagerExtension]
        extensions = getattr(self.config, 'extensions', default_extensions)

        # DBusManager, as of the WebKit2 port, is essentially required
        if DBusManager not in extensions:
            extensions.append(DBusManager)

        self.extensions = sorted([ext(self) for ext in extensions], key=lambda ext: ext.sort_order)

    def make_config_directories(self, roland, profile):
        for p in cache_path, config_path, runtime_path:
            p = p('', profile=profile)
            try:
                os.makedirs(p)
            except FileExistsError:
                pass

    def set_disk_cache(self, roland, profile):
        context = WebKit2.WebContext.get_default()

        disk_cache = cache_path('{}/web/'.format(self.profile))
        try:
            os.makedirs(disk_cache)
        except FileExistsError:
            pass
        context.set_disk_cache_directory(disk_cache)

        favicon_cache = cache_path('{}/favicon/'.format(self.profile))
        try:
            os.makedirs(favicon_cache)
        except FileExistsError:
            pass
        context.set_favicon_database_directory(favicon_cache)

    def set_web_extensions_info(self, context):
        context.set_web_extensions_initialization_user_data(GLib.Variant.new_string(self.profile))
        context.set_web_extensions_directory(config_path('webextensions/'))

    def setup(self):
        if self.setup_run:
            return

        self.setup_run = True

        WebKit2.WebContext.get_default().connect('initialize-web-extensions', self.set_web_extensions_info)
        WebKit2.WebContext.get_default().set_process_model(
            WebKit2.ProcessModel.MULTIPLE_SECONDARY_PROCESSES)

        try:
            import setproctitle
            setproctitle.setproctitle('roland')
        except Exception:
            pass

        if self.browser_view is BrowserTab:
            self.window = MultiTabBrowserWindow(self)
            self.set_tab_position(getattr(self.config, 'tab_bar_position', 'left'))
            self.window.show_all()
            self.add_window(self.window)

        for ext in self.extensions:
            try:
                ext.setup()
            except Exception as e:
                traceback.print_exc()
                self.notify("Failure setting up {}: {}".format(ext.name, e), critical=True)

    def is_enabled(self, extension):
        return self.get_extension(extension) is not None

    def get_extension(self, extensiontype):
        for ext in self.extensions:
            if isinstance(ext, extensiontype):
                return ext

    def on_command_line(self, roland, command_line):
        if not command_line.get_is_remote():
            self.setup()

        s = Gtk.Settings.get_default()
        s.props.gtk_key_theme_name = 'Emacs'

        urls = command_line.get_arguments()[1:]
        if not urls:
            # if we're just loading up a new window from a remote invocation,
            # or the session was empty
            if command_line.get_is_remote() or not self.get_browsers():
                urls = [getattr(self.config, 'home_page', 'http://google.com')]

        for url in urls:
            self.new_window(url)

        return 0

    def new_window(self, url, plaintext='', html='', background=False):
        self.emit('new-browser', url, plaintext, html, background)

    def notify(self, message, critical=False, header=''):
        if not Notify.is_initted():
            Notify.init('roland')
        n = Notify.Notification.new(header, message)
        logger = log.info
        if critical:
            logger = log.critical
            n.set_urgency(Notify.Urgency.CRITICAL)
        logger('{}: {}', header, message)
        n.show()

    def get_help(self, name):
        command = getattr(BrowserCommands, name, None)

        if command is None:
            # renamed function
            for func in dir(BrowserCommands):
                func = getattr(BrowserCommands, func)
                if getattr(func, '__name__', None) == name:
                    command = func
                    break
        help = getattr(command, '__doc__', None) or 'No help available'
        return help

    def get_commands(self):
        def name(f):
            func = getattr(BrowserCommands, f)
            return getattr(func, '__name__', f)

        def is_private(name):
            if name.startswith('__'):
                return True
            attr = getattr(BrowserCommands, name)
            return getattr(attr, 'private', False)

        def meets_requirements(f):
            func = getattr(BrowserCommands, f)
            extensions = getattr(func, 'extensions', [])
            return all(self.is_enabled(ext) for ext in extensions)

        return [name(f) for f in dir(BrowserCommands) if not is_private(f) and
                meets_requirements(f)]

    def set_clipboard(self, text, notify=True):
        primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        secondary = Gtk.Clipboard.get(Gdk.SELECTION_SECONDARY)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        primary.set_text(text, -1)
        secondary.set_text(text, -1)
        clipboard.set_text(text, -1)

        if notify:
            self.notify("Set clipboard to '{}'".format(text))

    def most_popular_urls(self):
        if not self.is_enabled(HistoryManager):
            return []
        return self.get_extension(HistoryManager).most_popular_urls()

    def hooks(self, name, *args, default=None):
        return getattr(self.config, name, lambda *args: default)(*args)

    def quit(self):
        if self.is_enabled(DownloadManager) and self.downloads:
            self.notify("Not quitting, {} downloads in progress.".format(len(self.downloads)))
            return

        Gtk.Application.quit(self)
