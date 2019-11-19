
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

"""IRTT Start console.

This module implements the IRTT start console application. Starters
are loaded from a start list and then displayed with a countdown on
the console. If a timy and uscbsrv connection are provided, start
times are reported via uscbsrv for the irtt race handler.

"""

# TODO:
#
#	- allow for missing gst and pygst modules on systems with no gstreamer
#	- provide a signalling mechanism to pass messages between S/F
#	- ui for config/startlist mgmnt
#	- remote startlist updates

import pygtk
pygtk.require("2.0")

# !! TODO: The following should be optional !!
import pygst
pygst.require("0.10")
import gst

import gtk
import glib
import pango
import pangocairo
import logging
import os
import sys

import metarace
from metarace import tod
from metarace import timy
from metarace import telegraph
from metarace import strops
from metarace import ucsv
from metarace import jsonconfig

# Global Defaults
USCBSRV_CHANNEL=u'#announce'

# Globals
CONFIGFILE=u'irtt_start.json'
LOGFILE=u'irtt_start.log'
TIMEFONT='Nimbus Sans L Condensed Bold '
COUNTERFONT='Nimbus Sans L Condensed Bold '
NOHDR = [u'Start', u'start', u'time', u'Time', u'']
# TYPICAL SYNC ERROR: 17619048	# typical playback error ~20ms
DEFAUDIOSYNCTHRESH = 100000000	# Tolerate no more than 100ms audio desync
				# note: on Pi/ARM, 200ms reqd due to proc lag
                                # this is now configurable

# squeeze text into a desired width
def fit_cent(cr, pr, oh, ow, msg, ch, fn):
    """squeeze text centered at h."""
    if msg is not None:
        cr.save()
        l = pr.create_layout()
        l.set_font_description(pango.FontDescription(fn + str(ch)))
        l.set_text(msg)
        (tw,th) = l.get_pixel_size()
        if tw > ow:
            cr.scale(float(ow)/float(tw),1.0)
            tw = ow
        cr.move_to(0.5*(ow-tw), oh)
        pr.update_layout(l)
        pr.show_layout(l)
        cr.restore()

# draw text centred
def text_cent(cr, pr, oh, ow, msg, ch, fn):
    """Position text centred at h."""
    if msg is not None:
        cr.save()
        l = pr.create_layout()
        l.set_font_description(pango.FontDescription(fn + str(ch)))
        l.set_text(msg)
        (tw,th) = l.get_pixel_size()
        cr.move_to(ow-(0.5 * tw), oh)
        pr.update_layout(l)
        pr.show_layout(l)
        cr.restore()

def tod2key(tod=None):
    """Return a key from the supplied time of day."""
    ret = None
    if tod is not None:
        ret = int(tod.truncate(0).timeval)
    return ret

