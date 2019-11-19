
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

"""Individual track time trial module.

This module provides a class 'ittt' which implements the 'race'
interface and manages data, timing and scoreboard for generic
individual track time trial like events:

 - TT
 - Pursuit
 - Team Sprint

"""

import gtk
import glib
import gobject
import pango
import os
import logging
import csv
import math
import ConfigParser

import metarace
from metarace import timy
from metarace import scbwin
from metarace import tod
from metarace import uiutil
from metarace import loghandler
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import timerpane
from metarace import printing

# config version string
EVENT_ID = 'tracktimetrial-1.4'

# startlist model columns
COL_BIB = 0
COL_FIRSTNAME = 1
COL_LASTNAME = 2
COL_CLUB = 3
COL_COMMENT = 4		# 'Catch, Abort, etc'
COL_SEED = 5		# Change to 'Heat' for future versions
COL_PLACE = 6
COL_START = 7
COL_FINISH = 8
COL_SPLITS = 9

# scb function key mappings
key_reannounce = 'F4'		     # (+CTRL) calls into delayed announce
key_startlist = 'F6'                 # re-display running time (startlist)
key_results = 'F4'                   # recalc/show result window

# timing function key mappings
key_armstart = 'F5'                  # arm for start impulse
key_armlap_A = 'F7'		     # arm for lap 'Front'
key_armlap_B = 'F8'		     # arm for lap 'Back'
key_armfinish_A = 'F9'               # arm for finish impulse 'Front'
key_armfinish_B = 'F10'              # arm for finish impulse 'Back'
key_catch_A = 'F11'		     # TODO: rider caught in race mode
key_catch_B = 'F12'

# extended function key mappings
key_abort = 'F5'                     # + ctrl for clear/abort
key_falsestart = 'F6'		     # + ctrl for false start
key_abort_A = 'F7'		     # + ctrl abort A
key_abort_B = 'F8'		     # + ctrl abort B

