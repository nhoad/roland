#include <string>

#include <msgpack.hpp>
#include "roland.hpp"

#include <libsoup/soup.h>
#include <glib-object.h>
#include <webkit2/webkit-web-extension.h>

extern "C" {
void webkit_web_extension_initialize_with_user_data(
    WebKitWebExtension *extension, GVariant *user_data);
}

static void
web_page_document_loaded_callback(WebKitWebPage *web_page, gpointer user_data)
{
    const gchar *uri = webkit_web_page_get_uri(web_page);

    if (uri == NULL) {
        return;
    }

    roland::dbus_execute("page_loaded", g_variant_new("(s)", uri));
}

static bool
web_page_send_request_callback(
    WebKitWebPage *web_page, WebKitURIRequest *request,
    WebKitURIResponse *redirected_response, gpointer user_data)
{
    auto c_uri = webkit_uri_request_get_uri(request);

    assert(c_uri != nullptr);

    if (redirected_response != nullptr) {
        auto headers = webkit_uri_response_get_http_headers(redirected_response);
        assert(headers != nullptr);
        auto hsts = soup_message_headers_get_one(headers, "Strict-Transport-Security");

        if (hsts != nullptr) {
            roland::dbus_execute("update_hsts_policy", g_variant_new("(ss)", c_uri, hsts));
        }
    }

    auto uri = std::string(c_uri);

    if (uri.find("http://") != 0) {
        // if the uri isn't http, we don't care
        return false;
    }

    auto variant_should_rewrite = roland::dbus_execute("hsts_policy", g_variant_new("(s)", c_uri));

    gboolean should_rewrite = FALSE;

    if (variant_should_rewrite.get() == nullptr) {
        logger(1, "NULL HSTS response from roland for " << c_uri);
    }

    g_variant_get(variant_should_rewrite.get(), "(b)", &should_rewrite);

    if (should_rewrite) {
        uri = std::string("https") + uri.substr(strlen("http"), std::string::npos);
        logger(1, "HSTS rewritten to " << uri);
        webkit_uri_request_set_uri(request, uri.c_str());
    } else {
        logger(6, "Not rewriting " << uri);
    }

    return false;
}

static void
web_page_created_callback(
    WebKitWebExtension *extension, WebKitWebPage *web_page, gpointer user_data)
{
    const int page_id = webkit_web_page_get_id(web_page);

    logger(1, "Starting page server for " << page_id);

    auto server = std::make_shared<roland::pageserver>(page_id);
    io::loop::instance()->add_selectable(server);

    g_signal_connect(
        web_page, "document-loaded",
        G_CALLBACK(web_page_document_loaded_callback),
        NULL
    );

    g_signal_connect(
        web_page, "send-request",
        G_CALLBACK(web_page_send_request_callback),
        NULL
    );
}

G_MODULE_EXPORT void
webkit_web_extension_initialize_with_user_data(
    WebKitWebExtension *extension, GVariant *user_data)
{
    gchar *c_profile;
    g_variant_get(user_data, "s", &c_profile);

    logging::level = 1;

    logger(1, "Roland web extension loaded for profile " << c_profile);

    // fired when a new window is created, not page navigation
    g_signal_connect(
        extension,
        "page-created",
        G_CALLBACK(web_page_created_callback),
        NULL
    );

    roland::init(c_profile, extension);
}
