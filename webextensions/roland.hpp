#include <sstream>
#include <future>
#include <mutex>
#include <thread>
using std::placeholders::_1;
#include <tuple>
#include <unordered_map>

#include <wordexp.h>
#include <sys/un.h>

#include <glib.h>
#include <webkit2/webkit-web-extension.h>
#include <webkitdom/webkitdom.h>

#include "io.hpp"

namespace roland
{
    class pageserver: public io::selectable
    {
        int page_id;
        int _listenfd;

        public:
            pageserver(int page_id) : page_id(page_id), _listenfd(-1) {
                this->start();
            };

            void start();

            /// Selectable API
            void do_read();
            void do_write() {};

            int fd() { return _listenfd; };
    };

    class session: public io::selectable
    {
        bool writing;
        int _fd;
        int page_id;
        msgpack::unpacker unpacker;
        std::string buf;
        std::mutex buffer_lock;

        public:
            session(int page_id, int fd):
                    writing(false), _fd(fd), page_id(page_id) {};

            void write(const std::string &buf);

            /// Selectable API
            void do_read();
            void do_write();

            int fd() { return _fd; };

            // handle deficiency in std::enable_shared_from_this and inheritance
            std::shared_ptr<session> shared_from_this() {
                return std::static_pointer_cast<session>(io::selectable::shared_from_this());
            };
    };

    class roland {
        std::string _profile;
        std::thread loop_thread;

        public:
        WebKitWebExtension *extension;
        std::string profile() { return _profile; };

        void init(std::string profile, WebKitWebExtension *extension);

        static roland* instance() {
            static roland roro;
            return &roro;
        };

#ifdef DEBUG
        void join() {
            loop_thread.join();
        };
#endif
    };

    class request {
        friend std::ostream &operator<<(std::ostream &os, const request &req);
        public:
        int id;
        int page_id;
        WebKitWebPage *page;
        std::shared_ptr<session> session;
        std::string command;
        MSGPACK_DEFINE(id, command);
    };

    typedef std::unordered_map<std::string, std::string> notes;

    class reply {
        friend std::ostream &operator<<(std::ostream &os, const request &req);
        public:
        int id;
        notes notes;
        MSGPACK_DEFINE(id, notes);
    };

    enum class commands {
        follow,
        remove_overlay,
        unknown,
    };

    std::ostream &operator<<(std::ostream &os, const request &req)
    {
        return os << "id=" << req.id << " page_id=" << req.page_id << " command=" << req.command;
    }

    void init(std::string profile, WebKitWebExtension *extension)
    {
        io::loop::instance()->init();
        roland::roland::instance()->init(profile, extension);
    };

    std::string server_path(int page_id);

    void do_remove_overlay(request *req);
    void do_follow(request *req);
    void process_request(request *req);

    commands command_to_enum(const std::string &command)
    {
        if (command == "follow") {
            return commands::follow;
        } else if (command == "remove_overlay") {
            return commands::remove_overlay;
        }
        return commands::unknown;
    }

    struct SharedGObjectDeleter
    {
        void operator()(void* p) const {
            g_object_unref(p);
        }
    };
}

void roland::roland::init(std::string profile, WebKitWebExtension *extension)
{
    this->_profile = profile;
    this->extension = extension;

    // FIXME: connect to "~/.config/roland/ui." + profile

    std::function<void()> run = std::bind(&io::loop::run, io::loop::instance());
    loop_thread = std::thread(run);
}

