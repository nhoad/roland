#include <stdio.h>
#include <stdlib.h>

#include <dbus/dbus.h>
#include <dbus/dbus-glib.h>
#include <glib-object.h>
#include <webkit2/webkit-web-extension.h>

static void roland_dbus_execute(const char *command, GVariant *arguments);

char *profile;

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
    g_variant_get(user_data, "s", &profile);

    printf("Roland web extension loaded for profile \"%s\"\n", profile);

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
    char service_name[255], service_path[255];
    snprintf(service_name, 255, "com.deschain.roland.%s", profile);
    snprintf(service_path, 255, "/com/deschain/roland/%s", profile);

    proxy = g_dbus_proxy_new_for_bus_sync(
        G_BUS_TYPE_SESSION, G_DBUS_PROXY_FLAGS_NONE, NULL, service_name, service_path, service_name,
        NULL, &error);

    g_dbus_proxy_call_sync(
        proxy, command, arguments, G_DBUS_CALL_FLAGS_NONE,
        -1, NULL, &error);
}

