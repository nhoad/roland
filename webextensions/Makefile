CFLAGS += -Wall -Wextra -Wno-unused-parameter -O3

.PHONY: all

roland.so: roland.c
	$(CC) $(CFLAGS) $(LDFLAGS) \
		-shared -o $@ -fPIC $< \
		`pkg-config --cflags --libs glib-2.0 webkit2gtk-4.0 dbus-1`
