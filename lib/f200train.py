
# Metarace : Cycle Race Abstractions
# Copyright (C) 2018  Neil Hamey
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

"""Sprint qualify module..

This module provides a class 'f200' which implements the 'race'
interface and manages data, timing and scoreboard for the
UCI flying 200 time trial.

Peculiarities when compared to ittt:

 - times are _truncated_ to thousandths
 - dead heat on thousandths is broken by time over last 100m
 - autoarm is the default

TODO: 
 - use sectormap to track and report rider speed
 - report split/rank over each sector

"""

import gtk
import glib
import gobject
import pango
import os
import logging
import csv
import ConfigParser

import metarace
from metarace import timy
from metarace import scbwin
from metarace import tod
from metarace import unt4
from metarace import loghandler
from metarace import uiutil
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import timerpane
from metarace import printing
from metarace import analysis


# config version string
EVENT_ID = 'trackf200-1.3'

# startlist model columns
COL_BIB = 0
COL_FIRSTNAME = 1
COL_LASTNAME = 2
COL_CLUB = 3
COL_COMMENT = 4		# 
COL_SEED = 5		# Change to seed for future versions
COL_PLACE = 6
COL_START = 7
COL_STARTRW = 8
COL_FINISH = 9
COL_FINISHRW = 10
COL_PRESTART = 11
COL_PRESTARTRW = 12
COL_50 = 13
COL_50RW = 14
COL_100 = 15   # 100m split ToD
COL_100RW = 16
COL_150 = 17
COL_150RW = 18
COL_INFO = 4


# scb function key mappings (also trig announce)
key_reannounce = 'F4'                # (+CTRL) calls into delayed announce
key_startlist = 'F3'                 # startlist
key_results = 'F4'                   # recalc/show result window
key_timerwin = 'F6'                 # re-show timing window

# timing function key mappings
key_armstart = 'F5'                  # arm for start impulse
key_arm50 = 'F7'		             # de/arm 50m
key_armsplit = 'F8'                  # de/arm 100m
key_arm150 = 'F9'                    # de/arm 150m
key_armfinish = 'F10'                 # de/arm for finish impulse

# extended function key mappings
key_reset = 'F5'                     # + ctrl for clear/abort
key_falsestart = 'F6'		     # + ctrl for false start
key_abort = 'F7'		     # + ctrl abort A

class f200ranks(object):
    """Helper class for managing flying 200 ranks."""
    def __init__(self, lbl=u''):
        self.__label = lbl
        self.__store = []

    def __iter__(self):
        return self.__store.__iter__()

    def __len__(self):
        return len(self.__store)

    def __getitem__(self, key):
        return self.__store[key]

    def rank(self, bib, series=u''):
        """Return current 0-based rank for given bib."""
        ret = None
        count = 0
        i = 0
        last200 = None
        last100 = None
        for lt in self.__store:
            if last200 is not None:
                if lt[0] != last200 or lt[1] != last100:
                    i = count   # normal case... each rider gets new rank
                    #i += 1	# normal case
            if lt[0].refid == bib and lt[0].index == series:
                ret = i
                break
            last200 = lt[0]
            last100 = lt[1]
            count += 1
        return ret

    def clear(self):
        self.__store = []

    def remove(self, bib, series=u''):
        i = 0
        while i < len(self.__store):
            if (self.__store[i][0].refid == bib
                  and self.__store[i][0].index == series):
                del self.__store[i]
            else:
                i += 1

    def insert(self, t, s=None, bib=None, series=u''):
        """Insert t into ordered list."""
        ret = None
        trunc = True
        if t in tod.FAKETIMES: # re-assign a coded 'finish'
            t = tod.FAKETIMES[t]
            trunc = False

        if type(t) is tod.tod:
            if s is None:
                s = tod.ZERO
            if bib is None:
                bib = t.index
            # Truncate input times to thousandths unless special case
            if trunc:
                t = t.truncate(3)
                s = s.truncate(3)
            rt0 = tod.tod(timeval=t.timeval, chan=self.__label,
                       refid=bib, index=series)
            rt1 = tod.tod(timeval=s.timeval, chan=self.__label,
                       refid=bib, index=series)
            last = None
            i = 0
            found = False
            for lt in self.__store:
                if rt0 < lt[0]:	# 200 time faster - insert ok
                    self.__store.insert(i, [rt0, rt1])
                    found = True
                    break
                elif rt0 == lt[0]: # 200 time same, compare on last 100
                    if rt1 < lt[1]: # last 100 time faster - insert ok
                        self.__store.insert(i, [rt0, rt1])
                        found = True
                        break
                i += 1
            if not found:
                self.__store.append([rt0, rt1])

