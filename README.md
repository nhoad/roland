roland
======

Another minimal web browser with vim-like bindings, but in Python in this time

Roland aims to be modular yet complete, with a small code base. It currently
comes in at well under 1000 lines of code, yet provides...

 - configuration via Python (see `example_config.py`)
 - configurable popup blocker
 - download manager
 - history
 - cookie management
 - link-follow support

Roland tries to provide as little UI as possible, because that way there's less
reason to use the mouse. It uses libnotify for notifications (i.e. errors,
download progress) and `dmenu` for prompting (though I am considering changing
this to a more traditional Vi-like prompt).
