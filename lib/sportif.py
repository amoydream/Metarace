
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

"""Mass participartion 'sportif' ride handler.

This module provides a class 'sportif' which implements the sportif
ride handler. Written for the 2010 'Ride the Worlds' sportif, and modified
for the 2012 Netti Challenge series, it implements only the required
methods to fit into the roadmeet framework.

"""

## NOTES:
##
##  - THIS IS INCOMPLETE CODE, custom made for a specific event. Some
##    modification will be required for use with other sportif events.
##

import gtk
import glib
import gobject
import os
import logging

import metarace
from metarace import jsonconfig
from metarace import tod
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import printing
from metarace import uiutil

# Model columns

# basic infos
COL_BIB = 0
COL_NAMESTR = 1
COL_CAT = 2
COL_COMMENT = 3
COL_SORTKEY = 4
COL_TEAM = 5

# timing infos
COL_START = 6		# Rider's recorded departure or None for un-start
COL_RFTIME = 7		# recorded return time
COL_RFSEEN = 8		# list of tods this rider 'seen' by rfid

# rider commands
RIDER_COMMANDS_ORD = ['que', 'add', 'del', 'dnf', 'com']
RIDER_COMMANDS = {'add':'Add riders',
                  'del':'Delete riders',
                  'que':'Query riders',
                  'com':'Add comment',
                  'dnf':'Did not Finish' }

# timing keys
key_armstart = 'F5'
key_clearscratch = 'F6'
key_armfinish = 'F9'
key_raceover = 'F10'

# extended fn keys	(ctrl + key)
key_abort = 'F5'

# config version string
EVENT_ID = 'sportif-1.6'

def key_bib(x):
    """Sort on bib field of aux row."""
    return strops.riderno_key(x[1])

def key_name(x):
    """Sort on sort order key."""
    return x[1]

def key_tod(x):
    """Sort on arrival time."""
    ret = 90000000	# highest
    if x[2] is not None:
        ret = int(x[2].as_seconds())*1000 + strops.riderno_key(x[1])
    else:
        ret += strops.riderno_key(x[1])
    return ret

def key_team_res(x):
    """Sort on team aggregate time."""
    return int(x[4].timeval)

