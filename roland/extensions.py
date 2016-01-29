import base64
import datetime
import hashlib
import itertools
import json
import os
import re
import sqlite3
from collections import namedtuple
from urllib import request, parse as urlparse

import logbook
import msgpack
from Crypto import Random
from Crypto.Cipher import AES
from gi.repository import Gio, WebKit2
from werkzeug import parse_dict_header

from .utils import config_path

log = logbook.Logger('roland.extensions')


class Extension:
    sort_order = 0

    def __init__(self, roland):
        self.roland = roland
        self.name = self.__class__.__name__

    def setup(self):
        """Setup method, for setting any state in the extension.

        If this is fatal, Roland will ignore the error.
        """
        pass

    def before_run(self):
        """Very early setup method, happens during Roland.__init__. Should not
        typically be used."""
        pass


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
    # Make SessionManager load up last, because it needs to do things after
    # TLSErrorByPassExtension has setup exclusions.
    sort_order = 1

    def setup(self):
        try:
            with open(config_path('session.{}.json', self.roland.profile), 'r') as f:
                session = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            self.roland.notify("Error loading session: {}".format(e))
        else:
            lazy = getattr(self.roland.config, 'lazy', True)
            first = True
            for page in session:
                self.roland.new_window(page['uri'], title=page.get('title'), lazy=lazy if not first else False)
                first = False

        self.roland.connect('shutdown', self.on_shutdown)

    def on_shutdown(self, app):
        self.save_session()

    def save_session(self):
        session = []
        for browser in self.roland.get_browsers():
            if browser.lazy:
                uri = browser.lazy_uri
            else:
                uri = browser.webview.get_uri()

            if uri in (None, 'about:blank', 'http:/'):
                continue

            # FIXME: add back/forwards history here?
            session.append({
                'uri': uri,
                'title': browser.get_title() or 'No Title',
            })

        with open(config_path('session.{}.json', self.roland.profile), 'w') as f:
            json.dump(session, f, indent=4)


class TLSErrorByPassExtension(Extension):
    def setup(self):
        cert_bypass_path = config_path(
            'tls.{}/bypass/'.format(self.roland.profile))
        try:
            os.makedirs(cert_bypass_path)
        except FileExistsError:
            pass

        try:
            os.makedirs(config_path('tls.{}/error/'.format(self.roland.profile)))
        except FileExistsError:
            pass

        context = WebKit2.WebContext.get_default()
        for host in os.listdir(cert_bypass_path):
            with open(os.path.join(cert_bypass_path, host)) as f:
                certificate = f.read()

            certificate = Gio.TlsCertificate.new_from_pem(certificate, len(certificate))
            context.allow_tls_certificate_for_host(certificate, host)

    def bypass(self, host):
        cert_error_path = config_path(
            'tls.{}/error/{}'.format(self.roland.profile, host))
        cert_bypass_path = config_path(
            'tls.{}/bypass/{}'.format(self.roland.profile, host))

        context = WebKit2.WebContext.get_default()
        try:
            with open(cert_error_path) as f:
                certificate = f.read()
        except FileNotFoundError:
            pass
        else:
            with open(cert_bypass_path, 'w') as f:
                f.write(certificate)

            certificate = Gio.TlsCertificate.new_from_pem(certificate, len(certificate))
            context.allow_tls_certificate_for_host(certificate, host)
            self.roland.notify("Certificate exclusion added for {}".format(host))
            return

        client = Gio.SocketClient.new()
        # no validation at all, needed to download the bad certificate.
        client.set_tls_validation_flags(0)
        client.set_tls(True)

        def callback(client, task):
            connection = client.connect_finish(task)

            tls_connection = connection.get_base_io_stream()
            certificate = tls_connection.get_peer_certificate()
            context.allow_tls_certificate_for_host(certificate, host)

            with open(cert_bypass_path, 'w') as f:
                f.write(certificate.props.certificate_pem)

            self.roland.notify("Certificate exclusion added for {}".format(host))

        client.connect_to_host_async(host, 443, None, callback)