class irtt_start(object):
    """IRTT Start Display application."""
 
    def show(self):
        self.window.show()

    def hide(self):
        self.window.show()

    def start(self):
        """Start threads."""
        if not self.started:
            self.timer.start()
            self.scb.start()
            self.started = True

    def shutdown(self):
        """Cleanly shutdown."""
        self.scb.exit()
        self.timer.exit()
        self.scb.join()
        self.timer.join()
        self.started = False

    def window_destroy_cb(self, window):
        """Handle destroy signal."""
        if self.started:
            self.shutdown()
        self.running = False
        gtk.main_quit()
    
    def area_expose_event_cb(self, widget, event):
        """Update desired portion of drawing area."""
        x , y, width, height = event.area
        widget.window.draw_drawable(widget.get_style().fg_gc[gtk.STATE_NORMAL],
                                    self.area_src, x, y, x, y, width, height)
        return False

    def area_redraw(self):
        """Lazy full area redraw method."""
        cr = self.area_src.cairo_create()
        pr = pangocairo.CairoContext(cr)
        cr.identity_matrix()

        # bg filled
        cr.set_source_rgb(0.85,0.85,0.90)
        cr.paint()

        # countdown box
        cbh = 0.56*self.height
        cbw = 0.98*self.width
        cbxo = 0.5 * (self.width-cbw)
        cbho = 0.5 * (self.height-cbh)
        cr.rectangle(cbxo, cbho, cbw, cbh)
        cr.set_source_rgb(0.92, 0.92, 1.0)
        cr.fill()

        cr.set_source_rgb(0.1, 0.1, 0.1)
        # time string txt
        if self.tod is not None:
            oh = 0.00 * self.height
            ch = 0.12 * self.height
            text_cent(cr, pr, oh, 0.5 * self.width,
                      self.tod.meridian(),
                      ch, TIMEFONT)

        # countdown txt
        if self.countdown is not None:
            ctx = u''
            if self.countdown >= 0:
                ctx = unicode(self.countdown)
            else:
                ctx = u'+' + unicode(-self.countdown)
            oh = 0.09 * self.height
            ch = 0.48 * self.height
            text_cent(cr, pr, oh, 0.5 * self.width, ctx, ch, COUNTERFONT)

        # rider name txt
        if self.riderstr is not None:
            oh = 0.80 * self.height
            ch = 0.10 * self.height
            #text_cent(cr, pr, oh, 0.5 * self.width,
                      #self.riderstr, ch, COUNTERFONT)
            fit_cent(cr, pr, oh, self.width,
                      self.riderstr, ch, COUNTERFONT)

        # starter bulbs
        if self.bulb is not None:
            rad = 0.12 * self.height
            oh = 0.5 * self.height
            ow = 0
            if self.bulb == 'red':
                ow = 0.15 * self.width
                cr.set_source_rgb(1.0, 0.2, 0.2)
            elif self.bulb == 'green':
                ow = 0.85 * self.width
                cr.set_source_rgb(0.2, 1.0, 0.2)
            cr.move_to(ow, oh)
            cr.arc(ow, oh, rad, 0, 6.3)
            cr.fill()

    def area_configure_event_cb(self, widget, event):
        """Re-configure the drawing area and redraw the base image."""
        x, y, width, height = widget.get_allocation()
        ow = 0
        oh = 0
        if self.area_src is not None:
            ow, oh = self.area_src.get_size()
        if width > ow or height > oh:
            self.area_src = gtk.gdk.Pixmap(widget.window, width, height)
        self.width = width
        self.height = height
        self.area_redraw()
        self.area.queue_draw()
        return True

    def clear(self):
        """Clear all elements and dearm timer."""
        self.countdown = None
        self.riderstr = None
        self.bulb = None
        self.armed = False
        
    def loadconfig(self):
        """Load config from disk."""
        cr = jsonconfig.config({u'irtt_start':{
                                    u'uscbsrv':'',
                                    u'channel':USCBSRV_CHANNEL, 
                                    u'fullscreen':False,
                                    u'timer':'',
                                    u'backlightlow':0.25,
                                    u'backlighthigh':1.0,
                                    u'backlightdev':None,
                                    u'syncthresh':DEFAUDIOSYNCTHRESH,
                                    u'startlist':u'startlist.csv'}})
        cr.add_section(u'irtt_start')
        # check for config file
        cfile = metarace.default_file(CONFIGFILE)
        # read in sysdefaults before checking for config file
        cr.merge(metarace.sysconf, u'irtt_start')

        try:
            self.log.info(u'Reading config from: ' +repr(cfile))
            with open(cfile, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading config: ' + unicode(e))

        if strops.confopt_bool(cr.get(u'irtt_start', u'fullscreen')):
            self.window.fullscreen()

        # set timer port
        tport = cr.get(u'irtt_start', u'timer')
        self.timer.setport(tport)
        self.timer.sane()
        self.timer.keylock()
        self.timer.setcb(self.starttrig)
        self.timer.armlock(True)	# always re-armlock
        self.timer.arm(timy.CHAN_START)

        # load backlight parameters
        self.backlightdev = cr.get(u'irtt_start', u'backlightdev')
        self.backlightlow = strops.confopt_float(cr.get(u'irtt_start',
                                                 u'backlightlow'),0.25)
        self.backlighthigh = strops.confopt_float(cr.get(u'irtt_start',
                                                 u'backlighthigh'),1.0)
        if os.path.exists(self.backlightdev):
            try:
                with open(os.path.join(self.backlightdev,u'max_brightness'),
                                       'rb') as bf:
                    mbstr = bf.read()
                    self.backlightmax = strops.confopt_posint(mbstr,20)
                    self.backlightdev = os.path.join(self.backlightdev,
                                                     u'brightness')
                    self.log.info(u'Using backlight dev '
                                    + repr(self.backlightdev) 
                                    + u'; Max={0}, Low={1}%, High={2}%'.format(
                                        self.backlightmax,
                                        int(100.0*self.backlightlow),
                                        int(100.0*self.backlighthigh)))
            except Exception as e:
                self.log.error(u'Reading from backlight device: ' + repr(e))
                self.backlightdev = None
        else:
            self.log.info(u'Backlight control not configured.')
            self.backlightdev = None

        # audio sync thresh
        self.syncthresh = strops.confopt_posint(
                                   cr.get(u'irtt_start', u'syncthresh'),
                                   DEFAUDIOSYNCTHRESH)
        self.log.info(u'Sync threshold set to: {0:0.3f}s'.format(
                                            float(self.syncthresh)*1e-9))
              
        # set sender port
        nhost = cr.get(u'irtt_start', u'uscbsrv')
        nchannel = cr.get(u'irtt_start', u'channel')
        self.scb.set_portstr(nhost, nchannel)

        # load riders
        datafile = cr.get(u'irtt_start', u'startlist')
        try:
            rlist = []
            with open(datafile,'rb') as f:
                cr = ucsv.UnicodeReader(f)
                for r in cr:
                    key = None
                    st = None
                    bib = u''
                    series = u''
                    name = u''
                    next = None
                    # load rider info
                    # start, no, series, name, cat
                    if len(r) > 0 and r[0] not in NOHDR: # time & no provided
                        st = tod.str2tod(r[0])
                        if len(r) > 1:	# got bib
                            bib = r[1]
                        if len(r) > 2:	# got series
                            series = r[2]
                        if len(r) > 3:  # got name
                            name = u' '.join([r[1],r[3]])
                        if st is not None:
                            # enough data to add a starter
                            key = tod2key(st)
                            nr = [st, bib, series, name, next]
                            self.ridermap[key] = nr
                            rlist.append(key)
            # sort startlist and build list linkages
            curoft = tod2key(tod.tod(u'now'))
            self.currider = None
            rlist.sort()
            prev = None
            for r in rlist:
                if prev is not None:
                    self.ridermap[prev][4] = r	# prev -> next
                prev = r
                if self.currider is None and r > curoft:
                    self.currider = r
                    rvec  = self.ridermap[r]
                    stxt = tod.tod(r).meridian()
                    sno = rvec[1]
                    sname = rvec[3]
                    self.log.info(u'Setting first rider to: '
                          + u','.join([sno, sname]) + u' @ ' + stxt)
            # last link will be None

        except Exception as e:
            # always an error - there must be startlist to continue
            self.log.error(u'Error loading from startlist: '
                             + unicode(e))

    def draw_and_update(self, data=None):
        """Redraw in main loop, not in timeout."""
        self.area_redraw()
        self.area.queue_draw()
        return False

    def delayed_cursor(self):
        """Remove the mouse cursor from the text area."""
        pixmap = gtk.gdk.Pixmap(None, 1, 1, 1)
        color = gtk.gdk.Color()
        cursor = gtk.gdk.Cursor(pixmap, pixmap, color, color, 0, 0)
        self.area.get_window().set_cursor(cursor)
        return False

    def remote_msg(self, msg):
        """Log a message to the uscbsrv."""
        #self.log.debug(msg)
        self.scb.add_rider([msg], u'message')

    def starttrig(self, e):
        """Process a trigger."""
        chan = timy.chan2id(e.chan)
        if (chan == timy.CHAN_START and self.armed
            and self.currider is not None and self.currider in self.ridermap):
            ls = tod.tod(u'now')# log the 'now' time and the rider's wall start
            # emit rider vec
            cr = self.ridermap[self.currider]
            self.scb.add_rider([cr[1], cr[2], e.rawtime(),
                                cr[0].rawtime(), ls.rawtime()], u'starter')
            self.log.info(u'Starter: ' + u','.join([cr[1], e.rawtime()]))
            self.armed = False
        else:
            # emit an anon tod
            self.log.info(u'Impulse: ' + e.rawtime())
            self.scb.add_rider([u'Impulse: ' + unicode(e)], u'message')
        return False

    def riderlogstr(self, key):
        """Return nice log string."""
        ret = u''
        if key in self.ridermap:
            r = self.ridermap[key]
            ret = u' '.join([r[0].rawtime(0),
                             r[1], r[3]])
        return ret

    def check_play_pos(self, expect):
        """Check and handle audio desync error."""
        try:
            curpos,postype = self.player.query_position(gst.FORMAT_TIME, None)
            err = abs(expect - curpos)
            if err > self.syncthresh:
                self.log.error(u'Audio desync detected: ' + repr(err) + 
                                u'@' + repr(curpos))
                self.player.set_state(gst.STATE_NULL)
            else:
                self.log.debug(u'Audio in sync, error=' + repr(err))
        except Exception as e:
            self.log.error(u'Reading audio position: ' + repr(e))
        return False

    def timeout(self, data=None):
        """Handle timeout."""

        # 1: Terminate?
        if not self.running:
            return False

        # 2: Process?
        try:
            ntime = tod.tod(u'now')
            ntod = ntime.truncate(0)
            if ntime >= self.nc.truncate(1):
                self.tod = ntod
                self.nc += tod.ONE
                self.process_timeout()
            else:
                self.log.debug(u'Timeout called early: ' + ntime.rawtime())
                # no need to advance, desired timeout not yet reached
        except Exception as e:
            self.log.error(u'Timeout: ' + unicode(e))

        # 3: Re-Schedule
        tt = tod.tod(u'now')+tod.tod(u'0.01')
        while self.nc < tt:	# ensure interval is positive
            if tod.MAX - tt < tod.ONE:
                self.log.debug(u'Midnight rollover.')
                break
            self.log.debug(u'May have missed an interval, catching up.')
            self.nc += tod.ONE	# 0.01 allows for processing delay
        ival = int(1000.0 * float((self.nc - tod.tod(u'now')).timeval))
        glib.timeout_add(ival, self.timeout)

        # 4: Return False
        return False	# must return False

    def set_backlight(self, percent=None):
        """Attempt to adjust screen brightness between riders."""
        if self.backlightdev and abs(percent - self.backlight) > 0.05:
            if percent < 0.0:
                percent = 0.0
            elif percent > 1.0:
                percent = 1.0
            nb = int(0.5 + percent * self.backlightmax)
            try:
                with open(self.backlightdev, 'wb') as f:
                    f.write(str(nb))
            except Exception as e:
                self.log.error(u'Writing to backlight control: ' + repr(e))
            self.backlight = percent

    def process_timeout(self):
        """Process countdown, redraw display if required."""
        curoft = tod2key(self.tod)
        if self.currider is not None:
            cdn = self.currider - curoft
            if cdn == 10:
                self.player.set_state(gst.STATE_PLAYING)
                self.riderstr = self.ridermap[self.currider][3]
                self.bulb = 'red'
                self.log.debug(u'Player started for: '
                                 + self.riderlogstr(self.currider))
            elif cdn in [8, 7, 6]:	# check audio stream sync
                self.check_play_pos(int((10-cdn)*1e9))
            elif cdn == 15:
                self.player.set_state(gst.STATE_PAUSED)
                self.riderstr = self.ridermap[self.currider][3]
                self.bulb = 'red'
                self.remote_msg(u'15SEC: ' + self.riderlogstr(self.currider))
                self.set_backlight(self.backlighthigh)
            elif cdn == 50 or cdn == 24:
                self.riderstr = self.ridermap[self.currider][3]
                self.bulb = 'red'
                self.log.info(u'LOAD: ' + self.riderlogstr(self.currider))
            elif cdn == 30:
                self.set_backlight(self.backlighthigh)
            elif cdn == 5:
                self.timer.armlock(True)	# always re-armlock
                self.timer.arm(timy.CHAN_START)
                self.armed = True	# self arm is not same as timer arm
                self.remote_msg(u'ARM: ' + self.riderlogstr(self.currider))
            elif cdn == 0:
                self.bulb = 'green'
                self.countdown = 0
                self.log.info(u'GO: ' + self.riderlogstr(self.currider))
            elif cdn == -4:	# load sets minimum gap-> ~25sec
                self.remote_msg(u'CLEAR: ' + self.riderlogstr(self.currider))
                self.set_backlight(self.backlightlow)
                self.clear() # note also removes the bulb
                self.currider = self.ridermap[self.currider][4]

            if cdn >= 0 and cdn <= 30:
                if self.bulb:
                    self.countdown = cdn
                else:
                    self.countdown = None
            else:
                if cdn < 0 and cdn > -5:
                    self.countdown = cdn
                else:
                    self.countdown = None
        else:
            self.clear()	# also does dearm
            self.riderstr = u'[finished]'
            # no more riders or error in init.
        self.draw_and_update()

    def gst_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_EOS:
            self.player.set_state(gst.STATE_NULL)	# close player
        elif t == gst.MESSAGE_ERROR:
            self.player.set_state(gst.STATE_NULL)
            err, debug = message.parse_error()
            self.log.error(u"gst error: %s" % err, debug)
        else:
            pass
            #self.log.debug(u"gst message: " + repr(message))

    def __init__(self):
        # logger and handler
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        self.loghandler = logging.FileHandler(LOGFILE)
        self.loghandler.setLevel(logging.DEBUG)
        self.loghandler.setFormatter(logging.Formatter(
                       '%(asctime)s %(levelname)s:%(name)s: %(message)s'))
        self.log.addHandler(self.loghandler)
        self.log.info(u'IRTT Starter - Init.')

        # require one timy and one uscbsrv
        self.timer = timy.timy()
        self.scb = telegraph.telegraph()

        self.started = False
        self.running = True

        # Audio output
        self.player = gst.element_factory_make("playbin2", "player")
        self.player.set_property("audio-sink",
                         gst.element_factory_make("alsasink", "sink"))
        self.player.set_property("video-sink",
                         gst.element_factory_make("fakesink", "fakesink"))
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.gst_message)
        self.player.set_property('uri', u'file://'
                             + os.path.join(metarace.DB_PATH, u'start.wav'))

        # variables
        self.armed = False
        self.width = 0
        self.height = 0
        self.backlight = 0.0
        self.backlightmax = 20
        self.backlightdev = None
        self.backlightlow = 0.25
        self.backlighthigh = 1.0
        self.syncthresh = 100000000
        self.tod = tod.tod(u'now').truncate(0)
        self.nc = self.tod + tod.tod(u'1.22') # set interval a little off mark
        self.countdown = None
        self.riderstr = None
        self.bulb = None
        self.currider = None
        self.ridermap = {}
        self.window = gtk.Window()
        self.window.set_title(u'Start Clock')
        self.window.connect('destroy', self.window_destroy_cb)
        self.area_src = None
        self.area = gtk.DrawingArea()
        self.area.connect('configure_event', self.area_configure_event_cb)
        self.area.connect('expose_event', self.area_expose_event_cb)
        self.area.set_size_request(400,220)
        self.area.show()
        self.window.add(self.area)
        self.log.info(u'Starting clock intervals at: ' + self.nc.rawtime(3))
        glib.timeout_add(2000, self.timeout)
        glib.timeout_add_seconds(5, self.delayed_cursor)

def main():
    """Run the application."""
    configpath = None
    # expand config on cmd line to realpath _before_ doing chdir
    if len(sys.argv) > 2:
        print(u'usage: irtt_start [configdir]\n')
        sys.exit(1)
    elif len(sys.argv) == 2:
        configpath = os.path.realpath(os.path.dirname(sys.argv[1]))

    metarace.init()
    if configpath is not None:
        os.chdir(configpath)
    app = irtt_start()
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

