
# Metarace : Cycle Race Abstractions
# Copyright (C) 2012  Nathan Fraser
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

## TODO: Remove/replace this program

# simple announcer terminal for old metarace track meet
#
# approx 80x28, addressed with UNT4/RTF style line/pos/erp/erl

import pygtk
pygtk.require("2.0")

import gtk
import glib
import gobject
import pango
import math
import os
import sys

import metarace
from metarace import unt4
from metarace import tod
from metarace import strops
from metarace import telegraph
from metarace import jsonconfig

SCB_W = 80
SCB_H = 28
FONTSIZE=20     # font size in pixels
MOTD=u'Metarace Track Announce'         # Message of the day

# Config filename
CONFIGFILE=u'road_announce.json'
LOGFILE=u'road_announce.log'

class announce(object):
    def loadconfig(self):
        """Load config from disk."""
        cr = jsonconfig.config({u'track_announce':{
                                    u'remoteport':u'',
                                    u'remoteuser':u'',
                                    u'remotechan':u'#agora',
                                    u'fontsize':unicode(FONTSIZE),
                                    u'fullscreen':False,
                                    u'motd':MOTD}})
        cr.add_section(u'track_announce')
        cwfilename = metarace.default_file(CONFIGFILE)
        # read in sysdefaults before checking for config file
        cr.merge(metarace.sysconf, u'track_announce')

        # check for config file
        try:
            with open(cwfilename, 'rb') as f:
                cr.read(f)
        except Exception as e:
            print(u'Reading app config: ' + repr(e))

        self.motd = cr.get(u'track_announce', u'motd')
        self.fullscreen = strops.confopt_bool(
                              cr.get(u'track_announce', u'fullscreen'))

        self.remoteport = cr.get(u'track_announce', u'remoteport')
        self.remotechan = cr.get(u'track_announce', u'remotechan')
        self.remoteuser = cr.get(u'track_announce', u'remoteuser')
        self.io.set_portstr(portstr=self.remoteport,
                             channel=self.remotechan)
        if self.remoteuser:
            print(u'Enabled remote control by: '
                          + repr(self.remoteuser))
        else:
            print(u'Promiscuous remote control enabled.')

    def intro(self):
        m = unt4.unt4()
        m.yy=SCB_H-1
        m.text='metarace track announce ' + metarace.VERSION
        m.xx=SCB_W-len(m.text)
        self.msg_cb(m)
        
    def show(self):
        self.window.show()

    def hide(self):
        self.window.show()

    def start(self):
        """Start io thread."""
        if not self.started:
            self.io.start()
            self.started = True

    def shutdown(self):
        """Cleanly shutdown."""
        self.io.exit()
        self.io.join()
        self.started = False

    def destroy_cb(self, window):
        """Handle destroy signal."""
        if self.started:
            self.shutdown()
        self.running = False
        gtk.main_quit()
    
    def clear(self):
        """Re-set all lines."""
        ntxt = ''
        for i in range(0,SCB_H-1):
            ntxt += ''.ljust(SCB_W) + '\n'
        ntxt += ''.ljust(SCB_W)
        self.buffer.set_text(ntxt)

    def showmotd(self):
        """Draw 'welcome'."""
        if self.motd != '':
            m = unt4.unt4(yy=0, xx=0, text=self.motd, erl=True)
            self.msg_cb(m)

    def delayed_cursor(self):
        """Remove the mouse cursor from the text area."""
        pixmap = gtk.gdk.Pixmap(None, 1, 1, 1)
        color = gtk.gdk.Color()
        cursor = gtk.gdk.Cursor(pixmap, pixmap, color, color, 0, 0)
        self.view.get_window(gtk.TEXT_WINDOW_TEXT).set_cursor(cursor)
        self.clear()
        self.showmotd()
        # Try to position on twinview
        self.window.set_gravity(gtk.gdk.GRAVITY_NORTH_EAST)
        width, height = self.window.get_size()
        self.window.move(gtk.gdk.screen_width() - width,0)
        if self.fullscreen:
            self.window.stick()
            self.window.maximize()
        return False

    def msg_cb(self, m, nick=u'', chan=u''):
        """Handle message packet in main thread."""
        if self.remoteuser and self.remoteuser.lower() != nick.lower():
            #self.log.debug(u'Ignoring command from ' + repr(nick))
            return False
        if m.erp:
            self.clear()
        if m.yy is not None:
            rem = SCB_W - (m.xx + len(m.text))
            if rem <= 0:
                m.text = m.text[0:(SCB_W-m.xx)]
            elif m.erl:
                m.text += ' '* rem
            j = self.buffer.get_iter_at_line_offset(m.yy, m.xx)
            k = self.buffer.get_iter_at_line_offset(m.yy,
                                                    m.xx + len(m.text))
            self.buffer.delete(j, k)
            self.buffer.insert(j, m.text)
        return False

    def view_size_allocate_cb(self, widget, alloc, data=None):
        """Respond to window resize."""
        cw = alloc.width // SCB_W
        ch = alloc.height // SCB_H
        lh = ch
        if cw * 2 < ch:
            lh = cw * 2
        fh = int(math.floor(0.80 * lh))
        if fh != self.fh:
            widget.modify_font(
                    pango.FontDescription('monospace bold {0}px'.format(fh)))
            self.fh = fh
        
    def __init__(self):
        self.io = telegraph.telegraph()
        self.io.set_pub_cb(self.msg_cb)
        self.started = False
        self.running = True
        self.rscount = 0
        self.fh = 0
        self.motd = ''
        self.failcount = 0
        self.failthresh = 45    # connect timeout ~45sec
        self.remoteuser = u''
        self.fullscreen = False	# maximise window?

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'announce.ui'))
        self.window = b.get_object('window')
        self.window.set_decorated(False)
        self.window.set_default_size(400,300)
        self.buffer = b.get_object('buffer')
        self.view = b.get_object('view')
        #self.view.modify_base(gtk.STATE_NORMAL, gtk.gdk.Color('#001'))
        #self.view.modify_fg(gtk.STATE_NORMAL, gtk.gdk.Color('#001'))
        #self.view.modify_bg(gtk.STATE_NORMAL, gtk.gdk.Color('#001'))
        #self.view.modify_text(gtk.STATE_NORMAL, gtk.gdk.Color('#eef'))
        self.clear() # compulsory clear -> fills all lines
        self.intro()
        glib.timeout_add_seconds(5,self.delayed_cursor)
        b.connect_signals(self)

def main():
    """Run the announce application."""
    metarace.init()
    app = announce()
    app.loadconfig()
    app.show()
    app.start()
    try:
        metarace.mainloop()
    except:
        app.shutdown()
        raise

if __name__ == '__main__':
    main()