class HSTSExtension(Extension):
    def setup(self):
        self.create_hsts_db()

    def create_hsts_db(self):
        conn = self.get_hsts_db()

        cursor = conn.cursor()
        try:
            cursor.execute('create table hsts '
                           '(domain text unique, expiry timestamp)')
        except Exception:
            pass  # already exists
        else:
            self.create_initial_db()
        conn.commit()
        conn.close()

    def create_initial_db(self):
        self.roland.notify("You don't have any HSTS entries. Downloading Chromium's preload list.")
        raw = request.urlopen('https://raw.githubusercontent.com/scheib/chromium/master/net/http/transport_security_state_static.json').read().decode('utf8')

        # JSON with comments? wild
        hsts = json.loads(re.sub(r'^ *?//.*$', '', raw, flags=re.MULTILINE))

        entries = []

        expiry = datetime.datetime.now() + datetime.timedelta(days=365)
        for entry in hsts['entries']:
            if entry.get('mode') == 'force-https':
                domain = entry['name']
                if entry.get('include_subdomains'):
                    domain = '.' + domain

                entries.append((domain, expiry))

        with self.get_hsts_db() as conn:
            cursor = conn.cursor()
            cursor.executemany('insert into hsts (domain, expiry) '
                               'values (?, ?)', entries)
            conn.commit()

    def get_hsts_db(self):
        return sqlite3.connect(config_path('hsts.{}.db', self.roland.profile), detect_types=sqlite3.PARSE_DECLTYPES)

    def add_entry(self, uri, hsts_header):
        parsed = parse_dict_header(hsts_header)
        max_age, *rest = parsed['max-age'].split(';', 1)

        include_subdomains = False
        if rest:
            include_subdomains = 'includesubdomains' in rest[0].lower()

        domain = urlparse.urlparse(uri).netloc
        if include_subdomains:
            domain = '.' + domain
        max_age = int(max_age)

        expiry = datetime.datetime.now() + datetime.timedelta(seconds=max_age)

        with self.get_hsts_db() as conn:
            cursor = conn.cursor()
            cursor.execute('insert or replace into hsts (domain, expiry) '
                           'values (?, ?)', (domain, expiry))
            conn.commit()

    def check_url(self, uri):
        domain = urlparse.urlparse(uri).netloc

        with self.get_hsts_db() as conn:
            cursor = conn.cursor()

            domains = [
                domain,   # straight domain match, e.g. lastpass.com
            ]

            if '.' in domain:
                subdomain, base = domain.split('.', 1)

                domains.extend([
                    '.' + base,  # subdomain match, e.g. foo.keyerror.com
                    '.' + domain  # match base domain for a domain supportinb subdomains, e.g. keyerror.com
                ])

            cursor.execute('select expiry from hsts '
                           'where domain in ({})'.format(','.join('?' for i in domains)),
                           domains)
            expiries = [expiry for (expiry,) in cursor.fetchall()]
            if expiries:
                expiry = expiries[0]

                if datetime.datetime.now() <= expiry:
                    log.info("HSTS for {} is True", uri)
                    return True

        log.info("HSTS for {} is False", uri)
        return False


class UserContentManager(Extension):
    def setup(self):
        path = config_path('stylesheet.{}.css', self.roland.profile)
        try:
            with open(path) as f:
                stylesheet = f.read()
        except FileNotFoundError:
            stylesheet = ''

        path = config_path('script.{}.js', self.roland.profile)
        try:
            with open(path) as f:
                script = f.read()
        except FileNotFoundError:
            script = ''

        self.script = WebKit2.UserScript.new(
            script,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.END,
            None,
            None
        )

        self.stylesheet = WebKit2.UserStyleSheet.new(
            stylesheet,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserStyleLevel.USER,
            None,
            None
        )

        self.manager = WebKit2.UserContentManager.new()
        self.manager.add_script(self.script)
        self.manager.add_style_sheet(self.stylesheet)


class DBusManager(Extension):
    def before_run(self):
        try:
            from dbus.mainloop.glib import DBusGMainLoop
        except ImportError:
            self.roland.notify('DBus is not available. Many large parts of roland will not work.', critical=True)
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
            def open_window(self, url, related_id=None):
                # handle request from web extension
                if isinstance(url, bytes):
                    url = url.decode('utf8')

                if related_id:
                    related = roland.get_browser(page_id=related_id)
                    webview = roland.new_webview(related=related.webview)
                    webview.load_uri(url)
                    roland.add_window(roland.browser_view.from_webview(webview, roland))
                    webview.emit('ready-to-show')
                else:
                    # FIXME: this background should be configurable
                    # This also forces new window follows to background
                    roland.new_window(uri, background=True)
                return 1

            @dbus.service.method(name)
            def page_loaded(self, url):
                if roland.is_enabled(HistoryManager):
                    history_manager = roland.get_extension(HistoryManager)
                    history_manager.update(url)
                return 1

            @dbus.service.method(name)
            def update_hsts_policy(self, url, hsts):
                ext = roland.get_extension(HSTSExtension)

                if ext is not None:
                    return ext.add_entry(url)
                return False

            @dbus.service.method(name)
            def hsts_policy(self, url):
                try:
                    ext = roland.get_extension(HSTSExtension)

                    if ext is not None:
                        return ext.check_url(url)
                except Exception as e:
                    log.exception("Error checking HSTS policy for {}", url)
                return False

            @dbus.service.method(name)
            def enter_insert(self, page_id):
                window = roland.find_browser(page_id)

                if window:
                    from roland.core import Mode
                    window.set_mode(Mode.Insert)

                return 1

        self.roland_api = DBusAPI()


