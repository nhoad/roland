import os
import sqlite3
import json


from gi.repository import WebKit, Soup

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

    def on_navigation_policy_decision_requested(
            self, webview, frame, request, navigation_action, policy_decision):
        uri = request.get_uri()

        if uri == 'about:blank':
            return False

        conn = self.get_history_db()
        cursor = conn.cursor()

        cursor.execute('select url from history where url = ?', (uri,))
        rec = cursor.fetchone()

        if rec is None:
            cursor.execute('insert into history (url, view_count)'
                           'values (?, 1)', (uri,))
        else:
            cursor.execute('update history set view_count = view_count + 1 '
                           'where url = ?', (uri,))
        conn.commit()
        conn.close()

        self.webframes = []

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

    def download_status_changed(self, download, status, location):
        if download.get_status() == WebKit.DownloadStatus.FINISHED:
            self.roland.notify('Download finished: %s' % location)
            self.roland.downloads.pop(location)
        elif download.get_status() == WebKit.DownloadStatus.ERROR:
            self.roland.notify('Download failed: %s' % location, critical=True)
            self.roland.downloads.pop(location)
        elif download.get_status() == WebKit.DownloadStatus.CANCELLED:
            self.roland.notify('Download cancelled: %s' % location)
            self.roland.downloads.pop(location)
            try:
                os.unlink(location)
            except OSError:
                pass

    def on_mime_type_policy_decision_requested(
            self, browser, frame, request, mime_type, policy_decision):
        if browser.can_show_mime_type(mime_type):
            return False
        policy_decision.download()
        return True


class CookieManager(Extension):
    def setup(self):
        self.cookiejar = Soup.CookieJarDB.new(
            config_path('cookies.{}.db', self.roland.profile), False)
        self.cookiejar.set_accept_policy(Soup.CookieJarAcceptPolicy.ALWAYS)
        self.session = WebKit.get_default_session()
        self.session.add_feature(self.cookiejar)


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

        self.roland_api = DBusAPI()
