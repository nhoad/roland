#include <sstream>
#include <future>
#include <mutex>
#include <thread>
using std::placeholders::_1;
#include <tuple>
#include <unordered_map>

#include <wordexp.h>
#include <sys/un.h>

#include <dbus/dbus-glib.h>
#include <dbus/dbus.h>
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
        bool close_on_complete;
        int _fd;
        int page_id;
        size_t bytes_written;
        msgpack::unpacker unpacker;
        std::string buf;
        std::mutex buffer_lock;

        public:
            session(int page_id, int fd):
                    writing(false), close_on_complete(true), _fd(fd),
                    page_id(page_id), bytes_written(0) {};

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
        std::map<int, std::shared_ptr<WebKitDOMNodeList>> highlight_matches;
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

    typedef std::unordered_map<std::string, std::string> notes;

    class request {
        friend std::ostream &operator<<(std::ostream &os, const request &req);
        public:
        int id;
        int page_id;
        notes arguments;
        WebKitWebPage *page;
        std::shared_ptr<session> session;
        std::string command;
        MSGPACK_DEFINE(id, command, arguments);
    };

    class reply {
        friend std::ostream &operator<<(std::ostream &os, const request &req);
        public:
        int id;
        notes notes;
        void write(std::shared_ptr<session> session);
        MSGPACK_DEFINE(id, notes);
    };

    enum class commands {
        highlight,
        click,
        remove_overlay,
        get_source,
        form_fill,
        serialise_form,
        set_log_level,
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

    void do_click(request *req);
    void do_highlight(request *req);
    void do_remove_overlay(request *req);
    void do_get_source(request *req);
    void do_form_fill(request *req);
    void do_serialise_form(request *req);
    void do_set_log_level(request *req);
    void process_request(request *req);
    void run_highlight(const std::string selector, std::shared_ptr<request> req);

    commands command_to_enum(const std::string &command)
    {
        if (command == "highlight") {
            return commands::highlight;
        } else if (command == "click") {
            return commands::click;
        } else if (command == "remove_overlay") {
            return commands::remove_overlay;
        } else if (command == "get_source") {
            return commands::get_source;
        } else if (command == "form_fill") {
            return commands::form_fill;
        } else if (command == "set_log_level") {
            return commands::set_log_level;
        } else if (command == "serialise_form") {
            return commands::serialise_form;
        }
        return commands::unknown;
    }

    struct SharedGObjectDeleter
    {
        void operator()(void* p) const {
            g_object_unref(p);
        }
    };

    struct SharedGVariantDeleter
    {
        void operator()(GVariant* p) const {
            g_variant_unref(p);
        }
    };

    std::shared_ptr<GVariant> dbus_execute(const char *command, GVariant *arguments)
    {
        GError *error;
        GDBusProxy *proxy;

        error = NULL;
        char service_name[255], service_path[255];
        snprintf(service_name, 255, "com.deschain.roland.%s", roland::roland::instance()->profile().c_str());
        snprintf(service_path, 255, "/com/deschain/roland/%s", roland::roland::instance()->profile().c_str());

        proxy = g_dbus_proxy_new_for_bus_sync(
            G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_NONE, NULL, service_name, service_path, service_name,
            NULL, &error);

        return std::shared_ptr<GVariant>(g_dbus_proxy_call_sync(
            proxy, command, arguments, G_DBUS_CALL_FLAGS_NONE,
            -1, NULL, &error), SharedGVariantDeleter());
    };

    std::string flatten_whitespace(std::string key)
    {
        std::istringstream iss(key);
        key = "";
        std::string s;
        while(iss >> s){
             if (key != "" )
                 key += " " + s;
             else
                 key = s;
        }
        return key;
    }

    void click(const int page_id, const std::string &click_id, const bool new_window)
    {
        auto matches = roland::instance()->highlight_matches[page_id];

        int id = std::atoi(click_id.c_str());

        if (matches != nullptr) {
            auto node = webkit_dom_node_list_item(matches.get(), id);

            if (node != nullptr) {
                if (new_window) {
                    const auto url = webkit_dom_html_anchor_element_get_href(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(node));
                    dbus_execute("open_window", g_variant_new("(s)", url));
                } else {
                    webkit_dom_html_element_click(WEBKIT_DOM_HTML_ELEMENT(node));
                    webkit_dom_element_focus(WEBKIT_DOM_ELEMENT(node));

                    if (WEBKIT_DOM_IS_HTML_TEXT_AREA_ELEMENT(node) ||
                        WEBKIT_DOM_IS_HTML_SELECT_ELEMENT(node) ||
                        (WEBKIT_DOM_IS_HTML_INPUT_ELEMENT(node) &&
                             strcmp(webkit_dom_html_input_element_get_input_type(WEBKIT_DOM_HTML_INPUT_ELEMENT(node)), "button") != 0)) {
                        dbus_execute("enter_insert", g_variant_new("(i)", page_id));
                    }
                }
            }
        }
       roland::instance()->highlight_matches[page_id] = nullptr;
    }

    void remove_overlay(std::shared_ptr<request> req)
    {
        auto dom = webkit_web_page_get_dom_document(req->page);
        auto html = webkit_dom_document_query_selector(dom, "html", nullptr);
        auto overlays = std::shared_ptr<WebKitDOMNodeList>(webkit_dom_document_query_selector_all(dom, ".roland_overlay", nullptr), SharedGObjectDeleter());

        const auto len = webkit_dom_node_list_get_length(overlays.get());
        for (int i=0; i < len; i++) {
            auto overlay = webkit_dom_node_list_item(overlays.get(), i);
            webkit_dom_node_remove_child(WEBKIT_DOM_NODE(html), WEBKIT_DOM_NODE(overlay), nullptr);
        }
    }
}

