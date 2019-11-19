
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
TIMETICK=12	# pixels per second
FONTSIZE=20	# font size in pixels
MOTD=''		# Message of the day
SCROLLDELAY=50  # Autoscroll loop delay
SCROLLTHRESH=40 # Pause count before/after scroll
LOGHANDLER_LEVEL=logging.DEBUG

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
MAPWIDTH=STARTTIME*TIMETICK
MAPHMARGIN=8
MAPVMARGIN=6

## interpolate between two x,y points
def posinterp(pp1, pp2, t):
    """Return an x,y point in the segment pp1, pp2 at offset t."""
    xl = pp1[3]
    yl = pp1[4]
    tl = pp1[0]
    xh = pp2[3]
    yh = pp2[4]
    th = pp2[0]
    frac = (t-tl)/(th-tl)
    ret = (xl + frac*(xh-xl),
           yl + frac*(yh-yl))
    return ret

## TODO: replace this fn
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
        self.window.hide()

    def start(self):
        """Start io thread."""
        if not self.started:
            self.io.start()
            self.started = True
            self.redraw_flag = True

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
        self.hide()
        glib.idle_add(self.app_destroy_handler)
    
    def track_area_expose_event_cb(self, widget, event):
        """Update desired portion of drawing area."""
        x , y, width, height = event.area
        widget.window.draw_drawable(widget.get_style().fg_gc[gtk.STATE_NORMAL],
                                    self.track_src, x, y, x, y, width, height)
        return False

    def map_area_expose_event_cb(self, widget, event):
        """Update desired portion of drawing area."""
        x , y, width, height = event.area
        widget.window.draw_drawable(widget.get_style().fg_gc[gtk.STATE_NORMAL],
                                    self.map_src, x, y, x, y, width, height)
        return False

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

    def checks(self, cr, x, y, sz):
        """Draw check at pos x,y."""
        cr.move_to(x-sz,y-sz)
        cr.set_source_rgb(0.0,0.0,0.0)
        cr.rectangle(x-sz,y-sz,sz,sz)
        cr.rectangle(x,y,sz,sz)
        cr.fill()
        cr.set_source_rgb(1.0,1.0,1.0)
        cr.rectangle(x,y-sz,sz,sz)
        cr.rectangle(x-sz,y,sz,sz)
        cr.fill()

    def climbs(self, cr, x, y, sz):
        """Draw KoM icon at pos x,y."""
        cr.set_source_rgb(0.0,0.4,0.0)
        cr.move_to(x-sz,y-sz)
        cr.line_to(x+sz,y-sz)
        cr.line_to(x,y+sz)
        cr.fill()

    def encirc(self, cr, x, y, char, sz):
        """Draw an enircled number atop a reference marker."""
        # crosshairs
        cz = 0.6*sz
        cr.save()
        cr.set_line_width(0.15*sz)
        cr.set_source_rgb(0.0,0.0,0.0)
        cr.move_to(x-cz, y-cz)
        cr.line_to(x+cz, y+cz)
        cr.move_to(x-cz, y+cz)
        cr.line_to(x+cz, y-cz)
        cr.stroke()

        # encirc
        cr.set_source_rgb(0.1,0.1,0.1)
        cr.move_to(x, y+2.4*sz)
        cr.arc(x, y+2.4*sz, 1.4*sz, 0, 6.3)
        cr.fill()
        # text
        cr.move_to(x-0.7*sz, y+1.7*sz)
        cr.scale(1.0,-1.0)
        cr.set_source_rgb(1.0,1.0,1.0)
        cr.set_font_size(2.0*sz)
        cr.show_text(char)
        cr.restore()

    def track_redraw(self):
        """Re-Draw lazy tracker."""
        if self.track_src is None or not self.showtrack:
            return
        cr = self.track_src.cairo_create()
        cr.identity_matrix()
        # bg filled
        cr.set_source_rgb(0.85,0.85,0.9)
        cr.paint()
        if self.profile is None:
             return
        width = self.track_winx
        height = self.track_winy
        # bounding box.
        xl = self.track_bbox[0][0]
        xh = self.track_bbox[1][0]
        xm = xh-xl
        yl = self.track_bbox[0][1]
        yh = self.track_bbox[1][1]
        ym = yh-yl
        sf = (width-16)/xm
        asf = (height-16)/ym
        asz = xm/60.0
        lsz = xm/150.0
        if abs(asf) < abs(sf):
            sf = asf		# use vert if too 'tall'
            asz = ym/60.0
            lsz = ym/150.0
        xof = 8+(width-16)*(0-xl)/(xm)
        yof = 8+(height-16)*yh/ym
        cr.translate(xof, yof)
        cr.scale(sf, -sf)
        cr.set_line_width(lsz)
        cr.set_source_rgb(0.05,0.05,0.95)
        fp = self.profile[0]
        lap = set() 
        cr.move_to(fp[3],fp[4])
        sp = True
        for np in self.profile[1:]:
            phash = (int(np[3]), int(np[4]))
            if phash not in lap:
                if sp:
                    cr.line_to(np[3],np[4])
                sp = True
                lap.add(phash)
            else:
                sp = False
                cr.move_to(np[3],np[4])
        cr.stroke()


        # finish line
        lp = self.profile[-1]
        self.checks(cr, lp[3], lp[4], asz)

        # kom
        lp = self.profile[27]
        #self.climbs(cr, lp[3], lp[4], asz)

        if self.lastmainpt is not None:
            self.encirc(cr, self.lastmainpt[0], self.lastmainpt[1], u'M', asz)
        if self.lastleadpt is not None:
            self.encirc(cr, self.lastleadpt[0], self.lastleadpt[1], u'L', asz)
        if self.lastchasept is not None:
            self.encirc(cr, self.lastchasept[0], self.lastchasept[1], u'C', asz)
        if self.lastsagpt is not None:
            self.encirc(cr, self.lastsagpt[0], self.lastsagpt[1], u'S', asz)

    def map_redraw(self):
        """Lazy full map redraw method."""
        if self.map_src is None:
            return
        cr = self.map_src.cairo_create()

        # if there are riders in the map, do a bunches map
        if len(self.riders) > 0:
            self.draw_ridermap(cr)
        else: # else draw the profile
            self.draw_profile(cr)

    def draw_profile(self, cr):
        width = self.map_winsz
        height = 80
        cr.identity_matrix()

        # bg filled
        cr.set_source_rgb(0.85,0.85,0.9)
        cr.paint()
        if self.profile is None:
             return

        dw = width - (2 * MAPHMARGIN)
        dh = height - (2 * MAPVMARGIN)

        cr.set_line_width(1.0)

        xmod = 1

        xof = 0
        cnt = 0
        while xof < dw and float(cnt) < self.dmax:
            lh = 4
            if cnt % 10000 == 0:
                lh = 12
                cr.set_source_rgb(0.05,0.05,0.05)
                cr.move_to(xof+MAPHMARGIN+1,
                           MAPVMARGIN+dh-lh-2)
                cr.show_text(unicode(cnt//1000)+'km')
            elif cnt % 5000 == 0:
                lh = 8
            cr.set_source_rgb(0.05,0.05,0.05)
            cr.move_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh)
            cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh)
            cr.stroke()
            if cnt % 10000 == 0:
                cr.set_source_rgb(0.96,0.96,0.96)
                cr.move_to(xof+MAPHMARGIN, MAPVMARGIN)
                cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh-2)
                cr.stroke()
            cnt += 5000
            xof =dw*(float(cnt)/self.dmax)

        cr.set_source_rgb(0.05,0.05,0.95)
        xo = MAPHMARGIN
        zr = self.zmax - self.zmin
        yo = dh*((self.profile[0][1]-self.zmin)/zr)
        cr.move_to(xo, MAPVMARGIN+dh-yo)
        for r in self.profile:
            xp = xo + dw*r[0]
            yz = dh*((r[1]-self.zmin)/zr)
            cr.line_to(xp,MAPVMARGIN+dh-yz)

        cr.stroke()
        if self.distance is not None:
           drem = 1000.0*self.distance
           xof = (self.dmax-drem)/self.dmax
           if xof < 1.0:
               xp = MAPHMARGIN + dw*xof
               cr.set_source_rgb(0.95,0.05,0.05)
               cr.move_to(xp,MAPVMARGIN)
               cr.line_to(xp,MAPVMARGIN+dh)
               cr.stroke()
        if self.distancemain is not None:
           drem = 1000.0*self.distancemain
           xof = (self.dmax-drem)/self.dmax
           if xof < 1.0:
               xp = MAPHMARGIN + dw*xof
               cr.set_source_rgb(0.05,0.95,0.05)
               cr.move_to(xp,MAPVMARGIN)
               cr.line_to(xp,MAPVMARGIN+dh)
               cr.stroke()

    def draw_ridermap(self, cr):
        width = self.map_winsz
        height = 80
        cr.identity_matrix()

        # bg filled
        cr.set_source_rgb(0.85,0.85,0.9)
        cr.paint()

        # scale: | . . . . i . . . . | . . . 
        cr.set_line_width(1.0)
        cr.set_font_size(15.0)
        xof = 0
        dw = width - (2 * MAPHMARGIN)
        dh = height - (2 * MAPVMARGIN)
        cnt = 0
        while xof < dw:
            lh = 4
            if cnt % 10 == 0:
                lh = 12
                cr.set_source_rgb(0.05,0.05,0.05)
                cr.move_to(xof+MAPHMARGIN+1,
                           MAPVMARGIN+dh-lh-2)
                cr.show_text(tod.tod(int(cnt)).rawtime(0))
            elif cnt % 5 == 0:
                lh = 8
            cr.set_source_rgb(0.05,0.05,0.05)
            cr.move_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh)
            cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh)
            cr.stroke()
            if cnt % 5 == 0:
                cr.set_source_rgb(0.96,0.96,0.96)
                cr.move_to(xof+MAPHMARGIN, MAPVMARGIN)
                cr.line_to(xof+MAPHMARGIN, MAPVMARGIN+dh-lh-2)
                cr.stroke()
            cnt += 1
            xof += self.timetick

        cr.set_line_width(2.0)
        inbox = False
        cnt = 0
        st=None
        x1=None
        x2=None
        for r in self.riders:
            if r[7] is not None:	# have a row
                if st is None:
                    st = r[7].truncate(0)	# save lap split
                if not inbox:
                    x1 = r[7].truncate(0)-st
                    inbox = True
                x2 = r[7]-st
            else:			# have a break
                if inbox:
                    self.do_bubble(cr, cnt, x1, x2)
                    cnt += 1
                inbox = False
        if inbox:
            self.do_bubble(cr, cnt, x1, x2)

    def track_area_configure_event_cb(self, widget, event):
        """Re-configure the drawing area and redraw the base image."""
        x, y, width, height = widget.get_allocation()
        self.track_winx = width
        self.track_winy = height
        self.track_src = gtk.gdk.Pixmap(widget.window, width, height)
        self.track_redraw()
        return True

    def map_area_configure_event_cb(self, widget, event):
        """Re-configure the drawing area and redraw the base image."""
        x, y, width, height = widget.get_allocation()
        self.map_winsz = width
        if width > self.map_w:
            nw = MAPWIDTH
            if width > MAPWIDTH:
                nw = width
            self.map_src = gtk.gdk.Pixmap(widget.window, nw, height)
            self.map_w = nw
            self.map_redraw()
        return True

    def clear(self):
        self.title_str = None
        self.laptype = None
        self.lapstart = None
        self.lapfin = None
        #self.lbl_header.set_text(self.motd)
        self.lbl_resultmsg.set_text(u'')
        self.lbl_resultmsg.hide()
        self.final = False
        #self.elap_lbl.set_text('')
        #self.gap_lbl.set_text('')
        self.riders.clear()
        self.redraw_flag = True
        
    def projectpos(self, bunch, msg):
        # receive a projected map position for main field
        sr = float(msg)
        if sr > 0 and sr < 1:
            #print(u'search for : ' + repr(sr))
            sp = self.profile[0]
            np = None
            for p in self.profile[1:]:
                if p[0] > sr:
                    np = p
                    break
                sp = p
            npt = posinterp(sp, np, sr)
            if bunch == u'main':
                self.lastmainpt = npt
            elif bunch == u'lead':
                self.lastleadpt = npt
            elif bunch == u'chase':
                self.lastchasept = npt
            elif bunch == u'sag':
                self.lastsagpt = npt
            self.redraw_flag = True

    def lambert_fix(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) > 7:
            lid = sr[0]		# lambert ident
            tof = sr[1]
            xof = sr[2]
            yof = sr[3]
            spd = sr[6]
            tim = sr[7]
            # compare tim to last fix time
            # if out of order, ignore
            # if new fix, save to lamb props and flag redraw

    def append_rider(self, msg):
        sr = msg.split(chr(unt4.US))
        if len(sr) == 5:
            rftime = tod.str2tod(sr[4])
            if rftime is not None:
                if len(self.riders) == 0:
                    # Case 1: Starting a new lap
                    if self.lapfin is not None:
                        self.cur_split = self.lapfin
                    else:
                        self.cur_split = rftime
                    if self.lapstart is not None:
                        self.cur_lap = (self.cur_split
                                        - self.lapstart).truncate(0)
                    elif self.elapstart:	# no lap start?
                        self.cur_lap = (self.cur_split
                                        - self.elapstart).truncate(0)
                    else:
                        self.cur_lap = self.cur_split.truncate(0)
                    if self.final:
                        # overwrite cur split for down times
                        self.cur_split = rftime	# check if down from here?
                        self.cur_lap = rftime
                    self.cur_bunchid = 0
                    self.cur_bunchcnt = 1
                    self.last_time = rftime
                    nr=[sr[0],sr[1],sr[2],sr[3],
                        self.cur_lap.rawtime(0),
                        self.cur_bunchcnt,
                        COLOURMAP[self.cur_bunchid][0],
                        rftime]
                elif rftime < self.last_time or rftime - self.last_time < tod.tod('5.0'):
                    # Case 2: Same bunch
                    self.last_time = rftime
                    self.cur_bunchcnt += 1
                    nr=[sr[0],sr[1],sr[2],sr[3],
                        '',
                        self.cur_bunchcnt,
                        COLOURMAP[self.cur_bunchid][0],
                        rftime]
                else:
                    # Case 3: New bunch
                    self.riders.append(['','','','','','','#fefefe',None])
                    self.cur_bunchid = (self.cur_bunchid + 1)%COLOURMAPLEN
                    self.cur_bunchcnt = 1
                    self.last_time = rftime
                    nr=[sr[0],sr[1],sr[2],sr[3],
                        '+' + (rftime - self.cur_split).rawtime(0),
                        self.cur_bunchcnt,
                        COLOURMAP[self.cur_bunchid][0],
                        rftime]
            else: 
                # Informative non-timeline record
                nr=[sr[0],sr[1],sr[2],sr[3],
                        '', '', '#fefefe',None]
                
            self.riders.append(nr)
            self.redraw_flag = True

    def msg_cb(self, m, nick, chan):
        """Handle message packet in main thread."""
        if self.remoteuser and self.remoteuser.lower() != nick.lower():
            #self.log.debug(u'Ignoring command from ' + repr(nick))
            return False
        if m.header == 'rider':
            self.append_rider(m.text)
        elif m.header == 'title':
            if m.text:
                self.title_str = m.text
            else:
                self.title_str = None
        elif m.header == 'finstr':
            if m.text:
                self.finstr = m.text
            else:
                self.finstr = None
        elif m.header == 'start':
            self.elapstart = tod.str2tod(m.text)
        elif m.header == 'finish':
            self.elapfin = tod.str2tod(m.text)
        elif m.header == 'lapstart':
            self.lapstart = tod.str2tod(m.text)
        elif m.header == 'lapfin':
            self.lapfin = tod.str2tod(m.text)
        elif m.header == 'resultmsg':
            self.lbl_resultmsg.set_text(m.text)
            if m.text:
                self.lbl_resultmsg.show()
            else:
                self.lbl_resultmsg.hide()
        elif m.header == u'laplbl':
            if m.text:
                self.laplbl = m.text
            else:
                self.laplbl = None      # force None for empty string
        elif m.header == u'laptype':
            if m.text:
                self.laptype = m.text
            else:
                self.laptype = None     # force None for empty string
        elif m.header == u'oncourse':
            self.lambert_fix(m.text)
        elif m.header == u'leadpos':
            self.projectpos(u'lead', m.text)
        elif m.header == u'mainpos':
            self.projectpos(u'main', m.text)
        elif m.header == u'chasepos':
            self.projectpos(u'chase', m.text)
        elif m.header == u'sagpos':
            self.projectpos(u'sag', m.text)
        elif m.header == u'distance':
            self.distance = None
            try:
                a = float(m.text)
                if a > 0.1:
                    self.distance = a
                    self.redraw_flag = True
            except:
                self.log.debug(u'Invalid lead distance: ' + repr(m.text))
        elif m.header == u'distancemain':
            self.distancemain = None
            try:
                a = float(m.text)
                if a > 0.1:
                    self.distancemain = a
                    self.redraw_flag = True	# might get two at a time
            except:
                self.log.debug(u'Invalid main distance: ' + repr(m.text))
        elif m.header == u'timerstat':
            self.timerstat = m.text
        elif m.header == u'bunches':
            self.final = True
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
                                    u'minelevrange':300.0,
                                    u'fullscreen':False,
                                    u'groupcol':True,
                                    u'catcol':True,
                                    u'bunchmap':True,
                                    u'showtrack':False,
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

        self.timetick = strops.confopt_posint(cr.get(u'road_announce',
                                              u'timetick'), TIMETICK)
        self.fontsize = strops.confopt_posint(cr.get(u'road_announce',
                                              u'fontsize'), FONTSIZE)
        maxlap = tod.str2tod(cr.get(u'road_announce',u'maxlaptime'))
        if maxlap is not None:
            self.maxlaptime = maxlap
        self.motd = cr.get(u'road_announce', u'motd')
        if strops.confopt_bool(cr.get(u'road_announce', u'fullscreen')):
            self.window.fullscreen()
        if strops.confopt_bool(cr.get(u'road_announce', u'search')):
            self.search_ent.show()
            self.search_ent.set_sensitive(True)
        self.showtrack = strops.confopt_bool(cr.get(u'road_announce', u'showtrack'))
        if self.showtrack:
            self.track_area.show()
        autoscroll = strops.confopt_bool(cr.get(u'road_announce',
                                                u'autoscroll'))
        if autoscroll != self.autoscroll:
            self.autoscroll = True
            self.scrollcnt = 0
            glib.timeout_add(SCROLLDELAY, self.doautoscroll)

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

        if not strops.confopt_bool(cr.get(u'road_announce', 'groupcol')):
            self.view.get_column(5).set_visible(False)
        if not strops.confopt_bool(cr.get(u'road_announce', 'catcol')):
            self.view.get_column(3).set_visible(False)
        if not strops.confopt_bool(cr.get(u'road_announce', 'bunchmap')):
            self.map_area.hide()

        fnszstr = str(self.fontsize)+'px'
        self.lbl_header.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.elap_lbl.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.gap_lbl.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.search_entry.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.lbl_resultmsg.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.view.modify_font(pango.FontDescription(FONTFACE + ' '+fnszstr))
        zrangemin = strops.confopt_float(cr.get(u'road_announce',
                                                u'minelevrange'),200.0)
        routefile = metarace.default_file(u'route.dat')
        self.profile = None
        self.zmin = None
        self.zmax = None
        self.dmax = None
        if os.path.exists(routefile):
            print(u'loading profile...')
            self.profile = []
            with open(routefile, 'rb') as f:
                dmax = 0.0
                xmin = -1.0
                xmax = 1.0
                ymin = -1.0
                ymax = 1.0
                zmin = 90000.0
                zmax = -100.0
                for l in f:
                    r = l.split()
                    t = strops.confopt_float(r[1])
                    x = strops.confopt_float(r[2])
                    y = strops.confopt_float(r[3])
                    z = strops.confopt_float(r[4])
                    d = strops.confopt_float(r[6])
                    if z < zmin:
                        zmin = z
                    if z > zmax:
                        zmax = z
                    if x < xmin:
                        xmin = x
                    if x > xmax:
                        xmax = x
                    if y < ymin:
                        ymin = y
                    if y > ymax:
                        ymax = y
                    if d > dmax:
                        dmax = d
                    self.profile.append([t,z,d,x,y]) 
                self.zmin = zmin
                self.zmax = zmax
                self.xmin = xmin
                self.xmax = xmax
                self.ymin = ymin
                self.ymax = ymax
                self.dmax = dmax
                erange = zmax - zmin	# profile min elevation range
                if erange < zrangemin:
                    self.zmin -= 0.5*(zrangemin - erange)
                    self.zmax = self.zmin + zrangemin
                #print(repr([zmin,zmax,dmax]))
                routemeta = metarace.default_file(u'route.json')
                rjdat = jsonconfig.config()
                if os.path.exists(routemeta):
                    with open(routemeta, 'rb') as g:
                        rjdat.read(g)
                    bbmin = rjdat.get(u'route', u'mapmin')
                    bbmax = rjdat.get(u'route', u'mapmax')
                    self.track_bbox = [[bbmin[u'x'],bbmin[u'y']],
                                       [bbmax[u'x'],bbmax[u'y']]]

    def doautoscroll(self):
        """Automatically handle smooth scroll in text view after delay."""
        if self.autoscroll and self.running:
            vmax = self.vscroll.get_upper()
            vpage = self.vscroll.get_page_size()
            vpos = self.vscroll.get_value()
            #print(' _ '.join(map(str, [vmax, vpage, vpos])))
            if vpage+vpos+1 > vmax:
                # cancel running scroll?
                if self.scrollcnt +  int(vpage) >  int(vmax) + SCROLLTHRESH +SCROLLTHRESH:
                    self.scrollcnt = 0
                    self.vscroll.set_value(0.0)
                else:
                    self.scrollcnt += 2
            else:
                if self.scrollcnt > 0:
                    # scrolling is required....
                    if self.scrollcnt > SCROLLTHRESH:
                        self.vscroll.set_value(vpos+2.0)
                    self.scrollcnt += 2
                else:
                    # does scrolling need to start?
                    if vpage+vpos+1 < vmax:
                        self.scrollcnt = 1
            return True
        else:
            return False

    def timeout(self):
        """Update status."""
        # 1: Terminate?
        if not self.running:
            return False
        # 2: Process?
        try:
            ntime = tod.tod(u'now')
            ntod = ntime.truncate(0)
            if ntime >= self.nc.truncate(1):
                self.tod = ntod
                self.process_timeout()
                # and advance one second
                self.nc += tod.ONE
            else:
                self.log.debug(u'Timeout called early: ' + ntime.rawtime())
                # no need to advance, desired timeout not yet reached
        except Exception as e:
            self.log.error(u'Timeout: ' + unicode(e))

        # 3: Re-Schedule
        tt = tod.tod(u'now')+tod.tod(u'0.01')
        while self.nc < tt:     # ensure interval is positive
            if tod.MAX - tt < tod.ONE:
                self.log.debug(u'Midnight rollover.')
                break
            self.log.debug(u'May have missed an interval, catching up.')
            self.nc += tod.ONE  # 0.01 allows for processing delay
        ival = int(1000.0 * float((self.nc - tod.tod(u'now')).timeval))
        glib.timeout_add(ival, self.timeout)

        # 4: Return False
        return False    # must return False

    def process_timeout(self):
        """Perform required timeout activities."""
        estr = u''
        dstr = u''
        if self.elapstart is not None:
            if self.elapfin is not None:
                # Race over - show elap and down if poss
                estr = (self.elapfin - self.elapstart).rawtime(0)
                if self.timerstat != u'finished':
                    if self.tod > self.elapfin:
                        dstr = u'+' + (self.tod - self.elapfin).rawtime(0)
            else:
                # race in progress, show run time and distance or lap
                elap = self.tod - (self.elapstart+tod.tod('0.2'))
                if elap > tod.MAXELAP:
                    elap = tod.ZERO
                estr = elap.rawtime(0)
                if self.distance is not None and self.distance > 0.5:
                    dstr = u'~{0:1.1f}km'.format(self.distance)
                if self.lapfin is not None:
                    # lap down time overwrites dist, but only if valid
                    laptm = self.tod - self.lapfin
                    if laptm < self.maxlaptime:
                        dstr = u'+' + laptm.rawtime(0)
        self.elap_lbl.set_text(estr)
        self.gap_lbl.set_text(dstr)

        #if self.timerstat == 'running' and self.laplbl:
            #tmsg = u''
            #if self.finstr:
                #tmsg = self.finstr + u' '
            ## add lap and sprint info
            #tmsg += self.laplbl
            #if self.laptype:
                #tmsg += u' - ' + self.laptype
            #self.lbl_header.set_text(tmsg)
        #else:
        if True:	# always show the title_str, it now has better info
            if self.title_str:
                self.lbl_header.set_text(self.title_str)

        if self.redraw_flag:
            self.redraw_flag = False
            self.map_redraw()		# update src map
            self.track_redraw()		# update src map
            self.map_area.queue_draw()	# queue copy to screen
            self.track_area.queue_draw()	# queue copy to screen

        # check connection status
        if not self.io.connected():
            self.failcount += 1
            if self.failcount > self.failthresh:
                self.io.set_portstr(force=True)
                self.failcount = 0
            self.log.debug(u'Telegraph connection failed, count = '
                           + repr(self.failcount))
        else:
            self.failcount = 0            

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
        self.dosave = False
        self.redraw_flag = False

        self.io = telegraph.telegraph()
        self.io.set_pub_cb(self.msg_cb)
        #self.io.debug = True	# uncomment to log all IRC activity
        self.started = False
        self.running = True
        self.failcount = 0
        self.failthresh = 45	# connect timeout ~45sec

        self.autoscroll = False
        self.scrollcnt = 0

        self.timetick = TIMETICK
        self.fontsize = FONTSIZE
        fnszstr = str(self.fontsize)+'px'

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'rr_announce.ui'))
        self.window = b.get_object('window')

        self.lbl_header = b.get_object('lbl_header')
        self.lbl_header.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.lbl_header.set_text('metarace road announce ' + metarace.VERSION)
        self.lbl_resultmsg = b.get_object('lbl_resultmsg')
        self.lbl_resultmsg.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        self.lbl_resultmsg.set_text(u'')
        self.search_ent =  b.get_object('search_entry')
        self.elap_lbl = b.get_object('elap_lbl')
        self.elap_lbl.set_text('-h--:--')
        self.elap_lbl.modify_font(pango.FontDescription(FONTFACE + ' '+fnszstr))
        self.gap_lbl = b.get_object('gap_lbl')
        self.gap_lbl.set_text('~jjj.lkm')
        self.gap_lbl.modify_font(pango.FontDescription(FONTFACE + ' '+fnszstr))
        self.map_winsz = 0
        self.map_xoft = 0
        self.map_w = 0
        self.map_area = b.get_object('map_area')
        self.map_src = None
        self.map_area.set_size_request(-1, 80)
        self.map_area.show()

        self.track_winx = 0
        self.track_winy = 0
        self.track_xoft = 0
        self.track_w = 0
        self.track_area = b.get_object('track_area')
        self.track_src = None
        self.track_area.set_size_request(300, -1)
        self.showtrack = False
        ## defer to loadconfig?
        self.track_area.hide()

        # lap & bunch status values
        self.cur_lap = tod.tod(0)
        self.cur_split = tod.tod(0)
        self.cur_bunchid = 0
        self.cur_bunchcnt = 0

        self.profile = None
        self.zmin = None
        self.zmax = None
        self.xmin = None
        self.xmax = None
        self.ymin = None
        self.ymax = None
        self.dmax = None

        self.riders = gtk.ListStore(gobject.TYPE_STRING,  # rank
                                    gobject.TYPE_STRING,  # no.
                                    gobject.TYPE_STRING,  # namestr
                                    gobject.TYPE_STRING,  # cat/com
                                    gobject.TYPE_STRING,  # timestr
                                    gobject.TYPE_STRING,  # bunchcnt
                                    gobject.TYPE_STRING,  # colour
                                    gobject.TYPE_PYOBJECT) # rftod

        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(False)
        t.set_rules_hint(True)
        t.set_headers_visible(False)
        self.search_entry = b.get_object('search_entry')
        self.search_entry.modify_font(pango.FontDescription(fnszstr))
        t.set_search_entry(b.get_object('search_entry'))
        t.set_search_column(1)
        t.modify_font(pango.FontDescription(FONTFACE + ' ' + fnszstr))
        uiutil.mkviewcoltxt(t, 'Rank', 0,width=45)
        uiutil.mkviewcoltxt(t, 'No.', 1,calign=1.0,width=45)
        uiutil.mkviewcoltxt(t, 'Rider', 2,expand=True,fixed=True)
        #uiutil.mkviewcoltxt(t, 'Cat', 3,calign=0.0)
        uiutil.mkviewcoltxt(t, 'Time', 4,calign=1.0,width=80)
        uiutil.mkviewcoltxt(t, 'Bunch', 5,width=50,bgcol=6,calign=0.5)
        t.show()
        ts = b.get_object('text_scroll')
        ts.add(t)
        t.grab_focus()
        self.vscroll = ts.get_vadjustment()
        b.connect_signals(self)

        # animation variables
        self.title_str = None
        self.final = False
        self.elapstart = None
        self.elapfin = None
        self.finstr = None	# used with lapstr
        self.timerstat = u'running'     # default assume run state
        self.distance = None
        self.distancemain = None
        self.laplbl = None
        self.laptype = None
        self.lapstart = None
        self.lapfin = None
        self.lastmainpt = None
        self.lastleadpt = None
        self.lastchasept = None
        self.lastsagpt = None

        # start timer
        self.tod = tod.tod(u'now').truncate(0)
        self.nc = self.tod + tod.tod(u'1.22') # set interval a little off mark
        self.maxlaptime = tod.tod('2:00') # default maximum lap time
        self.log.debug(u'Starting clock intervals at: ' + self.nc.rawtime(3))
        glib.timeout_add(2000, self.timeout)


def main():
    """Run the announce application."""
    configpath = None

    # expand configpath on cmd line to realpath _before_ doing chdir
    if len(sys.argv) > 2:
        print(u'usage: road_announce [configdir]\n')
        sys.exit(1)
    elif len(sys.argv) == 2:
        rdir = sys.argv[1]
        if not os.path.isdir(rdir):
            rdir = os.path.dirname(rdir)
        configpath = os.path.realpath(rdir)

    metarace.init()
    if configpath is not None:
        os.chdir(configpath)
    app = rr_announce(configpath)
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

