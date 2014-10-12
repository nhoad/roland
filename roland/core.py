#!/usr/bin/env python3

import code
import fnmatch
import enum
import html
import imp
import itertools
import os
import shlex
import socket
import threading
import traceback

from urllib import parse as urlparse

from gi.repository import GObject, Gdk, Gio, Gtk, Notify, Pango, Soup, WebKit

from .extensions import (
    Extension, CookieManager, DBusManager, DownloadManager, HistoryManager,
    SessionManager)
from .utils import config_path, get_keyname, get_pretty_size


Mode = enum.Enum('Mode', 'Insert Normal Motion SubCommand Prompt')


def private(func):
    """Decorator for methods on BrowserCommands that shouldn't be displayed in
    the command suggestions.
    """
    func.private = True
    return func


class BrowserCommands:
    @private
    def select_window(self):
        def present_window(selected):
            try:
                win_id = name_to_id[selected]
                win = id_to_window[win_id]
            except KeyError:
                pass
            else:
                win.present()

        windows = self.roland.get_windows()
        name_to_id = {'%d: %s' % (i, w.title.title): i for (i, w) in enumerate(windows)}
        id_to_window = {i: w for (i, w) in enumerate(windows)}
        self.entry_line.display(
            present_window, prompt="Window", force_match=True, glob=True,
            suggestions=sorted(name_to_id))
        return True

    @private
    def open(self, url=None, new_window=False):
        def open_window(url):
            if new_window:
                self.roland.new_window(url)
            else:
                self.webview.load_uri(url)

        if url is None:
            prompt = 'open'
            if new_window:
                prompt += ' (new window)'
            self.entry_line.display(
                open_window, prompt=prompt, glob=True,
                suggestions=self.roland.most_popular_urls())
        else:
            open_window(url)
        return True

    def save_session(self):
        self.roland.save_session()

    @private
    def open_or_search(self, text=None, new_window=False):
        def open_or_search(text):
            if urlparse.urlparse(text).scheme:
                self.open(text, new_window=new_window)
            else:
                if ' ' in text or '_' in text:
                    self.search(text, new_window=new_window)
                else:
                    try:
                        socket.gethostbyname(text)
                    except socket.error:
                        self.search(text, new_window=new_window)
                    else:
                        self.open('http://'+text, new_window=new_window)
        if text is None:
            prompt = 'open/search'
            if new_window:
                prompt += ' (new window)'

            self.entry_line.display(
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

        self.entry_line.display(
            open_window, prompt=prompt, initial=self.webview.get_uri() or '')
        return True

    def navigate_up(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)
        if url.path not in ('', '/'):
            url = url._replace(path=os.path.dirname(url.path)).geturl()
            self.open(url)

    def navigate_top(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)._replace(path='').geturl()
        self.open(url)

    def close(self):
        # explicitly trigger quitting in case downloads are in progress
        if len(self.roland.get_windows()) == 1:
            self.roland.quit()
            return

        Gtk.Window.close(self)
        Gtk.Window.destroy(self)

    @private
    def change_user_agent(self, user_agent=None):
        def change_user_agent(user_agent):
            if not user_agent:
                return
            for window in self.roland.get_windows():
                window.web_view.get_settings().props.user_agent = user_agent
            self.roland.notify('lel wut')

        if user_agent is None:
            user_agents = [self.roland.config.default_user_agent] + self.roland.hooks('user_agent_choices', default=[])
            self.entry_line.display(change_user_agent, prompt="User Agent", suggestions=user_agents)
        else:
            change_user_agent(user_agent)
        return True

    @private
    def search(self, text=None, new_window=False):
        def search(text):
            url = self.roland.config.search_page.format(text)
            self.open(url, new_window=new_window)

        if text is None:
            self.entry_line.display(search, prompt='Search',)
        else:
            search(text)
        return True

    def back(self):
        self.webview.go_back()

    def forward(self):
        self.webview.go_forward()

    def run_javascript(self, script):
        self.webview.execute_script(script)

    @private
    def follow(self, new_window=False):
        all_elems = []

        def is_visible(elem):
            return (elem.get_offset_height() != 0 or elem.get_offset_width() != 0)

        def get_offset(elem):
            x, y = 0, 0

            while elem is not None:
                x += elem.get_offset_left() - elem.get_scroll_left()
                y += elem.get_offset_top() - elem.get_scroll_top()
                elem = elem.get_offset_parent()
            return x, y

        cleanup_elems = []

        elem_count = 1

        main_frame = self.webview.get_main_frame()
        webframes = [main_frame] + [main_frame.find_frame(name) for name in self.webframes]

        suggestions = []

        for i, frame in enumerate(webframes):
            dom = frame.get_dom_document()
            if new_window:
                elems = dom.query_selector_all('a')
            else:
                elems = dom.query_selector_all('a, input:not([type=hidden]), textarea, select, button')
            elems = [elems.item(i) for i in
                     range(elems.get_length()) if is_visible(elems.item(i))]
            all_elems.extend(elems)

            overlay = dom.create_element('div')
            html = ''

            for i, elem in enumerate(elems, elem_count):
                css = ';'.join([
                    'left: {}px',
                    'top: {}px',
                    'position: fixed',
                    'font-size: 12px',
                    'background-color: #ff6600',
                    'color: white',
                    'font-weight: bold',
                    'font-family: Monospace',
                    'padding: 0px 1px',
                    'border: 1px solid black',
                    'z-index: 100000'
                ]).format(*get_offset(elem))

                span = '<span style="%s">%d</span>' % (css, i)
                html += span
                if elem.get_tag_name().lower() == 'a':
                    text = '{} ({})'.format(elem.get_text().strip(), elem.get_href())
                else:
                    text = elem.get_name()

                suggestions.append('{}: {}'.format(i, text))
            elem_count += len(elems)

            overlay.set_inner_html(html)

            html_elem = dom.query_selector_all('html').item(0)
            html_elem.append_child(overlay)
            cleanup_elems.append((html_elem, overlay))

        def select_elem(choice):
            remove_elems()
            elem = all_elems[int(choice)-1]

            if elem.get_tag_name().lower() == 'a':
                if new_window:
                    url = elem.get_href()
                    self.roland.new_window(url)
                else:
                    elem.click()
            else:
                self.set_mode(Mode.Insert)
                elem.focus()

        def remove_elems():
            for html_elem, overlay in cleanup_elems:
                html_elem.remove_child(overlay)

        prompt = 'Follow'
        if new_window:
            prompt += ' (new window)'
        self.entry_line.display(
            select_elem, prompt=prompt,
            cancel=remove_elems, suggestions=suggestions)
        return True

    @private
    def search_page(self, text=None, forwards=True, case_insensitive=None):
        def search_page(text):
            nonlocal case_insensitive
            if case_insensitive is None:
                case_insensitive = text.lower() != text

            self.previous_search = text
            self.webview.mark_text_matches(text, case_insensitive, 0)
            self.webview.set_highlight_text_matches(True)
            self.webview.search_text(text, case_insensitive, True, True)

        if text is None:
            self.entry_line.display(search_page, prompt='Search page')
        else:
            search_page(text)
        return True

    @private
    def next_search_result(self, forwards=True, case_insensitive=None):
        if self.previous_search:
            self.search_page(
                text=self.previous_search,
                forwards=forwards,
                case_insensitive=case_insensitive,
            )

    def zoom_in(self):
        self.webview.zoom_in()
        # binding for C-Up scrolls, this stops that
        return True

    def zoom_out(self):
        self.webview.zoom_out()
        # binding for C-Down scrolls, this stops that
        return True

    def zoom_reset(self):
        self.webview.set_zoom_level(1)

    def stop(self):
        self.webview.stop_loading()

    @private
    def move(self, x=0, y=0):
        self.webview.execute_script('window.scrollBy(%d, %d);' % (x*30, y*30))

    def shell(self):
        self.roland.notify('Starting shell...')
        t = threading.Thread(target=code.interact, kwargs={'local': {'roland': self.roland}})
        t.daemon = True
        t.start()

    def quit(self):
        self.roland.quit()

    def reload(self):
        self.webview.reload()

    def reload_bypass_cache(self):
        self.webview.reload_bypass_cache()

    @private
    def cancel_download(self):
        if not self.roland.is_enabled(DownloadManager):
            self.roland.notify("Download manager not enabled")
            return

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

        self.entry_line.display(
            cancel_download, prompt="Cancel download", force_match=True,
            glob=True, suggestions=list(self.roland.downloads.keys()))

        return True

    @private
    def list_downloads(self):
        if not self.roland.is_enabled(DownloadManager):
            self.roland.notify("Download manager not enabled")
            return

        if not self.roland.downloads:
            self.roland.notify("No downloads in progress")
            return

        for location, download in self.roland.downloads.items():
            if download.get_progress() == 1.0:
                continue  # completed while we were doing this
            progress = get_pretty_size(download.get_current_size())
            total = get_pretty_size(download.get_total_size())
            self.roland.notify('%s - %s out of %s' % (location, progress, total))


class EntryLine(Gtk.VBox):
    def __init__(self, status_line, browser, font, fg, bg):
        Gtk.VBox.__init__(self)

        self.status_line = status_line
        self.browser = browser
        self.font = font
        self.fg = fg
        self.bg = bg

        self.prompt = Gtk.Label()
        self.prompt.modify_font(font)
        self.prompt.set_alignment(0.0, 0.5)
        self.prompt.override_background_color(0, bg)
        self.prompt.override_color(0, fg)

        self.input = Gtk.Entry()
        self.input.set_has_frame(False)
        self.input.modify_font(font)
        self.input.override_background_color(0, bg)
        self.input.override_color(0, fg)
        self.input.connect('key-release-event', self.on_key_release_event)
        self.input.connect('backspace', self.on_key_release_event, None)

        self.input_container = Gtk.HBox()
        self.input_container.pack_start(self.prompt, False, False, 0)
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

    def display(self, callback, suggestions=None, force_match=False,
                glob=False, prompt='', initial='', cancel=None):
        self.callback = callback
        self.suggestions = suggestions or []
        self.force_match = force_match
        self.glob = glob
        self.lock_suggestions = False
        self.cancel = cancel

        self.prompt.set_text('{}:'.format(prompt))
        self.prompt.show()
        self.show()
        self.input.set_text(initial)
        if initial:
            self.input.set_position(-1)
        self.input.show()
        self.status_line.hide()
        self.get_toplevel().set_focus(self.input)

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
            if labels:
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
            entries = [e for e in self.suggestions if e.startswith(t)]

        for entry in reversed(entries[:20]):
            # FIXME: highlight matching portion
            l = Gtk.Label()
            l.set_alignment(0.0, 0.5)
            l.set_text(entry)
            l.modify_font(self.font)
            l.override_background_color(0, self.bg)
            l.override_color(0, self.fg)
            self.pack_end(l, False, False, 0)
            l.show()

    def remove_completions(self):
        for child in self.get_children():
            if child != self.input_container:
                self.remove(child)


class StatusLine(Gtk.HBox):
    def __init__(self, font, fg, bg):
        Gtk.HBox.__init__(self)

        self.left = Gtk.Label()
        self.middle = Gtk.Label()
        self.right = Gtk.Label()

        self.left.set_alignment(0.0, 0.5)
        self.right.set_alignment(1.0, 0.5)

        for i in [self.left, self.middle, self.right]:
            i.modify_font(font)
            i.override_background_color(0, bg)
            i.override_color(0, fg)
            self.add(i)

        self.buffered_command = ''
        self.uri = ''
        self.trusted = True

    def set_uri(self, uri):
        self.uri = uri
        self.update_right()

    def set_mode(self, text):
        self.left.set_markup(text)

    def set_trust(self, trusted):
        self.trusted = trusted
        self.update_right()

    def set_buffered_command(self, text):
        self.buffered_command = text
        self.update_right()

    def update_right(self):
        text = []
        if self.buffered_command:
            text.append('<b>{}</b>'.format(self.buffered_command))

        if self.uri:
            text.append(html.escape(self.uri))

        if not self.trusted:
            text.append('<span foreground="red"><b>untrusted</b></span>')

        self.right.set_markup(' <b>|</b> '.join(text))


class BrowserTitle:
    title = ''
    progress = 0

    def __str__(self):
        if self.progress < 100:
            return '[{}%] Loading... {}'.format(self.progress, self.title)
        return self.title or ''


class BrowserWindow(BrowserCommands, Gtk.Window):
    def on_download_requested(self, browser, download):
        save_path = os.path.join(self.roland.save_location,
                                 download.get_suggested_filename())

        orig_save_path = save_path
        for i in itertools.count(1):
            if os.path.exists(save_path):
                save_path = orig_save_path + ('.%d' % i)
            else:
                break

        uri = download.get_uri()

        def start_download(location):
            network_request = WebKit.NetworkRequest.new(uri)
            download = WebKit.Download.new(network_request)
            download.connect('notify::status', self.roland.download_status_changed, location)
            download.set_destination_uri('file://' + location)
            self.roland.downloads[location] = download
            download.start()

        # FIXME: multiple prompts in one window?
        prompt = "Download location ({})".format(uri)
        self.entry_line.display(start_download, prompt=prompt, initial=save_path)

        return False

    def __init__(self, roland):
        super().__init__()
        self.roland = roland
        self.previous_search = ''
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
        self.set_default_size(1000, 800)
        self.connect('key-press-event', self.on_key_press_event)

        # will already be initialised for popups
        if self.webview is None:
            self.webview = WebKit.WebView()

        settings = self.webview.get_settings()
        settings.props.user_agent = self.roland.config.default_user_agent
        settings.props.enable_running_of_insecure_content = self.roland.config.run_insecure_content
        settings.props.enable_display_of_insecure_content = self.roland.config.display_insecure_content
        stylesheet = 'file://{}'.format(
            config_path('stylesheet.{}.css', self.roland.profile))
        settings.props.user_stylesheet_uri = stylesheet
        self.status_line = StatusLine(
            self.roland.font, self.roland.fg, self.roland.bg)
        self.entry_line = EntryLine(
            self.status_line, self, self.roland.font, self.roland.fg,
            self.roland.bg)

        self.set_mode(Mode.Normal)

        self.webview.connect('notify::title', self.update_title_from_event)
        self.webview.connect('notify::progress', self.update_title_from_event)
        self.webview.connect('notify::load-status', self.on_load_status)
        self.webview.connect('close-web-view', lambda *args: self.destroy())
        self.webview.connect('create-web-view', self.on_create_web_view)
        self.webview.connect('frame-created', self.on_frame_created)
        self.webframes = []

        if self.roland.is_enabled(HistoryManager):
            history_manager = self.roland.get_extension(HistoryManager)
            self.webview.connect('navigation-policy-decision-requested', history_manager.on_navigation_policy_decision_requested)
        self.webview.connect('navigation-policy-decision-requested', self.on_navigation_policy_decision_requested)

        if self.roland.is_enabled(DownloadManager):
            download_manager = self.roland.get_extension(DownloadManager)
            self.webview.connect('download-requested', self.on_download_requested)
            self.webview.connect('mime-type-policy-decision-requested', download_manager.on_mime_type_policy_decision_requested)

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

    def on_load_status(self, webview, load_status):
        if self.webview != webview:
            return
        if self.webview.get_load_status() == WebKit.LoadStatus.FINISHED:
            if self.webview.get_uri().startswith('http://'):
                self.status_line.set_trust(True)
                return
            main_frame = self.webview.get_main_frame()
            data_source = main_frame.get_data_source()
            network_request = data_source.get_request()
            soup_message = network_request.get_message()

            if (soup_message.get_flags() & Soup.MessageFlags.CERTIFICATE_TRUSTED):
                self.status_line.set_trust(True)
            else:
                self.status_line.set_trust(False)

    def on_navigation_policy_decision_requested(
            self, webview, frame, request, navigation_action, policy_decision):
        uri = request.get_uri()
        if self.webview.get_main_frame() == frame:
            self.status_line.set_uri(uri)
            self.status_line.set_trust(True)  # assume trust until told otherwise
            self.webframes.clear()

    def update_title_from_event(self, widget, event):
        if event.name == 'title':
            title = self.webview.get_title()
            self.title.title = title
        elif event.name == 'progress':
            self.title.progress = int(self.webview.get_progress() * 100)

        self.set_title(str(self.title))

    def on_frame_created(self, webview, webframe):
        self.webframes.append(webframe.get_name())

    def on_create_web_view(self, webview, webframe):
        if self.roland.hooks('should_open_popup', webframe.get_uri(), default=True):
            v = WebKit.WebView()
            self.roland.windows.append(BrowserWindow.from_webview(v, self.roland))
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
            if keyname == 'Escape':
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
        else:
            assert self.mode == Mode.Insert

            if keyname == 'Escape':
                self.set_mode(Mode.Normal)

    def set_mode(self, mode, *args):
        assert mode in Mode
        self.mode = mode

        if mode == Mode.Normal:
            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>NORMAL</b>')
            self.status_line.set_buffered_command('')
        elif mode == Mode.SubCommand:
            command, self.sub_commands = args

            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>COMMAND</b>')
            self.status_line.set_buffered_command(command)
        elif mode == Mode.Prompt:
            pass
        else:
            assert mode == Mode.Insert, "Unknown Mode %s" % mode
            self.webview.set_can_focus(True)
            self.webview.grab_focus()
            self.status_line.set_mode('<b>INSERT</b>')
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

        self.entry_line.display(
            run_command, prompt='command', force_match=True,
            suggestions=self.roland.get_commands())
        return True

    def run_command(self, name, *args):
        try:
            command = getattr(self, name)
        except AttributeError:
            self.roland.notify("No such command '{}'".format(name))
            return

        try:
            command(*args)
        except Exception as e:
            self.roland.notify("Error calling '{}': {}".format(name, str(e)))
            traceback.print_exc()


class Roland(Gtk.Application):
    __gsignals__ = {
        'new_browser': (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    def __init__(self):
        Gtk.Application.__init__(self, application_id='deschain.roland', flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.setup_run = False
        self.connect('command-line', self.on_command_line)

        self.load_config()
        self.before_run()

    def before_run(self):
        for ext in self.extensions:
            before_run = getattr(ext, 'before_run', None)
            if before_run is not None:
                before_run()

    def do_new_browser(self, url):
        window = BrowserWindow(self)
        window.start(url)
        self.add_window(window)

    def set_profile(self, profile):
        self.profile = profile
        self.set_application_id('{}.{}'.format('deschain.roland', profile))

    def load_config(self):
        try:
            os.makedirs(config_path(''))
        except OSError:
            pass

        self.config = imp.load_source('roland.config', config_path('config.py'))

        if not hasattr(self.config, 'default_user_agent') or self.config.default_user_agent is None:
            self.config.default_user_agent = WebKit.WebSettings().props.user_agent
        if not hasattr(self.config, 'run_insecure_content'):
            self.config.run_insecure_content = WebKit.WebSettings().props.enable_running_of_insecure_content
        if not hasattr(self.config, 'display_insecure_content'):
            self.config.display_insecure_content = WebKit.WebSettings().props.enable_display_of_insecure_content

        font = getattr(self.config, 'font', '')
        fg = getattr(self.config, 'foreground_color', None)
        bg = getattr(self.config, 'background_color', None)

        self.font = Pango.FontDescription.from_string(font)

        if fg:
            self.fg = Gdk.RGBA()
            self.fg.parse(fg)
        else:
            self.fg = None

        if bg:
            self.bg = Gdk.RGBA()
            self.bg.parse(bg)
        else:
            self.fg = None

        default_extensions = [
            CookieManager, DBusManager, DownloadManager, HistoryManager,
            SessionManager]
        extensions = getattr(self.config, 'extensions', default_extensions)

        self.extensions = [ext(self) for ext in extensions]

    def setup(self):
        if self.setup_run:
            return

        self.setup_run = True

        try:
            import setproctitle
            setproctitle.setproctitle('roland')
        except Exception:
            pass

        for ext in self.extensions:
            setup = getattr(ext, 'setup', None)
            if setup is not None:
                try:
                    setup()
                except Exception as e:
                    self.notify("Failure setting up {}: {}".format(ext.name, e))

    def is_enabled(self, extension):
        return self.get_extension(extension) is not None

    def get_extension(self, extensiontype):
        for ext in self.extensions:
            if isinstance(ext, extensiontype):
                return ext

    def on_command_line(self, roland, command_line):
        self.setup()

        urls = command_line.get_arguments()[1:]
        if not urls:
            # if we're just loading up a new window from a remote invocation,
            # or the session was empty
            if command_line.get_is_remote() or not self.get_windows():
                urls = [self.config.home_page]

        for url in urls:
            self.do_new_browser(url)

        return 0

    def new_window(self, url):
        self.emit('new-browser', url)

    def notify(self, message, critical=False, header=''):
        if not Notify.is_initted():
            Notify.init('roland')
        n = Notify.Notification.new(header, message)
        if critical:
            n.set_urgency(Notify.Urgency.CRITICAL)
        n.show()

    def get_commands(self):
        def is_private(name):
            if name.startswith('__'):
                return True
            attr = getattr(BrowserCommands, name)
            return getattr(attr, 'private', False)
        return [f for f in dir(BrowserCommands) if not is_private(f)]

    def set_clipboard(self, text, notify=True):
        primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        secondary = Gtk.Clipboard.get(Gdk.SELECTION_SECONDARY)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        primary.set_text(text, -1)
        secondary.set_text(text, -1)
        clipboard.set_text(text, -1)

        if notify:
            self.notify("Set clipboard to '{}'".format(text))

    @Extension.register_fallback(HistoryManager)
    def most_popular_urls(self):
        return []

    def hooks(self, name, *args, default=None):
        return getattr(self.config, name, lambda *args: default)(*args)

    def quit(self):
        if self.is_enabled(DownloadManager) and self.downloads:
            self.notify("Not quitting, {} downloads in progress.".format(len(self.downloads)))
            return

        Gtk.Application.quit(self)