class sportif(object):
    """Sportif ride handler."""

    def loadcats(self, cats=u''):
        self.cats = []  # clear old cat list
        catlist = cats.split()
        if u'AUTO' in catlist:  # ignore any others and re-load from rdb
            self.cats = self.meet.rdb.listcats()
            self.autocats = True
        else:
            self.autocats = False
            for cat in catlist:
                if cat != u'':
                    cat = cat.upper()
                    self.cats.append(cat)
        self.cats.append(u'')   # always include one empty cat
        self.log.debug(u'Result category list updated: ' + repr(self.cats))

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        self.resettimer()
        self.cats = []
        cr = jsonconfig.config({u'event':{
                                          u'start':u'',
                                          u'finish':u'',
                                          u'categories':[],
                                          u'mintime':u'5:00',
                                          u'maxtime':u'20h00:00',
                                          u'minimums':[],
                                          u'maximums':[],
                                          u'timerstat':u'idle',
                                          u'finished':False,
                                          u'timeorder':False,
                                          u'id':EVENT_ID,
                                          u'startlist':[]}})
        cr.add_section(u'event')
        cr.add_section(u'riders')
        # check for config file
        try:
            with open(self.configpath, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading event config: ' + unicode(e))

        # load _result_ categories
        catlist = cr.get(u'event', u'categories')
        if u'AUTO' in catlist:  # ignore any others and re-load from rdb
            self.cats = self.meet.rdb.listcats()
            self.autocats = True
        else:
            self.autocats = False
            for cat in catlist:
                if cat != u'':
                    cat = cat.upper()
                    self.cats.append(cat)
        self.cats.append(u'')   # always include one empty cat

        # Load abs minimum and maximum
        mtm = tod.str2tod(cr.get(u'event', u'mintime'))
        if mtm is not None:
            self.mintime = mtm.truncate(0)
        mtm = tod.str2tod(cr.get(u'event', u'maxtime'))
        if mtm is not None:
            self.maxtime = mtm.truncate(0)

        # Load maps for minimums and maximums
        self.minmap = {}
        self.maxmap = {}
        minsrc = cr.get(u'event', u'minimums')
        maxsrc = cr.get(u'event', u'maximums')
        for cat in self.cats:
            if cat:	# use mintime/maxtime for u'' cat
                mcat = cat.upper()
                mtm = None
                if mcat in minsrc:
                    mtm = tod.str2tod(minsrc[mcat]) 
                self.minmap[mcat] = mtm
                mtm = None
                if mcat in maxsrc:
                    mtm = tod.str2tod(maxsrc[mcat]) 
                self.maxmap[mcat] = mtm

        starters = cr.get(u'event', u'startlist')
        # for a sportif - allow lookup on refid by riderno
        for r in starters:
            bib = r	# unless is a refid
            sr = self.meet.rdb.getrefid(r)
            if sr is not None:
                ser = self.meet.rdb.getvalue(sr, riderdb.COL_SERIES)
                if ser == self.series:
                    # overwrite bib with lookup from riderdb
                    bib = self.meet.rdb.getvalue(sr, riderdb.COL_BIB)
            self.addrider(bib)
            if cr.has_option(u'riders', r):
                nr = self.getrider(bib)
                ril = cr.get(u'riders', r)      # rider op is vec
                # bib = comment,rftod,rfseen...
                lr = len(ril)
                if lr > 0:
                    nr[COL_COMMENT] = ril[0]
                if lr > 1:
                    nr[COL_START] = tod.str2tod(ril[1])
                if lr > 2:
                    nr[COL_RFTIME] = tod.str2tod(ril[2])
                if lr > 3:
                    for i in range(3, lr):
                        laptod = tod.str2tod(ril[i])
                        if laptod is not None:
                            nr[COL_RFSEEN].append(laptod)
        self.timeorder = strops.confopt_bool(cr.get(u'event', u'timeorder'))
        self.set_start(cr.get(u'event', u'start'))
        self.set_finish(cr.get(u'event', u'finish'))
        if strops.confopt_bool(cr.get(u'event', u'finished')):
            self.set_finished()
        else:
            self.timerstat = u'idle'
            timereq = cr.get(u'event', u'timerstat')
            if timereq == 'armstart':
                self.armstart()
            elif timereq == 'armfinish':
                self.armfinish()

        self.recalculate()
        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get(u'event', u'id')
        if eid != EVENT_ID:
            self.log.error(u'Event configuration mismatch: '
                           + repr(eid) + ' != ' + repr(EVENT_ID))

    def get_ridercmdorder(self):
        return RIDER_COMMANDS_ORD

    def get_ridercmds(self):
        """Return a dict of rider bib commands for container ui."""
        ## TODO: Append points classifications to commands.
        return RIDER_COMMANDS

    def get_startlist(self):
        """Return a list of riders 'registered' to event."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ret

    def saveconfig(self):
        """Save event config to disk."""
        if self.readonly:
            self.log.error(u'Attempt to save readonly ob.')
            return
        cw = jsonconfig.config()
        cw.add_section(u'event')
        if self.start is not None:
            cw.set(u'event', u'start', self.start.rawtime())
        if self.finish is not None:
            cw.set(u'event', u'finish', self.finish.rawtime())
        cw.set(u'event', u'finished', self.timerstat == 'finished')
        cw.set(u'event', u'timerstat', self.timerstat)
        cw.set(u'event', u'timeorder', self.timeorder)

        cw.set(u'event', u'mintime', self.mintime.rawtime(0))
        cw.set(u'event', u'maxtime', self.maxtime.rawtime(0))
        minmap = {}
        maxmap = {}
        for cat in self.cats:
            if cat:	# use mintime/maxtime for u'' cat
                mcat = cat.upper()
                mtm = u''
                if mcat in self.minmap and self.minmap[mcat] is not None:
                    mtm = self.minmap[mcat].rawtime(0)
                minmap[mcat] = mtm
                mtm = u''
                if mcat in self.maxmap and self.maxmap[mcat] is not None:
                    mtm = self.maxmap[mcat].rawtime(0)
                maxmap[mcat] = mtm
        cw.set(u'event', u'minimums', minmap)
        cw.set(u'event', u'maximums', maxmap)
        cw.set(u'event', u'startlist', self.get_startlist())    
        if self.autocats:
            cw.set(u'event', u'categories', [u'AUTO'])
        else:
            cw.set(u'event', u'categories', self.get_catlist())

        cw.add_section(u'riders')
        for r in self.riders:
            st = u''
            if r[COL_START] is not None:
                st = r[COL_START].rawtime(2)
            rt = u''
            if r[COL_RFTIME] is not None:
                rt = r[COL_RFTIME].rawtime(2)
            # bib = comment,rftod,rfseen...
            slice = [r[COL_COMMENT].decode('utf-8'), st, rt]
            for t in r[COL_RFSEEN]:
                if t is not None:
                    slice.append(t.rawtime(2))
            cw.set(u'riders', r[COL_BIB].decode('utf-8'), slice)
        cw.set(u'event', u'id', EVENT_ID)
        self.log.debug(u'Saving config to: ' + repr(self.configpath))
        with open(self.configpath, 'wb') as f:
            cw.write(f)

    def show(self):
        """Show event container."""
        self.frame.show()

    def hide(self):
        """Hide event container."""
        self.frame.hide()

    def title_stats_recalc_clicked_cb(self, button, entry=None):
        """Force recalc of meet stats."""
        self.recalculate()

    def title_close_clicked_cb(self, button, entry=None):
        """Close and save the race."""
        self.meet.close_event()

    def set_titlestr(self, titlestr=None):
        """Update the title string label."""
        if titlestr is None or titlestr == u'':
            titlestr = u'Sportif Ride'
        self.title_namestr.set_text(titlestr)

    def destroy(self):
        """Emit destroy signal to race handler."""
        self.frame.destroy()

    def get_results(self):
        """Extract results in flat mode (not yet implemented)."""
        return []

    def reorder_startlist(self):
        """Reorder riders for a startlist."""
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append([cnt, r[COL_BIB]])
            cnt += 1
        if len(aux) > 1:
            aux.sort(key=key_bib)
            self.riders.reorder([a[0] for a in aux])
        return cnt

    def startlist_report(self):
        """Return a startlist report."""
        ret = []
        sec = printing.section()
        sec.heading = u'Startlist'
        cnt = self.reorder_startlist()
        for r in self.riders:
            sec.lines.append([None, r[COL_BIB].decode('utf-8'),
                                    r[COL_NAMESTR].decode('utf-8'),
                                    r[COL_CAT].decode('utf-8')])
        ret.append(sec)
        if cnt > 1:
            sec = printing.section()
            sec.lines.append([None, None, u'Total riders: ' + unicode(cnt)])
            ret.append(sec)
        return ret

    def reorder_byname(self):
        """Reorder riders for a result."""
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append([cnt, r[COL_SORTKEY]])
            cnt += 1
        if len(aux) > 1:
            aux.sort(key=key_name)
            self.riders.reorder([a[0] for a in aux])
        return cnt

    def reorder_bytime(self):
        """Reorder riders for a result."""
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append([cnt, r[COL_BIB], r[COL_RFTIME]])
            cnt += 1
        if len(aux) > 1:
            aux.sort(key=key_tod)
            self.riders.reorder([a[0] for a in aux])
        return cnt

    def result_report(self):
        """Return a result report."""
        ret =  []
        tmap = {}	# teams result map

        # set the 'start' time
        st = tod.tod(0)
        if self.start is not None:
            st = self.start

        sec = printing.section()
        sec.heading = u'Result'

        if self.timeorder:
            self.reorder_bytime()
        else:
            self.reorder_byname()

        for r in self.riders:
            et = self.get_rider_elapsed(self.getiter(r[COL_BIB]))
            bib = r[COL_BIB].decode('utf-8')
            cat = self.ridercat(r[COL_CAT].decode('utf-8'))
            if et is not None and cat:
                if self.minmap[cat] is not None and et < self.minmap[cat]:
                    self.log.warn(u'Rider time less than minimum: '
                                     + repr(bib))
                    et = None	
                elif self.maxmap[cat] is not None and et > self.maxmap[cat]:
                    self.log.warn(u'Rider time greater than maximum: '
                                     + repr(bib))
                    et = None	
            estr = u''
            if et is not None:
                team = r[COL_TEAM].decode('utf-8')
                if team:
                    if team not in tmap:
                        tmap[team] = []	# vec of elapsed times
                    tmap[team].append(et)
                estr = et.rawtime(0)
            elif r[COL_COMMENT]:
                estr = r[COL_COMMENT]
            elif r[COL_START] is not None:
                estr = u'[on course]'
            else:
                estr = u'[not started]'

            sec.lines.append([None, bib,
                                    r[COL_NAMESTR].decode('utf-8'),
                                    r[COL_CAT].decode('utf-8'),
                                    estr, None])
        ret.append(sec)

        sec = printing.section()
        sec.heading = u'Teams Result'
        tres = []
        for team in tmap:
            rvec = tmap[team]
            if len(rvec) > 1:
                rvec.sort()
                aggtm = rvec[0].truncate(0) + rvec[1].truncate(0)
                tres.append([None, None, team, None, aggtm, None])
        if len(tres) > 0:
            tres.sort(key=key_team_res)
            for res in tres:
                res[4] = res[4].rawtime(0)
                sec.lines.append(res)
        if len(sec.lines) > 0:
            ret.append(sec)
        return ret

    def camera_report(self):
        """Return a judges (camera) report."""
        self.log.error(u'Judges report not available for sportif rides.')
        return None

    def points_report(self):
        """Return a points tally report."""
        self.log.error(u'Points tally report not available for sportif rides.')
        return None

    def stat_but_clicked(self):
        """Deal with a status button click in the main container."""
        self.log.debug(u'Stat button clicked.')

    def fmt_rider_result(self, r, st):
        """Return a string for the provided rider reference."""
# 0123456789012		# used in the scratch view - leave for now
# [not started]|
# [on course]|
# 12h13:12.2|
        fstr = u''
        et = self.get_rider_elapsed(self.getiter(r[COL_BIB]))
        if et is not None:
            fstr = et.rawtime(1)
        elif r[COL_START] is not None:
            fstr = u'[on course]'
        else:
            fstr = u'[not started]'
        return fstr

    def query_rider(self, bib=None):
        """List info on selected rider in the scratchpad."""

        # set the start time if required
        st = tod.tod(0)
        if self.start is not None:
            st = self.start

        # get the rider
        r = self.getrider(bib)
        if r is not None:
            fstr = self.fmt_rider_result(r, st)
            self.log.info(u'Rider ' + repr(bib) + u' : ' + repr(fstr))
            self.meet.announce_rider([u'',bib,
                                     r[COL_NAMESTR].decode('utf-8'),
                                     r[COL_CAT].decode('utf-8'),
                                     fstr])
        else:
            self.log.info(u'Rider = ' + repr(bib) + u' not in startlist.')

        return False # allow push via idle_add(...

    def add_comment(self, comment=''):
        """Append a race comment."""
        self.log.info(u'Add comment: ' + repr(comment))

    def ctrl_change(self, acode='', entry=None):
        """Notify change in action combo."""
        if entry is not None:
            entry.set_text(u'')

    def race_ctrl(self, acode='', rlist=''):
        """Apply the selected action to the provided bib list."""
        if acode == 'del':
            rlist = strops.reformat_riderlist(rlist,
                                              self.meet.rdb, self.series)
            for bib in rlist.split():
                self.delrider(bib)
            return True
        elif acode == 'add':
            rlist = strops.reformat_riderlist(rlist,
                                              self.meet.rdb, self.series)
            for bib in rlist.split():
                self.addrider(bib)
            return True
        elif acode == 'que':
            rlist = strops.reformat_biblist(rlist)
            for bib in rlist.split():
                self.query_rider(bib)
            return True
        elif acode == 'dnf':
            self.dnfriders(strops.reformat_biblist(rlist))
            return True
        elif acode == 'com':
            self.add_comment(rlist)
            return True
        else:
            self.log.error(u'Ignoring invalid action.')
        return False

    def dnfriders(self, biblist='', code='dnf'):
        """Remove each rider with supplied code."""
        recalc = False
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                #r[COL_INRACE] = False
                r[COL_COMMENT] = code
                recalc = True
                self.log.info('Rider ' + str(bib)
                               + ' did not finish with code: ' + code)
            else:
                self.log.warn('Unregistered Rider ' + str(bib) + ' unchanged.')
        if recalc:
            self.recalculate()
        return False

    def startlist_gen(self, cat=''):
        """Generator function to export a startlist."""
        mcat = self.ridercat(cat)
        self.reorder_startlist()
        for r in self.riders:
            if mcat == '' or mcat == self.ridercat(r[COL_CAT]):
                start = ''
                if r[COL_START] is not None and r[COL_START] != tod.ZERO:
                    start = r[COL_START].rawtime(1)
                bib = r[COL_BIB]
                series = self.series
                name = r[COL_NAMESTR]
                cat = r[COL_CAT]
                firstxtra = ''
                lastxtra = ''
                clubxtra = r[COL_TEAM]
                dbr = self.meet.rdb.getrider(r[COL_BIB],self.series)
                if dbr is not None:
                    firstxtra = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_FIRST).capitalize()
                    lastxtra = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_LAST).upper()
                yield [start, bib, series, name, cat,
                       firstxtra, lastxtra, clubxtra]

    def result_gen(self, cat=''):
        """Generator function to export a final result."""
        self.recalculate()
        self.reorder_startlist()
        mcat = self.ridercat(cat)
        rcount = 0
        lrank = None
        lcrank = None
        for r in self.riders:
            if mcat == '' or mcat == self.ridercat(r[COL_CAT]):
                rcount += 1
                bib = r[COL_BIB]
                crank = None
                ft = self.get_rider_elapsed(self.getiter(r[COL_BIB]))
                yield [crank, bib, ft, None, None]

    def clear_results(self):
        """Clear all data from event model."""
        pass	# not relevant for sportif
        #self.log.debug(u'Clear results not implemented.')

    def getrider(self, bib):
        """Return reference to selected rider no."""
        ret = None
        for r in self.riders:
            if r[COL_BIB].decode('utf-8') == bib:
                ret = r
                break
        return ret

    def getiter(self, bib):
        """Return temporary iterator to model row."""
        i = self.riders.get_iter_first()
        while i is not None:
            if self.riders.get_value(i, COL_BIB).decode('utf-8') == bib:
                break
            i = self.riders.iter_next(i)
        return i

    def delrider(self, bib=''):
        """Remove the specified rider from the model."""
        i = self.getiter(bib)
        if i is not None:
            self.riders.remove(i)

    def addrider(self, bib=u''):
        """Add specified rider to race model."""
        if bib == u'' or self.getrider(bib) is None:
            nr = [bib, u'', u'', u'', u'', u'', None, None, []]
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                first = self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST)
                last = self.meet.rdb.getvalue(dbr, riderdb.COL_LAST)
                club = self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB)

                nr[COL_NAMESTR] = strops.listname(first, last, club)
                nr[COL_CAT] = self.meet.rdb.getvalue(dbr, riderdb.COL_CAT)
                nr[COL_SORTKEY] = last.lower().ljust(30) + first.lower()
                nr[COL_TEAM] = club
            return self.riders.append(nr)
        else:
            return None

    def resettimer(self):
        """Reset race timer."""
        self.set_finish()
        self.set_start()
        self.clear_results()
        self.timerstat = 'idle'
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Idle')
        self.meet.stat_but.set_sensitive(True)
        self.set_elapsed()
        
    def armstart(self):
        """Process an armstart request."""
        if self.timerstat in ['idle','running']:
            self.timerstat = 'armstart'
            uiutil.buttonchg(self.meet.stat_but, uiutil.bg_armstart,
                                    'Arm Start')
        elif self.timerstat == 'armstart':
            self.timerstat = 'idle'
            uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Idle') 

    def armfinish(self):
        """Process an armfinish request."""
        if self.timerstat != 'armfinish':
            self.timerstat = 'armfinish'
            uiutil.buttonchg(self.meet.stat_but, uiutil.bg_armfin, 'Arm Finish')
            self.meet.stat_but.set_sensitive(True)
        elif self.timerstat == 'armfinish':
            self.timerstat = 'running'
            uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Running')

    def key_event(self, widget, event):
        """Handle global key presses in event."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_abort:    # override ctrl+f5
                    self.resettimer()
                    return True
            if key[0] == 'F':
                if key == key_armstart:
                    self.armstart()
                    return True
                elif key == key_armfinish:
                    self.armfinish()
                    return True
                elif key == key_raceover:
                    self.set_finished()
                    return True
                elif key == key_clearscratch:
                    self.meet.announce_clear()
                    self.meet.announce_title(self.meet.title_str)
                    return True
        return False

    def shutdown(self, win=None, msg=u'Race Sutdown'):
        """Close event."""
        self.log.debug(u'shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def starttrig(self, e):
        """Process a 'start' trigger signal."""
        if self.timerstat == 'armstart':
            self.set_start(e)
        return False

    def rfidtrig(self, e):
        """Process rfid event."""
        if e.refid == '':	# assume trigger
            return self.starttrig(e)

        # else assume a passing
        r = self.meet.rdb.getrefid(e.refid)
        if r is None:
            self.log.info('Unknown tag: ' + e.refid + '@' + e.rawtime(2))
            #return False
            r = self.meet.rdb.addempty(e.refid, self.series)
            self.meet.rdb.editrider(r, refid=e.refid)

        bib = self.meet.rdb.getvalue(r, riderdb.COL_BIB)
        ser = self.meet.rdb.getvalue(r, riderdb.COL_SERIES)
        if ser != self.series:
            self.log.error(u'Ignored non-series rider: ' + repr(bib)
                               + u'.' + repr(ser))
            return

        lr = self.getrider(bib)
        if lr is None:	# sportif uses relaxed 'club mode'
            if self.timerstat in ['armstart','armfinish']:
                self.addrider(bib)
                lr = self.getrider(bib)
                self.log.info(u'Added starter: ' + repr(bib)
                              + u' @ ' + e.rawtime(1))
            else:
                self.log.warn('Saw: ' + repr(bib)
                          + ' @ ' + e.rawtime(1))
                return False

        # at this point should always have a valid rider vector
        if self.timerstat == 'armstart':
            if lr[COL_START] is None:
                self.log.info(u'Starting: ' + bib + u' @ ' + e.rawtime(1))
                lr[COL_START] = e
                self.__dorecalc = True	# starters flag recalc
            else:
                self.log.error(u'Duplicate start rider = ' + repr(bib)
                                  + u' @ ' + e.rawtime(1))
                lr[COL_RFSEEN].append(e)
            glib.idle_add(self.query_rider, bib)
        elif self.timerstat not in ['idle', 'finished']:
            # save RF ToD into 'seen' vector and log
            lr[COL_RFSEEN].append(e)
            self.log.info(u'Saw: ' + repr(bib) + u' @ ' + e.rawtime(1))
            # record finish time if required
            if self.timerstat == 'armfinish':
                if lr[COL_RFTIME] is None:
                    lr[COL_RFTIME] = e
                    self.__dorecalc = True	# finishers flag recalc
                    glib.idle_add(self.query_rider, bib)
                else:
                    self.log.error(u'Duplicate finish rider = ' + repr(bib)
                                      + u' @ ' + e.rawtime(1))

    def new_start_trigger(self, rfid):
        """Collect a RFID trigger signal and apply it to the model."""
        if self.newstartdlg is not None and self.newstartent is not None:
            et = tod.str2tod(self.newstartent.get_text())
            if et is not None:
                st = rfid - et
                self.set_start(st)
                self.newstartdlg.response(1)
                self.newstartdlg = None # try to ignore the 'up' impulse
            else:
                self.log.warn('Invalid elapsed time: Start not updated.')
        return False

    def new_start_trig(self, button, entry=None):
        """Use the 'now' time to update start offset."""
        self.meet.timer.trig(refid='0')

    def verify_timent(self, entry, data=None):
        et = tod.str2tod(entry.get_text())
        if et is not None:
            entry.set_text(et.rawtime())
        else:
            self.log.info('Invalid elapsed time.')

    def elapsed_dlg(self, addriders=''):
        """Run a 'new start' dialog."""
        if self.timerstat == 'armstart':
            self.log.error('Start is armed, unarm to add new start time.')
            return

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'new_start.ui'))
        dlg = b.get_object('newstart')
        dlg.set_transient_for(self.meet.window)
        self.newstartdlg = dlg

        timent = b.get_object('time_entry')
        self.newstartent = timent
        timent.connect('activate', self.verify_timent)

        self.meet.timer.setcb(self.new_start_trigger)
        b.get_object('now_button').connect('clicked', self.new_start_trig)

        response = dlg.run()
        self.newstartdlg = None
        if response == 1:       # id 1 set in glade for "Apply"
            self.log.info('Start time updated: ' + self.start.rawtime(2))
        else:
            self.log.info('Set elapsed time cancelled.')
        self.meet.timer.setcb(self.rfidtrig)
        dlg.destroy()

    def recalculate(self):
        """Update any internal structures and trigger export if required."""
        tcnt = 0	# Total
        scnt = 0	# Started
        uscnt = 0	# Un-Started
        fcnt = 0	# Finished
        acnt = 0	# DNF
        for r in self.riders:
            tcnt += 1
            if r[COL_COMMENT]:	# assume dnf/dns - overrides start/finish
                acnt += 1
            elif r[COL_RFTIME] is not None:
                fcnt += 1
                if r[COL_START] is None:	# un-started
                    uscnt += 1
            elif r[COL_START] is not None:
                scnt += 1

        statmsg = unicode(tcnt) + u' riders'
        if fcnt > 0:
            statmsg += u'; ' + unicode(fcnt) + u' finished'
            if uscnt > 0:
                statmsg += u'; ' + unicode(uscnt) + u' missed at start'
        if acnt > 0:
            statmsg += u'; ' + unicode(acnt) + u' dnf'
        if scnt > 0:
            statmsg += u'; ' + unicode(scnt) + u' on course'
        statmsg += u'.'
        self.stats_lbl.set_text(statmsg)

    def timeout(self):
        """Update elapsed time and recalculate if required."""
        if not self.winopen:
            return False
        if self.start is not None:
            self.set_elapsed()
        if self.__dorecalc:
            self.__dorecalc = False
            self.recalculate()
        return True

    def set_start(self, start=''):
        """Set the start time."""
        if type(start) is tod.tod:
            self.start = start
        else:
            self.start = tod.str2tod(start)
        if self.start is not None and self.finish is None:
            self.set_running()

    def set_finish(self, finish=''):
        """Set the finish time."""
        if type(finish) is tod.tod:
            self.finish = finish
        else:
            self.finish = tod.str2tod(finish)
        if self.finish is None:
            if self.start is not None:
                self.set_running()
        else:
            if self.start is None:
                self.set_start('0')

    def set_elapsed(self):
        """Update the elapsed time field."""
        pass

    def set_running(self):
        """Update event status to running."""
        self.log.info(u'Running trigger')
        #self.timerstat = 'running'
        #uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Running')

    def set_finished(self):
        """Update event status to finished."""
        self.timerstat = 'finished'
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Finished')
        self.meet.stat_but.set_sensitive(False)
        if self.finish is None:
            self.set_finish(tod.tod('now'))
        self.set_elapsed()

    def get_catlist(self):
        """Return the ordered list of categories."""
        rvec = []
        for cat in self.cats:
            if cat != '':
                rvec.append(cat)
        return rvec

    def ridercat(self, cat):
        """Return a category from the result for the riders cat."""
        ret = u''        # default is the 'None' category - uncategorised
        checka = cat.upper()
        if checka in self.cats:
            ret = checka
        return ret

    def get_rider_elapsed(self, iter):
        """Return appropriate elapsed time for this rider."""
        ret = None
        st = self.riders.get_value(iter, COL_START)
        ft = self.riders.get_value(iter, COL_RFTIME)
        if ft is not None:
            if st is not None:
                if self.start is None:
                    ret = ft - st
                else:	# wave starts manually offset from shared 'zero'
                    ret = ft - st - self.start
            else:
                if self.start is not None:
                    ret = ft - self.start 
                else:
                    ret = ft
        return ret

    def info_time_edit_clicked_cb(self, button, data=None):
        """Run the edit times dialog."""
        st = ''
        if self.start is not None:
            st = self.start.rawtime(2)
        ft = ''
        if self.finish is not None:
            ft = self.finish.rawtime(2)
        rvec = uiutil.edit_times_dlg(self.meet.window, st, ft)
        if rvec[0] == 1:
            self.set_start(st)
            self.set_finish(ft)
            self.log.info(u'Adjusted race times.')

    def editcol_cb(self, cell, path, new_text, col):
        """Edit column callback."""
        new_text = new_text.strip()
        self.riders[path][col] = new_text

    # show start offset
    def showstart_cb(self, col, cr, model, iter, data=None):
        st = model.get_value(iter, COL_START)
        otxt = ''
        if st is not None:
            otxt = st.rawtime(0)
        cr.set_property('text', otxt)

    # show finish offset
    def showfin_cb(self, col, cr, model, iter, data=None):
        ft = model.get_value(iter, COL_RFTIME)
        otxt = ''
        if ft is not None:
            otxt = ft.rawtime(0)
        cr.set_property('text', otxt)

    # show elapsed time
    def showelap_cb(self, col, cr, model, iter, data=None):
        et = self.get_rider_elapsed(iter)
        otxt = ''
        if et is not None:
            otxt = et.rawtime(0)
        cr.set_property('text', otxt)

    # edit start
    def editstart_cb(self, cell, path, new_text, col=None):
        self.riders[path][COL_START] = tod.str2tod(new_text)

    def editfin_cb(self, cell, path, new_text, col=None):
        self.riders[path][COL_RFTIME] = tod.str2tod(new_text)

    def __init__(self, meet, event, ui=True):
        self.meet = meet
        self.event = event      # Note: now a treerowref
        self.evno = event[u'evid']
        self.series = event[u'seri']
        self.configpath = meet.event_configfile(self.evno)

        self.log = logging.getLogger('sportif')
        self.log.setLevel(logging.DEBUG)
        self.log.debug(u'opening event: ' + repr(self.evno))

        # race run time attributes
        self.readonly = not ui
        self.start = None
        self.finish = None
        self.winopen = True
        self.timerstat = 'idle'
        self.__dorecalc = False
        self.cats = []
        self.minmap = {}
        self.maxmap = {}
        self.mintime = tod.tod('5:00')		# abs min
        self.maxtime = tod.tod('20h00:00')	# abs max
        self.autocats = False

        # new start dialog
        self.newstartent = None
        self.newstartdlg = None                                                 

        self.riders = gtk.ListStore(gobject.TYPE_STRING, # BIB = 0
                                    gobject.TYPE_STRING, # NAMESTR = 1
                                    gobject.TYPE_STRING, # CAT = 2
                                    gobject.TYPE_STRING, # COMMENT = 3
                                    gobject.TYPE_STRING, # SORTKEY = 4
                                    gobject.TYPE_STRING, # TEAM = 5
                                    gobject.TYPE_PYOBJECT, # START = 6
                                    gobject.TYPE_PYOBJECT, # RFTIME = 7
                                    gobject.TYPE_PYOBJECT) # RFSEEN = 8

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'sportif.ui'))

        # !! destroy??
        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # meta info pane
        self.title_namestr = b.get_object('title_namestr')
        self.set_titlestr()
        self.stats_lbl = b.get_object('stats_lbl')

        # results pane
        t = gtk.TreeView(self.riders)
        t.set_reorderable(True)
        t.set_rules_hint(True)
        t.show()
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'Rider', COL_NAMESTR, expand=True)
        uiutil.mkviewcoltxt(t, 'Comment', COL_COMMENT,
                                cb=self.editcol_cb, width=80)
        uiutil.mkviewcoltod(t, 'Start', cb=self.showstart_cb, width=50,
                                editcb=self.editstart_cb)
        uiutil.mkviewcoltod(t, 'Fin', cb=self.showfin_cb, width=50,
                                editcb=self.editfin_cb)
        uiutil.mkviewcoltod(t, 'Time', cb=self.showelap_cb, width=50)
        b.get_object('race_result_win').add(t)

        if ui:
            # connect signal handlers
            b.connect_signals(self)
            self.meet.timer.setcb(self.rfidtrig)
