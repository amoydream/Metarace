
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

import pygtk
pygtk.require("2.0")

import gtk
import glib
import gobject
import pango
import threading
import random
import irclib
import time
import os
import sys
import logging

import metarace
from metarace import unt4
from metarace import tod
from metarace import uiutil
from metarace import strops
from metarace import telegraph
from metarace import jsonconfig

# Global Defaults
FONTFACE='Nimbus Sans L Bold Condensed'
FONTSIZE=20	# font size in pixels
MOTD=''		# Message of the day

# Config filename
CONFIGFILE=u'road_announce.json'
LOGFILE=u'road_announce.log'

# Bunches colourmap
COLOURMAP=[['#ffa0a0','red',1.0,0.1,0.1],
           ['#a0ffa0','green',0.1,1.0,0.1],
           ['#a0a0ff','blue',0.1,0.1,1.0],
           ['#f0b290','amber',0.9,0.6,0.1],
           ['#b290f0','violet',0.7,0.1,0.8],
           ['#f9ff10','yellow',0.9,1.0,0.1],
           ['#ff009b','pink',1.0,0.0,0.7],
           ['#00ffc3','cyan',0.1,1.0,0.8]]
COLOURMAPLEN=len(COLOURMAP)
STARTTIME=80	# in seconds
#MAPWIDTH=STARTTIME*TIMETICK
MAPHMARGIN=8
MAPVMARGIN=6
LOGHANDLER_LEVEL=logging.DEBUG

def roundedrecMoonlight(cr,x,y,w,h,radius_x=4,radius_y=4):
    """Draw a rounded rectangle."""

    #from mono moonlight aka mono silverlight
    #test limits (without using multiplications)
    # http://graphics.stanford.edu/courses/cs248-98-fall/Final/q1.html
    ARC_TO_BEZIER = 0.55228475
    if radius_x > w - radius_x:
        radius_x = w / 2
    if radius_y > h - radius_y:
        radius_y = h / 2

    #approximate (quite close) the arc using a bezier curve
    c1 = ARC_TO_BEZIER * radius_x
    c2 = ARC_TO_BEZIER * radius_y

    cr.new_path();
    cr.move_to ( x + radius_x, y)
    cr.rel_line_to ( w - 2 * radius_x, 0.0)
    cr.rel_curve_to ( c1, 0.0, radius_x, c2, radius_x, radius_y)
    cr.rel_line_to ( 0, h - 2 * radius_y)
    cr.rel_curve_to ( 0.0, c2, c1 - radius_x, radius_y, -radius_x, radius_y)
    cr.rel_line_to ( -w + 2 * radius_x, 0)
    cr.rel_curve_to ( -c1, 0, -radius_x, -c2, -radius_x, -radius_y)
    cr.rel_line_to (0, -h + 2 * radius_y)
    cr.rel_curve_to (0.0, -c2, radius_x - c1, -radius_y, radius_x, -radius_y)
    cr.close_path ()

class rr_announce(object):
    """Road race announcer application."""
 
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
        self.started = False
        self.hide()
        self.io.exit()
        print(u'Waiting for remote to exit...')
        self.io.join()

    def app_destroy_handler(self):
        if self.started:
            #self.saveconfig()
            self.shutdown()     # threads are joined in shutdown
        # close event and remove main log handler
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
        self.running = False
        # flag quit in main loop
        gtk.main_quit()
        return False

    def window_destroy_cb(self, window):
        """Handle destroy signal."""
        #self.hide()
        glib.idle_add(self.app_destroy_handler)
    
    #def map_area_expose_event_cb(self, widget, event):
        #"""Update desired portion of drawing area."""
        #x , y, width, height = event.area
        #widget.window.draw_drawable(widget.get_style().fg_gc[gtk.STATE_NORMAL],
                                    #self.map_src, x, y, x, y, width, height)
        #return False

    def do_bubble(self, cr, cnt, x1, x2):
        """Draw a rider bubble from x1 to x2 on the map."""
        rx = int(self.timetick*float(x1.timeval))	# conversion to
        rx2 = int(self.timetick*float(x2.timeval))     # device units
        rw = rx2 - rx
        if rw < 8:			# clamp min width
            rw = 8
        cidx = cnt%COLOURMAPLEN
        roundedrecMoonlight(cr,rx+MAPHMARGIN,8+MAPVMARGIN,rw,30)
        cr.set_source_rgba(COLOURMAP[cidx][2],
                           COLOURMAP[cidx][3],
                           COLOURMAP[cidx][4],0.8)
        cr.fill_preserve()
        cr.set_source_rgb(0.2,0.2,0.2)
        cr.stroke()

    #def map_redraw(self):
        #"""Lazy full map redraw method."""
        #cr = self.map_src.cairo_create()
