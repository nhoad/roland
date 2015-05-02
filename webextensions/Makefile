CPPC=g++
CPPFLAGS = -std=c++11 -Wall -Wno-deprecated-declarations
LDFLAGS = -lpthread
.PHONY: all

roland.so: roland.cpp
	$(CPPC) $(CPPFLAGS) $(LDFLAGS) \
		-shared -o $@ -fPIC $< \
		`pkg-config --cflags --libs glib-2.0 webkit2gtk-4.0 dbus-1`

install: roland.so
	cp roland.so ~/.config/roland/webextensions/
