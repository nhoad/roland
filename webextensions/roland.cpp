#include <string>

#include <msgpack.hpp>
#include "roland.hpp"

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