void roland::roland::init(std::string profile, WebKitWebExtension *extension)
{
    this->_profile = profile;
    this->extension = extension;

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
        if (!writing && fd() != -1) {
            do_write();
        }
    });
}

void roland::session::do_write()
{
    assert(_fd != -1);

    writing = false;

    std::lock_guard<std::mutex> guard(buffer_lock);
    if (buf.size()) {
        int written;
        std::tie(written, buf) = io::write(_fd, buf);

        // io::write closes for us, so no need to worry about that
        if (written >= 0) {
            writing = true;
            bytes_written += written;
        }
    } else if (close_on_complete && bytes_written > 0) {
        // when a socket is first opened, epoll will tell us it's writable,
        // because well it is. If we haven't written anything yet we don't
        // want to close, because that's pretty rude.
        do_close();
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
                reply.notes["error"] = "invalid page requested";
                reply.write(shared_from_this());
            } else {
                ::roland::process_request(alloced_request);
            }
        }, std::move(request));
    }
}

void roland::reply::write(std::shared_ptr<session> session)
{
    msgpack::sbuffer buf;
    msgpack::pack(buf, *this);
    session->write(std::string(buf.data(), buf.size()));
}

std::string roland::server_path(int page_id)
{
    std::string profile = ::roland::roland::instance()->profile();
    std::string server_path = "~/.config/roland/runtime/webprocess." + profile + "." + std::to_string(page_id);
    wordexp_t exp_result;
    wordexp(server_path.c_str(), &exp_result, 0);
    server_path = exp_result.we_wordv[0];
    return server_path;
}

void roland::do_highlight(request *req)
{
    assert(req->page != nullptr);

    // webkit stuff is not threadsafe, so we need to execute this within the
    // confines of gtk's event loop.
    // having two event loops is so sucky :( particularly because mine is so
    // easy to use, but maybe i'm biased.
    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);
        auto selector = req->arguments["selector"];
        run_highlight(selector, req);
        return false;
    }, req);
}