class PasswordManagerExtension(Extension):
    FormFill = namedtuple('FormFill', 'id last_used description domain form_data')
    BS = AES.block_size

    key = None

    def setup(self):
        self.create_password_db()

    def create_password_db(self):
        conn = self.get_password_db()

        cursor = conn.cursor()
        try:
            cursor.execute('create table managed '
                           '(id integer primary key, last_used timestamp, '
                           ' description text, domain text, data text)')
        except Exception:
            pass  # already exists
        conn.commit()
        conn.close()

    def pad(self, s):
        return s + ((self.BS - len(s) % self.BS) * chr(self.BS - len(s) % self.BS)).encode('ascii')

    def unpad(self, s):
        return s[:-s[-1]]

    def encrypt(self, raw):
        assert isinstance(raw, bytes), "{!r} isn't a bytestring".format(raw)
        raw = self.pad(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw))

    def decrypt(self, enc):
        assert isinstance(enc, bytes), "{!r} isn't a bytestring".format(enc)
        enc = base64.b64decode(enc)
        iv = enc[:self.BS]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self.unpad(cipher.decrypt(enc[self.BS:]))

    def attempt_initialise(self, window):
        with self.get_password_db() as db:
            curs = db.cursor()
            curs.execute('select count(*) from managed')
            # at least one record has been created, i.e. a password exists
            if curs.fetchone()[0] > 0:
                return True

        while True:
            password = window.entry_line.blocking_prompt(
                prompt='Enter password <b>(pick a good one)</b>',
                private=True
            )
            confirm = window.entry_line.blocking_prompt(
                prompt='Confirm password',
                private=True
            )

            if password is None or confirm is None:
                return False
            elif password == confirm:
                self.key = hashlib.sha256(password.encode('utf8')).digest()
                domain = '!!frozen-brains-tell-no-tales!!'
                self.save_form(domain, {})
                return True
            else:
                self.roland.notify("Password doesn't match confirmation")

    def unlock(self, window):
        if not self.attempt_initialise(window):
            raise ValueError('Database not initialised')

        if self.key is not None:
            return

        for i in range(3):
            password = window.entry_line.blocking_prompt(
                prompt='Master password',
                private=True
            )

            if password is None:
                break

            try:
                self.test_password(password)
            except ValueError:
                self.roland.notify('Incorrect password')
            else:
                self.key = hashlib.sha256(password.encode('utf8')).digest()
                return
        raise ValueError('Could not unlock database')

    def test_password(self, password):
        self.key = hashlib.sha256(password.encode('utf8')).digest()
        try:
            if not self.get_for_domain(b'!!frozen-brains-tell-no-tales!!'):
                raise ValueError('Incorrect password')
        finally:
            self.key = None

    def get_for_domain(self, domain):
        assert isinstance(domain, bytes)
        with self.get_password_db() as db:
            curs = db.cursor()
            curs.execute('select id, last_used, description, domain, data from managed order by last_used desc')
            records = curs.fetchall()
        return [
            self.FormFill(id, last_used, self.decrypt(encrypted_description), self.decrypt(encrypted_domain), msgpack.loads(self.decrypt(data)))
            for (id, last_used, encrypted_description, encrypted_domain, data) in records if self.decrypt(encrypted_domain) == domain
        ]

    def save_form(self, domain, form, description=None):
        if description is None:
            description = 'Form for {}'.format(domain)

        encrypted_description = self.encrypt(description.encode('utf8'))
        encrypted_domain = self.encrypt(domain.encode('utf8'))
        encrypted_form = self.encrypt(msgpack.dumps(form))

        record = (datetime.datetime.now(), encrypted_description, encrypted_domain, encrypted_form)

        with self.get_password_db() as db:
            cursor = db.cursor()

            cursor.execute('insert into managed (last_used, description, domain, data) '
                           'values (?, ?, ?, ?)', record)
            db.commit()

    def get_password_db(self):
        return sqlite3.connect(config_path('passwords.{}.db', self.roland.profile), detect_types=sqlite3.PARSE_DECLTYPES)

    def update_last_used(self, record_id):
        with self.get_password_db() as db:
            cursor = db.cursor()

            cursor.execute('update managed set last_used = ? where id = ?', (datetime.datetime.now(), record_id))
