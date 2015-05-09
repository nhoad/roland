roland
======

Another minimal web browser with vim-like bindings, but in Python in this time

Roland aims to be modular yet complete, with a small code base. It currently
comes in at well under 2500 lines of code, yet provides...

 - UI is *fast*. None of the latency from Vimperator here.
 - configuration via Python (see `example_config.py`)
 - configurable popup blocker
 - download manager
 - history
 - cookie management
 - link-follow support

Roland tries to provide as little UI as possible, because that way there's less
reason to use the mouse. It uses libnotify for notifications (i.e. errors,
download progress) and an entry field for all other input. Roland aims to have
NO popup windows of any kind, nothing that is not controllable via the
keyboard. If you find one, it's a bug.

Profile support
---------------

Roland supports multiple profiles. Specify which profile you want to use with
--profile, e.g. `roland --profile jake` to load the 'jake' profile.

If not specified, the profile is set to 'default'.

This affects history, cookies, session and stylesheet data.


User Stylesheets
----------------

Just like Firefox's chrome.css, this allows you to style pages with your own
custom CSS. Found at`~/.config/roland/stylesheet.<profile>.css`.


Configuration
-------------

Configuration goes at ~/.config/roland/config.py. See `example_config.py` for
my config.
