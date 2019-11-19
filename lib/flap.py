
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

"""Flying lap

This module provides a class 'flap' which implements the 'race'
interface and manages data, timing and scoreboard for the
UCI flying lap time trial.

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
from metarace import uiutil
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import timerpane
from metarace import printing

# config version string
EVENT_ID = 'trackflap-1.3'

# startlist model columns
COL_BIB = 0
COL_FIRSTNAME = 1
COL_LASTNAME = 2
COL_CAT = 2
COL_CLUB = 3
COL_INRACE = 3		# mix with comment for dns/dnf etc
COL_COMMENT = 4	
COL_SEED = 5
COL_PLACE = 6
COL_START = 7
COL_FINISH = 8
COL_200 = 9		# 200m split ToD

# scb function key mappings (also trig announce)
key_reannounce = 'F4'                # (+CTRL) calls into delayed announce
key_startlist = 'F3'                 # startlist
key_results = 'F4'                   # recalc/show result window
key_timerwin = 'F6'                 # re-show timing window

# timing function key mappings
key_armstart = 'F5'                  # arm for start impulse
key_armsplit = 'F7'		     # de/arm 100m
key_armfinish = 'F9'                 # de/arm for finish impulse

# extended function key mappings
key_reset = 'F5'                     # + ctrl for clear/abort
key_falsestart = 'F6'		     # + ctrl for false start
key_abort = 'F7'		     # + ctrl abort A

class f200ranks(object):
    """Helper class for managing flying 200 ranks."""
    def __init__(self, lbl=''):
        self.__label = lbl
        self.__store = []

    def __iter__(self):
        return self.__store.__iter__()

    def __len__(self):
        return len(self.__store)

    def __getitem__(self, key):
        return self.__store[key]

    def rank(self, bib, series=''):
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

    def remove(self, bib, series=''):
        i = 0
        while i < len(self.__store):
            if (self.__store[i][0].refid == bib
                  and self.__store[i][0].index == series):
                del self.__store[i]
            else:
                i += 1

    def insert(self, t, s=None, bib=None, series=''):
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

class flap(object):
    """Data handling for flying lap."""

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
        name_w = self.meet.scb.linelen - 11
        pcount = 0
        rcount = 0
        for r in self.riders:
            rcount += 1
            if r[COL_PLACE] is not None and r[COL_PLACE] != '':
                pcount += 1
                plstr = r[COL_PLACE].decode('utf-8')
                if plstr.isdigit():
                    if int(plstr) > 20:
                        break
                    plstr = plstr + u'.'
                fmtplaces.append([plstr,
                        r[COL_BIB].decode('utf-8'),
                        strops.fitname(r[COL_FIRSTNAME].decode('utf-8'),
                        r[COL_LASTNAME].decode('utf-8'), name_w),
                        r[COL_CLUB].decode('utf-8')])
        FMT = [(3,u'l'), (3,u'r'), u' ',(name_w, u'l'), (4,u'r')]
        evtstatus = u'Standings'
        if rcount > 0 and pcount == rcount:
            evtstatus = u'Result'
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                              head=self.meet.racenamecat(self.event),
                              subhead=evtstatus,
                              coldesc=FMT,
                              rows=fmtplaces)
        self.meet.scbwin.reset()

    def todstr(self, col, cr, model, iter, data=None):
        """Format tod into text for listview."""
        ft = model.get_value(iter, COL_FINISH)
        if ft is not None:
            sp = model.get_value(iter, COL_200)
            st = model.get_value(iter, COL_START)
            if st is None:
                st = tod.tod(0)
            if st == tod.tod(0):
                cr.set_property('style', pango.STYLE_OBLIQUE)
            else:
                cr.set_property('style', pango.STYLE_NORMAL)
            mstr = (ft - st).rawtime(3).rjust(6)
            sstr = ''.rjust(7)
            if sp is not None:
                sstr = '/' + (ft - sp).rawtime(3).ljust(6)
            cr.set_property('text', mstr+sstr)
        else:
            cr.set_property('text', '')

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        self.results.clear()
        self.splits.clear()

        defautoarm = 'Yes'
        self.seedsrc = 1 # for autospec loads, fetch seed from the rank col

        cr = ConfigParser.ConfigParser({'startlist':'',
                                        'id':EVENT_ID,
					'start':'',
                                        'lstart':'',
                                        'fsbib':'',
                                        'fsstat':'idle',
                                        'showinfo':'No',
                                        'distance':'250',
                                        'autospec':'',
                                        'inomnium':'No',
					'distunits':'metres',
                                        'autoarm':defautoarm})
        cr.add_section('event')
        cr.add_section('riders')
        if os.path.isfile(self.configpath):
            self.log.debug('Attempting to read config from '
                               + repr(self.configpath))
            cr.read(self.configpath)

        #self.autospec = cr.get('event', 'autospec')
        self.distance = strops.confopt_dist(cr.get('event', 'distance'))
        self.units = strops.confopt_distunits(cr.get('event', 'distunits'))
        self.autoarm = strops.confopt_bool(cr.get('event', 'autoarm'))
        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))
        self.inomnium = strops.confopt_bool(cr.get('event', 'inomnium'))
        if self.inomnium:
            # SWITCH: Event is part of an omnium, make any req'd overrides
            self.seedsrc = 1    # read seeding from omnium ranking

        # re-load starters/results
        self.onestart = False
        for r in cr.get('event', 'startlist').split():
            nr=[r, '', '', '', '', '', '', None, None, None]
            co = ''
            st = None
            ft = None
            sp = None
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
                if len(ril) >= 6:		# 200m ToD
                    sp = tod.str2tod(ril[5])
            dbr = self.meet.rdb.getrider(r, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            nri = self.riders.append(nr)
            self.settimes(nri, st, ft, sp, doplaces=False, comment=co)
        self.placexfer()

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
            self.log.error('Event configuration mismatch: '
                           + repr(eid) + ' != ' + repr(EVENT_ID))
            #self.readonly = True

    def saveconfig(self):
        """Save race to disk."""
        if self.readonly:
            self.log.error('Attempt to save readonly ob.')
            return
        cw = ConfigParser.ConfigParser()
        cw.add_section('event')

        # save basic race properties
        cw.set('event', 'distance', self.distance)
        cw.set('event', 'distunits', self.units)
        cw.set('event', 'autospec', self.autospec)
        cw.set('event', 'autoarm', self.autoarm)
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
        cw.add_section('riders')

        # save out all starters
        for r in self.riders:
            # place is saved for info only
            slice = [r[COL_COMMENT], r[COL_SEED], r[COL_PLACE]]
            tl = [r[COL_START], r[COL_FINISH], r[COL_200]]
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
                rno = r[COL_BIB].decode('utf-8')
                rh = self.meet.newgetrider(rno, self.series)
                rname = u''
                uci = None
                if rh is not None:
                    rname = rh[u'namestr']
                    if rh[u'ucicode']: uci = rh[u'ucicode']
                hlist.append([str(count)+'.1', rno, rname, uci])
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
            rec.extend([heat, r[1], r[2], None])
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
        sec.set_single()

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

    def get_startlist(self):
        """Return a list of bibs in the rider model."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ' '.join(ret)

    def delayed_announce(self):
        """Initialise the announcer's screen after a delay."""
        if self.winopen:
            self.meet.announce.clrall()
            self.meet.ann_title(' '.join([
                  u'Event', self.evno, u':',
                  self.event[u'pref'], self.event[u'info']]))

            self.meet.announce.linefill(1, '_')
            self.meet.announce.linefill(7, '_')

            # fill in front straight
            fbib = self.fs.getrider()
            if fbib is not None and fbib != '':
                r = self.getrider(fbib)
                if r is not None:
                    clubstr = ''
                    if r[COL_CLUB] != '' and len(r[COL_CLUB]) <= 3:
                        clubstr = '(' + r[COL_CLUB] + ')'
                    namestr = strops.fitname(r[COL_FIRSTNAME],
                                             r[COL_LASTNAME], 24, trunc=True)
                    placestr = '   ' # 3 ch
                    if r[COL_PLACE] != '':
                        placestr = strops.truncpad(r[COL_PLACE] + '.', 3)
                    bibstr = strops.truncpad(r[COL_BIB], 3, 'r')
                    tmstr = ''
                    et = None
                    if r[COL_START] is not None and r[COL_FINISH] is not None:
                        et = (r[COL_FINISH] - r[COL_START]).truncate(3)
                        tmstr = ' Lap: ' + et.rawtime(3).rjust(12)
                    cmtstr = ''
                    if et is not None:
                        cmtstr = strops.truncpad('Average: ' + et.speedstr(250), 38, 'r')
                    elif r[COL_COMMENT] is not None and r[COL_COMMENT] != '':
                        cmtstr = strops.truncpad(
                                 '[' + r[COL_COMMENT].strip() + ']', 38, 'r')
                    self.meet.announce.postxt(3,0,'        Current Rider')
                    self.meet.announce.postxt(4,0,' '.join([placestr, bibstr,
                                                         namestr, clubstr]))
                    self.meet.announce.postxt(5,0,strops.truncpad(tmstr, 38, 'r'))
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

                clubstr = ''
                if r[COL_CLUB] != '' and len(r[COL_CLUB]) <= 3:
                    clubstr = ' (' + r[COL_CLUB] + ')'
 
                namestr = strops.truncpad(strops.fitname(r[COL_FIRSTNAME],
                              r[COL_LASTNAME], 22-len(clubstr),
                                  trunc=True)+clubstr, 22, elipsis=False)
                placestr = '   ' # 3 ch
                if r[COL_PLACE] != '':
                    placestr = strops.truncpad(r[COL_PLACE] + '.', 3)
                bibstr = strops.truncpad(r[COL_BIB], 3, 'r')
                tmstr = '       ' # 7 ch
                if r[COL_START] is not None and r[COL_FINISH] is not None:
                    tmstr = strops.truncpad(
                           (r[COL_FINISH] - r[COL_START]).rawtime(3), 7, 'r')
                self.meet.announce.postxt(curline, posoft, ' '.join([
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
            rcount += 1		# count all rders in model
            rno = r[COL_BIB].decode('utf-8')
            rh = self.meet.newgetrider(rno, self.series)
            rank = None
            rname = u''
            if rh is not None:
                rname = rh[u'namestr']
            rtime = None
            rcat = None
            if self.event[u'cate']:
                if rh is not None and rh[u'cat']:
                    rcat = rh[u'cat']
            if rh is not None and rh[u'ucicode']:
                rcat = rh[u'ucicode']	# overwrite by force
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
                    rtime = time.rawtime(3)

            sec.lines.append([rank, rno, rname, rcat, rtime, dtime])

        # section subheading
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
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

    def split_trig(self, sp, t):
        """Register lap trigger."""
        bib = sp.getrider()
        self.splits.insert(t-self.curstart, bib)
        rank = self.splits.rank(bib)
        self.log_split(sp.getrider(), self.curstart, t)
        #self.log_lap(sp.getrider(), sp.lap+1, self.curstart, t, prev)
        sp.intermed(t, 1)
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            lapstr = strops.num2ord(str(rank + 1)) + ' at 50m'
            #self.meet.scbwin.setr1('(' + str(rank + 1) + ')')
            #glib.timeout_add_seconds(1, self.clear_rank,
                                        #self.meet.scbwin.setr1)
            # announce lap and rank to uSCBsrv
            self.meet.announce.postxt(5, 8, strops.truncpad(lapstr,17)
                                      + ' ' + self.fs.ck.get_text())
        if self.autoarm:	# ready for finish...
            self.armfinish(self.fs)

    def fin_trig(self, sp, t):
        """Register finish trigger."""
        sp.finish(t)
        ri = self.getiter(sp.getrider())
        split = None
        if len(sp.splits) > 0:
            split = sp.splits[0]
        if ri is not None:
            self.settimes(ri, self.curstart, t, split)
        else:
            self.log.warn('Rider not in model, finish time will not be stored.')
        self.log_elapsed(sp.getrider(), self.curstart, t, split)
        if self.timerwin and type(self.meet.scbwin) is scbwin.scbtt:
            place = self.riders.get_value(ri, COL_PLACE)
            self.meet.scbwin.setr1('(' + place + ')')
            self.meet.scbwin.sett1(self.fs.ck.get_text())
            dist = self.meet.get_distance(self.distance, self.units)
            if dist is not None:
                spstr = (t - self.curstart).speedstr(dist).strip()
                glib.timeout_add_seconds(1, self.clear_200_ttb,
                                            self.meet.scbwin,
                                            'Avg:',
                                            spstr.rjust(12))
            else:
                glib.timeout_add_seconds(2, self.clear_200_ttb,
                                            self.meet.scbwin)
            self.meet.gemini.set_rank(place)
            self.meet.gemini.set_time((t - self.curstart).rawtime(2))
            self.meet.gemini.show_brt()
        # call for a delayed announce...
        glib.idle_add(self.delayed_announce)
        self.meet.delayed_export()

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
            if chan == timy.CHAN_FINISH:	# start line
                self.torunning(e)
        elif self.timerstat == 'running':
            if chan == timy.CHAN_200:	# 200m line
                stat = self.fs.getstatus()
                if stat == 'armint':
                    self.split_trig(self.fs, e)
                    ## else ignore spurious 200m trig
            elif chan == timy.CHAN_FINISH:	# finish line
                stat = self.fs.getstatus()
                if stat == 'armfin':
                    self.fin_trig(self.fs, e)
        return False

    def timeout(self):
        """Update scoreboard and respond to timing events."""
        if not self.winopen:
            return False
        now = tod.tod('now')
        if self.fs.status in ['running', 'armint', 'armfin']:
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
            scb.setr2('Fastest:')
            scb.sett2(self.results[0][0].timestr(3))
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
        self.curstart = st
        if lst is None:
            lst = tod.tod('now')
        self.lstart = lst
        self.timerstat = 'running'
        self.onestart = True
        if self.autoarm:
            self.armsplit(self.fs)
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
        if ri is None:  # adding a new record
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            return self.riders.append(nr)
        else:           # patch 'seed' on an existing record
            if not ri[COL_SEED] and istr:
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
            if t[0] == tod.FAKETIMES['dsq']:
                #place = 'comment'      # no need for lookup now
                place = 'dsq'
            else:
                place = self.results.rank(bib)+1
            i = self.getiter(bib)
            if i is not None:
                if place == 'comment':
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

    def settimes(self, iter, st=None, ft=None, split=None,
                             doplaces=True, comment=None):
        """Transfer race times into rider model."""
        bib = self.riders.get_value(iter, COL_BIB)
        # clear result for this bib
        self.results.remove(bib)
        self.splits.remove(bib)
        # assign tods
        self.riders.set_value(iter, COL_START, st)
        self.riders.set_value(iter, COL_FINISH, ft)
        self.riders.set_value(iter, COL_200, split)
        # save result
        if st is None:
            st = tod.ZERO
        if ft is not None:
            last200 = None
            if split is not None:
                self.splits.insert(split - st, bib) # save first 50 split
                last200 = ft - split	# and prepare to record second 200
            self.results.insert(ft - st, last200, bib)
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

    def armsplit(self, sp, cid=timy.CHAN_200):
        """Arm timer for a 50m/200m split."""
        if self.timerstat == 'running':
            if sp.getstatus() == 'running':
                sp.toarmint()
                self.meet.timer.arm(cid)
            elif sp.getstatus() == 'armint':
                sp.torunning()
                self.meet.timer.dearm(cid)
                self.meet.timer.dearm(timy.CHAN_FINISH)# assume manual override

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
                self.meet.timer_log_msg(self.fs.getrider(),
                                        '- False start -')
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
            if sp.getstatus() in ['running', 'armint', 'finish']:
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
        self.toidle(idletimers=False)

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
                                self.fmtridername(self.fs),
                                '')
        self.meet.gemini.reset_fields()
        self.meet.gemini.set_bib(self.fs.getrider())
        self.meet.gemini.show_brt()
        self.timerwin = True
        self.meet.scbwin.reset()

    def toarmstart(self):
        """Set timer to arm start."""
        if self.fs.status == 'load':
            self.fs.toarmstart()
            self.timerstat = 'armstart'
            self.curstart = None
            self.lstart = None
            self.meet.timer.arm(timy.CHAN_FINISH)
            self.showtimerwin()
            if self.fs.status == 'armstart':
                self.meet.scbwin.sett1('       0.0     ')
                nstr = self.fs.biblbl.get_text()
                if not self.onestart:
                    self.meet.timer.printline(self.meet.racenamecat(self.event))
                self.meet.timer_log_msg(self.fs.getrider(), nstr)
                self.meet.gemini.set_bib(self.fs.getrider())
                self.meet.gemini.set_time(' 0.0 ')
                self.meet.gemini.set_rank('')
                self.meet.gemini.show_brt()
            glib.idle_add(self.delayed_announce)

    def toidle(self, idletimers=True):
        """Set timer to idle state."""
        if idletimers:
            self.fs.toidle()
        self.timerstat = 'idle'
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
                self.log.warn('Adding non-starter: ' + repr(bib))
                self.addrider(bib)
                r = self.getrider(bib)
            else:       # 'champs' mode
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
            else:
                self.log.warn('Ignoring non-starter: ' + repr(bib))
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
        self.meet.timer_log_msg(bib, '- Time Cleared -')

    def log_split(self, bib, start, split):
        """Print 50m split log."""
        self.meet.timer_log_straight(bib, '50', split-start, 3)
        
    def log_elapsed(self, bib, start, finish, split=None, manual=False):
        """Print elapsed log info."""
        if manual:
            self.meet.timer_log_msg(bib, '- Manual Adjust -')
        self.meet.timer_log_straight(bib, 'ST', start)
        self.meet.timer_log_straight(bib, 'FIN', finish)
        if split is not None:
            self.meet.timer_log_straight(bib, 'L200', finish - split, 3)
        self.meet.timer_log_straight(bib, 'TIME', finish - start, 3)

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

        self.log = logging.getLogger('flap')
        self.log.setLevel(logging.DEBUG)
        self.log.debug('Creating new flap event: ' + str(self.evno))

        # properties
        self.distance = 250
        self.units = 'metres'
        self.autoarm = True

        # race run time attributes
        self.onestart = False
        self.readonly = not ui
        self.winopen = ui
        self.timerwin = False
        self.timerstat = 'idle'
        self.curstart = None
        self.lstart = None
        self.results = f200ranks('FIN')
        self.splits = tod.todlist('50')
        self.autospec = event[u'auto']
        self.inomnium = False
        self.seedsrc = 1        # default seeding is by rank in last round

        self.riders = gtk.ListStore(gobject.TYPE_STRING,   # 0 bib
                                    gobject.TYPE_STRING,   # 1 firstname
                                    gobject.TYPE_STRING,   # 2 lastname
                                    gobject.TYPE_STRING,   # 3 club
                                    gobject.TYPE_STRING,   # 4 Comment
                                    gobject.TYPE_STRING,   # 5 seed
                                    gobject.TYPE_STRING,   # 6 place
                                    gobject.TYPE_PYOBJECT, # 7 Start
                                    gobject.TYPE_PYOBJECT, # 8 Finish
                                    gobject.TYPE_PYOBJECT) # 9 50m 

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
        b.get_object('race_type').set_text('Flying Lap')
        

        # Timer Pane
        mf = b.get_object('race_timer_pane')
        self.fs = timerpane.timerpane('Timer')
        self.fs.bibent.connect('activate', self.bibent_cb, self.fs)
        self.fs.hide_laps()
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
        uiutil.mkviewcoltxt(t, 'Seed', COL_SEED, self.editcol_db)
        uiutil.mkviewcoltod(t, '250m/Last 200m', cb=self.todstr)
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