#
        #width = self.map_winsz
        #height = 80
        #cr.identity_matrix()
#
        ## bg filled
        #cr.set_source_rgb(0.85,0.85,0.9)
        #cr.paint()
#
        ## scale: | . . . . i . . . . | . . . 
        #cr.set_line_width(1.0)
        #cr.set_font_size(15.0)
        #xof = 0
        #dw = width - (2 * MAPHMARGIN)
        #dh = height - (2 * MAPVMARGIN)
        #cnt = 0
        #while xof < dw:
            #lh = 4
            #if cnt % 10 == 0:
                #lh = 12
                #cr.set_source_rgb(0.05,0.05,0.05)
                #cr.move_to(xof+MAPHMARGIN+1,
                           #MAPVMARGIN+dh-lh-2)
                #cr.show_text(tod.tod(int(cnt)).rawtime(0))
            #elif cnt % 5 == 0:
                #lh = 8
            #cr.set_source_rgb(0.05,0.05,0.05)
            #cr.move_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh)
            #cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh)
            #cr.stroke()
            #if cnt % 5 == 0:
                #cr.set_source_rgb(0.96,0.96,0.96)
                #cr.move_to(xof+MAPHMARGIN, MAPVMARGIN)
                #cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh-2)
                #cr.stroke()
            #cnt += 1
            #xof += self.timetick
#
        #cr.set_line_width(2.0)
        #inbox = False
        #cnt = 0
        #st=None
        #x1=None
        #x2=None
        #for r in self.riders:
            #if r[7] is not None:	# have a row
                #if st is None:
                    #st = r[7].truncate(0)	# save lap split
                #if not inbox:
                    #x1 = r[7].truncate(0)-st
                    #inbox = True
                #x2 = r[7]-st
            #else:			# have a break
                #if inbox:
                    #self.do_bubble(cr, cnt, x1, x2)
                    #cnt += 1
                #inbox = False
        #if inbox:
            #self.do_bubble(cr, cnt, x1, x2)

    #def map_area_configure_event_cb(self, widget, event):
        ##"""Re-configure the drawing area and redraw the base image."""
        #x, y, width, height = widget.get_allocation()
        #self.map_winsz = width
        #if width > self.map_w:
            #nw = MAPWIDTH
            #if width > MAPWIDTH:
                #nw = width
            #self.map_src = gtk.gdk.Pixmap(widget.window, nw, height)
            #self.map_w = nw
            #self.map_redraw()
        #return True

    def clear(self):
        self.lbl_header.set_text(self.motd)
        self.elap_lbl.set_text('')
        self.riders.clear()
        self.inters.clear()
        #self.map_redraw()		# update src map
        #self.map_area.queue_draw()	# queue copy to screen
        