void roland::run_highlight(const std::string selector, std::shared_ptr<request> req)
{
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
    auto dom = webkit_web_page_get_dom_document(req->page);

    // FIXME: selector over all frames?
    auto raw_elems = std::shared_ptr<WebKitDOMNodeList>(webkit_dom_document_query_selector_all(dom, selector.c_str(), nullptr), SharedGObjectDeleter());

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

        if (WEBKIT_DOM_IS_HTML_ANCHOR_ELEMENT(elem)) {
            text << i << ": "
                 << webkit_dom_html_anchor_element_get_text(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(elem))
                 << " ("
                 << webkit_dom_html_anchor_element_get_href(WEBKIT_DOM_HTML_ANCHOR_ELEMENT(elem))
                 << ')';
        } else if (WEBKIT_DOM_IS_HTML_SELECT_ELEMENT(elem)) {
            text << i << ": " << webkit_dom_html_select_element_get_name(WEBKIT_DOM_HTML_SELECT_ELEMENT(elem));
        } else if (WEBKIT_DOM_IS_HTML_INPUT_ELEMENT(elem)) {
            const std::string type = webkit_dom_html_input_element_get_input_type(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));

            if (type == "submit" || type == "button") {
                text << i << ": " << webkit_dom_html_input_element_get_value(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));
            } else {
                text << i << ": " << webkit_dom_html_input_element_get_name(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));
            }
        } else if (WEBKIT_DOM_IS_HTML_BUTTON_ELEMENT(elem)) {
            text << i << ": " << webkit_dom_html_button_element_get_value(WEBKIT_DOM_HTML_BUTTON_ELEMENT(elem));
        } else if (WEBKIT_DOM_IS_HTML_TEXT_AREA_ELEMENT(elem)) {
            text << i << ": " << webkit_dom_html_text_area_element_get_name(WEBKIT_DOM_HTML_TEXT_AREA_ELEMENT(elem));
        } else if (WEBKIT_DOM_IS_HTML_FORM_ELEMENT(elem)) {
            text << i << ": " << webkit_dom_html_form_element_get_action(WEBKIT_DOM_HTML_FORM_ELEMENT(elem));
        } else {
            text << i << ": I don't know what I am";
        }
        auto key = flatten_whitespace(text.str());
        reply.notes[key] = std::to_string(i);
    }

    roland::instance()->highlight_matches[req->page_id] = raw_elems;

    auto overlay = webkit_dom_document_create_element(dom, "div", nullptr);
    webkit_dom_element_set_inner_html(overlay, html.str().c_str(), nullptr);

    auto html_elem = webkit_dom_document_query_selector(dom, "html", nullptr);

    webkit_dom_node_append_child(WEBKIT_DOM_NODE(html_elem), WEBKIT_DOM_NODE(overlay), nullptr);

    webkit_dom_element_set_attribute_ns(overlay, nullptr, "class", "roland_overlay", nullptr);

    reply.id = req->id;
    reply.write(req->session);
}


void roland::do_remove_overlay(request *req)
{
    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);

        remove_overlay(req);

        ::roland::reply reply;
        reply.id = req->id;
        reply.write(req->session);

        return false;
    }, req);
}

void roland::do_click(request *req)
{
    ::roland::reply reply;
    reply.id = req->id;
    reply.write(req->session);

    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);
        remove_overlay(req);

        std::string click_id = req->arguments["click_id"];
        bool new_window = (std::string(req->arguments["new_window"]) == "True");

        click(req->page_id, click_id, new_window);

        return false;
    }, req);
}

void roland::do_get_source(request *req)
{
    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);

        auto dom = webkit_web_page_get_dom_document(req->page);
        auto html = webkit_dom_document_query_selector(dom, "html", nullptr);

        std::string text = webkit_dom_element_get_outer_html(html);

        ::roland::reply reply;
        reply.id = req->id;
        reply.notes["html"] = text;
        reply.write(req->session);

        return false;
    }, req);
}

void roland::do_form_fill(request *req)
{
    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);

        auto dom = webkit_web_page_get_dom_document(req->page);

        auto non_empty = [] (char *value) -> bool {
            return (value != nullptr || strlen(value) > 0);
        };

        for (const auto &pair: req->arguments) {
            auto selector = pair.first;
            auto value = pair.second;

            auto raw_elems = std::shared_ptr<WebKitDOMNodeList>(webkit_dom_document_query_selector_all(dom, selector.c_str(), nullptr), SharedGObjectDeleter());

            const auto len = webkit_dom_node_list_get_length(raw_elems.get());
            for (int i=0; i < len; i++) {
                auto input = webkit_dom_node_list_item(raw_elems.get(), i);
                if (WEBKIT_DOM_IS_HTML_SELECT_ELEMENT(input)) {
                    auto orig_value = webkit_dom_html_select_element_get_value(WEBKIT_DOM_HTML_SELECT_ELEMENT(input));
                    if (non_empty(orig_value))
                        continue;

                    webkit_dom_html_select_element_set_value(WEBKIT_DOM_HTML_SELECT_ELEMENT(input), value.c_str());
                } else if (WEBKIT_DOM_IS_HTML_TEXT_AREA_ELEMENT(input)) {
                    auto orig_value = webkit_dom_html_select_element_get_value(WEBKIT_DOM_HTML_SELECT_ELEMENT(input));
                    if (non_empty(orig_value))
                        continue;

                    webkit_dom_html_text_area_element_set_value(WEBKIT_DOM_HTML_TEXT_AREA_ELEMENT(input), value.c_str());
                } else if (WEBKIT_DOM_IS_HTML_INPUT_ELEMENT(input)) {
                    const std::string type = webkit_dom_html_input_element_get_input_type(WEBKIT_DOM_HTML_INPUT_ELEMENT(input));

                    if (type == "checkbox") {
                        webkit_dom_html_input_element_set_checked(WEBKIT_DOM_HTML_INPUT_ELEMENT(input), value == "on");
                    } else {
                        auto orig_value = webkit_dom_html_select_element_get_value(WEBKIT_DOM_HTML_SELECT_ELEMENT(input));
                        if (non_empty(orig_value))
                            continue;
                        webkit_dom_html_input_element_set_value(WEBKIT_DOM_HTML_INPUT_ELEMENT(input), value.c_str());
                    }
                }
            }
        }

        ::roland::reply reply;
        reply.id = req->id;
        reply.write(req->session);

        return false;
    }, req);
}

