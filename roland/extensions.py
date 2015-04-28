import os
import sqlite3
import json
import itertools


from gi.repository import WebKit2

from .utils import config_path


class Extension:
    def __init__(self, roland):
        self.roland = roland
        self.name = self.__class__.__name__

    @staticmethod
    def register_fallback(extension):
        def decorator(fallback):
            method_name = fallback.__name__

            def caller(roland):
                if roland.is_enabled(extension):
                    ext = roland.get_extension(extension)
                    method = getattr(ext, method_name)
                    return method()
                else:
                    return fallback(roland)
            return caller
        return decorator


class HistoryManager(Extension):
    def setup(self):
        self.create_history_db()

    def create_history_db(self):
        conn = self.get_history_db()

        cursor = conn.cursor()
        cursor.execute('create table if not exists history '
                       '(url text, view_count integer)')
        conn.commit()
        conn.close()

    def get_history_db(self):
        return sqlite3.connect(config_path('history.{}.db', self.roland.profile))

    def update(self, url):
        if url == 'about:blank':
            return False

        conn = self.get_history_db()
        cursor = conn.cursor()

        cursor.execute('select url from history where url = ?', (url,))
        rec = cursor.fetchone()

        if rec is None:
            cursor.execute('insert into history (url, view_count)'
                           'values (?, 1)', (url,))
        else:
            cursor.execute('update history set view_count = view_count + 1 '
                           'where url = ?', (url,))
        conn.commit()
        conn.close()

        return False

    def most_popular_urls(self):
        conn = self.get_history_db()
        cursor = conn.cursor()
        cursor.execute('select url from history order by view_count desc limit 500')
        urls = [url for (url,) in cursor.fetchall()]
        conn.close()
        return urls


class DownloadManager(Extension):
    save_location = os.path.expanduser('~/Downloads/')

    def setup(self):
        self.roland.downloads = {}

        context = WebKit2.WebContext.get_default()
        context.connect('download-started', self.download_started)

    def download_started(self, webcontext, download):
        download.connect('failed', self.failed)
        download.connect('finished', self.finished)
        download.connect('decide-destination', self.decide_destination)
        download.connect('created-destination', self.created_destination)

    def created_destination(self, download, destination):
        self.roland.notify("Downloading {}".format(destination))

    def decide_destination(self, download, suggested_filename):
        save_path = os.path.join(
            self.save_location, suggested_filename)

        orig_save_path = save_path
        for i in itertools.count(1):
            if os.path.exists(save_path):
                save_path = orig_save_path + ('.%d' % i)
            else:
                break

        download.set_destination('file://' + save_path)
        self.roland.downloads[save_path] = download
        return True

    def failed(self, download, error):
        location = download.get_destination()[len('file://'):]
        if error == WebKit2.DownloadError.CANCELLED_BY_USER:
            self.roland.notify('Download cancelled: %s' % location)
            self.roland.downloads.pop(location)
        else:
            self.roland.notify('Download failed: %s' % location, critical=True)
            self.roland.downloads.pop(location)

    def finished(self, download):
        location = download.get_destination()[len('file://'):]
        self.roland.notify('Download finished: %s' % location)
        self.roland.downloads.pop(location)


class CookieManager(Extension):
    def setup(self):
        cookiejar_path = config_path('cookies.{}.db', self.roland.profile)

        cookiejar = WebKit2.WebContext.get_default().get_cookie_manager()

        cookiejar.set_accept_policy(WebKit2.CookieAcceptPolicy.ALWAYS)

        cookiejar.set_persistent_storage(
            cookiejar_path, WebKit2.CookiePersistentStorage.SQLITE)


class SessionManager(Extension):
    def setup(self):
        try:
            with open(config_path('session.{}.json', self.roland.profile), 'r') as f:
                session = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            self.roland.notify("Error loading session: {}".format(e))
        else:
            for page in session:
                self.roland.do_new_browser(page['uri'])

        self.roland.connect('shutdown', self.on_shutdown)

    def on_shutdown(self, app):
        self.save_session()

    def save_session(self):
        session = []
        for window in self.roland.get_windows():
            uri = window.webview.get_uri()

            if uri is not None:
                # FIXME: add back/forwards history here?
                session.append({'uri': uri})

        with open(config_path('session.{}.json', self.roland.profile), 'w') as f:
            json.dump(session, f, indent=4)


class DBusManager(Extension):
    def before_run(self):
        try:
            from dbus.mainloop.glib import DBusGMainLoop
        except ImportError:
            pass
        else:
            DBusGMainLoop(set_as_default=True)

    def setup(self):
        self.create_dbus_api()

    def create_dbus_api(self):
        import dbus
        import dbus.service

        name = 'com.deschain.roland.{}'.format(self.roland.profile)

        roland = self.roland

        class DBusAPI(dbus.service.Object):
            def __init__(self):
                bus_name = dbus.service.BusName(name, bus=dbus.SessionBus())
                dbus.service.Object.__init__(self, bus_name, '/com/deschain/roland/{}'.format(roland.profile))

            @dbus.service.method(name)
            def open_window(self, url):
                roland.do_new_browser(url)
                return 1

            @dbus.service.method(name)
            def page_loaded(self, url):
                if roland.is_enabled(HistoryManager):
                    history_manager = roland.get_extension(HistoryManager)
                    history_manager.update(url)
                return 1

        self.roland_api = DBusAPI()