## NOT TOMORROW
    #def start_line(self, msg):
        #sr = msg.split(chr(unt4.US))
        #if len(sr) == 5:
            #if sr[1] != '':	# got a rider
                #st = tod.str2tod(sr[4])
                #sts = '' 
                #if st is not None:
                    #sts = st.rawtime(0)
                    #self.start_time.set_text(ets)
                #self.start_rider.set_text(sr[1] + ' ' + sr[2] + ' ' + sr[3])
            #else:		# clearit
                #self.fin_rank.set_text('')
                #self.fin_rider.set_text('')
                #self.fin_time.set_text('')
                #self.fin_box.hide()

    def inter_line(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) == 5:
            if sr[1] != '':	# got a rider
                et = tod.str2tod(sr[4])
                ets = '' 
                if et is not None:
                    if '.' in sr[4]:
                        ets = et.rawtime(2)
                    else:
                        ets = et.rawtime(0) + '   '
                    self.int_time.set_text(ets)
                rs = sr[0]
                if rs != '' and rs.isdigit():
                    rs += '.'
                self.int_rank.set_text(rs)
                self.int_rider.set_text(sr[1] + ' ' + sr[2] + ' ' + sr[3])
                #self.int_box.show()
            else:		# clearit
                self.int_rank.set_text('')
                self.int_rider.set_text('')
                self.int_time.set_text('')
                #self.int_box.hide()

    def finish_line(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) == 5:
            if sr[1] != '':	# got a rider
                et = tod.str2tod(sr[4])
                ets = '' 
                if et is not None:
                    if '.' in sr[4]:
                        ets = et.rawtime(2)
                    else:
                        ets = et.rawtime(0) + '   '
                    self.fin_time.set_text(ets)
                rs = sr[0]
                if rs != '' and rs.isdigit():
                    rs += '.'
                self.fin_rank.set_text(rs)
                self.fin_rider.set_text(sr[1] + ' ' + sr[2] + ' ' + sr[3])
                self.fin_box.show()
            else:		# clearit
                self.fin_rank.set_text('')
                self.fin_rider.set_text('')
                self.fin_time.set_text('')
                self.fin_box.hide()

    def append_rider(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) == 5:
            rs = sr[0]
            if rs != '' and rs.isdigit():
                rs += '.'
            rftime = tod.str2tod(sr[4])
            if rftime is not None:
                if len(self.riders) == 0:
                    # Case 1: Starting a new cat
                    self.cur_cat = rftime
                    nr=[rs,sr[1],sr[2],sr[3],rftime.rawtime(2),'',None]
                else:
                    # Case 2: down on leader
                    downtime = rftime - self.cur_cat
                    self.last_time = rftime
                    nr=[rs,sr[1],sr[2],sr[3],rftime.rawtime(2),
                        '+' + downtime.rawtime(1),None]
            else: 
                # Informative non-timeline record
                nr=[sr[0],sr[1],sr[2],sr[3],
                        '', '', None]
                
            self.riders.append(nr)
            #self.map_redraw()		# update src map
            #self.map_area.queue_draw()	# queue copy to screen

    def append_arrival(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) == 6:
            rs = sr[0]
            nr=[sr[0],sr[1],sr[2],sr[3],sr[4],sr[5],None]
        else: 
                # Informative non-timeline record
            nr=[sr[0],sr[1],sr[2],sr[3], '', '', None]
                
        self.inters.append(nr)
        #self.map_redraw()		# update src map
        #self.map_area.queue_draw()	# queue copy to screen

    def msg_cb(self, m, nick, chan):
        """Handle message packet in main thread."""
        redraw = False
        if self.remoteuser and self.remoteuser.lower() != nick.lower():
            #self.log.debug(u'Ignoring command from ' + repr(nick))
            return False
        if m.header == 'rider':
            self.append_rider(m.text)
        if m.header == 'arrivalrow':
            self.append_arrival(m.text)
        elif m.header == 'finish':
            self.finish_line(m.text)
        elif m.header == 'ttsplit':
            self.inter_line(m.text)
        elif m.header == 'time':
            self.elap_lbl.set_text(m.text)
        elif m.header == 'title':
            self.lbl_header.set_text(m.text)
        elif m.header == 'start':
            self.cur_split = tod.str2tod(m.text)
        elif m.erp:
            self.clear()
        return False
        
    def loadconfig(self):
        """Load config from disk."""
        cr = jsonconfig.config({u'road_announce':{
                                    u'remoteport':u'',
                                    u'remoteuser':u'',
                                    u'remotechan':u'#agora',
                                    u'autoscroll':False,
                                    u'timetick':12,
                                    u'fontsize':20,
                                    u'fullscreen':False,
                                    u'groupcol':True,
                                    u'catcol':True,
                                    u'bunchmap':True,
                                    u'search':False,
                                    u'maxlaptime':'2:00',
                                    u'motd':MOTD}})
        cr.add_section(u'road_announce')
        cwfilename = metarace.default_file(CONFIGFILE)
        # read in sysdefaults before checking for config file
        cr.merge(metarace.sysconf, u'road_announce')

        # re-set log file
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
            self.loghandler.close()
            self.loghandler = None
        self.loghandler = logging.FileHandler(
                             os.path.join(self.configpath, LOGFILE))
        self.loghandler.setLevel(LOGHANDLER_LEVEL)
        self.loghandler.setFormatter(logging.Formatter(
                       '%(asctime)s %(levelname)s:%(name)s: %(message)s'))
        self.log.addHandler(self.loghandler)

        # check for config file
        try:
            with open(cwfilename, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading app config: ' + repr(e))

        self.fontsize = strops.confopt_posint(cr.get(u'road_announce',
                                              u'fontsize'), FONTSIZE)

        fnszstr = str(self.fontsize)+'px'
        self.font = pango.FontDescription(FONTFACE + ' ' + fnszstr)

        self.motd = cr.get(u'road_announce', u'motd')
        if strops.confopt_bool(cr.get(u'road_announce', u'fullscreen')):
            self.window.fullscreen()

        self.remoteport = cr.get(u'road_announce', u'remoteport')
        self.remotechan = cr.get(u'road_announce', u'remotechan')
        self.remoteuser = cr.get(u'road_announce', u'remoteuser')
        self.io.set_portstr(portstr=self.remoteport,
                             channel=self.remotechan)
        if self.remoteuser:
            self.log.info(u'Enabled remote control by: '
                          + repr(self.remoteuser))
        else:
            self.log.info(u'Promiscuous remote control enabled.')

        self.lbl_header.modify_font(self.font)
        self.elap_lbl.modify_font(self.font)
        self.search_lbl.modify_font(self.font)
        self.search_entry.modify_font(self.font)
        self.view.modify_font(self.font)
        self.iview.modify_font(self.font)
        self.fin_rank.modify_font(self.font)
        self.fin_label.modify_font(self.font)
        self.fin_rider.modify_font(self.font)
        self.fin_time.modify_font(self.font)
        self.int_rank.modify_font(self.font)
        self.int_label.modify_font(self.font)
        self.int_rider.modify_font(self.font)
        self.int_time.modify_font(self.font)

    def __init__(self, configpath=None):
        # logger and log handler
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        self.loghandler = None  # set in loadconfig to meet dir

        # meet configuration path and options
        if configpath is None:
            configpath = u'.'   # None assumes 'current dir'
        self.configpath = configpath
        self.loglevel = logging.INFO    # UI log window

        self.io = telegraph.telegraph()
        self.io.set_pub_cb(self.msg_cb)
        self.started = False
        self.running = True

        self.fontsize = FONTSIZE
        fnszstr = str(self.fontsize)+'px'
        self.font = pango.FontDescription(FONTFACE + ' ' + fnszstr)

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'irtt_announce.ui'))
        self.window = b.get_object('window')

        self.lbl_header = b.get_object('lbl_header')
        self.lbl_header.modify_font(self.font)
        self.lbl_header.set_text('metarace irtt announce ' + metarace.VERSION)
        self.elap_lbl = b.get_object('elap_lbl')
        self.elap_lbl.set_text('--:--')
        self.elap_lbl.modify_font(self.font)

        b.get_object('fin_pfx_lbl').modify_font(self.font)
        self.fin_label = b.get_object('fin_pfx_lbl')
        self.fin_label.modify_font(self.font)
        self.fin_rank = b.get_object('fin_rank_lbl')
        self.fin_rank.modify_font(self.font)
        self.fin_rider = b.get_object('fin_rider_lbl')
        self.fin_rider.modify_font(self.font)
        self.fin_time = b.get_object('fin_time_lbl')
        self.fin_time.modify_font(self.font)

        b.get_object('int_pfx_lbl').modify_font(self.font)
        self.int_label = b.get_object('int_pfx_lbl')
        self.int_label.modify_font(self.font)
        self.int_rank = b.get_object('int_rank_lbl')
        self.int_rank.modify_font(self.font)
        self.int_rider = b.get_object('int_rider_lbl')
        self.int_rider.modify_font(self.font)
        self.int_time = b.get_object('int_time_lbl')
        self.int_time.modify_font(self.font)

        self.fin_box = b.get_object('fin_box')	# seems to work?
        self.fin_box.hide()

        #self.map_winsz = 0
        #self.map_xoft = 0
        #self.map_w = 0
        #self.map_area = b.get_object('map_area')
        #self.map_src = None
        #self.map_area.set_size_request(-1, 80)
        #self.map_area.show()

        # lap & bunch status values
        self.cur_lap = tod.tod(0)
        self.cur_split = tod.tod(0)
        self.cur_bunchid = 0
        self.cur_bunchcnt = 0

        self.riders = gtk.ListStore(gobject.TYPE_STRING,  # rank
                                    gobject.TYPE_STRING,  # no.
                                    gobject.TYPE_STRING,  # namestr
                                    gobject.TYPE_STRING,  # cat/com
                                    gobject.TYPE_STRING,  # timestr
                                    gobject.TYPE_STRING,  # downtime
                                    gobject.TYPE_PYOBJECT) # rftod

        self.inters = gtk.ListStore(gobject.TYPE_STRING,  # rank
                                    gobject.TYPE_STRING,  # no.
                                    gobject.TYPE_STRING,  # namestr
                                    gobject.TYPE_STRING,  # turn
                                    gobject.TYPE_STRING,  # eta
                                    gobject.TYPE_STRING,  # speed
                                    gobject.TYPE_PYOBJECT) # rftod
        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(False)
        t.set_rules_hint(True)
        t.set_headers_visible(False)
        self.search_lbl = b.get_object('search_lbl')
        self.search_lbl.modify_font(self.font)
        self.search_entry = b.get_object('search_entry')
        self.search_entry.modify_font(self.font)
        t.set_search_entry(b.get_object('search_entry'))
        t.set_search_column(1)
        t.modify_font(self.font)
        uiutil.mkviewcoltxt(t, 'Rank', 0,width=40)
        uiutil.mkviewcoltxt(t, 'No.', 1,calign=1.0,width=40)
        uiutil.mkviewcoltxt(t, 'Rider', 2,expand=True,fixed=True)
        uiutil.mkviewcoltxt(t, 'Cat', 3,calign=0.0)
        uiutil.mkviewcoltxt(t, 'Time', 4,calign=1.0,width=100)
        uiutil.mkviewcoltxt(t, 'Gap', 5,calign=1.0,width=90)
        # "down" time?
        #uiutil.mkviewcoltxt(t, 'Bunch', 5,width=50,bgcol=6,calign=0.5)
        b.get_object('text_scroll').add(t)
        t.show()
        t.grab_focus()

        ## let's suppress the arrivals for now
        ##b.get_object('arrival_xpnd').hide()
        t = gtk.TreeView(self.inters)
        self.iview = t
        t.set_reorderable(False)
        t.set_rules_hint(True)
        t.set_headers_visible(False)
        t.modify_font(self.font)
        uiutil.mkviewcoltxt(t, '', 0,width=40)
        uiutil.mkviewcoltxt(t, 'No.', 1,calign=1.0,width=40)
        uiutil.mkviewcoltxt(t, 'Rider', 2,expand=True,fixed=True)
        uiutil.mkviewcoltxt(t, 'Turn', 3,calign=0.0)
        uiutil.mkviewcoltxt(t, 'Time', 4,calign=1.0,width=100)
        uiutil.mkviewcoltxt(t, 'Avg', 5,calign=1.0,width=90)
        t.show()
        b.get_object('arrival_scroll').add(t)
        b.connect_signals(self)

def main():
    """Run the announce application."""
    metarace.init()
    app = rr_announce()
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