void roland::pageserver::start()
{
    if ((_listenfd = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) {
        int xerrno = errno;
        logger(1, "error creating socket " << io::error(xerrno));
        return;
    }

    std::string server_path = ::roland::server_path(page_id);

    struct sockaddr_un server_addr;
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sun_family = AF_UNIX;
    strncpy(server_addr.sun_path, server_path.c_str(), server_path.size());

    unlink(server_path.c_str());

    if (bind(_listenfd, (struct sockaddr *) &server_addr, sizeof(server_addr)) < 0) {
        int xerrno = errno;
        logger(1, "error binding socket to " << server_path << ": " << io::error(xerrno));
        close(_listenfd);
        _listenfd = -1;
        return;
    }

    io::nonblocking(_listenfd);

    if (listen(_listenfd, SOMAXCONN) < 0) {
        int xerrno = errno;
        logger(1, "error listening on FD " << fd() << server_path << ": " << io::error(xerrno));
        close(_listenfd);
        _listenfd = -1;
        return;
    }
}

void roland::pageserver::do_read()
{
    struct sockaddr in_addr;
    socklen_t in_len;
    int sock;

    in_len = sizeof(in_addr);

    while (true) {
        if ((sock = accept(_listenfd, &in_addr, &in_len)) < 0) {
            if ((errno == EAGAIN) || (errno == EWOULDBLOCK)) {
                break;
            } else {
                int xerrno = errno;
                logger(1, "error accepting on FD " << fd() << ": " << io::error(xerrno));
                break;
            }
        }

        logger(4, "accepted FD " << sock);
        io::nonblocking(sock);

        auto session = std::make_shared<::roland::session>(page_id, sock);
        io::loop::instance()->add_selectable(session);
    }
}

void roland::session::write(const std::string &buf)
{
    std::lock_guard<std::mutex> guard(buffer_lock);

    this->buf += buf;

    auto self = shared_from_this();
    io::loop::instance()->call_soon([self, this]() {
        if (fd() != -1) {
            do_write();
        }
    });
}

void roland::session::do_write()
{
    assert(_fd != -1);

    if (writing) {
        return;
    }

    writing = false;

    std::lock_guard<std::mutex> guard(buffer_lock);
    if (buf.size()) {
        buf = io::write(_fd, buf);
        writing = true;
    }
}

void roland::session::do_read()
{
    auto buf = io::consume(_fd);

    if (!buf.size()) {
        do_close();
        return;
    }

    unpacker.reserve_buffer(buf.size());
    memcpy(unpacker.buffer(), buf.data(), buf.size());
    unpacker.buffer_consumed(buf.size());

    auto self = shared_from_this();

    msgpack::unpacked r;
    while(unpacker.next(&r)) {
        msgpack::object obj = r.get();
        ::roland::request request;
        request.page_id = page_id;
        obj.convert(&request);

        std::async(std::launch::async, [self, this](::roland::request request) {
            logger(1, "request received " << request);
            WebKitWebPage *page = webkit_web_extension_get_page(::roland::roland::instance()->extension, page_id);

            auto alloced_request = new ::roland::request(request);

            alloced_request->page = page;
            alloced_request->session = self;

            if (page == nullptr) {
                ::roland::reply reply;
                reply.id = request.id;
                msgpack::sbuffer buf;
                msgpack::pack(buf, reply);
                this->write(std::string(buf.data(), buf.size()));
                reply.notes["error"] = "invalid page requested";
            } else {
                ::roland::process_request(alloced_request);
            }
        }, std::move(request));
    }
}

std::string roland::server_path(int page_id)
{
    std::string profile = ::roland::roland::instance()->profile();
    std::string server_path = "~/.config/roland/webprocess." + profile + "." + std::to_string(page_id);
    wordexp_t exp_result;
    wordexp(server_path.c_str(), &exp_result, 0);
    server_path = exp_result.we_wordv[0];
    return server_path;
}

gboolean grosscall(gpointer userdata)
{
    return false;
}

void roland::do_follow(request *req)
{
    assert(req->page != nullptr);

    // webkit stuff is not threadsafe, so we need to execute this within the
    // confines of gtk's event loop.
    // having two event loops is so sucky :( particularly because mine is so
    // easy to use, but maybe i'm biased.
    g_idle_add([] (gpointer data) -> gboolean {

        auto is_visible = [](WebKitDOMElement *elem) -> bool {
            return (int(webkit_dom_element_get_offset_height(elem)) != 0 ||
                    int(webkit_dom_element_get_offset_width(elem)) != 0);
        };


        auto get_offset = [](WebKitDOMElement *elem) -> std::tuple<int, int> {
            int x = 0, y = 0;

            while (elem != nullptr) {
                x += webkit_dom_element_get_offset_left(elem) - webkit_dom_element_get_scroll_left(elem);
                y += webkit_dom_element_get_offset_top(elem) - webkit_dom_element_get_scroll_top(elem);
                elem = webkit_dom_element_get_offset_parent(elem);
            }
            return std::make_tuple(x, y);
        };

        auto req = std::shared_ptr<request>((request*)data);
        auto dom = webkit_web_page_get_dom_document(req->page);

        // FIXME: selector over all frames?
        // FIXME: select all form fields as well. At the moment the
        // implementation works by getting the anchor elements and returning
        // their links - this is to do with the async nature of web/ui process split.
        // std::shared_ptr<WebKitDOMNodeList> raw_elems(webkit_dom_document_query_selector_all(dom, "a, input:not([type=hidden]), textarea, select, button", nullptr), SharedGObjectDeleter());
        std::shared_ptr<WebKitDOMNodeList> raw_elems(webkit_dom_document_query_selector_all(dom, "a", nullptr), SharedGObjectDeleter());

        const auto len = webkit_dom_node_list_get_length(raw_elems.get());

        ::roland::reply reply;

        std::stringstream html;

        for (int i=0; i < len; i++) {
            auto node = webkit_dom_node_list_item(raw_elems.get(), i);

            if (!WEBKIT_DOM_IS_ELEMENT(node))
                continue;

            auto elem = (WebKitDOMElement*)node;

            if (!is_visible(elem))
                continue;

            int left, top;
            std::tie(left, top) = get_offset(elem);

            std::stringstream span;

            span << "<span style=\""
                 << "left: " << left << "px;"
                 << "top: " << top << "px;"
                 << "position: fixed;"
                 << "font-size: 12px;"
                 << "background-color: #ff6600;"
                 << "color: white;"
                 << "font-weight: bold;"
                 << "font-family: Monospace;"
                 << "padding: 0px 1px;"
                 << "border: 1px solid black;"
                 << "z-index: 100000;"
                 << "\">" << i << "</span>";

            html << span.str();

            std::stringstream text;

            text << i << ": "
                 << webkit_dom_html_anchor_element_get_text(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(elem))
                 << " ("
                 << webkit_dom_html_anchor_element_get_href(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(elem))
                 << ')';


            const auto uri = webkit_dom_html_anchor_element_get_href(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(elem));
            reply.notes[text.str()] = uri;
        }

        // FIXME: add a unique ID for the div and delete it.
        auto overlay = webkit_dom_document_create_element(dom, "div", nullptr);
        webkit_dom_element_set_inner_html(overlay, html.str().c_str(), nullptr);

        auto html_elem = webkit_dom_document_query_selector(dom, "html", nullptr);

        webkit_dom_node_append_child(WEBKIT_DOM_NODE(html_elem), WEBKIT_DOM_NODE(overlay), nullptr);

        webkit_dom_element_set_attribute_ns(overlay, nullptr, "class", "roland_overlay", nullptr);

        reply.id = req->id;
        msgpack::sbuffer buf;
        msgpack::pack(buf, reply);
        req->session->write(std::string(buf.data(), buf.size()));

        return false;
    }, req);
}


void roland::do_remove_overlay(request *req)
{
    g_idle_add([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);

        auto dom = webkit_web_page_get_dom_document(req->page);
        auto html = webkit_dom_document_query_selector(dom, "html", nullptr);
        auto overlay = webkit_dom_document_query_selector(dom, ".roland_overlay", nullptr);

        if (overlay != nullptr) {
            webkit_dom_node_remove_child(WEBKIT_DOM_NODE(html), WEBKIT_DOM_NODE(overlay), nullptr);
        }

        ::roland::reply reply;
        reply.id = req->id;
        msgpack::sbuffer buf;
        msgpack::pack(buf, reply);
        req->session->write(std::string(buf.data(), buf.size()));

        return false;
    }, req);
}

void roland::process_request(request *req)
{
    auto s = command_to_enum(req->command);

    switch(s) {
        case commands::remove_overlay:
            do_remove_overlay(req);
            break;
        case commands::follow:
            do_follow(req);
            break;
        case commands::unknown:
        {
            ::roland::reply reply;
            reply.notes["error"] = "unknown command";
            reply.id = req->id;
            msgpack::sbuffer buf;
            msgpack::pack(buf, reply);
            req->session->write(std::string(buf.data(), buf.size()));
            break;
        }
    }
};
