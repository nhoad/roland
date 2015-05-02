#include <iostream>
using std::cout;
using std::endl;

#include <string>

#include <dbus/dbus.h>
#include <dbus/dbus-glib.h>
#include <glib-object.h>
#include <webkit2/webkit-web-extension.h>

extern "C" {
void webkit_web_extension_initialize_with_user_data(
    WebKitWebExtension *extension, GVariant *user_data);
}

static void roland_dbus_execute(const char *command, GVariant *arguments);

static std::string profile;

static void
web_page_document_loaded_callback(WebKitWebPage *web_page, gpointer user_data)
{
    const gchar *uri = webkit_web_page_get_uri(web_page);

    roland_dbus_execute("page_loaded", g_variant_new("(s)", uri));
}

static void
web_page_created_callback(
    WebKitWebExtension *extension, WebKitWebPage *web_page, gpointer user_data)
{
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

    profile = c_profile;

    cout << "Roland web extension loaded for profile '" << profile << "'" << endl;

    // fired when a new window is created, not page navigation
    g_signal_connect(
        extension,
        "page-created",
        G_CALLBACK(web_page_created_callback),
        NULL
    );
}

static void
roland_dbus_execute(const char *command, GVariant *arguments) {
    // FIXME: make this all... async and stuff.
    GError *error;
    GDBusProxy *proxy;

    error = NULL;
    std::string service_name, service_path;

    service_name = "com.deschain.roland." + profile;
    service_path = "/com/deschain/roland/" + profile;

    proxy = g_dbus_proxy_new_for_bus_sync(
        G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_NONE, NULL,
        service_name.c_str(), service_path.c_str(), service_name.c_str(),
        NULL, &error);

    g_dbus_proxy_call_sync(
        proxy, command, arguments, G_DBUS_CALL_FLAGS_NONE,
        -1, NULL, &error);
}