class f200train(object):
    """Data handling for tt, pursuit and flying 200."""

    def key_event(self, widget, event):
        """Race window key press handler."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_reset:    # override ctrl+f5
                    self.toidle()
                    return True
                elif key == key_reannounce:
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_falsestart:	# false start both lanes
                    self.falsestart()
                    return True
                elif key == key_abort:	# abort front straight rider
                    self.abortrider(self.fs)
                    return True
            elif key[0] == 'F':
                if key == key_armstart:
                    self.armstart()
                    return True
                elif key == key_armsplit:
                    self.armsplit(self.fs)
                    return True
                elif key == key_arm50:
                    self.arm50(self.fs)
                    return True
                elif key == key_arm150:
                    self.arm150(self.fs)
                    return True
                elif key == key_armfinish:
                    self.armfinish(self.fs)
                    return True
                elif key == key_startlist:
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_timerwin:
                    self.showtimerwin()
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_results:
                    self.do_places()
                    glib.idle_add(self.delayed_announce)
                    return True
        return False

    def do_places(self):
        """Show race result on scoreboard."""
        self.meet.scbwin = None
        self.timerwin = False     # TODO: bib width enhancement
        fmtplaces = []
        self.meet.announce.gfx_overlay(1)
        self.meet.announce.gfx_set_title(u'Result: '
                            + self.event[u'pref'] + u' '
                            + self.event[u'info'])
        name_w = self.meet.scb.linelen - 11
        pcount = 0
        rcount = 0
        for r in self.riders:
            rcount += 1
            if r[COL_PLACE] is not None and r[COL_PLACE] != u'':
                pcount += 1
                plstr = r[COL_PLACE]
                if r[COL_PLACE].isdigit():
                    plstr += u'.'
                    #if int(r[COL_PLACE]) > 12:
                        #break
                first = r[COL_FIRSTNAME].decode('utf-8')
                last = r[COL_LASTNAME].decode('utf-8')
                club = r[COL_CLUB].decode('utf-8')
                bib = r[COL_BIB]
                elapstr = ''
                if r[COL_START] and r[COL_FINISH]:
                    elapstr = (r[COL_FINISH]-r[COL_START]).rawtime(3)
                fmtplaces.append([plstr, bib,
                            strops.fitname(first, last, name_w),
                                  club])
                self.meet.announce.gfx_add_row([plstr, 
                            strops.resname(first, last, club),
                            elapstr])
        FMT = [(3,u'l'), (3,u'r'), u' ',(name_w, u'l'), (4,u'r')]

        evtstatus = u'Standings'
        if rcount > 0 and pcount == rcount:
            evtstatus = u'Result'

        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                               head=self.meet.racenamecat(self.event),
                               subhead=evtstatus.upper(),
                               coldesc=FMT,
                               rows=fmtplaces)
        self.meet.scbwin.reset()

    def todstr(self, col, cr, model, iter, data=None):
        """Format tod into text for listview."""
        ft = model.get_value(iter, COL_FINISH)
        if ft is not None:
            ftrw = model.get_value(iter, COL_FINISHRW)
            sp = model.get_value(iter, COL_100)
            sprw = model.get_value(iter, COL_100RW)
            st = model.get_value(iter, COL_START)
            strw = model.get_value(iter, COL_STARTRW)
            spa = model.get_value(iter, COL_50)
            sparw = model.get_value(iter, COL_50RW)
            spb = model.get_value(iter, COL_150)
            spbrw = model.get_value(iter, COL_150RW)
            pres = model.get_value(iter, COL_PRESTART)
            presrw = model.get_value(iter, COL_PRESTARTRW)
            if st is None:
                st = tod.tod(0)
            if st == tod.tod(0):
                cr.set_property('style', pango.STYLE_OBLIQUE)
            else:
                cr.set_property('style', pango.STYLE_NORMAL)
            mstr = (ft - st).rawtime(3).rjust(6)
            sstr = u''.rjust(7)
            if sp is not None:
                sstr = u'/' + (ft - sp).rawtime(3).ljust(6)
            cr.set_property('text', mstr+sstr)
        else:
            cr.set_property('text', u'')

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        self.results.clear()
        self.splits50.clear()
        self.prestart50.clear()
        self.splits.clear()
        self.splits150.clear()

        defautoarm = 'Yes'
        self.seedsrc = 1 # for autospec loads, fetch seed from the rank col

        cr = ConfigParser.ConfigParser({'startlist':'',
                                        'id':EVENT_ID,
					                    'start':'',
                                        'lstart':'',
                                        'fsbib':'',
                                        'fsstat':'idle',
                                        'showinfo':'No',
                                        'distance':'200',
                                        'autospec':'',
                                        'inomnium':'No',
					                    'distunits':'metres',
                                        'autoarm':defautoarm,
                                        'avgspeedrpt':'True',
                                        'actspeedrpt':'False',
                                        'wirelessstart':'Fales',
                                        'wireless50':'False',
                                        'wireless100':'False',
                                        'wireless150':'False',
                                        'wirelessfinish':'False'})
        cr.add_section('event')
        cr.add_section('riders')
        cr.add_section('traces')
        if os.path.isfile(self.configpath):
            self.log.debug(u'Attempting to read config from '
                               + repr(self.configpath))
            cr.read(self.configpath)

        #self.autospec = cr.get('event', 'autospec')
        self.distance = strops.confopt_dist(cr.get('event', 'distance'))
        self.units = strops.confopt_distunits(cr.get('event', 'distunits'))
        self.autoarm = strops.confopt_bool(cr.get('event', 'autoarm'))
        self.avgspeedrpt = strops.confopt_bool(cr.get('event', 'avgspeedrpt'))
        self.actspeedrpt = strops.confopt_bool(cr.get('event', 'actspeedrpt'))
        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))
        self.inomnium = strops.confopt_bool(cr.get('event', 'inomnium'))
        if self.inomnium:
            # SWITCH: Event is part of an omnium, make any req'd overrides
            self.seedsrc = 3    # read seeding from omnium points standinds

        # re-load starters/results
        self.onestart = False
        for r in cr.get('event', 'startlist').split():
            nr=[r, '', '', '', '', '', '', None, None, None, None, None, None,
                None, None, None, None, None, None]
            co = ''
            st = None
            strw = None
            ft = None
            ftrw = None
            pres = None
            presrw = None
            spb = None
            spbrw = None
            sp = None
            sprw = None
            spa = None
            sparw = None
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
                if len(ril) >= 5:	# Start ToD Rear Wheel
                    strw = tod.str2tod(ril[4])
                if len(ril) >= 6:	# Finish ToD
                    ft = tod.str2tod(ril[5])
                if len(ril) >= 7:	# Finish ToD Rear Wheel
                    ftrw = tod.str2tod(ril[6])
                if len(ril) >= 8:	# Pre 50m ToD
                    pres = tod.str2tod(ril[7])
                if len(ril) >= 9:	# Pre 50m ToD Rear Wheel
                    presrw = tod.str2tod(ril[8])
                if len(ril) >= 10:	# 50m ToD
                    spb = tod.str2tod(ril[9])
                if len(ril) >= 11:	# 50m ToD Rear Wheel
                    spbrw = tod.str2tod(ril[10])
                if len(ril) >= 12:   # 100m ToD
                    sp = tod.str2tod(ril[11])
                if len(ril) >= 13:   # 100m ToD Rear Wheel
                    sprw = tod.str2tod(ril[12])
                if len(ril) >= 14:   # 150m ToD
                    spa = tod.str2tod(ril[13])
                if len(ril) >= 15:   # 150m ToD Rear Wheel
                    sparw = tod.str2tod(ril[14])
            dbr = self.meet.rdb.getrider(r, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            nri = self.riders.append(nr)
            self.settimes(nri, st, strw, ft, ftrw, pres, presrw, spb, spbrw, sp, sprw, spa, sparw, doplaces=False, comment=co)
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
            self.log.error(u'Attempt to save readonly ob.')
            return
        cw = ConfigParser.ConfigParser()
        cw.add_section('event')

        # save basic race properties
        cw.set('event', 'distance', self.distance)
        cw.set('event', 'distunits', self.units)
        cw.set('event', 'autospec', self.autospec)
        cw.set('event', 'autoarm', self.autoarm)
        cw.set('event', 'avgspeedrpt', self.avgspeedrpt)
        cw.set('event', 'actspeedrpt', self.actspeedrpt)
        cw.set('event', 'startlist', self.get_startlist())
        cw.set('event', 'inomnium', self.inomnium)
        cw.set('event', 'showinfo', self.info_expand.get_expanded())
        cw.set('event', 'wirelessstart', self.wirelessstart)
        cw.set('event', 'wireless50', self.wireless50)
        cw.set('event', 'wireless100', self.wireless100)
        cw.set('event', 'wireless150', self.wireless150)
        cw.set('event', 'wirelessfinish', self.wirelessfinish)

        # extract and save timerpane config for interrupted run
        if self.curstart is not None:
            cw.set('event', 'start', self.curstart.rawtime())
        if self.lstart is not None:
            cw.set('event', 'lstart', self.lstart.rawtime())
        cw.set('event', 'fsstat', self.fs.getstatus())
        cw.set('event', 'fsbib', self.fs.getrider())


        cw.add_section('traces')
        for rider in self.traces:
            cw.set('traces', rider, u'\n'.join(self.traces[rider]))

        cw.add_section('riders')

        # save out all starters
        for r in self.riders:
            # place is saved for info only
            slice = [r[COL_COMMENT], r[COL_SEED], r[COL_PLACE]]
            tl = [r[COL_START], r[COL_STARTRW], r[COL_FINISH], r[COL_FINISHRW], r[COL_PRESTART], r[COL_PRESTARTRW], 
                  r[COL_50], r[COL_50RW], r[COL_100], r[COL_100RW], r[COL_150], r[COL_150RW]]
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

    def sort_startlist(self, x, y):
        """Comparison function for ttt seeding."""
        if x[1] == y[1]:        # same seed? revert to bib ascending
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

    def get_heats(self, placeholders=0):
        """Return a list of heats in the event."""
        ret = []

        # arrange riders by seeding
        self.reorder_startlist()

        # then build aux map of heats
        hlist = []
        count = len(self.riders)
        if count < placeholders:
            count = placeholders
        if placeholders == 0:
            for r in self.riders:
                rno = r[COL_BIB]
                rh = self.meet.newgetrider(rno, self.series)
                info = None
                rname = u''
                if rh is not None:
                    rname = rh[u'namestr']
                    if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                        ph = self.meet.newgetrider(rh[u'note'], self.series)
                        if ph is not None:
                            info = [[u' ',ph[u'namestr'] + u' - Pilot', ph[u'ucicode']]]
                hlist.append([str(count)+'.1', rno, rname, info])
                                 # all heats are one up
                count -= 1
        else:
            for r in range(0, placeholders):
                rno = ''
                rname = ''
                hlist.append([str(count)+'.1', rno, rname, None])
                count -= 1

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
            rec.extend([heat, r[1], r[2], r[3]])
            lcnt += 1
            lh = h
        if len(rec) > 0:
            ret.append(rec)
        return ret

    def startlist_report(self, program=False):
        """Return a startlist report."""
        ret = []
        cnt = 0
        sec = printing.dual_ittt_startlist()
        sec.set_single()	# 200s are one-up

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
        sec.lines = self.get_heats()
        ret.append(sec)
        return ret

    def get_startlist(self):
        """Return a list of bibs in the rider model."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ' '.join(ret)	# will become u' ' once config unisafe

    def delayed_announce(self):
        """Initialise the announcer's screen after a delay."""
        if self.winopen:
            self.meet.announce.clrall()
            self.meet.ann_title(u' '.join([
                  u'Event', self.evno, u':',
                  self.event[u'pref'], self.event[u'info']]))

            self.meet.announce.linefill(1, u'_')
            self.meet.announce.linefill(7, u'_')

            # fill in front straight (only one?)
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
                    et = None
                    if r[COL_START] is not None and r[COL_FINISH] is not None:
                        et = (r[COL_FINISH] - r[COL_START]).truncate(3)
                        tmstr = u'200m: ' + et.rawtime(3).rjust(12)
                    cmtstr = u''
                    if et is not None:
                        cmtstr = strops.truncpad(u'Average: '
                                                + et.speedstr(200), 38, u'r')
                    elif r[COL_COMMENT] is not None and r[COL_COMMENT] != u'':
                        cmtstr = strops.truncpad(
                             u'[' + r[COL_COMMENT].strip() + u']', 38, u'r')
                    self.meet.announce.postxt(3,0,u'        Current Rider')
                    self.meet.announce.postxt(4,0,u' '.join([placestr, bibstr,
                                                         namestr, clubstr]))
                    self.meet.announce.postxt(5,0,strops.truncpad(tmstr,
                                                   38, u'r'))
                    self.meet.announce.postxt(6,0,cmtstr)

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
 
                namestr = strops.truncpad(strops.fitname(r[COL_FIRSTNAME],
                              r[COL_LASTNAME], 22-len(clubstr),
                                  trunc=True)+clubstr, 22, elipsis=False)
                placestr = u'   ' # 3 ch
                if r[COL_PLACE] != u'':
                    placestr = strops.truncpad(r[COL_PLACE] + u'.', 3)
                bibstr = strops.truncpad(r[COL_BIB], 3, u'r')
                tmstr = u'       ' # 7 ch
                if r[COL_START] is not None and r[COL_FINISH] is not None:
                    tmstr = strops.truncpad(
                           (r[COL_FINISH] - r[COL_START]).rawtime(3), 7, u'r')
                self.meet.announce.postxt(curline, posoft, u' '.join([
                      placestr, bibstr, namestr, tmstr]))
                curline += 1

    def shutdown(self, win=None, msg='Exiting'):
        """Terminate race object."""
        self.log.debug(u'Race Shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def do_properties(self):
        """Run race properties dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'race_properties.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.meet.window)
        b.get_object('race_score_type').hide()
        b.get_object('race_timing_label').hide()
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
        se = b.get_object('race_series_entry')
        se.set_text(self.series)
        as_e = b.get_object('auto_starters_entry')
        as_e.set_text(self.autospec)

        response = dlg.run()
        if response == 1:       # id 1 set in glade for "Apply"
            dval = di.get_text()
            if dval.isdigit():
                self.distance = int(dval)
            if du.get_active() == 0:
                self.units = 'metres'
            else:
                self.units = 'laps'

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
            if r[COL_COMMENT] in ['caught', 'rel']:
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
        sec = printing.section()	# result goes to std section
        sec.heading = u'Event ' + self.evno + u': ' + u' '.join([
                       self.event[u'pref'], self.event[u'info']
                              ]).strip()
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            sec.subheading = substr
        sec.lines = []
        ftime = None
        for r in self.riders:
            rcat = None		# should check ev[u'cate']
            plink = u''
            rno = r[COL_BIB].decode('utf-8')
            rank = None
            rh = self.meet.newgetrider(rno, self.series)
            rname = u''
            if rh is not None:
                rname = rh[u'namestr']
                # consider partners here
                if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                    ph = self.meet.newgetrider(rh[u'note'], self.series)
                    if ph is not None:
                        plink = [u'', u'', ph[u'namestr'] + u' - Pilot', ph[u'ucicode'], u'', u'',u'']
                if self.event[u'cate']:
                    if rh[u'cat']:
                        rcat = rh[u'cat']
                if rh[u'ucicode']:
                    rcat = rh[u'ucicode']   # overwrite by force
            rtime = None
            stime = None
            info = None
            dtime = None
            combtimes = None
            if self.onestart:
                if r[COL_PLACE] != u'':
                    if r[COL_PLACE].isdigit():
                        rank = r[COL_PLACE] + u'.'
                    else:
                        rank = r[COL_PLACE]
                if r[COL_FINISH] is not None:
                    time = (r[COL_FINISH]-r[COL_START]).truncate(3)
                    if ftime is None:
                        ftime = time
                    else:
                        dtime = u'+' + (time - ftime).rawtime(2)
                    rtime = time.rawtime(3)
                if r[COL_100] is not None:
                    sptime = (r[COL_100]-r[COL_START]).truncate(3)
                else:
                    sptime = tod.tod(0)
                stime = sptime.rawtime(3)
                if r[COL_FINISH] is not None:
                    combtimes = u'(' + stime + u')  ' + rtime
            sec.lines.append([rank, rno, rname, rcat, combtimes, dtime, plink])
        ret.append(sec)
        return ret
            
    def analysis_report(self, recurse=False):
        """Return a list of printing sections containing the race result."""
        self.placexfer()
        reta = []
        seca = analysis.section()	# result goes to std section
        seca.heading = u'Event ' + self.evno + u': ' + u' '.join([
                       self.event[u'pref'], self.event[u'info']
                              ]).strip()
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            seca.subheading = substr
        seca.lines = []
        ftime = None
        for r in self.riders:
            rcat = None		# should check ev[u'cate']
            plink = u''
            rno = r[COL_BIB].decode('utf-8')
            rank = None
            rh = self.meet.newgetrider(rno, self.series)
            rname = u''
            inf = None
            if r[COL_INFO] is not '':
                inf = r[COL_INFO]
            if rh is not None:
                rname = rh[u'namestr']
                # consider partners here
                if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                    ph = self.meet.newgetrider(rh[u'note'], self.series)
                    if ph is not None:
                        plink = [u'', u'', ph[u'namestr'] + u' - Pilot', ph[u'ucicode'], u'', u'',u'']
                if self.event[u'cate']:
                    if rh[u'cat']:
                        rcat = rh[u'cat']
                if rh[u'ucicode']:
                    rcat = rh[u'ucicode']   # overwrite by force
            rtime = None
            info = None
            dtime = None
            pre50 = u'Pre 50m: '
            split50 = u'50m: '
            split100 = u'100m: '
            split150 = u'150m: '
            split200 = u'200m: '
            pretime = None
            time50 = None
            time100 = None
            time150 = None
            time200 = None
            avgpre = u'Pre 50m: '
            avg50 = u'50m: '
            avg100 = u'100m: '
            avg150 = u'150m: '
            avg200 = u'200m: '
            speedpre = u'Pre 50m: '
            speedstart = u'Start: '
            speed50 = u'     '
            speed100 = u'100m: '
            speed150 = u'      '
            speed200 = u'Finish: '
            final = None
            splits = None
            avgspeeds = None
            actspeeds = None
            if self.onestart:
                if r[COL_PLACE] != u'':
                    if r[COL_PLACE].isdigit():
                        rank = r[COL_PLACE] + u'.'
                    else:
                        rank = r[COL_PLACE]
                if r[COL_FINISH] is not None:
                    time = (r[COL_FINISH]-r[COL_START]).truncate(3)
                    if ftime is None:
                        ftime = time
                    else:
                        dtime = u'+' + (time - ftime).rawtime(2)
                    rtime = time.rawtime(3)
                    final = (r[COL_FINISH]-r[COL_START]).truncate(3).speedstr(200)
                if r[COL_PRESTART] is not None:
                    pretime = (r[COL_START]-r[COL_PRESTART]).truncate(3)
                    pre50 = u'Pre 50m: ' + pretime.ref2str().strip()
                    avgpre = u'Pre 50m: ' + pretime.speedstr(50)
                if r[COL_50] is not None:
                    time50 = ((r[COL_50]-r[COL_START])-self.delay).truncate(3)
                    rank50 = str(self.splits50.rank(rno)+1)
                    split50 = u'50m: ' + time50.ref2str().strip() + u'(' + rank50 + u')'
                    avg50 = u'50m: ' + time50.speedstr(50)
                if r[COL_100] is not None:
                    time100 = ((r[COL_100]-r[COL_50])+self.delay).truncate(3)
                    rank100 = str(self.splits.rank(rno)+1)
                    split100 = u'100m: ' + time100.ref2str().strip() + u' / ' + (r[COL_100]-r[COL_START]).truncate(3).ref2str().strip() + u'(' + rank100 + u')'
                    avg100 = u'100m: ' + time100.speedstr(50)
                if r[COL_150] is not None:
                    time150 = ((r[COL_150]-r[COL_100])-self.delay).truncate(3)
                    rank150 = str(self.splits150.rank(rno)+1)
                    split150 = u'150m: ' + time150.ref2str().strip() + u' / ' + (r[COL_150]-r[COL_START]).truncate(3).ref2str().strip() + u'(' + rank150 + u')'
                    avg150 = u'150m: ' + time150.speedstr(50)
                if r[COL_FINISH] and r[COL_150] is not None:
                    time200 = ((r[COL_FINISH]-r[COL_150])+self.delay).truncate(3)
                    rank200 = str(r[COL_PLACE])
                    split200 = u'200m: ' + time200.ref2str().strip() + u' / ' + (r[COL_FINISH]-r[COL_START]).truncate(3).ref2str().strip()
                    avg200 = u'200m: ' + time200.speedstr(50)+ u'    '
                if r[COL_INFO] is not '':
                    if r[COL_PRESTART] and r[COL_PRESTARTRW] is not None:
                        speedpre = u'Pre 50m: ' + (r[COL_PRESTARTRW]-r[COL_PRESTART]).truncate(3).speedstr(inf)
                    if r[COL_START] and r[COL_STARTRW] is not None:
                        speedstart = u'Start: ' + (r[COL_STARTRW]-r[COL_START]).truncate(3).speedstr(inf)
                    if r[COL_50] and r[COL_50RW] is not None:
                        speed50 = u'50m: ' + (r[COL_50RW]-r[COL_50]).truncate(3).speedstr(inf)
                    if r[COL_100] and r[COL_100RW] is not None:
                        speed100 = u'100m: ' + (r[COL_100RW]-r[COL_100]).truncate(3).speedstr(inf)
                    if r[COL_150] and r[COL_150RW] is not None:
                        speed150 = u'150m: ' + (r[COL_150RW]-r[COL_150]).truncate(3).speedstr(inf)
                    if r[COL_FINISH] and r[COL_FINISHRW] is not None:
                        speed200 = u'Finish: ' + (r[COL_FINISHRW]-r[COL_FINISH]).truncate(3).speedstr(inf)
                splits = str(pre50) + u'    ' + str(split50) + u'    ' + str(split100) + u'    ' + str(split150) + u'    ' + str(split200)
                avgspeeds = str(avgpre) + u'    ' + str(avg50) + u'      ' + str(avg100) + u'      ' + str(avg150) + u'      ' + str(avg200)
                actspeeds = str(speedpre) + u'    ' + str(speedstart) + u'    ' + str(speed50) + u'    ' + str(speed100) + u'    ' + str(speed150) + u'    ' + str(speed200)
            seca.lines.append([rank, rno, rname, rcat, rtime, dtime, plink])
            seca.lines.append([None, u'Splits', splits, None, None, None, None])
            if self.avgspeedrpt:
                seca.lines.append([None, u'Avg Spds', avgspeeds, None, final, None, None])
            if self.actspeedrpt and r[COL_INFO] is not '':
                seca.lines.append([None, u'Act Spds', actspeeds, None, None, None, None])
        reta.append(seca)
        return reta
    
    def editent_cb(self, entry, col):
        """Shared event entry update callback."""
        if col == u'pref':
            self.event[u'pref'] = entry.get_text().decode('utf-8', 'replace')
        elif col == u'info':
            self.event[u'info'] = entry.get_text().decode('utf-8', 'replace')
        self.update_expander_lbl_cb()

    def update_expander_lbl_cb(self):
        """Update race info expander label."""
        self.info_expand.set_label(u'Race Info : '
                    + self.meet.racenamecat(self.event, 64))

    def clear_rank(self, cb):
        """Run callback once in main loop idle handler."""
        cb(u'')
        return False

    def split_trig(self, sp, t):
        """Register lap trigger."""
        bib = sp.getrider()
        self.splits.insert(t-self.curstart, bib)
        rank = self.splits.rank(bib)
        self.log_split(sp.getrider(), self.curstart, t)
        #self.log_lap(sp.getrider(), sp.lap+1, self.curstart, t, prev)
        sp.intermed(t, 1)	# show 100m split... delay ~2 sec
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            lapstr = strops.num2ord(str(rank + 1)) + ' at 100m'
            self.meet.scbwin.setr1('(' + str(rank + 1) + ')')
            glib.timeout_add_seconds(1, self.clear_rank,
                                        self.meet.scbwin.setr1)
            # announce lap and rank to uSCBsrv
            self.meet.announce.postxt(5, 8, strops.truncpad(lapstr,17)
                                      + ' ' + self.fs.ck.get_text())
        self.meet.timer.arm(timy.CHAN_100)
        self.arm150(self.fs)
        
    def split_trigrw(self, sp, t):
        sp.intermedrw(t, 1)
        self.arm150(self.fs)


    def prestart(self, sp, t):
        if self.prestartfw == 1: 
            sp.prestart(t, 1)
            self.prestartfw = 2
            self.meet.timer.arm(timy.CHAN_FINISH)
            return
        if self.prestartfw == 2:
            sp.prestartrw(t,1)
            self.meet.timer.arm(timy.CHAN_200)
            
    def split_50(self, sp, t):
        bib = sp.getrider()
        self.splits50.insert(t-self.curstart, bib)
        rank = self.splits50.rank(bib)
        self.log_split50(sp.getrider(), self.curstart, t)
        sp.intermed(t, 1)
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            lapstr = strops.num2ord(str(rank + 1)) + ' at 50m'
            self.meet.scbwin.setr1('(' + str(rank + 1) + ')')
            glib.timeout_add_seconds(1, self.clear_rank,
                                        self.meet.scbwin.setr1)
            # announce lap and rank to uSCBsrv
            self.meet.announce.postxt(5, 8, strops.truncpad(lapstr,17)
                                  + ' ' + self.fs.ck.get_text())
        #self.meet.timer.arm(timy.CHAN_PB)
        self.armsplit(self.fs)
           
    def split_50rw(self, sp, t):
        #sp.intermedrw(t, 1)
        self.armsplit(self.fs)

    def split_150(self, sp, t):
        bib = sp.getrider()
        self.splits150.insert(t-self.curstart, bib)
        rank = self.splits150.rank(bib)
        self.log_split150(sp.getrider(), self.curstart, t)
        sp.intermed(t, 1)
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            lapstr = strops.num2ord(str(rank + 1)) + ' at 150m'
            self.meet.scbwin.setr1('(' + str(rank + 1) + ')')
            glib.timeout_add_seconds(1, self.clear_rank,
                                        self.meet.scbwin.setr1)
            # announce lap and rank to uSCBsrv
            self.meet.announce.postxt(5, 8, strops.truncpad(lapstr,17)
                                  + ' ' + self.fs.ck.get_text())
        #self.meet.timer.arm(timy.CHAN_PA)
        self.armfinish(self.fs)
        
    def split_150rw(self, sp, t):
        #sp.intermedrw(t, 1)
        self.armfinish(self.fs)

            
    def fin_trig(self, sp, t):
        """Register finish trigger."""
        if self.finishfw == 1:
            sp.finish(t)
            self.curfin = t
            self.meet.timer.arm(timy.CHAN_FINISH)
            self.finishfw = 2
        elif self.finishfw == 2:
            sp.intermedfrw(t, 1)
            sp.tofinish()
            ri = self.getiter(sp.getrider())
            split = None
            if len(sp.splits) > 0:
                try:
                    pres = sp.splits[0]
                except:
                    pres = None
                try:
                    presrw = sp.splitsrw[0]
                except:
                    presrw = None
                try:
                    strw = sp.splitsrw[1]
                except:
                    strw = None    
                try:
                    spb = sp.splits[1]
                except:
                    spb = None            
                spbrw = None
                try:
                    split = sp.splits[2]
                except:
                    split = None   
                try:
                    splitrw = sp.splitsrw[2]
                except:
                    splitrw = None    
                try:
                    spa = sp.splits[3]
                except:
                    spa = None    
                sparw = None
                try:
                    ftrw = sp.splitsrw[3]
                except:
                    ftrw = None    
            if ri is not None:
                self.settimes(ri, self.curstart, strw, self.curfin, ftrw, pres, presrw, spb, spbrw, split, splitrw, spa, sparw)
            else:
                self.log.warn('Rider not in model, finish time will not be stored.')
            self.log_elapsed(sp.getrider(), self.curstart, self.curfin, split)
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                place = self.riders.get_value(ri, COL_PLACE)
                self.meet.scbwin.setr1('(' + place + ')')
                self.meet.scbwin.sett1(self.fs.ck.get_text())
                dist = self.meet.get_distance(self.distance, self.units)
                if dist is not None:
                    elap = self.curfin - self.curstart
                    spstr = elap.speedstr(dist).strip()
                    glib.timeout_add_seconds(1, self.clear_200_ttb,
                                                self.meet.scbwin,
                                                'Avg:',
                                                spstr.rjust(12))
                    spt=elap.rawspeed(dist).rjust(5) + u'   km/h'
                    msg = unt4.unt4( header=unichr(unt4.DC3) + u'S9',
                                        xx=1,
                                        yy=4,
                                        text=spt)
                    self.log.info(u'sending: ' + msg.text)
                    self.meet.udptimer.sendto(msg.pack(),
                                              (self.meet.udpaddr,6789))
 
                else:
                    glib.timeout_add_seconds(1, self.clear_200_ttb,
                                                self.meet.scbwin)
                self.meet.gemini.set_rank(place)
                self.meet.gemini.set_time((self.curfin - self.curstart).rawtime(2))
                self.meet.gemini.show_brt()
                self.meet.announce.gfx_overlay(2)
                self.meet.announce.gfx_set_title(self.event[u'pref'] + u' '
                                    + self.event[u'info'])
                if self.fs.namevec is not None:
                    self.fs.namevec[0] = u'(' + place + u')'
                    self.fs.namevec[2] = (self.curfin - self.curstart).rawtime(3)
                    self.meet.announce.gfx_add_row(self.fs.namevec)

            # call for a delayed announce...
            glib.idle_add(self.delayed_announce)
            self.meet.delayed_export()
            #self.meet.timer.arm(timy.CHAN_FINISH)
        

    def rftimercb(self, e):
        """Handle rftimer event."""
        if e.refid == '':       # got a trigger
            #return self.starttrig(e)
            return False
        return False

    def timercb(self, e):
        """Handle a timer event."""
        chan = timy.chan2id(e.chan)
        if self.timerstat == 'armstart':
            if chan == timy.CHAN_200:	# 200m line
                self.torunning(self.fs, e)
            if chan == timy.CHAN_FINISH:
                self.prestart(self.fs, e)
        elif self.timerstat == 'running':
            if chan == timy.CHAN_200:
                stat = self.fs.getstatus()
                if stat == 'arm50':
                    self.torunningrw(self.fs, e)
            if chan == timy.CHAN_PB:
                stat = self.fs.getstatus()
                if stat == 'arm50':
                    self.split_50(self.fs, e)
                if stat == 'armint':
                    self.split_50rw(self.fs, e)
            if chan == timy.CHAN_100:	# 100m line
                stat = self.fs.getstatus()
                if stat == 'armint':
                    self.split_trig(self.fs, e)
                if stat == 'arm150':
                    self.split_trigrw(self.fs, e)
                # else ignore spurious 100m trig
            if chan == timy.CHAN_PA:
                stat = self.fs.getstatus()
                if stat == 'arm150':
                    self.split_150(self.fs, e)
                if stat == 'armfin':
                    self.split_150rw(self.fs, e)
            if chan == timy.CHAN_FINISH:	# finish line
                stat = self.fs.getstatus()
                if stat in ['armfin', 'arm50', 'armint', 'arm150']:
                    self.fin_trig(self.fs, e)
                elif stat == 'finish':
                    self.fin_trig(self.fs, e)
        return False

    def timeout(self):
        """Update scoreboard and respond to timing events."""
        if not self.winopen:
            return False
        now = tod.tod('now')
        if self.fs.status in ['running', 'arm50', 'armint', 'arm150', 'armfin']:
            self.fs.runtime(now - self.lstart)
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                self.meet.scbwin.sett1(self.fs.ck.get_text())
                elapstr = (now - self.lstart).rawtime(1).rjust(4) + ' '
                self.meet.gemini.set_time(elapstr)
                self.meet.gemini.show_brt()
        return True

    def show_200_ttb(self, scb):
        """Display time to beat."""
        if len(self.results) > 0:
            scb.setr2(u'Fastest:')
            scb.sett2(self.results[0][0].timestr(3))
        return False

    def clear_200_ttb(self, scb, r2=u'', t2=u''):
        """Clear time to beat."""
        scb.setr2(r2)
        scb.sett2(t2)
        return False

    def torunning(self, sp, t, lst=None):
        """Set timer running."""
        if self.fs.status == 'armstart':
            self.fs.start(t)
            self.curstart = t
            if lst is None:
                lst = tod.tod('now')
            self.lstart = lst
            self.timerstat = 'running'
            self.onestart = True
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                glib.timeout_add_seconds(1, self.show_200_ttb,
                        self.meet.scbwin)
            self.meet.timer.arm(timy.CHAN_200)
            self.arm50(self.fs)
    
    def torunningrw(self, sp,t):
            sp.intermedrw(t, 1)
            self.arm50(self.fs)
			
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
        nr=[bib, '', '', '', '', istr, '', None, None, None, None, None, None, None, None, None, None, None, None]
        ri = self.getrider(bib)
        if ri is None:  # adding a new record
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            return self.riders.append(nr)
        else:           # patch 'seed' on an existing record
            ri[COL_SEED] = istr
            return True         # not sure about this one?

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
        for t in self.results:
            bib = t[0].refid
            if t[0] > tod.FAKETIMES['max']:
                if t[0] == tod.FAKETIMES['dsq']:
                    place = 'dsq'
                elif t[0] == tod.FAKETIMES['dns']:
                    place = 'dns'
                elif t[0] == tod.FAKETIMES['dnf']:
                    place = 'dnf'
                #else:
                    #place = 'dnf' # leave to naturally sort
            else:
                place = self.results.rank(bib)+1
            i = self.getiter(bib)
            if i is not None:
                if place == 'comment':	# superfluous but ok
                    place = self.riders.get_value(i, COL_COMMENT)
                self.riders.set_value(i, COL_PLACE, str(place))
                self.riders.swap(self.riders.get_iter(count), i)
                count += 1
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

    def settimes(self, iter, st=None, strw=None, ft=None, ftrw=None, pres=None, presrw=None, spb=None, spbrw=None, split=None, splitrw=None,
                             spa=None, sparw=None, doplaces=True, comment=None):
        """Transfer race times into rider model."""
        bib = self.riders.get_value(iter, COL_BIB)
        # clear result for this bib
        self.results.remove(bib)
        self.prestart50.remove(bib)
        self.splits50.remove(bib)
        self.splits.remove(bib)
        self.splits150.remove(bib)
        # assign tods
        self.riders.set_value(iter, COL_START, st)
        self.riders.set_value(iter, COL_STARTRW, strw)
        self.riders.set_value(iter, COL_FINISH, ft)
        self.riders.set_value(iter, COL_FINISHRW, ftrw)
        self.riders.set_value(iter, COL_PRESTART, pres)
        self.riders.set_value(iter, COL_PRESTARTRW, presrw)
        self.riders.set_value(iter, COL_50, spb)
        self.riders.set_value(iter, COL_50RW, spbrw)
        self.riders.set_value(iter, COL_100, split)
        self.riders.set_value(iter, COL_100RW, splitrw)
        self.riders.set_value(iter, COL_150, spa)
        self.riders.set_value(iter, COL_150RW, sparw)
        # save result
        if st is None:
            st = tod.ZERO
        if ft is not None:
            last100 = None
            if pres is not None:
                self.prestart50.insert(st - pres, bib)
            if spb is not None:
                self.splits50.insert(spb - st, bib)
            if split is not None:
                self.splits.insert(split - st, bib) # save first 100 split
                last100 = ft - split	# and prepare to record second 100
            if spa is not None:
                self.splits150.insert(spa - st, bib)
            self.results.insert(ft - st, last100, bib)
        else:	# DNF/etc
            self.results.insert(comment, None, bib)
        # copy annotation into model if provided, or clear
        if comment:
            self.riders.set_value(iter, COL_COMMENT, comment)
        else:
            self.riders.set_value(iter, COL_COMMENT, '')
        # if reqd, do places
        if doplaces:
            self.placexfer()

    def armstart(self):
        """Arm timer for start trigger."""
        if self.timerstat == 'armstart':
            self.toload()
        elif self.timerstat in ['load', 'idle']:
            self.toarmstart()

    def armsplit(self, sp, cid=timy.CHAN_100):
        """Arm timer for a 100m split."""
        if self.timerstat == 'running':
            if sp.getstatus() == 'running':
                sp.toarmint()
                self.meet.timer.arm(cid)
                self.meet.timer.arm(timy.CHAN_FINISH) # pessimistic arm
            elif sp.getstatus() == 'arm100m':
                sp.torunning()
                self.meet.timer.dearm(cid)
                self.meet.timer.dearm(timy.CHAN_FINISH)# assume manual override

    def arm50(self, sp, cid=timy.CHAN_PB):
        """Arm timer for a 50m split."""
        if self.timerstat == 'running':
            if sp.getstatus() == 'running':
                sp.toarm50()
                self.meet.timer.arm(cid)
                self.meet.timer.arm(timy.CHAN_PB) # pessimistic arm
            elif sp.getstatus() == 'arm50m':
                sp.torunning()
                self.meet.timer.dearm(cid)
                self.meet.timer.dearm(timy.CHAN_100)# assume manual override              


    def arm150(self, sp, cid=timy.CHAN_PA):
        """Arm timer for a 150m split."""
        if self.timerstat == 'running':
            if sp.getstatus() == 'running':
                sp.toarm150()
                self.meet.timer.arm(cid)
                self.meet.timer.arm(timy.CHAN_FINISH) # pessimistic arm
            elif sp.getstatus() == 'arm150m':
                sp.torunning()
                self.meet.timer.dearm(cid)
                self.meet.timer.dearm(timy.CHAN_FINISH)# assume manual override         
                
    #!def trace(self, rider, line):
        #!"""Append the suppliedmessage to rider trace."""
        #!if rider not in self.traces:
            #!self.traces[rider] = u''
        #!self.traces[rider] += '\n' + line

    def abortrider(self, sp):
        """Abort the selected lane."""
        if sp.getstatus() not in ['idle', 'finish']:
            bib = sp.getrider()
            ri = self.getiter(bib)
            if ri is not None:
                self.settimes(ri, st=self.curstart, comment='dnf')
            sp.tofinish()
            self.meet.timer_log_msg(bib, '- Abort -')
            #self.toidle() -> or allow manual override?
            glib.idle_add(self.delayed_announce)

    def falsestart(self):
        """Register false start."""
        if self.timerstat == 'running':
            if self.fs.getstatus() not in ['idle', 'finish']:
                self.fs.toload()
                bib = fs.getrider()
                self.meet.timer_log_msg(self.fs.getrider(), '- False start -')
                if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                    self.meet.scbwin.setr1('False')
                    self.meet.scbwin.sett1('Start')
            self.toidle(idletimers=False)
        elif self.timerstat == 'armstart':
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
                self.meet.scbwin.sett1('            ')
                self.meet.scbwin.sett2('            ')
            self.toload()

    def armfinish(self, sp, cid=timy.CHAN_FINISH):
        """Arm timer for finish trigger."""
        if self.timerstat == 'running':
            if sp.getstatus() in ['running', 'finish']:
                if sp.getstatus() == 'finish':
                    bib = sp.getrider()
                    self.meet.timer_log_msg(sp.getrider(), '- False Finish -')
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
        self.toidle(idletimers=False)
        self.prestartfw = 1
        self.startfw = 1
        self.split50fw = 1
        self.split100fw = 1
        self.split150fw = 1
        self.finishfw = 1

    def fmtridername(self, tp):
        """Prepare rider name for display on scoreboard."""
        name_w = self.meet.scb.linelen - 9
        bib = tp.getrider().strip()
        if bib != '':
            name = '[New Rider]'
            r = self.getrider(bib)
            if r is not None and r[COL_BIB] != '':
                first = r[COL_FIRSTNAME].decode('utf-8')
                last = r[COL_LASTNAME].decode('utf-8')
                club = r[COL_CLUB].decode('utf-8')
                name = strops.fitname(first,
                                      last,
                                      name_w)
                tp.namevec = [bib, strops.resname(first, last, club), '']
            return ' '.join([strops.truncpad(r[COL_BIB], 3, 'r'),
                             strops.truncpad(name, name_w),
                             strops.truncpad(club, 4, 'r')])
        else:
            tp.namevec = None
            return ''
        
    def showtimerwin(self):
        """Show timer window on scoreboard."""
        self.meet.scbwin = None
        self.meet.scbwin = scbwin.scbtt(self.meet.scb,
                                self.meet.racenamecat(self.event),
                                self.fmtridername(self.fs))
        self.meet.gemini.reset_fields()
        self.meet.gemini.set_bib(self.fs.getrider())
        self.meet.gemini.show_brt()
        self.meet.announce.gfx_overlay(2)
        self.meet.announce.gfx_set_title(self.event[u'pref'] + u' '
                            + self.event[u'info'])
        if self.fs.namevec is not None:
            self.meet.announce.gfx_add_row(self.fs.namevec)

        self.timerwin = True
        self.meet.scbwin.reset()

    def toarmstart(self):
        """Set timer to arm start."""
        if self.fs.status == 'load':
            self.fs.toarmstart()
            self.timerstat = 'armstart'
            self.curstart = None
            self.curfin = None
            self.lstart = None
            self.meet.timer.arm(timy.CHAN_200)
            self.meet.timer.arm(timy.CHAN_FINISH)
            self.showtimerwin()
            self.meet.delayimp('0.01')
            if self.fs.status == 'armstart':
                bib = self.fs.getrider()
                if bib not in self.traces:
                    self.traces[bib] = []
                self.fslog = loghandler.traceHandler(self.traces[bib])
                logging.getLogger().addHandler(self.fslog)
                self.meet.scbwin.sett1('       0.0     ')
                nstr = self.fs.biblbl.get_text()
                if not self.onestart:
                    self.meet.timer.printline(self.meet.racenamecat(self.event))
                self.meet.timer_log_msg(bib, nstr)
                self.meet.gemini.set_bib(bib)
                self.meet.gemini.set_time(' 0.0 ')
                self.meet.gemini.set_rank('')
                self.meet.gemini.show_brt()
            glib.idle_add(self.delayed_announce)
            self.prestartfw = 1
            self.startfw = 1
            self.split50fw = 1
            self.split100fw = 1
            self.split150fw = 1
            self.finishfw = 1

    def toidle(self, idletimers=True):
        """Set timer to idle state."""
        if self.fslog is not None:
            logging.getLogger().removeHandler(self.fslog)
            self.fslog = None
        if idletimers:
            self.fs.toidle()
        self.timerstat = 'idle'
        self.meet.delayimp('2.00')
        self.curstart = None
        self.lstart = None
        for i in range(0,8):
            self.meet.timer.dearm(i)
        if not self.onestart:
            pass
        self.fs.grab_focus()

    def lanelookup(self, bib=None):
        """Prepare name string for timer lane."""
        r = self.getrider(bib)
        if r is None:
            if self.meet.get_clubmode():        # fill in starters
                self.log.warn(u'Adding non-starter: ' + repr(bib))
                self.addrider(bib)
                r = self.getrider(bib)
            else:       # 'champs' mode
                return None
        rtxt = u'[New Rider]'
        if r is not None and (r[COL_FIRSTNAME] != u''
                              or r[COL_LASTNAME] != u''):
            rtxt = r[COL_FIRSTNAME] + u' ' + r[COL_LASTNAME]
            if r[3] != u'':
                rtxt += u'(' + r[3] + u')'
        return rtxt

    def bibent_cb(self, entry, tp):
        """Bib entry callback."""
        bib = entry.get_text().strip()
        if bib != u'' and bib.isalnum():
            nstr = self.lanelookup(bib)
            if nstr is not None:
                tp.biblbl.set_text(nstr)
                if tp.status == 'idle':
                    tp.toload()
                if self.timerstat == 'running':
                    tp.start(self.curstart)
            else:
                self.log.warn(u'Ignoring non-starter: ' + repr(bib))
                tp.toidle()
        else:
            tp.toidle()
    
    def time_context_menu(self, widget, event, data=None):
        """Popup menu for result list."""
        self.context_menu.popup(None, None, None, event.button,
                                event.time, selpath)

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
            ftod = self.riders.get_value(i, COL_FINISH)
            ft = ''
            if ftod is not None:
                ft = ftod.timestr()
            ret = uiutil.edit_times_dlg(self.meet.window,st,ft)
            if ret[0] == 1:
                stod = tod.str2tod(ret[1])
                ftod = tod.str2tod(ret[2])
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
        self.meet.timer_log_msg(bib, u'- Time Cleared -')

    def log_split(self, bib, start, split):
        """Print 100 split log."""
        self.meet.timer_log_straight(bib, u'100', split-start, 3)
        
    def log_split50(self, bib, start, split):
        """Print 50 split log."""
        self.meet.timer_log_straight(bib, u'50', split-start, 3)
        
    def log_split150(self, bib, start, split):
        """Print 150 split log."""
        self.meet.timer_log_straight(bib, u'150', split-start, 3)
		
    def log_elapsed(self, bib, start, finish, split=None, manual=False):
        """Print elapsed log info."""
        if manual:
            self.meet.timer_log_msg(bib, u'- Manual Adjust -')
        self.meet.timer_log_straight(bib, u'ST', start)
        self.meet.timer_log_straight(bib, u'FIN', finish)
        if split is not None:
            self.meet.timer_log_straight(bib, u'L100', finish - split, 3)
        self.meet.timer_log_straight(bib, u'TIME', finish - start, 3)

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

        self.log = logging.getLogger('f200')
        self.log.setLevel(logging.DEBUG)
        self.log.debug(u'Creating new f200 event: ' + self.evno)

        # properties
        self.distance = 200
        self.units = 'metres'
        self.autoarm = True
        self.avgspeedrpt = True
        self.actspeedrpt = True

        # race run time attributes
        self.onestart = False
        self.readonly = not ui
        self.winopen = ui
        self.timerwin = False
        self.timerstat = 'idle'
        self.curstart = None
        self.curfin = None
        self.lstart = None
        self.results = f200ranks('FIN')
        self.splits = tod.todlist('100')
        self.splits50 = tod.todlist('50')
        self.splits150 = tod.todlist('150')
        self.prestart50 = tod.todlist('PRE 50')
        self.autospec = event[u'auto']
        self.delay = tod.tod(0.2011)
        self.inomnium = False
        self.wirelessstart = False
        self.wireless50 = True
        self.wireless100 = False
        self.wireless150 = True
        self.wirelessfinish = False
        self.prestartfw = 1
        self.startfw = 1
        self.split50fw = 1
        self.split100fw = 1
        self.split150fw = 1
        self.finishfw = 1
        self.seedsrc = 1        # default seeding is by rank in last round

        self.riders = gtk.ListStore(gobject.TYPE_STRING,   # 0 bib
                                    gobject.TYPE_STRING,   # 1 firstname
                                    gobject.TYPE_STRING,   # 2 lastname
                                    gobject.TYPE_STRING,   # 3 club
                                    gobject.TYPE_STRING,   # 4 Comment
                                    gobject.TYPE_STRING,   # 5 seed
                                    gobject.TYPE_STRING,   # 6 place
                                    gobject.TYPE_PYOBJECT, # 7 Start
                                    gobject.TYPE_PYOBJECT, # 8 Start Rear Wheel
                                    gobject.TYPE_PYOBJECT, # 9 Finish
                                    gobject.TYPE_PYOBJECT, # 10 Finish Rear Wheel
                                    gobject.TYPE_PYOBJECT, # 11 Prestart 50m
                                    gobject.TYPE_PYOBJECT, # 12 Prestart Rear Wheel
                                    gobject.TYPE_PYOBJECT, # 13 50m
                                    gobject.TYPE_PYOBJECT, # 14 50m Rear Wheel
                                    gobject.TYPE_PYOBJECT, # 15 100m
                                    gobject.TYPE_PYOBJECT, # 16 100m Rear Wheel
                                    gobject.TYPE_PYOBJECT, # 17 150m
                                    gobject.TYPE_PYOBJECT) # 18 150m Rear Wheel

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
        b.get_object('race_type').set_text('Flying 200')
        self.traces = {}

        # Timer Pane
        mf = b.get_object('race_timer_pane')
        self.fs = timerpane.timerpane('Timer')
        self.fs.uport = self.meet.udptimer
        self.fs.uaddr = self.meet.udpaddr
        self.fs.bibent.connect('activate', self.bibent_cb, self.fs)
        self.fs.hide_laps()
        self.fslog = None
        mf.pack_start(self.fs.frame)

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
        uiutil.mkviewcoltxt(t, 'Info', COL_INFO, self.editcol_db)
        uiutil.mkviewcoltxt(t, 'Seed', COL_SEED, self.editcol_db)
        uiutil.mkviewcoltod(t, '200m/Last 100m', cb=self.todstr)
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