void roland::do_serialise_form(request *req)
{
    gdk_threads_add_idle([] (gpointer data) -> gboolean {
        auto req = std::shared_ptr<request>((request*)data);
        remove_overlay(req);

        std::string form_id = req->arguments["form_id"];

        int id = std::atoi(form_id.c_str());
        auto matches = roland::instance()->highlight_matches[req->page_id];

        ::roland::reply reply;
        reply.id = req->id;
        if (matches != nullptr) {
            auto node = webkit_dom_node_list_item(matches.get(), id);

            if (node != nullptr) {
                auto elems = std::shared_ptr<WebKitDOMHTMLCollection>(webkit_dom_html_form_element_get_elements(WEBKIT_DOM_HTML_FORM_ELEMENT(node)), SharedGObjectDeleter());
                const auto len = webkit_dom_html_collection_get_length(elems.get());

                for (int i=0; i < len; i++) {
                    auto elem = webkit_dom_html_collection_item(elems.get(), i);

                    std::stringstream selector;
                    char *value = nullptr;

                    if (WEBKIT_DOM_IS_HTML_SELECT_ELEMENT(elem)) {
                        const std::string name = webkit_dom_html_select_element_get_name(WEBKIT_DOM_HTML_SELECT_ELEMENT(elem));
                        selector << "select[name=\"" << name << "\"]";
                        value = webkit_dom_html_select_element_get_value(WEBKIT_DOM_HTML_SELECT_ELEMENT(elem));
                    } else if (WEBKIT_DOM_IS_HTML_INPUT_ELEMENT(elem)) {
                        const std::string type = webkit_dom_html_input_element_get_input_type(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));
                        const std::string name = webkit_dom_html_input_element_get_name(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));
                        value = webkit_dom_html_input_element_get_value(WEBKIT_DOM_HTML_INPUT_ELEMENT(elem));

                        if (type == "submit" || type == "button" || type == "hidden") {
                            continue;
                        }

                        selector << "input[type=\"" << type << "\"]";
                        selector << "[name=\"" << name << "\"]";
                    } else if (WEBKIT_DOM_IS_HTML_TEXT_AREA_ELEMENT(elem)) {
                        const std::string name = webkit_dom_html_text_area_element_get_name(WEBKIT_DOM_HTML_TEXT_AREA_ELEMENT(elem));
                        value = webkit_dom_html_text_area_element_get_value(WEBKIT_DOM_HTML_TEXT_AREA_ELEMENT(elem));
                        selector << "textarea[name=\"" << name << "\"]";
                    }

                    if (value != nullptr && strlen(value) > 0)
                        reply.notes[selector.str()] = value;
                }
            }
        }
        reply.write(req->session);
        return false;
    }, req);
}

void roland::do_set_log_level(request *req)
{
    int level = std::atoi(req->arguments["log_level"].c_str());
    logging::level = level;

    ::roland::reply reply;
    reply.id = req->id;
    reply.write(req->session);

    logger(1, "log level set to " << level);

    delete req;
};

void roland::process_request(request *req)
{
    auto s = command_to_enum(req->command);

    switch(s) {
        case commands::click:
            do_click(req);
            break;
        case commands::remove_overlay:
            do_remove_overlay(req);
            break;
        case commands::highlight:
            do_highlight(req);
            break;
        case commands::get_source:
            do_get_source(req);
            break;
        case commands::form_fill:
            do_form_fill(req);
            break;
        case commands::serialise_form:
            do_serialise_form(req);
            break;
        case commands::set_log_level:
            do_set_log_level(req);
            break;
        case commands::unknown:
        {
            logger(1, "Unknown command " << req->command);
            ::roland::reply reply;
            reply.notes["error"] = "unknown command";
            reply.id = req->id;
            reply.write(req->session);
            break;
        }
    }
};
