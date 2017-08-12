import asyncio
import functools
import io
import os
import threading
from collections import namedtuple

import gbulb
import logbook

from gi.repository import WebKit2WebExtension

from roland.utils import init_logging, runtime_path, RolandConfigBase

log = logbook.Logger(__name__)

Request = namedtuple('Request', 'id command params')
Highlight = namedtuple('Highlight', 'nodes node_lists')


class RolandWebExtension(RolandConfigBase):
    def __init__(self):
        gbulb.install(gtk=False)

        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            # woo threads
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.load_config()
        self.pages = {}
        self.highlight_matches = {}

    def run(self):
        def ignore(ext):
            return ext.__class__.__name__ not in [
                'HistoryManager',
                'HSTSExtension',
                'NotificationManager',
                'ClipboardManager',
            ]

        for ext in self.extensions:
            if ignore(ext):
                continue

            try:
                ext.setup()
            except Exception as e:
                log.exception("Failure setting up {}: {}".format(ext.name, e))
                self.notify("Failure setting up {}: {}".format(ext.name, e), critical=True)

        self.loop.run_forever()

    def do_yank(self, page, yank_id):
        self.do_remove_overlay(page)

        page_id = page.get_id()

        node = self.highlight_matches.pop(page_id).nodes[int(yank_id.decode('utf8'))]
        url = node.get_href()

        if url is not None:
            self.set_clipboard(url)

    def do_click(self, page, click_id, new_window):
        self.do_remove_overlay(page)

        page_id = page.get_id()

        node = self.highlight_matches.pop(page_id).nodes[int(click_id.decode('utf8'))]
        if new_window:
            url = node.get_href()
            from roland.api import dbus_execute
            dbus_execute('open_window', url, page_id)
        else:
            node.click()
            node.focus()

            insert_mode_types = (
                WebKit2WebExtension.DOMHTMLInputElement,
                WebKit2WebExtension.DOMHTMLSelectElement,
                WebKit2WebExtension.DOMHTMLButtonElement,
                WebKit2WebExtension.DOMHTMLTextAreaElement,
            )

            if isinstance(node, insert_mode_types):
                dbus_execute('insert_mode', page_id)

    def do_highlight(self, page, selector):
        selector = selector.decode('utf8')
        def is_visible(elem):
            return int(elem.get_offset_height()) != 0 or int(elem.get_offset_width()) != 0

        def get_offset(elem):
            x, y = 0, 0

            i = 0
            while elem is not None:
                i += 1
                x += elem.get_offset_left() - elem.get_scroll_left()
                y += elem.get_offset_top() - elem.get_scroll_top()
                elem = elem.get_offset_parent()
            return x, y

        i = -1
        def add_node(node):
            nonlocal i

            if not is_visible(node):
                return

            i += 1
            highlight.nodes[i] = node
            left, top = get_offset(node)
            span = ("<span style=\""
                    "left: " + str(left) + "px;"
                    "top: " + str(top) + "px;"
                    "position: fixed;"
                    "font-size: 12px;"
                    "background-color: #ff6600;"
                    "color: white;"
                    "font-weight: bold;"
                    "font-family: Monospace;"
                    "padding: 0px 1px;"
                    "border: 1px solid black;"
                    "z-index: 100000;"
                    "\">" + str(i) + "</span>")

            overlay_html.write(span)

            if isinstance(node, WebKit2WebExtension.DOMHTMLAnchorElement):
                text = '{} ({})'.format(node.get_text(), node.get_href())
            elif isinstance(node, WebKit2WebExtension.DOMHTMLInputElement):
                input_type = node.get_input_type()

                if input_type in ('submit', 'button'):
                    text = node.get_value()
                else:
                    text = node.get_name()
            elif isinstance(node, (WebKit2WebExtension.DOMHTMLSelectElement, WebKit2WebExtension.DOMHTMLFormElement)):
                text = node.get_name()
            elif isinstance(node, WebKit2WebExtension.DOMHTMLButtonElement):
                text = node.get_value()
            elif isinstance(node, WebKit2WebExtension.DOMHTMLTextAreaElement):
                text = node.get_name()
            else:
                text = node.get_inner_text()

            import re
            key = '{}: {}'.format(i, re.sub('\s\s+', ' ', text or '<unknown>').strip())

            notes[key] = str(i)

        def add_nodes(dom, selector):
            nodes = dom.query_selector_all(selector)
            highlight.node_lists.append(nodes)
            for node in (nodes.item(i) for i in range(nodes.get_length())):
                add_node(node)

        notes = {}
        overlay_html = io.StringIO()
        highlight = Highlight({}, [])


        dom = page.get_dom_document()

        add_nodes(dom, selector)

        frames = dom.query_selector_all('frame, iframe')

        for frame in (frames.item(i) for i in range(frames.get_length())):
            dom = frame.get_content_document()
            add_nodes(dom, selector)

        dom = page.get_dom_document()

        overlay = dom.create_element('div')
        overlay.set_inner_html(overlay_html.getvalue())

        html = dom.query_selector('html')
        html.append_child(overlay)
        overlay.set_attribute_ns(None, 'class', 'roland_overlay')

        self.highlight_matches[page.get_id()] = highlight

        return notes

    def do_remove_overlay(self, page):
        dom = page.get_dom_document()
        html = dom.query_selector('html')

        overlays = dom.query_selector_all('.roland_overlay')

        for overlay in (overlays.item(i) for i in range(overlays.get_length())):
            html.remove_child(overlay)

    def do_get_source(self, page):
        dom = page.get_dom_document()
        html = dom.query_selector('html')
        text = html.get_outer_html()
        return {'html': text}

    def do_form_fill(self, page, **selectors):
        dom = page.get_dom_document()

        for selector, value in selectors.items():
            elems = dom.query_selector_all(selector)

            for elem in (elems.get(i) for i in range(elems.get_length())):
                if isinstance(elem, WebKit2WebExtension.DOMHTMLInputElement) and elem.get_input_type() == 'checkbox':
                    elem.set_checked(value == 'on')
                elif elem.get_value():
                    continue
                else:
                    elem.set_value(value)

    def do_serialise_form(self, page, form_id):
        form_id = int(form_id.decode('utf8'))
        page_id = page.get_id()

        notes = {}

        if page_id in self.highlight_matches:
            node = self.highlight_matches[page_id].nodes[form_id]
            elems = node.get_elements()

            for elem in (elems.get(i) for i in range(elems.get_length())):
                if isinstance(elem, WebKit2WebExtension.DOMHTMLSelectElement):
                    name = elem.get_name()
                    selector = 'select[name="{}"]'.format(name)
                    value = elem.get_value()
                elif isinstance(elem, WebKit2WebExtension.DOMHTMLTextAreaElement):
                    name = elem.get_name()
                    selector = 'textarea[name="{}"]'.format(name)
                elif elem.get_input_type() in ('submit', 'button', 'hidden'):
                    continue
                else:
                    selector = 'input[type="{}"]'.format(elem.get_input_type())

                value = elem.get_value()
                notes[selector] = value

        return notes


    async def client_connected(self, reader, writer, *, page_id):
        import msgpack
        unpacker = msgpack.Unpacker()

        while True:
            b = await reader.read(64*1024)

            if not b:
                break

            unpacker.feed(b)

            try:
                request = Request(*unpacker.unpack())
            except msgpack.OutOfData:
                continue
            else:
                break

        try:
            cmd = getattr(self, 'do_{}'.format(request.command.decode('utf8')))
            resp = cmd(
                page=self.pages[page_id],
                **{k.decode('utf8'): v for (k, v) in request.params.items()},
            )
        except Exception as e:
            log.exception("Error handling request {}", request)
        else:
            if not resp:
                resp = {}
            resp = msgpack.dumps([request.id, resp])

            writer.write(resp)
            await writer.drain()
            writer.write_eof()

    def on_page_created(self, extension, web_page):
        page_id = web_page.get_id()
        log.info("Starting page server for {}", page_id)
        self.pages[page_id] = web_page

        path = runtime_path('webprocess.{}.sock'.format(page_id))

        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

        f = asyncio.ensure_future(asyncio.start_unix_server(functools.partial(self.client_connected, page_id=page_id), path=path, loop=self.loop), loop=self.loop)

        f.add_done_callback(lambda *args: log.info("Started page server for {}", page_id))

        web_page.connect("document-loaded", self.on_document_loaded)
        web_page.connect("send-request", self.on_send_request)

    def on_send_request(self, webpage, request, redirected_response):
        uri = request.get_uri()

        if redirected_response:
            headers = redirected_response.get_http_headers()
            hsts = headers.get_one("Strict-Transport-Security")

            if hsts:
                ext = self.get_extension('HSTSExtension')
                if ext is not None:
                    ext.add_entry(uri, hsts)

        if not uri.startswith('http://'):
            return False

        try:
            ext = self.get_extension('HSTSExtension')

            if ext is not None:
                should_rewrite = ext.check_url(uri)
        except Exception as e:
            log.exception("Error checking HSTS policy for {}", uri)
        else:
            should_rewrite = False

        if should_rewrite:
            from urllib import parse as urlparse
            uri = urlparse.urlparse(uri)._replace(scheme='https').geturl()
            request.set_uri(uri)
        return False

    def on_document_loaded(self, webpage):
        if self.is_enabled('HistoryManager'):
            history_manager = self.get_extension('HistoryManager')
            history_manager.update(webpage.get_uri())


def initialize(extension, arguments):
    init_logging()
    log.info("Plugin initialized")

    roland = RolandWebExtension()

    extension.connect('page-created', roland.on_page_created)

    threading.Thread(target=roland.run, daemon=True).start()