class ittt(object):
    """Data handling for tt, pursuit and flying 200."""

    def key_event(self, widget, event):
        """Race window key press handler."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_abort:    # override ctrl+f5
                    self.toidle()
                    return True
                elif key == key_reannounce:	# run delayed announce
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_falsestart:	# false start both lanes
                    self.falsestart()
                    return True
                elif key == key_abort_A:	# abort front straight rider
                    self.abortrider(self.fs)
                    return True
                elif key == key_abort_B:
                    self.abortrider(self.bs)
                    return True
            elif key[0] == 'F':
                if key == key_armstart:
                    self.armstart()
                    return True
                elif key == key_armlap_A:
                    if self.teampursuit:	# HACK
                        self.chan_A = timy.CHAN_PA
                    self.armlap(self.fs, self.chan_A)
                    if self.timetype == u'single':
                        self.meet.timer.arm(self.chan_B)
                    return True
                elif key == key_armlap_B:
                    if self.teampursuit:	# HACK
                        self.chan_B = timy.CHAN_PB
                    self.armlap(self.bs, self.chan_B)
                    return True
                elif key == key_armfinish_A:
                    if self.teampursuit:	# HACK
                        self.chan_A = timy.CHAN_AUX
                    self.armfinish(self.fs, self.chan_A)
                    return True
                elif key == key_armfinish_B:
                    if self.teampursuit:	# HACK
                        self.chan_B = timy.CHAN_100
                    self.armfinish(self.bs, self.chan_B)
                    return True
                elif key == key_catch_A:
                    self.catchrider(self.fs)
                    return True
                elif key == key_catch_B:
                    self.catchrider(self.bs)
                    return True
                elif key == key_startlist:
                    self.showtimerwin()
                    return True
                elif key == key_results:
                    self.do_places()
                    return True
        return False

    def do_places(self):
        """Show race result on scoreboard."""
        self.meet.scbwin = None
        self.timerwin = False     # TODO: bib width enhancement
        fmtplaces = []
        name_w = self.meet.scb.linelen - 12
        rcount = 0
        pcount = 0
        for r in self.riders:
            rcount += 1
            if r[COL_PLACE] is not None and r[COL_PLACE] != '':
                pcount += 1	#"placed"
                plstr = r[COL_PLACE].decode('utf-8')
                if plstr.isdigit():
                    #if int(plstr) > 20:
                        #break
                    plstr = plstr + u'.'
                if not self.teamnames:
                    name_w = self.meet.scb.linelen - 12
                    name = strops.fitname(r[COL_FIRSTNAME].decode('utf-8'),
                                          r[COL_LASTNAME].decode('utf-8'),
                                          name_w, trunc=True)
                    club = r[COL_CLUB].decode('utf-8')
                    fmtplaces.append([plstr,
                         r[COL_BIB].decode('utf-8'),
                         name,
                         strops.truncpad(club, 4,'r')])
                else:
                    name = r[COL_FIRSTNAME].decode('utf-8')
                    name_w = self.meet.scb.linelen - 8
                    club = r[COL_CLUB].decode('utf-8')
                    fmtplaces.append([plstr, name, club])
        evtstatus = u'Standings'
        if rcount > 0 and pcount == rcount:
            evtstatus = u'Result'

        fmt = [(3,u'l'), (4,u'r'), u' ',(name_w, u'l'), (4,u'r')]
        if self.teamnames:
            fmt = [(3,u'l'), u' ',(name_w, u'l'), (4,u'r')]
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                              head=self.meet.racenamecat(self.event),
                              subhead=evtstatus.upper(),
                              coldesc=fmt, rows=fmtplaces)
        self.meet.scbwin.reset()

    def todstr(self, col, cr, model, iter, data=None):
        """Format tod into text for listview."""
        ft = model.get_value(iter, COL_FINISH)
        if ft is not None:
            st = model.get_value(iter, COL_START)
            if st is None:
                st = tod.tod(0)
            if st == tod.tod(0):
                cr.set_property('style', pango.STYLE_OBLIQUE)
            else:
                cr.set_property('style', pango.STYLE_NORMAL)
            cr.set_property('text', (ft - st).rawtime(3))
        else:
            cr.set_property('text', u'')

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        self.results.clear()
        self.splits = []

        # failsafe defaults -> dual timer, C0 start, PA/PB
        deftimetype = 'dual'
        defdistance = ''
        defdistunits = 'metres'
        defchans = str(timy.CHAN_START)
        defchana = str(timy.CHAN_PA)
        defchanb = str(timy.CHAN_PB)
        defautoarm = 'No'
        self.seedsrc = 1 # for autospec loads, fetch seed from the rank col

        # type specific overrides
        if self.evtype == 'flying 200':
            deftimetype = 'single'
            defdistance = '200'
            defchana = str(timy.CHAN_FINISH)
            defchanb = str(timy.CHAN_100)
            defautoarm = 'Yes'
        elif self.evtype == 'flying lap':
            deftimetype = 'single'
            defdistance = '1'
            defdistunits = 'laps'
            defchans = str(timy.CHAN_FINISH)
            defchana = str(timy.CHAN_FINISH)
            defchanb = str(timy.CHAN_100)
        elif self.evtype in ['pursuit race', 'team pursuit race']:
            self.difftime = True	# NOT CONFIGURABLE

        if self.evtype in ['team pursuit', 'team pursuit race']:
            self.teampursuit = True
        else:
            self.teampursuit = False

        cr = ConfigParser.ConfigParser({'startlist':'',
                                        'id':EVENT_ID,
					'start':'',
                                        'lstart':'',
                                        'fsbib':'',
                                        'fsstat':'idle',
                                        'bsbib':'',
                                        'bsstat':'idle',
                                        'showinfo':'No',
                                        'showcats':'No',
                                        'comment':'',
                                        'distance':defdistance,
					'distunits':defdistunits,
                                        'chan_S':defchans,
                                        'chan_A':defchana,
                                        'chan_B':defchanb,
                                        'autoarm':defautoarm,
                                        'autospec':'',
					'inomnium':'No',
                                        'timetype':deftimetype})
        cr.add_section('event')
        cr.add_section('riders')
        cr.add_section('traces')
        if os.path.isfile(self.configpath):
            self.log.debug('Attempting to read config from '
                               + repr(self.configpath))
            cr.read(self.configpath)

        #self.autospec = cr.get('event', 'autospec')
        self.set_timetype(cr.get('event', 'timetype'))
        self.distance = strops.confopt_dist(cr.get('event', 'distance'))
        self.comment = cr.get('event', 'comment')
        if not self.comment or self.comment == u'None':
            self.comment = None
        self.units = strops.confopt_distunits(cr.get('event', 'distunits'))
        if self.event[u'laps']:
            self.units = 'laps'
            self.distance = strops.confopt_posint(self.event[u'laps'],
                                                  self.distance)
        self.chan_S = strops.confopt_chan(cr.get('event', 'chan_S'), defchans)
        self.chan_A = strops.confopt_chan(cr.get('event', 'chan_A'), defchana)
        self.chan_B = strops.confopt_chan(cr.get('event', 'chan_B'), defchanb)
        self.autoarm = strops.confopt_bool(cr.get('event', 'autoarm'))
        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))
        self.showcats = strops.confopt_bool(cr.get('event', 'showcats'))
        self.inomnium = strops.confopt_bool(cr.get('event', 'inomnium'))
        if self.inomnium:
            # SWITCH: Event is part of an omnium, make any req'd overrides
            self.seedsrc = 1	# read seeding from omnium points standinds

        # re-load starters/results
        self.onestart = False
        for r in cr.get('event', 'startlist').split():
            nr=[r, '', '', '', '', '', '', None, None, None]
            co = ''
            st = None
            ft = None
            sp = []
            if cr.has_option('riders', r):
                ril = csv.reader([cr.get('riders', r)]).next()
                if len(ril) >= 1:	# save comment for stimes
                    co = ril[0]
                if len(ril) >= 2:	# write heat into rec
                    nr[COL_SEED] = ril[1]
                if len(ril) >= 4:	# Start ToD and others
                    st = tod.str2tod(ril[3])
                    if st is not None:		# assigned in settimes
                        self.onestart = True
                if len(ril) >= 5:	# Finish ToD
                    ft = tod.str2tod(ril[4])
                j = 5
                while j < len(ril):	# Split ToDs
                    spt = tod.str2tod(ril[j])
                    print spt
                    sp.append(spt)
                    j += 1
            dbr = self.meet.rdb.getrider(r, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            nri = self.riders.append(nr)
            self.settimes(nri, st, ft, sp, doplaces=False, comment=co)
        if self.series and u't' in self.series:
            self.teamnames = True
        self.placexfer()

        # re-load any traces
        for r in cr.get('event', 'startlist').split():
            if cr.has_option('traces',r):
                self.traces[r] = cr.get('traces', r).split('\n')

        # re-join any existing timer state
        curstart = tod.str2tod(cr.get('event', 'start'))
        lstart = tod.str2tod(cr.get('event', 'lstart'))
        if lstart is None:
            lstart = curstart	# can still be None if start not set
        dorejoin = False
        # Front straight
        fsstat = cr.get('event', 'fsstat')
        if fsstat in ['running', 'load']: # running with no start gets load
            self.fs.setrider(cr.get('event', 'fsbib')) # will set 'load'
            if fsstat == 'running' and curstart is not None:     
                self.fs.start(curstart)  # overrides to 'running'
                dorejoin = True
        # Back straight
        bsstat = cr.get('event', 'bsstat')
        if bsstat in ['running', 'load']: # running with no start gets load
            self.bs.setrider(cr.get('event', 'bsbib')) # will set 'load'
            if bsstat == 'running' and curstart is not None:     
                self.bs.start(curstart)  # overrides to 'running'
                dorejoin = True

        if not self.onestart and self.autospec:
            self.meet.autostart_riders(self, self.autospec,
                                             infocol=self.seedsrc)
        if dorejoin:
            self.torunning(curstart, lstart)
        elif self.timerstat == 'idle':
            glib.idle_add(self.fs.grab_focus)

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get('event', 'id')
        if eid and eid != EVENT_ID:
            self.log.error(u'Event configuration mismatch: '
                           + repr(eid) + u' != ' + repr(EVENT_ID))
            #self.readonly = True

    def saveconfig(self):
        """Save race to disk."""
        if self.readonly:
            self.log.error('Attempt to save readonly ob.')
            return
        cw = ConfigParser.ConfigParser()
        cw.add_section('event')

        # save basic race properties
        cw.set('event', 'autospec', self.autospec)
        cw.set('event', 'timetype', self.timetype)
        cw.set('event', 'distance', self.distance)
        cw.set('event', 'distunits', self.units)
        cw.set('event', 'comment', self.comment)
        cw.set('event', 'chan_S', self.chan_S)
        cw.set('event', 'chan_A', self.chan_A)
        cw.set('event', 'chan_B', self.chan_B)
        cw.set('event', 'autoarm', self.autoarm)
        cw.set('event', 'showcats', self.showcats)
        cw.set('event', 'startlist', self.get_startlist())
        cw.set('event', 'inomnium', self.inomnium)
        cw.set('event', 'showinfo', self.info_expand.get_expanded())

        # extract and save timerpane config for interrupted run
        if self.curstart is not None:
            cw.set('event', 'start', self.curstart.rawtime())
        if self.lstart is not None:
            cw.set('event', 'lstart', self.lstart.rawtime())
        cw.set('event', 'fsstat', self.fs.getstatus())
        cw.set('event', 'fsbib', self.fs.getrider())
        cw.set('event', 'bsstat', self.bs.getstatus())
        cw.set('event', 'bsbib', self.bs.getrider())

        cw.add_section('traces')
        for rider in self.traces:
            cw.set('traces', rider, u'\n'.join(self.traces[rider]))

        cw.add_section('riders')

        # save out all starters
        for r in self.riders:
            # place is saved for info only
            slice = [r[COL_COMMENT], r[COL_SEED], r[COL_PLACE]]
            tl = [r[COL_START], r[COL_FINISH]]
            if r[COL_SPLITS] is not None:
                tl.extend(r[COL_SPLITS])
            for t in tl:
                if t is not None:
                    slice.append(t.rawtime())
                else:
                    slice.append('')
            cw.set('riders', r[COL_BIB],
                ','.join(map(lambda i: str(i).replace(',', '\\,'), slice)))
        cw.set('event', 'id', EVENT_ID)
        self.log.debug('Saving race config to: ' + self.configpath)
        with open(self.configpath, 'wb') as f:
            cw.write(f)

    def startlist_report(self, program=False):
        """Return a startlist report."""
        ret = []
        cnt = 0
        sec = printing.dual_ittt_startlist()
        sec.showheats = True
        if self.timetype == 'single':
            sec.set_single()
            if u't' in self.series and self.series != u'tmsl':
                sec.showheats = True

        headvec = [u'Event', self.evno, u':', self.event[u'pref'],
                         self.event[u'info']]
        if not program:
            headvec.append(u'- Start List')
        sec.heading = u' '.join(headvec)

        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            sec.subheading = substr
        if self.event[u'reco']:
            sec.footer = self.event[u'reco']
        if self.event[u'plac']: 
            sec.lines = self.get_heats(placeholders = self.event[u'plac'])
        else:
            sec.lines = self.get_heats()
        ret.append(sec)
        return ret

    def sort_startlist(self, x, y):
        """Comparison function for ttt seeding."""
        if x[1] == y[1]:	# same seed? revert to bib ascending
            return cmp(x[2], y[2])
        else:
            return cmp(x[1], y[1])

    def sort_heats(self, x, y):
        """Comparison function for ttt heats."""
        (xh, xl) = strops.heatsplit(x[0])
        (yh, yl) = strops.heatsplit(y[0])
        if xh == yh:
            return cmp(xl, yl)
        else:
            return cmp(xh, yh)

    def reorder_startlist(self):
        """Reorder model according to the seeding field."""
        if len(self.riders) > 1:
            auxmap = []
            cnt = 0
            for r in self.riders:
                auxmap.append([cnt, strops.riderno_key(r[COL_SEED]),
                                    strops.riderno_key(r[COL_BIB])])
                cnt += 1
            auxmap.sort(self.sort_startlist)
            self.riders.reorder([a[0] for a in auxmap]) 

    def get_heats(self, placeholders=0, cats=None):
        """Return a list of heats in the event."""
        ret = []

        # arrange riders by seeding
        self.reorder_startlist()

        # then build aux map of heats
        hlist = []
        emptyrows = False
        count = len(self.riders)
        if count < placeholders:
            count = placeholders
            miss = 2000
            while len(self.riders) < count:
                self.addrider(unicode(miss))	# WARNING!
                miss += 1
        blanknames = False	# HACK!!
        teams = False
        if u't' in self.series:	# Team no hack
            teams = True
        if placeholders > 0:
            blanknames = True
        if self.timetype == 'single':
            for r in self.riders:
                rno = r[COL_BIB].decode('utf-8')
                #if teams:	# Team no hack
                    #rno = u' '	# force name
                info = None
                if cats and rno in cats:
                    info = cats[rno]
                rh = self.meet.newgetrider(rno, self.series)
                rname = u''
                heat = unicode(count) + u'.1'
                if rh is not None:
                     if not teams:
                         rname = rh[u'namestr']
                     else:
                         rname = rh[u'first']
                         if teams:
                           info = []
                           col = u'black'
                           for trno in strops.reformat_riderlist(rh[u'note']).split():
                             trh = self.meet.newgetrider(trno) #!! SERIES?
                             if trh is not None:
                                 if self.series == u'tmsl':
                                     trno = col
                                     col = u'red'
                                 info.append([trno, trh[u'namestr'],None])
                     #if rh[u'ucicode']: info = rh[u'ucicode']
                     # consider partners here
                     if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                        ph = self.meet.newgetrider(rh[u'note'], self.series)
                        if ph is not None:
                            info = [[u' ',ph[u'namestr'] + u' - Pilot', ph[u'ucicode']]]
                hlist.append([heat, rno, rname, info])
                                 # all heats are one up
                count -= 1
        else:
            hno = int(math.ceil(0.5*count))
            lane = 1
            for r in self.riders:
                rno = r[COL_BIB].decode('utf-8')
                rh = self.meet.newgetrider(rno, self.series)
                rname = u''
                if u't' in self.series:	# Team no hack
                    rno = u' '	# force name
                heat = unicode(hno) + u'.' + unicode(lane)
                info = None
                if cats and rno in cats:
                    info = cats[rno]
                if rh is not None:
                    if not teams:
                        rname = rh[u'namestr']
                    else:
                        rname = rh[u'first']
                        if teams:
                           info = []
                           for trno in strops.reformat_riderlist(rh[u'note']).split():
                             trh = self.meet.newgetrider(trno) #!! SERIES?
                             if trh is not None:
                                 info.append([trno, trh[u'namestr'],None])
                    #if rh[u'ucicode']: info = rh[u'ucicode']
                    # consider partners here
                    if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                        ph = self.meet.newgetrider(rh[u'note'], self.series)
                        if ph is not None:
                            info = [[u' ',ph[u'namestr'] + u' - Pilot', ph[u'ucicode']]]
                hlist.append([heat, rno, rname, info])
                lane += 1
                if lane > 2:
                    hno -= 1
                    lane = 1

        # sort the heatlist into front/back heat 1, 2, 3 etc
        hlist.sort(self.sort_heats)

        lh = None
        lcnt = 0
        rec = []
        for r in hlist:
            (h,l) = strops.heatsplit(r[0])
            if lh is not None and (h != lh or lcnt > 1):
                lcnt = 0
                ret.append(rec)
                rec = []
            heat = str(h)
            if self.difftime:	# override heat if 'final'
                heat = u'-'
            if blanknames and len(r[1]) > 3:	# HACK for miss
                r[1] = u''
                r[2] = u''
                r[3] = None
            rec.extend([heat, r[1], r[2], r[3]])
            lcnt += 1
            lh = h
        if len(rec) > 0:
            ret.append(rec)
        return ret

    def get_startlist(self):
        """Return a list of bibs in the rider model."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return u' '.join(ret)

    def delayed_announce(self):
        """Initialise the announcer's screen after a delay."""
        if self.winopen:
            self.meet.announce.clrall()
            self.meet.ann_title(u' '.join([
                  u'Event', self.evno, u':',
                  self.event[u'pref'], self.event[u'info']]))

            self.meet.announce.linefill(1, u'_')
            self.meet.announce.linefill(7, u'_')

            # fill in front straight
            fbib = self.fs.getrider()
            if fbib is not None and fbib != u'':
                r = self.getrider(fbib)
                if r is not None:
                    clubstr = u''
                    if r[COL_CLUB] != u'' and len(r[COL_CLUB]) <= 3:
                        clubstr = u'(' + r[COL_CLUB] + u')'
                    namestr = strops.fitname(r[COL_FIRSTNAME],
                                             r[COL_LASTNAME], 24, trunc=True)
                    placestr = u'   ' # 3 ch
                    if r[COL_PLACE] != u'':
                        placestr = strops.truncpad(r[COL_PLACE] + u'.', 3)
                    bibstr = strops.truncpad(r[COL_BIB], 3, u'r')
                    tmstr = u''
                    if r[COL_START] is not None and r[COL_FINISH] is not None:
                        tmstr = (r[COL_FINISH] - r[COL_START]).rawtime(3)
                    cmtstr = u''
                    if r[COL_COMMENT] is not None and r[COL_COMMENT] != u'':
                        cmtstr = strops.truncpad(
                              u'[' + r[COL_COMMENT].strip() + u']', 38, u'r')
                    self.meet.announce.postxt(3,0,u'        Front Straight')
                    self.meet.announce.postxt(4,0,u' '.join([placestr, bibstr,
                                                         namestr, clubstr]))
                    self.meet.announce.postxt(5,26,strops.truncpad(tmstr,
                                                                  12, u'r'))
                    self.meet.announce.postxt(6,0,cmtstr)

            # fill in back straight
            bbib = self.bs.getrider()
            if bbib is not None and bbib != u'':
                r = self.getrider(bbib)
                if r is not None:
                    clubstr = u''
                    if r[COL_CLUB] != u'' and len(r[COL_CLUB]) <= 3:
                        clubstr = u'(' + r[COL_CLUB] + u')'
                    namestr = strops.fitname(r[COL_FIRSTNAME],
                                             r[COL_LASTNAME], 24, trunc=True)
                    placestr = u'   ' # 3 ch
                    if r[COL_PLACE] != u'':
                        placestr = strops.truncpad(r[COL_PLACE] + u'.', 3)
                    bibstr = strops.truncpad(r[COL_BIB], 3, u'r')
                    tmstr = u''
                    if r[COL_START] is not None and r[COL_FINISH] is not None:
                        tmstr = (r[COL_FINISH] - r[COL_START]).rawtime(3)
                    cmtstr = u''
                    if r[COL_COMMENT] is not None and r[COL_COMMENT] != u'':
                        cmtstr = strops.truncpad(
                             u'[' + r[COL_COMMENT].strip() + u']', 38, u'r')
                    self.meet.announce.postxt(3,42,u'        Back Straight')
                    self.meet.announce.postxt(4,42,u' '.join([placestr, bibstr,
                                                         namestr, clubstr]))
                    self.meet.announce.postxt(5,68,strops.truncpad(tmstr,
                                                12, u'r'))
                    self.meet.announce.postxt(6,42,cmtstr)

            # fill in leaderboard/startlist
            count = 0
            curline = 9
            posoft = 0
            for r in self.riders:
                count += 1
                if count == 19:
                    curline = 9
                    posoft = 42

                clubstr = u''
                if r[COL_CLUB] != u'' and len(r[COL_CLUB]) <= 3:
                    clubstr = u' (' + r[COL_CLUB] + u')'
 
                namestr = u''
                if not self.teamnames:
                    namestr = strops.truncpad(strops.fitname(r[COL_FIRSTNAME],
                              r[COL_LASTNAME], 20-len(clubstr))+clubstr, 20)
                else:
                    namestr = strops.truncpad(r[COL_FIRSTNAME].decode('utf-8'),
                                              20)
                placestr = u'   ' # 3 ch
                if r[COL_PLACE] != u'':
                    placestr = strops.truncpad(r[COL_PLACE] + u'.', 3)
                bibstr = strops.truncpad(r[COL_BIB], 3, u'r')
                tmstr = u'         ' # 9 ch
                if r[COL_START] is not None and r[COL_FINISH] is not None:
                    tmstr = strops.truncpad(
                           (r[COL_FINISH] - r[COL_START]).rawtime(3), 9, u'r')
                self.meet.announce.postxt(curline, posoft, u' '.join([
                      placestr, bibstr, namestr, tmstr]))
                curline += 1

    def shutdown(self, win=None, msg='Exiting'):
        """Terminate race object."""
        self.log.debug('Race Shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def do_properties(self):
        """Run race properties dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'ittt_properties.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.meet.window)
        tt = b.get_object('race_score_type')
        if self.timetype == 'dual':
            tt.set_active(0)
        else:
            tt.set_active(1)
        di = b.get_object('race_dist_entry')
        if self.distance is not None:
            di.set_text(str(self.distance))
        else:
            di.set_text('')
        du = b.get_object('race_dist_type')
        if self.units == 'laps':
            du.set_active(1)
        else:
            du.set_active(0)
        chs = b.get_object('race_stchan_combo')
        chs.set_active(self.chan_S)
        cha = b.get_object('race_achan_combo')
        cha.set_active(self.chan_A)
        chb = b.get_object('race_bchan_combo')
        chb.set_active(self.chan_B)
        aa = b.get_object('race_autoarm_toggle')
        aa.set_active(self.autoarm)
        se = b.get_object('race_series_entry')
        se.set_text(self.series)
        as_e = b.get_object('auto_starters_entry')
        as_e.set_text(self.autospec)

        response = dlg.run()
        if response == 1:       # id 1 set in glade for "Apply"
            if tt.get_active() == 1:
                self.set_timetype('single')
            else:
                self.set_timetype('dual')
            dval = di.get_text()
            if dval.isdigit():
                self.distance = int(dval)
            if du.get_active() == 0:
                self.units = 'metres'
            else:
                self.units = 'laps'
            self.chan_S = chs.get_active()
            self.chan_A = cha.get_active()
            self.chan_B = chb.get_active()
            self.autoarm = aa.get_active()           

            # update auto startlist spec
            nspec = as_e.get_text()
            if nspec != self.autospec:
                self.autospec = nspec
                self.event[u'auto'] = nspec
                if self.autospec:
                    self.meet.autostart_riders(self, self.autospec,
                                                     infocol=self.seedsrc)

            # update series
            ns = se.get_text()
            if ns != self.series:
                self.series = ns
                self.event[u'seri'] = ns

            # xfer starters if not empty
            slist = strops.reformat_riderlist(
                          b.get_object('race_starters_entry').get_text(),
                                        self.meet.rdb, self.series).split()
            # if no starters yet - automatically seed by order entered
            if len(self.riders) == 0:
                cnt = 1
                for s in slist:
                    self.addrider(s, cnt)
                    cnt += 1
            else:
                for s in slist:
                    self.addrider(s)

            self.log.debug('Edit race properties done.')
            glib.idle_add(self.delayed_announce)
        else:
            self.log.debug('Edit race properties cancelled.')

        # if prefix is empty, grab input focus
        if self.prefix_ent.get_text() == '':
            self.prefix_ent.grab_focus()
        dlg.destroy()

    def result_gen(self):
        """Generator function to export a final result."""
        for r in self.riders:
            bib = r[COL_BIB]
            rank = None
            time = None
            info = None
            if r[COL_COMMENT] in ['caught', 'rel', 'w/o']:
                info = r[COL_COMMENT]
            if self.onestart:
                if r[COL_PLACE] != '':
                    if r[COL_PLACE].isdigit():
                        rank = int(r[COL_PLACE])
                    else:
                        rank = r[COL_PLACE]
                if r[COL_FINISH] is not None:
                    time = (r[COL_FINISH]-r[COL_START]).truncate(3)

            yield [bib, rank, time, info]

    def result_report(self, recurse=False):
        """Return a list of printing sections containing the race result."""
        self.placexfer()
        ret = []
        sec = printing.section()
        sec.heading = u'Event ' + self.evno + u': ' + u' '.join([
                       self.event[u'pref'], self.event[u'info']
                              ]).strip()
        sec.lines = []
        ftime = None
        rcount = 0
        pcount = 0
        for r in self.riders:
            rcount += 1
            rno = r[COL_BIB].decode('utf-8')
            rh = self.meet.newgetrider(rno, self.series)
            rank = None
            rname = u''
            plink = None
            if not self.teamnames:
                rname = rh[u'namestr']
            else:	# Team no hack
                rno = u' '	# force name
                if rh is not None:
                    rname = rh[u'first']
            rtime = None
            rcat = None
            if self.event[u'cate']:
                if rh is not None:
                    if rh[u'cat']:
                        rcat = rh[u'cat']
            if rh is not None:
                if rh[u'ucicode']:
                    rcat = rh[u'ucicode']   # overwrite by force
            info = None
            dtime = None
            if self.onestart:
                if r[COL_PLACE] != '':
                    if r[COL_PLACE].isdigit():
                        rank = r[COL_PLACE] + '.'
                    else:
                        rank = r[COL_PLACE]
                    pcount += 1
                if r[COL_FINISH] is not None:
                    time = (r[COL_FINISH]-r[COL_START]).truncate(3)
                    if ftime is None: 
                        ftime = time
                    else:
                        dtime = '+' + (time - ftime).rawtime(2)
                    if r[COL_START] != tod.tod('0'):	# auto time
                        rtime = time.rawtime(3)
                    else:
                        rtime = time.rawtime(2) + u'\u2007'
                elif r[COL_COMMENT]:
                    rtime = str(r[COL_COMMENT])

            sec.lines.append([rank, rno, rname, rcat, rtime, dtime,None])
            # then add team members if relevant
            if u't' in self.series:
                for trno in strops.reformat_riderlist(rh[u'note']).split():
                    trh = self.meet.newgetrider(trno) #!! SERIES?
                    if trh is not None:
                        trname = trh[u'namestr']
                        trinf = trh[u'ucicode']
                        sec.lines.append([None, trno, trname, trinf,
                                                None, None, None])
            # or add tandem partner
            if rh is not None:
                # consider partners here
                if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                    ph = self.meet.newgetrider(rh[u'note'], self.series)
                    if ph is not None:
                        prname = ph[u'namestr'] + u' - Pilot'
                        prinf = ph[u'ucicode']
                        sec.lines.append([None, u'', prname, prinf,
                                                None, None, None])
        
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        ## Should be a method 
        if self.onestart:
            sec.subheading = substr
            if rcount > 0 and pcount < rcount:
                sec.subheading += u' - STANDINGS'
            else:
                sec.subheading += u' - Result'
        else:
            if substr:
                sec.subheading = substr

        ret.append(sec)

        # comment
        if self.comment is not None:
            sec = printing.bullet_text()
            sec.heading = u'Decisions of the commissaires panel'
            sec.lines.append([None, self.comment])
            ret.append(sec)

        return ret

    def editent_cb(self, entry, col):
        """Shared event entry update callback."""
        if col == u'pref':
            self.event[u'pref'] = entry.get_text().decode('utf-8', 'replace')
        elif col == u'info':
            self.event[u'info'] = entry.get_text().decode('utf-8', 'replace')
        self.update_expander_lbl_cb()

    def update_expander_lbl_cb(self):
        """Update race info expander label."""
        self.info_expand.set_label('Race Info : '
                    + self.meet.racenamecat(self.event, 64))

    def clear_rank(self, cb):
        """Run callback once in main loop idle handler."""
        cb('')
        return False

    def lap_trig(self, sp, t):
        """Register lap trigger."""
        rank = self.insert_split(sp.lap, t-self.curstart, sp.getrider())
        prev = None
        if sp.lap > 0:
            prev = sp.splits[sp.lap-1]
        self.log_lap(sp.getrider(), sp.lap+1, self.curstart, t, prev)
        sp.intermed(t)
        if self.difftime:
            if self.diffstart is None or self.difflane is sp:
                self.diffstart = t
                self.difflane = sp
            else:
                so = self.t_other(sp)
                if so.lap == sp.lap and self.diffstart is not None:
                    dt = t - self.diffstart
                    if dt < 4:
                        sp.difftime(dt)
                    self.difflane = None
                    self.diffstart = None
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            lapstr = strops.num2ord(str(rank + 1)) + ' on lap ' + str(sp.lap)
            if sp is self.fs:
                self.meet.scbwin.setr1('(' + str(rank + 1) + ')')
                glib.timeout_add_seconds(4, self.clear_rank,
                                            self.meet.scbwin.setr1)
                # announce lap and rank to uSCBsrv
                self.meet.announce.postxt(5, 8, strops.truncpad(lapstr,17)
                                          + ' ' + self.fs.ck.get_text())
            else:
                self.meet.scbwin.setr2('(' + str(rank + 1) + ')')
                glib.timeout_add_seconds(4, self.clear_rank,
                                            self.meet.scbwin.setr2)
                self.meet.announce.postxt(5, 50, strops.truncpad(lapstr,17)
                                          + ' ' + self.bs.ck.get_text())

    def fin_trig(self, sp, t):
        """Register finish trigger."""
        sp.finish(t)
        if self.difftime:
            if self.diffstart is None or self.difflane is sp:
                self.diffstart = t
                self.difflane = sp
            else:
                so = self.t_other(sp)
                if so.lap == sp.lap and self.diffstart is not None:
                    dt = t - self.diffstart
                    if dt < 4:
                        sp.difftime(dt)
                    self.difflane = None
                    self.diffstart = None
        ri = self.getiter(sp.getrider())
        if ri is not None:
            self.settimes(ri, self.curstart, t, sp.splits)
        else:
            self.log.warn('Rider not in model, finish time will not be stored.')
        prev = None
        if sp.lap > 0:
            prev = sp.getsplit(sp.lap - 1)
        self.log_elapsed(sp.getrider(), self.curstart, t, sp.lap+1, prev)
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            place = self.riders.get_value(ri, COL_PLACE)
            if sp is self.fs:
                elap = t - self.curstart
                self.meet.scbwin.setr1('(' + place + ')')
                self.meet.scbwin.sett1(self.fs.ck.get_text())
                self.meet.gemini.set_time(self.fs.ck.get_text(),0)
                if self.timetype == 'single': # Speed/TTB is hack mode
                    dist = self.meet.get_distance(self.distance, self.units)
                    if dist is not None:
                        spstr = elap.speedstr(dist).strip()
                        glib.timeout_add_seconds(1, self.clear_200_ttb,
                                                    self.meet.scbwin,
                                                    'Avg:',
                                                    spstr.rjust(12))
                    else:
                        glib.timeout_add_seconds(2, self.clear_200_ttb,
                                                    self.meet.scbwin)
            else:
                self.meet.scbwin.setr2('(' + place + ')')
                self.meet.scbwin.sett2(self.bs.ck.get_text())
                self.meet.gemini.set_time(self.bs.ck.get_text(),1)
            self.meet.gemini.show_dual()
        # call for a delayed announce...
        glib.idle_add(self.delayed_announce)
        # AND THEN, if other lane not armed, export result
        if self.t_other(sp).getstatus() != 'armfin':
            self.meet.delayed_export()

    def rftimercb(self, e):
        """Handle rftimer event."""
        if e.refid == '':       # got a trigger
            #return self.starttrig(e)
            return False
        return False

    def timercb(self, e):
        """Handle a timer event."""
        chan = strops.chan2id(e.chan)
        if self.timerstat == 'armstart':
            if chan == self.chan_S:
                self.torunning(e)
        elif self.timerstat == 'running':
            if chan == self.chan_A or (self.timetype == u'single' and self.chan_B):
                stat = self.fs.getstatus()
                if stat == 'armint':
                    self.lap_trig(self.fs, e)
                elif stat == 'armfin':
                    self.fin_trig(self.fs, e)
            elif chan == self.chan_B:
                stat = self.bs.getstatus()
                if stat == 'armint':
                    self.lap_trig(self.bs, e)
                elif stat == 'armfin':
                    self.fin_trig(self.bs, e)
        return False

    def timeout(self):
        """Update scoreboard and respond to timing events."""
        if not self.winopen:
            return False
        now = tod.tod('now')
        if self.fs.status in ['running', 'armint', 'armfin']:
            self.fs.runtime(now - self.lstart)
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                elapstr = self.fs.ck.get_text()
                self.meet.scbwin.sett1(elapstr)
                self.meet.gemini.set_time(elapstr[0:12],lane=0)
        if self.bs.status in ['running', 'armint', 'armfin']:
            self.bs.runtime(now - self.lstart)
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                elapstr = self.bs.ck.get_text()
                self.meet.scbwin.sett2(elapstr)
                self.meet.gemini.set_time(elapstr[0:12],lane=1)
        self.meet.gemini.show_dual()
        return True

    def show_200_ttb(self, scb):
        """Display time to beat."""
        if len(self.results) > 0:
            scb.setr2('Fastest:')
            scb.sett2(self.results[0].timestr(3))
        return False

    def clear_200_ttb(self, scb, r2='', t2=''):
        """Clear time to beat."""
        scb.setr2(r2)
        scb.sett2(t2)
        return False

    def torunning(self, st, lst=None):
        """Set timer running."""
        if self.fs.status == 'armstart':
            self.fs.start(st)
        if self.bs.status == 'armstart':
            self.bs.start(st)
        self.curstart = st
        if lst is None:
            lst = tod.tod('now')
        self.lstart = lst
        self.diffstart = None
        self.difflane = None
        self.timerstat = 'running'
        self.onestart = True
        if self.timetype == 'single':
            if self.autoarm:
                self.armfinish(self.fs, self.chan_A)
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                glib.timeout_add_seconds(3, self.show_200_ttb,
                        self.meet.scbwin)

    def clearplaces(self):
        """Clear rider places."""
        for r in self.riders:
            r[COL_PLACE] = ''

    def getrider(self, bib):
        """Return temporary reference to model row."""
        ret = None
        for r in self.riders:
            if r[COL_BIB] == bib:
                ret = r
                break
        return ret

    def addrider(self, bib='', info=None):
        """Add specified rider to race model."""
        istr = ''
        if info is not None:
            istr = str(info)
        nr=[bib, '', '', '', '', istr, '', None, None, None]
        ri = self.getrider(bib)
        if ri is None:	# adding a new record
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            return self.riders.append(nr)
        else:		# patch 'seed' on an existing record
            if not ri[COL_SEED] and istr:
                ri[COL_SEED] = istr
            return True		# not sure about this one?

    def editcol_db(self, cell, path, new_text, col):
        """Cell update with writeback to meet."""
        new_text = new_text.strip()
        self.riders[path][col] = new_text.strip()
        glib.idle_add(self.meet.rider_edit, self.riders[path][COL_BIB],
                                           self.series, col, new_text)

    def placexfer(self):
        """Transfer places into model."""
        self.clearplaces()
        count = 0
        place = 1
        lt = None
        for t in self.results:
            if lt is not None:
                if lt != t:
                    place = count + 1
                if t > tod.FAKETIMES['max']:
                    if t == tod.FAKETIMES['dsq']:
                        place = 'dsq'
                    elif t == tod.FAKETIMES['dns']:
                        place = 'dns'
                    elif t == tod.FAKETIMES['dnf']:
                        place = 'dnf'
                    #else:
                        #place = 'dnf' # leave to naturally sort
            i = self.getiter(t.refid)
            if i is not None:
                self.riders.set_value(i, COL_PLACE, str(place))
                self.riders.swap(self.riders.get_iter(count), i)
                count += 1
                lt = t
            else:
                self.log.warn('Rider not found in model, check places.')
            
    def getiter(self, bib):
        """Return temporary iterator to model row."""
        i = self.riders.get_iter_first()
        while i is not None:
            if self.riders.get_value(i, COL_BIB) == bib:
                break
            i = self.riders.iter_next(i)
        return i

    def settimes(self, iter, st=None, ft=None, splits=None,
                             doplaces=True, comment=None):
        """Transfer race times into rider model."""
        bib = self.riders.get_value(iter, COL_BIB)
        # clear result for this bib
        self.results.remove(bib)
        # assign tods
        self.riders.set_value(iter, COL_START, st)
        self.riders.set_value(iter, COL_FINISH, ft)
        # save result
        if st is None:
            st = tod.tod(0)
        if ft is not None:
            self.results.insert(ft-st, bib)
        else:	# DNF/Catch/etc
            self.results.insert(comment, bib)
        # clear any stale splits
        for sl in self.splits:		# for each split, remove this bib
            if sl is not None:
                sl.remove(bib)
        # transfer splits
        sl = []
        if splits is not None:
            i = 0
            for s in splits:
                if not len(self.splits) > i:
                    self.splits.append(tod.todlist(str(i)))
                sl.append(s)
                if s is not None:
                    self.splits[i].insert(s-st, bib)
                i += 1
        # save split tod list into rider
        self.riders.set_value(iter, COL_SPLITS, sl)
        # copy annotation into model if provided, or clear
        if comment:
            self.riders.set_value(iter, COL_COMMENT, comment)
        else:
            self.riders.set_value(iter, COL_COMMENT, '')
        # if reqd, do places
        if doplaces:
            self.placexfer()

    def insert_split(self, i, st, bib):
        """Insert rider split into correct lap."""
        # i is zero based lap count
        oft = len(self.splits)
        self.log.info('inserting split: ' + repr(bib) + ' on lap = ' + repr(i))
        while(oft <= i):
            self.splits.append(tod.todlist(str(oft)))
            oft += 1
        self.splits[i].insert(st, bib)
        return self.splits[i].rank(bib)
        
    def armstart(self):
        """Arm timer for start trigger."""
        if self.timerstat == 'armstart':
            self.toload()
        elif self.timerstat in ['load', 'idle']:
            self.toarmstart()

    def armlap(self, sp, cid):
        """Arm timer for a lap split."""
        if self.timerstat == 'running':
            if sp.getstatus() in ['caught', 'running']:
                sp.toarmint()
                self.meet.timer.arm(cid)
            elif sp.getstatus() == 'armint':
                sp.torunning()
                self.meet.timer.dearm(cid)

    def lanestr(self, sp):
        """Return f for front and b for back straight."""
        ret = 'f'
        if sp is self.bs:
            ret = 'b'
        return ret
        
    def abortrider(self, sp):
        """Abort the selected lane."""
        if sp.getstatus() not in ['idle', 'caught', 'finish']:
            bib = sp.getrider()
            ri = self.getiter(bib)
            if ri is not None:
                self.settimes(ri, st=self.curstart, splits=sp.splits,
                                  comment='abort')
            sp.tofinish()
            self.meet.timer_log_msg(bib, '- Abort -')
            # update main state? No, leave run... for unabort
            #if self.t_other(sp).getstatus() in ['idle', 'finish']:
                 #self.toidle() -> to finished
            glib.idle_add(self.delayed_announce)

    def catchrider(self, sp):
        """Selected lane has caught other rider."""
        # TODO: catch on time type swaps rider ordering
        if self.timetype != 'single':
            op = self.t_other(sp)
            if op.getstatus() not in ['idle', 'finish']:
                bib = op.getrider()
                ri = self.getiter(bib)
                if ri is not None:
                    self.settimes(ri, st=self.curstart,
                                      splits=op.splits, comment='caught')
                op.tofinish('caught')
                self.meet.timer_log_msg(bib, '- Caught -')
                if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                    if op is self.fs:
                        self.meet.scbwin.sett1(' [caught]     ')
                        self.meet.gemini.set_time(u'    -:--.-  ',0)
                    else:
                        self.meet.scbwin.sett2(' [caught]     ')
                        self.meet.gemini.set_time(u'    -:--.-  ',1)
            if sp.getstatus() not in ['idle', 'finish']:
                bib = sp.getrider()
                ri = self.getiter(bib)
                if ri is not None:
                    self.settimes(ri, st=self.curstart,
                                      splits=sp.splits, comment='catch')
                self.meet.timer_log_msg(bib, '- Catch -')
                # but continue by default - manual abort to override.
            glib.idle_add(self.delayed_announce)
        else:
            self.log.warn(u'Unable to catch with single rider.')

    def falsestart(self):
        """Register false start."""
        if self.timerstat == 'running':
            if self.fs.getstatus() not in ['idle', 'caught', 'finish']:
                self.fs.toload()
                self.meet.timer_log_msg(self.fs.getrider(),
                                        '- False start -')
                if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                    self.meet.scbwin.setr1('False')
                    self.meet.scbwin.sett1('Start')
            if self.bs.getstatus() not in ['idle', 'caught', 'finish']:
                self.bs.toload()
                self.meet.timer_log_msg(self.bs.getrider(),
                                        '- False start -')
                if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                    self.meet.scbwin.setr2('False')
                    self.meet.scbwin.sett2('Start')
            self.toidle(idletimers=False)
        elif self.timerstat == 'armstart':
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                self.meet.gemini.clear()
                self.meet.scbwin.sett1('            ')
                self.meet.scbwin.sett2('            ')
            self.toload()

    def armfinish(self, sp, cid):
        """Arm timer for finish trigger."""
        if self.timerstat == 'running':
            if sp.getstatus() in ['running', 'caught', 'finish']:
                if sp.getstatus() == 'finish':
                     self.meet.timer_log_msg(sp.getrider(),
                                             '- False finish -')
                     self.meet.scbwin.setr1('')
                     self.meet.scbwin.setr2('')
                sp.toarmfin()
                self.meet.timer.arm(cid)
            elif sp.getstatus() == 'armfin':
                sp.torunning()
                self.meet.timer.dearm(cid)

    def toload(self):
        """Set timer status to load."""
        if self.fs.status == 'armstart':
            self.fs.toload()
        if self.bs.status == 'armstart':
            self.bs.toload()
        self.toidle(idletimers=False)

    def fmtridername(self, tp):
        """Prepare rider name for display on scoreboard."""
        bib = tp.getrider().strip()
        if bib != '':
            name = '[New Rider]'
            r = self.getrider(bib)
            ret = name
            if r is not None and r[COL_BIB] != '':
                if self.teamnames:
                    club = r[COL_CLUB].decode('utf-8')
                    name = r[COL_FIRSTNAME].decode('utf-8')
                    name_w = self.meet.scb.linelen-5	# w=4 + space
                    ret = ' '.join([strops.truncpad(name, name_w, 'l'),
                                    strops.truncpad(club, 4, 'r')])
                    tp.namevec = [bib, ret, club]
                else:
                    name_w = self.meet.scb.linelen - 9
                    first = r[COL_FIRSTNAME].decode('utf-8')
                    last = r[COL_LASTNAME].decode('utf-8')
                    club = r[COL_CLUB].decode('utf-8')
                    name = strops.fitname(first,
                                          last,
                                          name_w)
                    tp.namevec = [bib, strops.resname(first, last, club), '']
                    ret = ' '.join([strops.truncpad(r[COL_BIB], 3, 'r'),
                                 strops.truncpad(name, name_w),
                                 strops.truncpad(club, 4, 'r')])
            return ret
        else:
            tp.namevec = None
            return ''
        
    def showtimerwin(self):
        """Show timer window on scoreboard."""
        self.meet.scbwin = None
        self.meet.scbwin = scbwin.scbtt(self.meet.scb,
                                self.meet.racenamecat(self.event),
                                self.fmtridername(self.fs),
                                self.fmtridername(self.bs))
        if self.timetype == 'single':
            self.meet.scbwin.set_single()
        self.meet.gemini.reset_fields()
        self.meet.announce.gfx_overlay(2)
        self.meet.announce.gfx_set_title(self.event[u'pref'] + u' '
                            + self.event[u'info'])
        self.meet.gemini.set_bib(self.fs.getrider(),0)
        self.meet.gemini.set_bib(self.bs.getrider(),1)
        if self.fs.namevec is not None:
            self.meet.announce.gfx_add_row(self.fs.namevec)
        if self.bs.namevec is not None:
            self.meet.announce.gfx_add_row(self.bs.namevec)
        self.timerwin = True
        self.meet.scbwin.reset()

    def toarmstart(self):
        """Set timer to arm start."""
        doarm = False
        if self.fs.status == 'load':
            self.fs.toarmstart()
            doarm = True
        if self.bs.status == 'load' and self.timetype != 'single':
            self.bs.toarmstart()
            doarm = True
        if doarm:
            self.timerstat = 'armstart'
            self.curstart = None
            self.lstart = None
            self.meet.timer.arm(self.chan_S)
            self.showtimerwin()
            if not self.onestart:
                self.meet.timer.printline(self.meet.racenamecat(self.event))
            self.meet.delayimp('0.01')
            if self.fs.status == 'armstart':
                bib = self.fs.getrider()
                if bib not in self.traces:
                    self.traces[bib] = []
                self.fslog = loghandler.traceHandler(self.traces[bib])
                logging.getLogger().addHandler(self.fslog)
                self.meet.scbwin.sett1('       0.0     ')
                nstr = self.fs.biblbl.get_text()
                self.meet.timer_log_msg(bib, nstr)
            if self.bs.status == 'armstart':
                bib = self.bs.getrider()
                if bib not in self.traces:
                    self.traces[bib] = []
                self.bslog = loghandler.traceHandler(self.traces[bib])
                logging.getLogger().addHandler(self.bslog)
                self.meet.scbwin.sett2('       0.0     ')
                nstr = self.bs.biblbl.get_text()
                self.meet.timer_log_msg(bib, nstr)
            if self.timetype == 'single':
                self.bs.toidle()
                self.bs.disable()
            glib.idle_add(self.delayed_announce)

    def toidle(self, idletimers=True):
        """Set timer to idle state."""
        if self.fslog is not None:
            logging.getLogger().removeHandler(self.fslog)
            self.fslog = None
        if self.bslog is not None:
            logging.getLogger().removeHandler(self.bslog)
            self.bslog = None
        if idletimers:
            self.fs.toidle()
            self.bs.toidle()
        self.timerstat = 'idle'
        self.meet.delayimp('2.00')
        self.curstart = None
        self.lstart = None
        self.diffstart = None
        for i in range(0,8):
            self.meet.timer.dearm(i)
        if not self.onestart:
            pass
        self.fs.grab_focus()

    def t_other(self, tp=None):
        """Return reference to 'other' timer."""
        if tp is self.fs:
            return self.bs
        else:
            return self.fs

    def lanelookup(self, bib=None):
        """Prepare name string for timer lane."""
        r = self.getrider(bib)
        if r is None:
            if self.meet.get_clubmode():	# fill in starters
                self.log.warn('Adding non-starter: ' + repr(bib))
                self.addrider(bib)
                r = self.getrider(bib)
            else:	# 'champs' mode
                return None
        rtxt = '[New Rider]'
        if r is not None and (r[COL_FIRSTNAME] != ''
                              or r[COL_LASTNAME] != ''):
            rtxt = r[COL_FIRSTNAME] + ' ' + r[COL_LASTNAME]
            if r[3] != '':
                rtxt += '(' + r[3] + ')'
        return rtxt

    def bibent_cb(self, entry, tp):
        """Bib entry callback."""
        bib = entry.get_text().strip()
        if bib != '' and bib.isalnum():
            nstr = self.lanelookup(bib)
            if nstr is not None:
                tp.biblbl.set_text(nstr)
                if tp.status == 'idle':
                    tp.toload()
                if self.timerstat == 'running':
                    tp.start(self.curstart)
                if self.timetype != 'single':
                    self.t_other(tp).grab_focus()
            else:
                self.log.warn('Ignoring non-starter: ' + repr(bib))
                tp.toidle()
        else:
            tp.toidle()
    
    def treeview_button_press(self, treeview, event):
        """Set callback for mouse press on model view."""
        if event.button == 3:
            pathinfo = treeview.get_path_at_pos(int(event.x), int(event.y))
            if pathinfo is not None:
                path, col, cellx, celly = pathinfo
                treeview.grab_focus()
                treeview.set_cursor(path, col, 0)
                self.context_menu.popup(None, None, None,
                                        event.button, event.time)
                return True
        return False

    def tod_context_clear_activate_cb(self, menuitem, data=None):
        """Clear times for selected rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            self.settimes(sel[1])
            self.log_clear(self.riders.get_value(sel[1], COL_BIB))
            glib.idle_add(self.delayed_announce)

    def tod_context_rel_activate_cb(self, menuitem, data=None):
        """Relegate rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            self.settimes(sel[1], comment='rel')
            #self.log_clear(self.riders.get_value(sel[1], COL_BIB))
            glib.idle_add(self.delayed_announce)

    def tod_context_dsq_activate_cb(self, menuitem, data=None):
        """Disqualify rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            self.settimes(sel[1], comment='dsq')
            #self.log_clear(self.riders.get_value(sel[1], COL_BIB))
            glib.idle_add(self.delayed_announce)

    def tod_context_print_activate_cb(self, menuitem, data=None):
        """Print Rider trace"""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            bib = self.riders.get_value(sel[1], COL_BIB)
            if bib in self.traces:
                self.meet.timer_reprint(self.meet.racenamecat(self.event),
                                        self.traces[bib])

    def now_button_clicked_cb(self, button, entry=None):
        """Set specified entry to the 'now' time."""
        if entry is not None:
            entry.set_text(tod.tod('now').timestr())

    def tod_context_edit_activate_cb(self, menuitem, data=None):
        """Run edit time dialog."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]	# grab off row iter
            stod = self.riders.get_value(i, COL_START)
            st = ''
            if stod is not None:
                st = stod.timestr()
            else:
                st = '0.0'
            ftod = self.riders.get_value(i, COL_FINISH)
            ft = ''
            if ftod is not None:
                ft = ftod.timestr()
            tvec = uiutil.edit_times_dlg(self.meet.window,st,ft)
            if tvec[0] == 1:
                stod = tod.str2tod(tvec[1])
                ftod = tod.str2tod(tvec[2])
                bib = self.riders.get_value(i, COL_BIB)
                if stod is not None and ftod is not None:
                    self.settimes(i, stod, ftod)	# set times
                    self.log_elapsed(bib, stod, ftod, manual=True)
                else:
                    self.settimes(i)			# clear times
                    self.log_clear(bib)
                self.log.info('Race times manually adjusted for no. %s', bib)
            else:
                self.log.info('Edit race times cancelled.')
            glib.idle_add(self.delayed_announce)

    def tod_context_del_activate_cb(self, menuitem, data=None):
        """Delete selected row from race model."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]	# grab off row iter
            if self.riders.remove(i):
                pass	# re-select?
            glib.idle_add(self.delayed_announce)

    def log_clear(self, bib):
        """Print clear time log."""
        self.meet.timer_log_msg(bib, '- Time Cleared -')

    def log_lap(self, bib, lap, start, split, prev=None):
        """Print lap split log."""
        if prev is None:
            prev = start
        self.meet.timer_log_straight(bib, str(lap), split-prev, 3)
        if lap > 1 and prev != start:
            self.meet.timer_log_straight(bib, 'time', split - start, 3)
        
    def log_elapsed(self, bib, start, finish,
                          lap=None, prev=None, manual=False):
        """Print elapsed log info."""
        if manual:
            self.meet.timer_log_msg(bib, '- Manual Adjust -')
        if prev is not None and prev != start:
            self.meet.timer_log_straight(bib, str(lap), finish - prev, 3)
        self.meet.timer_log_straight(bib, 'ST', start)
        self.meet.timer_log_straight(bib, 'FIN', finish)
        self.meet.timer_log_straight(bib, 'TIME', finish - start, 3)

    def set_timetype(self, data=None):
        """Update timer panes to match timetype or data if provided."""
        if data is not None:
            self.timetype = strops.confopt_pair(data, 'single', 'dual')
        if self.timetype == 'single':
            self.bs.frame.hide()
            self.bs.hide_laps()
            self.fs.frame.set_label('Timer')
            self.fs.show_laps()
        else:
            self.bs.frame.show()
            self.bs.show_laps()
            self.fs.frame.set_label('Front Straight')
            self.fs.show_laps()
        self.type_lbl.set_text(self.timetype.capitalize())

    def destroy(self):
        """Signal race shutdown."""
        if self.context_menu is not None:
            self.context_menu.destroy()
        self.frame.destroy()

    def show(self):
        """Show race window."""
        self.frame.show()

    def hide(self):
        """Hide race window."""
        self.frame.hide()

    def __init__(self, meet, event, ui=True):
        """Constructor."""
        self.meet = meet
        self.event = event      # Note: now a treerowref
        self.evno = event[u'evid']
        self.evtype = event[u'type']
        self.series = event[u'seri']
        self.configpath = meet.event_configfile(self.evno)

        self.log = logging.getLogger('ittt')
        self.log.setLevel(logging.DEBUG)
        self.log.debug(u'Creating new ittt event: ' + self.evno)

        # properties
        self.autospec = event[u'auto']	# automatic startlist
        self.timetype = 'dual'
        self.distance = None
        self.units = 'laps'
        self.autoarm = False
        self.comment = None
        self.difftime = False
        self.teampursuit = False
        self.teamnames = False		# team names only shown
        self.chan_A = timy.CHAN_PA	# default is ITT/Pursuit
        self.chan_B = timy.CHAN_PB
        self.chan_S = timy.CHAN_START
        self.fsvec = None
        self.bsvec = None
        self.fslog = None
        self.bslog = None
        self.traces = {}

        # race run time attributes
        self.inomnium = False
        self.seedsrc = 1	# default seeding is by rank in last round
        self.onestart = False
        self.readonly = not ui
        self.winopen = ui
        self.timerwin = False
        self.showcats = False
        self.timerstat = 'idle'
        self.curstart = None
        self.lstart = None
        self.diffstart = None		# for diff time in pursuit race
        self.difflane = None		# for diff time in pursuit race
	self.splits = []		# list of lap split lists
        self.results = tod.todlist('FIN')

        self.riders = gtk.ListStore(gobject.TYPE_STRING,   # 0 bib
                                    gobject.TYPE_STRING,   # 1 firstname
                                    gobject.TYPE_STRING,   # 2 lastname
                                    gobject.TYPE_STRING,   # 3 club
                                    gobject.TYPE_STRING,   # 4 Comment
                                    gobject.TYPE_STRING,   # 5 seeding
                                    gobject.TYPE_STRING,   # 6 place
                                    gobject.TYPE_PYOBJECT, # 7 Start
                                    gobject.TYPE_PYOBJECT, # 8 Finish
                                    gobject.TYPE_PYOBJECT) # 9 Lap Splits(0,n)

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'ittt.ui'))

        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # meta info pane
        self.info_expand = b.get_object('info_expand')
        b.get_object('race_info_evno').set_text(self.evno)
        self.showev = b.get_object('race_info_evno_show')
        self.prefix_ent = b.get_object('race_info_prefix')
        self.prefix_ent.connect('changed', self.editent_cb,u'pref')
        self.prefix_ent.set_text(self.event[u'pref'])
        self.info_ent = b.get_object('race_info_title')
        self.info_ent.connect('changed', self.editent_cb,u'info')
        self.info_ent.set_text(self.event[u'info'])
        self.type_lbl = b.get_object('race_type')

        # Timer Panes
        mf = b.get_object('race_timer_pane')
        self.fs = timerpane.timerpane('Front Straight')
        self.fs.uport = self.meet.udptimer
        self.fs.uaddr = self.meet.udpaddr
        self.fs.bibent.connect('activate', self.bibent_cb, self.fs)
        self.bs = timerpane.timerpane('Back Straight')
        self.bs.uport = self.meet.udptimer
        self.bs.uaddr = self.meet.udpaddr
        self.bs.urow = 6	# scb row for timer messages
        self.bs.bibent.connect('activate', self.bibent_cb, self.bs)
        mf.pack_start(self.fs.frame)
        mf.pack_start(self.bs.frame)
        mf.set_focus_chain([self.fs.frame, self.bs.frame, self.fs.frame])

        # Result Pane
        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_rules_hint(True)
        t.connect('button_press_event', self.treeview_button_press)
     
        # TODO: show team name & club but pop up for rider list
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'First Name', COL_FIRSTNAME,
                               self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Last Name', COL_LASTNAME,
                               self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Club', COL_CLUB, self.editcol_db)
        uiutil.mkviewcoltxt(t, 'Seed', COL_SEED, self.editcol_db)
        uiutil.mkviewcoltod(t, 'Time', cb=self.todstr)
        uiutil.mkviewcoltxt(t, 'Rank', COL_PLACE, halign=0.5, calign=0.5)
        t.show()
        b.get_object('race_result_win').add(t)

        # show window
        self.context_menu = None
        if ui:
            b.connect_signals(self)
            b = gtk.Builder()
            b.add_from_file(os.path.join(metarace.UI_PATH, 'tod_context.ui'))
            self.context_menu = b.get_object('tod_context')
            b.connect_signals(self)
