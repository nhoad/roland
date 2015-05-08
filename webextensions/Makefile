CPPC=clang++
CPPFLAGS = -std=c++11 -Wall -Wno-deprecated-declarations -g
LDFLAGS = -lpthread
.PHONY: all

roland.so: roland.cpp io.hpp roland.hpp
	$(CPPC) $(CPPFLAGS) $(LDFLAGS) \
		-shared -o $@ -fPIC $< \
		`pkg-config --cflags --libs glib-2.0 webkit2gtk-4.0`

install: roland.so
	cp roland.so ~/.config/roland/webextensions/

roland.testing: roland.cpp io.hpp
	$(CPPC) $(CPPFLAGS) $(LDFLAGS) -DDEBUG \
		-o $@ $< \
		`pkg-config --cflags --libs glib-2.0 webkit2gtk-4.0`
