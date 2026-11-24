# Copyright (C) 2004 Jesse Chappell <jesse@essej.net>
#  
# This file is free software; as a special exception the author gives
# unlimited permission to copy and/or distribute it, with or without 
# modifications, as long as this notice is preserved.
# 
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY, to the extent permitted by law; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

EXTRA_DIST = autogen.sh configure OSC top_marker README_Windows.md windows
EXTRA_DIST += tools/slweb.py tools/test_slweb.py \
	tools/slweb_web/index.html tools/slweb_web/app.css tools/slweb_web/app.js
SUBDIRS =  libs src

#------ albBegin ----------------------------------
# sllatency: the latency measurement tool (tools/sllatency.py), installed
# as "sllatency" next to sooperlooper and slgui.  A Python 3 script; it
# needs numpy and the Python jack module at run time, not to build.
EXTRA_DIST += tools/sllatency.py tools/test_sllatency.py

install-exec-local:
	$(MKDIR_P) $(DESTDIR)$(bindir)
	$(INSTALL_SCRIPT) $(srcdir)/tools/sllatency.py $(DESTDIR)$(bindir)/sllatency
	$(INSTALL_SCRIPT) $(srcdir)/tools/slweb.py $(DESTDIR)$(bindir)/slweb

install-data-local:
	$(MKDIR_P) $(DESTDIR)$(datadir)/sooperlooper/slweb
	$(INSTALL_DATA) $(srcdir)/tools/slweb_web/index.html $(DESTDIR)$(datadir)/sooperlooper/slweb/index.html
	$(INSTALL_DATA) $(srcdir)/tools/slweb_web/app.css $(DESTDIR)$(datadir)/sooperlooper/slweb/app.css
	$(INSTALL_DATA) $(srcdir)/tools/slweb_web/app.js $(DESTDIR)$(datadir)/sooperlooper/slweb/app.js

uninstall-local:
	rm -f $(DESTDIR)$(bindir)/sllatency
	rm -f $(DESTDIR)$(bindir)/slweb
	rm -f $(DESTDIR)$(datadir)/sooperlooper/slweb/index.html
	rm -f $(DESTDIR)$(datadir)/sooperlooper/slweb/app.css
	rm -f $(DESTDIR)$(datadir)/sooperlooper/slweb/app.js
#------ albEnd ----------------------------------

