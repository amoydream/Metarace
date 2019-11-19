
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

"""Generic track race object.

This module  provides an example class 'race' that implements all
required methods of the race interface and demonstrates typical race
data handling patterns. 

The generic 'race' model requires the following interface:

  Constructor:

    racetype(meet, event_h, ui=True/False)

  Shared "Public" Methods:

    race.do_properties()           - display a race property edit dialog
					or Pass
    race.loadconfig()              - read event details off disk
    race.saveconfig()              - save event details to disk
    race.destroy()                 - send destroy signal to event window
    race.show()                    - show event window
    race.hide()                    - hide event window
    race.addrider(bib)             - add a new starter with given bib
    race.delrider(bib)             - remove starter with given bib
    race.key_event(widget, event)  - race key handler
    race.timeout()                 - race specific update callback

"""

import gtk
import glib
import gobject
import pango
import logging
import ConfigParser
import csv
import os

import metarace
from metarace import tod
from metarace import jsonconfig
from metarace import timy
from metarace import unt4
from metarace import riderdb
from metarace import scbwin
from metarace import uiutil
from metarace import strops
from metarace import printing

# config version string
EVENT_ID = 'trackrace-1.3'

# race model column constants
COL_BIB = 0
COL_FIRSTNAME = 1
COL_LASTNAME = 2
COL_CLUB = 3
COL_INFO = 4
COL_DNF = 5
COL_PLACE = 6

# scb function key mappings
key_startlist = 'F3'			# show starters in table
key_results = 'F4'			# recalc/show result window
key_lapdown = 'F11'			# decrement tv lap counter

# timing function key mappings
key_armstart = 'F5'			# arm for start/200m impulse
key_showtimer = 'F6'			# show timer
key_armfinish = 'F9'			# arm for finish impulse

# extended function key mappings
key_abort = 'F5'			# + ctrl for clear/abort
key_falsestart = 'F6'                   # + ctrl for false start

class race(object):
    """Data handling for scratch, handicap, keirin, derby, etc races."""
    def clearplaces(self):
        """Clear places from data model."""
        for r in self.riders:
            r[COL_PLACE] = u''

    def getrider(self, bib):
        """Return temporary reference to model row."""
        ret = None
        for r in self.riders:
            if r[COL_BIB] == bib:
                ret = r		## DANGER- Leaky ref
                break
        return ret

    def getiter(self, bib):
        """Return temporary iterator to model row."""
        i = self.riders.get_iter_first()
        while i is not None:
            if self.riders.get_value(i, COL_BIB) == bib:
                break
            i = self.riders.iter_next(i)
        return i

    def delayed_reorder(self):
        """Call reorder if the flag is one."""
        if self.reorderflag > 1:
            self.reorderflag -= 1
        elif self.reorderflag == 1:
            self.reorder_handicap()
            self.reorderflag = 0
        else:
            self.reorderflag = 0	# clamp negatives
        return False

    def addrider(self, bib='', info=None):
        """Add specified rider to race model."""
        nr=[bib, '', '', '', '', False, '']
        er = self.getrider(bib)
        if bib == '' or er is None:
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
                    if self.evtype == u'handicap':	# reqd?
                        if info:
                            nr[COL_INFO] = info
            if self.evtype in ['handicap', 'keirin'] and not self.onestart:
                 self.reorderflag += 1
                 glib.timeout_add_seconds(1, self.delayed_reorder)
            return self.riders.append(nr)
        else:
            if er is not None:
                # Rider already in the model, set the info if
                # event type is handicap or event is part of omnium
                if self.evtype == u'handicap' or self.inomnium:
                    if not er[COL_INFO] and info: # don't overwrite if set
                        er[COL_INFO] = info
            return None

    def dnfriders(self, biblist=''):
        """Remove listed bibs from the race."""
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                r[COL_DNF] = True
                self.log.info(u'Rider ' + repr(bib) + u' withdrawn')
            else:
                self.log.warn(u'Did not withdraw no. = ' + repr(bib))
        return False
      
    def dnsriders(self, biblist=''):
        """Remove listed bibs from the race."""
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                r[COL_INFO] = 'dns'
                r[COL_DNF] = True
                self.log.info(u'Rider ' + repr(bib) + u' withdrawn')
            else:
                self.log.warn(u'Did not withdraw no. = ' + repr(bib))
        return False
      
    def dsqriders(self, biblist=''):
        """Remove listed bibs from the race."""
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                r[COL_INFO] = 'dsq'
                r[COL_DNF] = True
                self.log.info(u'Rider ' + repr(bib) + u' withdrawn')
            else:
                self.log.warn(u'Did not withdraw no. = ' + repr(bib))
        return False

    def delrider(self, bib):
        """Remove the specified rider from the model."""
        i = self.getiter(bib)
        if i is not None:
            self.riders.remove(i)

    def placexfer(self, placestr):
        """Transfer places in placestr to model."""
        self.clearplaces()
        self.results = []
        placeset = set()
        place = 1
        count = 0
        resname_w = self.meet.scb.linelen - 11

        for placegroup in placestr.split():
            for bib in placegroup.split(u'-'):
                if bib == u'dnf':
                    # HACK: skip one place for the 'last available' keirin
                    place += 1
                elif bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is None:	# ensure rider exists at this point
                        self.log.warn(u'Adding non-starter: ' + repr(bib))
                        self.addrider(bib) 
                        r = self.getrider(bib)
                    rank = place + self.startplace
                    r[COL_PLACE] = rank
                    club = r[COL_CLUB].decode('utf-8','replace')
                    if len(club) > 3:
                        # look it up?
                        if self.series in self.meet.ridermap:
                            rh = self.meet.ridermap[self.series][bib]
                            if rh is not None:
                                club = rh['note']
                    self.results.append([unicode(rank)+u'.',
                     r[COL_BIB].decode('utf-8','replace'),
                     strops.fitname(r[COL_FIRSTNAME].decode('utf-8','replace'),
                       r[COL_LASTNAME].decode('utf-8','replace'),
                       resname_w), club])
                    i = self.getiter(bib)
                    self.riders.swap(self.riders.get_iter(count), i)
                    count += 1
                else:
                    self.log.error('Ignoring duplicate no: ' +repr(bib))
            place = count+1	## FIX FOR incorrect deat heats testit
        if count > 0:
            self.onestart = True

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        # set defaults timetype based on event type
        deftimetype = 'start/finish'
        defdistance = ''
        defdistunits = 'laps'
        self.seedsrc = None	# default is no seed info
        if self.evtype == 'handicap':
            self.seedsrc = 3	# fetch handicap info from autospec
        if self.evtype in ['sprint', 'keirin']:
            deftimetype = '200m'
            defdistunits = 'metres'
            defdistance = '200'
        if self.evtype == 'elimination':
            self.action_model.append(['Eliminated', 'out'])
        cr = ConfigParser.ConfigParser({'startlist':'',
                                        'id':EVENT_ID,
                                        'ctrl_places':'',
                                        'start':'',
                                        'lstart':'',
					'comments':'',
                                        'finish':'',
                                        'runlap':'',
					'distance':defdistance,
					'distunits':defdistunits,
                                        'showinfo':'No',
                                        'inomnium':'No',
					'startplace':'0',
                                        'autospec':'',
                                        'timetype':deftimetype})
        cr.add_section('event')
        cr.add_section('riders')
        if os.path.isfile(self.configpath):
            self.log.debug('Attempting to read race config from path='
                           + repr(self.configpath))
            cr.read(self.configpath)
        self.inomnium = strops.confopt_bool(cr.get('event', 'inomnium'))
        if self.inomnium:
            self.seedsrc = 1	# fetch start list seeding from omnium
        rlist = cr.get('event', 'startlist').split()
        for r in rlist:
            nr=[r, '', '', '', '', False, '']
            if cr.has_option('riders', r):
                ril = csv.reader([cr.get('riders', r)]).next()
                for i in range(0,3):
                    if len(ril) > i:
                        nr[i+4] = ril[i].strip()
            # Re-patch name
            dbr = self.meet.rdb.getrider(r, self.series)
            if dbr is not None:
                for i in range(1,4):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            self.riders.append(nr)

        # race infos
        self.comments = cr.get('event','comments')
        self.startplace = strops.confopt_posint(cr.get('event', 'startplace'))
        self.set_timetype(cr.get('event', 'timetype'))
        self.distance = strops.confopt_dist(cr.get('event', 'distance'))
        self.units = strops.confopt_distunits(cr.get('event', 'distunits'))
        self.runlap = strops.confopt_posint(cr.get('event','runlap'))
        if self.timetype != '200m' and self.event[u'laps']:
            # use event program to override
            self.units = 'laps'
            self.distance = strops.confopt_posint(self.event[u'laps'],
                                                  self.distance)
        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))
        self.set_start(cr.get('event', 'start'), cr.get('event', 'lstart'))
        self.set_finish(cr.get('event', 'finish'))
        self.set_elapsed()
        places = strops.reformat_placelist(cr.get('event', 'ctrl_places'))
        self.ctrl_places.set_text(places)
        self.placexfer(places)
        if places:
            self.doscbplaces = False  # only show places on board if not set
            self.setfinished()
        else:
            if self.autospec:
                self.meet.autostart_riders(self, self.autospec,
                                                 infocol=self.seedsrc)
            if self.evtype in ['handicap', 'keirin'] or self.inomnium:
                self.reorder_handicap()

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get('event', 'id')
        if eid and eid != EVENT_ID:
            self.log.error('Event configuration mismatch: '
                           + repr(eid) + ' != ' + repr(EVENT_ID))
            #self.readonly = True

    def sort_riderno(self, x, y):
        """Sort riders by rider no."""
        return cmp(strops.riderno_key(x[1]),
                    strops.riderno_key(y[1]))

    def sort_handicap(self, x, y):
        """Sort function for handicap marks."""
        if x[2] != y[2]:
            if x[2] is None:	# y sorts first
                return 1
            elif y[2] is None:  # x sorts first
                return -1
            else:	# Both should be ints here
                return cmp(x[2], y[2])
        else:	# Defer to rider number
            return cmp(strops.riderno_key(x[1]),
                        strops.riderno_key(y[1]))

    def reorder_handicap(self):
        """Reorder rider model according to the handicap marks."""
        if len(self.riders) > 1:
            auxmap = []
            cnt = 0
            for r in self.riders:
                auxmap.append([cnt, r[COL_BIB].decode('utf-8','replace'), strops.mark2int(r[COL_INFO].decode('utf-8','replace'))])
                cnt += 1
            if self.inomnium or self.evtype == u'handicap':
                auxmap.sort(self.sort_handicap)
            else:
                auxmap.sort(self.sort_riderno)
            self.riders.reorder([a[0] for a in auxmap])

    def set_timetype(self, data=None):
        """Update state and ui to match timetype."""
        if data is not None:
            self.timetype = strops.confopt_pair(data, '200m', 'start/finish')
            self.finchan = timy.CHAN_FINISH
            if self.timetype == '200m':
                self.startchan = timy.CHAN_200
            else:
                self.startchan = timy.CHAN_START
        self.type_lbl.set_text(self.timetype.capitalize())

    def set_start(self, start='', lstart=None):
        """Set the race start."""
        if type(start) is tod.tod:
            self.start = start
            if lstart is not None:
                self.lstart = lstart
            else:
                self.lstart = self.start
        else:
            self.start = tod.str2tod(start)
            if lstart is not None:
                self.lstart = tod.str2tod(lstart)
            else:
                self.lstart = self.start
        if self.start is None:
            pass
        else:
            if self.finish is None:
                self.setrunning()

    def set_finish(self, finish=''):
        """Set the race finish."""
        if type(finish) is tod.tod:
            self.finish = finish
        else:
            self.finish = tod.str2tod(finish)
        if self.finish is None:
            if self.start is not None:
                self.setrunning()
        else:
            if self.start is None:
                self.set_start('0')
            self.setfinished()

    def log_elapsed(self):
        """Log race elapsed time on Timy."""
        self.meet.timer.printline(self.meet.racenamecat(self.event))
        self.meet.timer.printline('      ST: ' + self.start.timestr(4))
        self.meet.timer.printline('     FIN: ' + self.finish.timestr(4))
        self.meet.timer.printline('    TIME: '
                                   + (self.finish - self.start).timestr(2))

    def set_elapsed(self):
        """Update elapsed time in race ui and announcer."""
        if self.start is not None and self.finish is not None:
            et = self.finish - self.start
            ## UDP hack
            msg = unt4.unt4(
                            header=unichr(unt4.DC3) + u'R F$',
                            xx=0,
                            yy=0,
                            text=et.timestr(2)[0:12])
            if not self.readonly:
                self.meet.udptimer.sendto(msg.pack(),
                                      (self.meet.udpaddr,6789))
            self.time_lbl.set_text(et.timestr(2))
        elif self.start is not None:	# Note: uses 'local start' for RT
            runtm  = (tod.tod('now') - self.lstart).timestr(1)
            ## UDP hack
            msg = unt4.unt4(
                            header=unichr(unt4.DC3) + u'R F$',
                            xx=0,
                            yy=0,
                            text=runtm[0:12])
            if not self.readonly:
                self.meet.udptimer.sendto(msg.pack(),
                                      (self.meet.udpaddr,6789))
            self.time_lbl.set_text(runtm)

            if self.runlap is not None:
                if self.runlap != self.lastrunlap:
                    self.log.debug(u'SENT RUNLAP: ' + repr(self.runlap))
                    lapmsg = (unicode(self.runlap) + u'    ').rjust(12)
                    msg = unt4.unt4(header=unichr(unt4.DC3) + u'R F$',
                                    xx=0, yy=6, text=lapmsg)
                    if not self.readonly:
                        self.meet.udptimer.sendto(msg.pack(),
                                      (self.meet.udpaddr,6789))
                    self.lastrunlap = self.runlap
        elif self.timerstat == 'armstart':
            self.time_lbl.set_text(tod.tod(0).timestr(1))
            if self.runlap and self.runlap != self.lastrunlap:
                self.log.debug(u'SENT RUNLAP: ' + repr(self.runlap))
                lapmsg = (unicode(self.runlap) + u'    ').rjust(12)
                msg = unt4.unt4(header=unichr(unt4.DC3) + u'R F$',
                                xx=0, yy=6, text=lapmsg)
                if not self.readonly:
                    self.meet.udptimer.sendto(msg.pack(),
                                  (self.meet.udpaddr,6789))
                self.lastrunlap = self.runlap
        else:
            self.time_lbl.set_text('')

    def delayed_announce(self):
        """Initialise the announcer's screen after a delay."""
        if self.winopen:
            self.meet.announce.clrall()
            self.meet.ann_title(' '.join([
                  'Event', self.evno, ':', self.event[u'pref'],
                   self.event[u'info']]))

            # clear page
            self.meet.announce.linefill(1, '_')
            self.meet.announce.linefill(19, '_')

            # write out riders
            count = 0
            curline = 4
            posoft = 0
            for r in self.riders:
                count += 1
                if count == 14:
                    curline = 4
                    posoft = 41
                xtra = '    '
                if r[COL_INFO] is not None and r[COL_INFO] != '':
                    xtra = strops.truncpad(r[COL_INFO].decode('utf-8','replace'), 4, 'r')

                clubstr = ''
                if r[COL_CLUB] != '' and len(r[COL_CLUB]) <= 3:
                    clubstr = ' (' + r[COL_CLUB].decode('utf-8','replace') + ')'
                namestr = strops.truncpad(strops.fitname(r[COL_FIRSTNAME].decode('utf-8','replace'),
                              r[COL_LASTNAME].decode('utf-8','replace'), 25-len(clubstr),
                              trunc=True)+clubstr, 25, elipsis=False)

                placestr = '   '
                if r[COL_PLACE] != '':
                    placestr = strops.truncpad(r[COL_PLACE].decode('utf-8','replace') + '.', 3)
                elif r[COL_DNF]:
                    placestr = 'dnf'
                bibstr = strops.truncpad(r[COL_BIB].decode('utf-8','replace'), 3, 'r')
                self.meet.announce.postxt(curline, posoft, ' '.join([
                      placestr, bibstr, namestr, xtra]))
                curline += 1

            tp = ''
            if self.start is not None and self.finish is not None:
                et = self.finish - self.start
                if self.timetype == '200m':
                    tp = '200m: '
                else:
                    tp = 'Time: '
                tp += et.timestr(2) + '    '
                dist = self.meet.get_distance(self.distance, self.units)
                if dist:
                    tp += 'Avg: ' + et.speedstr(dist)
            self.meet.announce.setline(21, tp)
        return False

    def startlist_report(self, program=False):
        """Return a startlist report."""
        ret = []
        sec = None
        etype = self.event[u'type']
        twocol = True
        if not self.inomnium and not program and etype in [u'axe', u'run', u'handicap']:
            sec = printing.section()	# one column overrides
        else:
            sec = printing.twocol_startlist()
        headvec = []
        if etype != 'break':
            headvec.extend([u'Event', self.evno, u':'])
        headvec.append(self.event[u'pref'])
        headvec.append(self.event[u'info'])
        if not program:
            headvec.append(u'- Start List')
        sec.heading = u' '.join(headvec)
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            sec.subheading = substr

        self.reorder_handicap()
        sec.lines = []
        cnt = 0
        col2 = []
        if self.inomnium and len(self.riders) > 0:
            sec.lines.append([u' ',u' ',u'The Fence', None, None, None])
            col2.append([u' ',u' ',u'Sprinters Lane', None, None, None])
        for r in self.riders:
            cnt += 1
            rno = r[COL_BIB].decode('utf-8')
            rh = self.meet.newgetrider(rno, self.series)
            inf = r[COL_INFO].decode('utf-8','replace')
            if self.evtype in [u'keirin', u'sprint']: # encirc draw no
                inf = strops.drawno_encirc(inf)
            if self.inomnium:
                # inf holds seed, ignore for now
                inf = None
            # layout needs adjust
            #if rh[u'ucicode']:
                #inf = rh[u'ucicode']   # overwrite by force
            rname = u''
            if rh is not None:
                rname = rh[u'namestr']
            if self.inomnium:
                if cnt%2 == 1:
                    sec.lines.append([None, rno, rname, inf, None, None])
                else:
                    col2.append([None, rno, rname, inf, None, None])
            else:
                sec.lines.append([None, rno, rname, inf, None, None])
        for i in col2:
            sec.lines.append(i)
        if self.event[u'plac']:
            while cnt < self.event[u'plac']:
                sec.lines.append([None, None, None, None, None, None])
                cnt += 1
        ret.append(sec)

        ptype = u'Riders'
        if etype == u'run':
            ptype = u'Runners'
        elif self.evtype == u'axe':
            ptype = u'Axemen'
        if cnt > 0 and not program:
            sec = printing.bullet_text()
            sec.lines.append([None, 'Total ' + ptype + ': ' + str(cnt)])
            ret.append(sec)
        return ret

    def get_startlist(self):
        """Return a list of bibs in the rider model."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB].decode('utf-8','replace'))
        return u' '.join(ret)

    def saveconfig(self):
        """Save race to disk."""
        if self.readonly:
            self.log.error('Attempt to save readonly ob.')
            return
        cw = ConfigParser.ConfigParser()
        cw.add_section('event')
        if self.start is not None:
            cw.set('event', 'start', self.start.rawtime())
        if self.lstart is not None:
            cw.set('event', 'lstart', self.lstart.rawtime())
        if self.finish is not None:
            cw.set('event', 'finish', self.finish.rawtime())
        cw.set('event', 'ctrl_places', self.ctrl_places.get_text())
        cw.set('event', 'startlist', self.get_startlist())
        cw.set('event', 'showinfo', self.info_expand.get_expanded())
        cw.set('event', 'distance', self.distance)
        cw.set('event', 'distunits', self.units)
        cw.set('event', 'timetype', self.timetype)
        if self.runlap is not None:
            cw.set('event', 'runlap', self.runlap)
        cw.set('event', 'autospec', self.autospec)
        cw.set('event', 'inomnium', self.inomnium)
        cw.set('event', 'comments', self.comments)
        cw.set('event', 'startplace', self.startplace)

        cw.add_section('riders')
        for r in self.riders:
            bf = ''
            if r[COL_DNF]:
                bf='True'
            slice = [r[COL_INFO].decode('utf-8','replace'), bf, r[COL_PLACE].decode('utf-8','replace')]
            cw.set('riders', r[COL_BIB], 
                ','.join(map(lambda i: unicode(i).replace(',', '\\,'), slice)))
        cw.set('event', 'id', EVENT_ID)
        self.log.debug('Saving race config to: ' + self.configpath)
        with open(self.configpath, 'wb') as f:
            cw.write(f)

    def shutdown(self, win=None, msg='Exiting'):
        """Terminate race object."""
        self.log.debug('Race shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def do_properties(self):
        """Run race properties dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'race_properties.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.meet.window)
        rt = b.get_object('race_score_type')
        if self.timetype != '200m':
            rt.set_active(0)
        else:
            rt.set_active(1)
        di = b.get_object('race_dist_entry')
        if self.distance is not None:
            di.set_text(str(self.distance))
        else:
            di.set_text('')
        du = b.get_object('race_dist_type')
        if self.units == 'metres':
            du.set_active(0)
        else:
            du.set_active(1)
        se = b.get_object('race_series_entry')
        se.set_text(self.series)
        as_e = b.get_object('auto_starters_entry')
        as_e.set_text(self.autospec)
        response = dlg.run()
        if response == 1:       # id 1 set in glade for "Apply"
            self.log.debug('Updating race properties.')
            if rt.get_active() == 0:
                self.set_timetype('start/finish')
            else:
                self.set_timetype('200m')
            dval = di.get_text()
            if dval.isdigit():
                self.distance = int(dval)
            if du.get_active() == 0:
                self.units = 'metres'
            else:
                self.units = 'laps'

            # update series
            ns = se.get_text()
            if ns != self.series:
                self.series = ns
                self.event[u'seri'] = ns

            # update auto startlist spec
            nspec = as_e.get_text()
            if nspec != self.autospec:
                self.autospec = nspec
                self.event[u'auto'] = nspec
                places = self.ctrl_places.get_text()
                if not places:
                    if self.autospec:
                        self.meet.autostart_riders(self, self.autospec,
                                                         self.seedsrc)
                    if self.evtype == 'handicap':
                        self.reorder_handicap()

            # xfer starters if not empty
            slist = strops.reformat_riderlist(
                          b.get_object('race_starters_entry').get_text(),
                                        self.meet.rdb, self.series).split()
            for s in slist:
                self.addrider(s)

            glib.idle_add(self.delayed_announce)
        else:
            self.log.debug('Edit race properties cancelled.')

        # if prefix is empty, grab input focus
        if self.prefix_ent.get_text() == '':
            self.prefix_ent.grab_focus()
        dlg.destroy()

    def resettimer(self):
        """Reset race timer."""
        self.finish = None
        self.start = None
        self.lstart = None
        self.timerstat = 'idle'
        self.ctrl_places.set_text('')
        self.placexfer('')
        self.meet.timer.dearm(self.startchan)
        self.meet.timer.dearm(self.finchan)
        uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Idle')
        self.stat_but.set_sensitive(True)
        self.set_elapsed()

    def setrunning(self):
        """Set timer state to 'running'."""
        self.timerstat = 'running'
        uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Running')

    def setfinished(self):
        """Set timer state to 'finished'."""
        self.timerstat = 'finished'
        uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Finished')
        self.stat_but.set_sensitive(False)
        self.ctrl_places.grab_focus()

    def armstart(self):
        """Toggle timer arm start state."""
        if self.timerstat == 'idle':
            self.timerstat = 'armstart'
            uiutil.buttonchg(self.stat_but, uiutil.bg_armstart, 'Arm Start')
            self.meet.timer.arm(self.startchan)
            if self.timetype == '200m':
                # also accept C0 on sprint types
                self.meet.timer.arm(timy.CHAN_START)
        elif self.timerstat == 'armstart':
            self.timerstat = 'idle'
            self.time_lbl.set_text('')
            uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Idle')
            self.meet.timer.dearm(self.startchan)
            if self.timetype == '200m':
                # also accept C0 on sprint types
                self.meet.timer.dearm(timy.CHAN_START)

    def armfinish(self):
        """Toggle timer arm finish state."""
        if self.timerstat == 'running':
            self.timerstat = 'armfinish'
            uiutil.buttonchg(self.stat_but, uiutil.bg_armfin, 'Arm Finish')
            self.meet.timer.arm(self.finchan)
        elif self.timerstat == 'armfinish':
            self.timerstat = 'running'
            uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Running')
            self.meet.timer.dearm(self.finchan)
        return False	# for use in delayed callback

    def showtimer(self):
        """Display the running time on the scoreboard."""
        if self.timerstat == 'idle':
            self.armstart()
        tp = 'Time:'
        if self.timetype == '200m':
            tp = '200m:'
        self.meet.announce.send_cmd(u'eliminated',u' ')
        self.meet.scbwin = scbwin.scbtimer(scb=self.meet.scb,
                                     line1=self.meet.racenamecat(self.event),
                                     line2=u'',
                                     timepfx=tp)
        wastimer = self.timerwin
        self.timerwin = True
        if self.timerstat == 'finished':
            if not wastimer:
                self.meet.scbwin.reset()
            if self.start is not None and self.finish is not None:
                elap = self.finish - self.start
                self.meet.scbwin.settime(elap.timestr(2))
                dist = self.meet.get_distance(self.distance, self.units)
                if dist:
                    self.meet.scbwin.setavg(elap.speedstr(dist))
                    ## UDP hack
                    spt=elap.rawspeed(dist).rjust(5) + u'   km/h'
                    msg = unt4.unt4( header=unichr(unt4.DC3) + u'S9',
                                    xx=1,
                                    yy=4,
                                    text=spt)
                    self.log.info(u'sending: ' + msg.text)
                    if not self.readonly:
                        self.meet.udptimer.sendto(msg.pack(),
                                              (self.meet.udpaddr,6789))
            self.meet.scbwin.update()
        else:
            self.meet.scbwin.reset()

    def key_event(self, widget, event):
        """Race window key press handler."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_abort:	# override ctrl+f5
                    self.resettimer()
                    return True
            if key[0] == 'F':
                if key == key_armstart:
                    self.armstart()
                    return True
                elif key == key_armfinish:
                    self.armfinish()
                    return True
                elif key == key_showtimer:
                    self.showtimer()
                    return True
                elif key == key_startlist:
                    self.do_startlist()
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_results:
                    self.doscbplaces = True	# override if already clear
                    self.do_places()
                    glib.idle_add(self.delayed_announce)
                    return True
                elif key == key_lapdown:
                    if self.runlap is not None and self.runlap > 0:
                        self.runlap -= 1
                    return True
        return False

    def do_places(self):
        """Update model and show race result on scoreboard."""
        ##self.placexfer(self.ctrl_places.get_text())
        secs = self.result_report()	# NOTE: calls into placexfer
        self.timerwin = False
        tp = 'Time:'
        if self.start is not None and self.finish is None:
            self.finish = tod.tod('now')
            self.set_elapsed()
        if self.timetype == '200m':
            tp = '200m:'
            # this was where winner was set on gemini... dubious
        ts = None
        if self.start is not None and self.finish is not None:
            ts = (self.finish - self.start).timestr(2)
        if self.doscbplaces:
            FMT = [(3, u'l'), (3,u'r'),u' ',
                  (self.meet.scb.linelen-11, u'l'),(4,u'r')]

            ## TODO: provisional/final?
            evtstatus = u'RESULT'
            self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                                  head=self.meet.racenamecat(self.event),
                                  subhead=evtstatus,
                                  coldesc=FMT, rows=self.results,
                                  #coldesc=FMT, rows=self.results[0:4],
                                  timepfx=tp, timestr=ts)
            self.meet.scbwin.reset()
            self.doscbplaces = False
            self.meet.announce.gfx_overlay(1)
            self.meet.announce.gfx_set_title(u'Result: ' 
                                + self.event[u'pref'] + u' '
                                + self.event[u'info'])
            if len(secs) > 0:
                for l in secs[0].lines:
                    self.meet.announce.gfx_add_row([l[0], l[2], l[4]])
        self.setfinished()

    def do_startlist(self):
        """Show start list on scoreboard."""

        self.reorder_handicap()
        self.meet.scbwin = None
        self.timerwin = False
        startlist = []
        self.meet.announce.gfx_overlay(1)
        self.meet.announce.gfx_set_title(u'Startlist: ' 
                            + self.event[u'pref'] + u' '
                            + self.event[u'info'])
        name_w = self.meet.scb.linelen-8
        for r in self.riders:
            if not r[5]:
                nfo = r[4]			# Try info field
                if self.evtype in [u'sprint']: # add asterisk
                    nfo = nfo + r[3].rjust(3)	# fall back on club/affil
                if nfo is None or nfo == '':
                    nfo = r[3]
                    if len(nfo) > 3:
                        # look it up?
                        if self.series in self.meet.ridermap:
                            rh = self.meet.ridermap[self.series][r[0]]
                            if rh is not None:
                                nfo = rh['note']
                startlist.append([r[0], strops.fitname(r[1], r[2],
                                 name_w), nfo])
                inf = r[COL_INFO].decode('utf-8','replace').strip()
                if self.evtype in [u'keirin', u'sprint']: # encirc draw no
                    inf = strops.drawno_encirc(inf)
                self.meet.announce.gfx_add_row([r[0], 
                                          strops.resname(r[1].decode('utf-8'),
                                                r[2].decode('utf-8'),
                                                r[3].decode('utf-8')),
                                                inf])
        FMT = [(3, u'r'), u' ', (name_w,u'l'),
                   (4,u'r')]
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                             head=self.meet.racenamecat(self.event),
                             subhead=u'STARTLIST',
                             coldesc=FMT, rows=startlist)
        self.meet.scbwin.reset()

    def stat_but_cb(self, button):
        """Race ctrl button callback."""
        if self.timerstat in ('idle', 'armstart'):
            self.armstart()
        elif self.timerstat in ('running', 'armfinish'):
            self.armfinish()

    def checkplaces(self, places=''):
        """Check the proposed places against current race model."""
        ret = True
        placeset = set()
        for no in strops.reformat_biblist(places).split():
            # repetition? - already in place set?
            if no in placeset:
                self.log.error('Duplicate no in places: ' + repr(no))
                ret = False
            placeset.add(no)
            # rider in the model?
            lr = self.getrider(no)
            if lr is None:
                if not self.meet.get_clubmode():
                    self.log.error('Non-starter in places: ' + repr(no))
                    ret = False
                # otherwise club mode allows non-starter in places
            else:
                # rider still in the race?
                if lr[COL_DNF]:
                    self.log.error(u'DNF rider in places: ' + repr(no))
                    ret = False
        return ret

    def race_ctrl_places_activate_cb(self, entry, data=None):
        """Respond to activate on place entry."""
        places = strops.reformat_placelist(entry.get_text())
        if self.checkplaces(places):
            entry.set_text(places)
            self.do_places()
            glib.idle_add(self.delayed_announce)
            self.meet.delayed_export() ## check this is ok here
        else:
            self.log.error('Places not updated.')

    def race_ctrl_action_activate_cb(self, entry, data=None):
        """Perform current action on bibs listed."""
        rlist = entry.get_text()
        acode = self.action_model.get_value(
                  self.ctrl_action_combo.get_active_iter(), 1)
        if acode == 'dnf':
            self.dnfriders(strops.reformat_biblist(rlist))
            entry.set_text('')
        elif acode == 'dns':
            self.dnsriders(strops.reformat_biblist(rlist))
            entry.set_text('')
        elif acode == 'dsq':
            self.dsqriders(strops.reformat_biblist(rlist))
            entry.set_text('')
        elif acode == 'add':
            rlist = strops.reformat_riderlist(rlist,
                                              self.meet.rdb, self.series)
            for bib in rlist.split():
                self.addrider(bib)
            entry.set_text('')
        elif acode == 'del':
            rlist = strops.reformat_riderlist(rlist,
                                              self.meet.rdb, self.series)
            for bib in rlist.split():
                self.delrider(bib)
            entry.set_text('')
        elif acode == 'lap':
            self.runlap = strops.confopt_posint(rlist)
        elif acode == 'com':	# add comment to contest
            self.add_comments(rlist)
            entry.set_text('')
        elif acode == 'out' and self.evtype == 'elimination':
            bib = rlist.strip()
            if self.eliminate(bib):
                entry.set_text('')
            ## HACK
            return False
        else:
            self.log.error('Ignoring invalid action.')
            return False
        glib.idle_add(self.delayed_announce)

    def add_comments(self, newcomments):
        """Append a race comment."""
        if len(self.comments)>0:
            self.comments += (u'!' + newcomments)
        elif len(self.comments) == 0:
            self.comments += newcomments
        self.log.info('Added race comment: ' + repr(newcomments))
        self.saveconfig()

    def update_expander_lbl_cb(self):
        """Update race info expander label."""
        self.info_expand.set_label(u'Race Info : ' 
                    + self.meet.racenamecat(self.event, 64))

    def eliminate(self, bib):
        """Register rider as eliminated."""
        ret = False
        r = self.getrider(bib)
        if r is not None:
            if not r[COL_DNF]:
                oplaces = self.ctrl_places.get_text()
                if bib not in strops.reformat_biblist(oplaces).split():
                    self.ctrl_places.set_text(bib + ' ' + oplaces)
                    ret = True
                    fname = r[COL_FIRSTNAME].decode('utf-8','replace')
                    lname = r[COL_LASTNAME].decode('utf-8','replace')
                    club = r[COL_CLUB].decode('utf-8','replace')
                    rno = r[COL_BIB].decode('utf-8','replace')
                    rstr = (rno + u' '
                            + strops.fitname(fname, lname,
                                          self.meet.scb.linelen-3-len(rno)))
                    self.meet.scbwin = scbwin.scbintsprint(scb=self.meet.scb,
                                 line1=self.meet.racenamecat(self.event),
                                 line2=u'Rider Eliminated',
                            coldesc=[ u' ',(self.meet.scb.linelen-1, u'l')],
                            rows=[ [rstr] ])
                    self.meet.scbwin.reset()
                    self.meet.gemini.reset_fields()
                    self.meet.gemini.set_bib(bib)
                    self.meet.gemini.show_brt()

                    self.meet.announce.send_cmd(u'eliminated', bib.rjust(2))
                    self.meet.announce.gfx_overlay(2)
                    self.meet.announce.gfx_set_title(
                                        self.event[u'pref'] + u' '
                              + self.event[u'info'] + u' - Eliminated:')
                    self.meet.announce.gfx_add_row([bib, 
                                strops.resname(fname, lname, club),''])
                    # and announce it:
                    nrstr = strops.truncpad(strops.resname_bib(
                                         r[COL_BIB].decode('utf-8','replace'), r[COL_FIRSTNAME].decode('utf-8','replace'),
                                         r[COL_LASTNAME].decode('utf-8','replace'), r[COL_CLUB].decode('utf-8','replace')), 60)
                    self.meet.announce.postxt(21, 0, u'Out: ' + nrstr)
                    self.log.info(u'Eliminated: ' + repr(bib))
                else:
                    self.log.error(u'Rider in places or eliminated: ' + repr(bib))
            else:
                self.log.error(u'Cannot eliminate dnf rider: ' + repr(bib))
        else:
            self.log.error(u'Cannot eliminate non-starter: ' + repr(bib))

        return ret

    def editent_cb(self, entry, col):
        """Shared event entry update callback."""
        if col == u'pref':
            self.event[u'pref'] = entry.get_text().decode('utf-8', 'replace')
        elif col == u'info':
            self.event[u'info'] = entry.get_text().decode('utf-8', 'replace')
        self.update_expander_lbl_cb()

    def editcol_cb(self, cell, path, new_text, col):
        """Startlist cell update callback."""
        new_text = new_text.decode('utf-8','replace').strip()
        if col == COL_BIB:
            if new_text.isalnum():
                if self.getrider(new_text) is None:
                    self.riders[path][COL_BIB] = new_text
                    dbr = self.meet.rdb.getrider(new_text, self.series)
                    if dbr is not None:
                        for i in range(1,4):
                            self.riders[path][i] = self.meet.rdb.getvalue(
                                                                 dbr, i)
        else:
            self.riders[path][col] = new_text.strip()

    def editcol_db(self, cell, path, new_text, col):
        """Cell update with writeback to meet."""
        new_text = new_text.decode('utf-8','replace').strip()
        self.riders[path][col] = new_text
        glib.idle_add(self.meet.rider_edit,
                      self.riders[path][COL_BIB].decode('utf-8','replace'),
                                           self.series, col, new_text)

    def gotorow(self, i=None):
        """Select row for specified iterator."""
        if i is None:
            i = self.riders.get_iter_first()
        if i is not None:
            self.view.scroll_to_cell(self.riders.get_path(i))
            self.view.set_cursor_on_cell(self.riders.get_path(i))

    def dnf_cb(self, cell, path, col):
        """Toggle rider dnf flag."""
        self.riders[path][col] = not self.riders[path][col]

    def starttrig(self, e):
        """React to start trigger."""
        if self.timerstat == 'armstart':
            if self.distance and self.units == u'laps':
                self.runlap = self.distance - 1
                self.log.debug(u'SET RUNLAP: ' + repr(self.runlap))
            self.start = e
            self.lstart = tod.tod('now')
            self.setrunning()
            if self.timetype == '200m':
                glib.timeout_add_seconds(4, self.armfinish)
                # delayed auto arm 200... 

    def fintrig(self, e):
        """React to finish trigger."""
        if self.timerstat == 'armfinish':
            self.finish = e
            self.setfinished()
            self.set_elapsed()
            self.log_elapsed()
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtimer:
                self.showtimer()
                #if self.start is not None:
                    #self.meet.gemini.rtick(self.finish - self.start, 2)
            glib.idle_add(self.delayed_announce)

    def rftimercb(self, e):
        """Handle rftimer event."""
        if e.refid == '' or e.chan == u'STS':       # got a trigger
            #return self.starttrig(e)
            return False

        # else assume this is a passing
        r = self.meet.rdb.getrefid(e.refid)
        if r is None:
            self.log.info('Unknown tag: ' + e.refid + '@' + e.rawtime(2))
            return False

        bib = self.meet.rdb.getvalue(r, riderdb.COL_BIB)
        ser = self.meet.rdb.getvalue(r, riderdb.COL_SERIES)
        if ser != self.series:
            self.log.error(u'Ignored non-series rider: ' + repr(bib))
            return False
        r = self.getrider(bib)
        if r is not None:
            self.log.info(u'SAW ' + repr(bib))
        return False

    def timercb(self, e):
        """Handle a timer event."""
        chan = timy.chan2id(e.chan)
            #chan = self.meet.timer.chanid(e.chan)
        if chan == self.startchan or chan == timy.CHAN_START:
            self.log.debug('Got a start impulse.')
            self.starttrig(e)
        elif chan == self.finchan:
            self.log.debug('Got a finish impulse.')
            self.fintrig(e)
        return False

    def timeout(self):
        """Update scoreboard and respond to timing events."""
        if not self.winopen:
            return False
        if self.finish is None:
            self.set_elapsed()
            if self.timerwin and type(self.meet.scbwin) is scbwin.scbtimer:
                self.meet.scbwin.settime(self.time_lbl.get_text())
        return True

    def race_info_time_edit_activate_cb(self, button):
        """Display race timing edit dialog."""
        ostx = ''
        oftx = ''
        if self.start is not None:
            ostx =  self.start.rawtime(4)
        else:
            ostx = '0.0'
        if self.finish is not None:
            oftx = self.finish.rawtime(4)
        ret = uiutil.edit_times_dlg(self.meet.window, ostx, oftx)
        if ret[0] == 1:
            try:
                stod = None
                if ret[1]:
                    stod = tod.tod(ret[1], 'MANU', 'C0i')
                    self.meet.timer.printline(' ' + str(stod))
                ftod = None
                if ret[2]:
                    ftod = tod.tod(ret[2], 'MANU', 'C1i')
                    self.meet.timer.printline(' ' + str(ftod))
                self.set_start(stod)
                self.set_finish(ftod)
                self.set_elapsed()
                if self.start is not None and self.finish is not None:
                    self.log_elapsed()
                self.log.info('Updated race times.')
            except Exception as v:
                self.log.error('Error updating times: ' + str(v))

            glib.idle_add(self.delayed_announce)
        else:
            self.log.info('Edit race times cancelled.')

    def result_gen(self):
        """Generator function to export a final result."""
        ft = None
        for r in self.riders:
            bib = r[COL_BIB].decode('utf-8','replace')
            rank = None
            info = ''
            if self.evtype in ['handicap', 'sprint']:
                # include handicap and previous win info
                info = r[COL_INFO].decode('utf-8','replace').strip()
            if self.onestart:
                if not r[COL_DNF]:
                    if r[COL_PLACE] is not None and r[COL_PLACE] != '':
                        rank = int(r[COL_PLACE].decode('utf-8','replace'))
                else:
                    if r[COL_INFO] in ['dns', 'dsq']:
                        rank = r[COL_INFO]
                    else:
                        rank = 'dnf'
            time = None
            if self.finish is not None and ft is None:
                time = (self.finish - self.start).rawtime(2)
                ft = True
            yield [bib, rank, time, info]

    def result_report(self, recurse=False):
        """Return a list of printing sections containing the race result."""
        self.placexfer(self.ctrl_places.get_text())
        ret = []
        sec = printing.section()
        sec.heading = u'Event ' + self.evno + u': ' + u' '.join([
                          self.event[u'pref'], self.event[u'info'] ]).strip()
        sec.lines = []
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        first = True
        fs = ''
        if self.finish is not None:
            fs = self.time_lbl.get_text().strip()
        rcount = 0
        pcount = 0
        for r in self.riders:
            plstr = u''
            rcount += 1
            rno = r[COL_BIB].decode('utf-8')
            rh = self.meet.newgetrider(rno, self.series)
            rname = u''
            if rh is not None:
                rname = rh[u'namestr']
            inf = r[COL_INFO].decode('utf-8','replace').strip()
            if self.evtype in [u'keirin', u'sprint']: # encirc draw no
                inf = strops.drawno_encirc(inf)
            if r[COL_DNF]:
                pcount += 1
                if r[COL_INFO] in ['dns', 'dsq']:
                    plstr = r[COL_INFO]
                    inf = None
                else:
                    plstr = u'dnf'
            elif self.onestart and r[COL_PLACE] != u'':
                plstr = r[COL_PLACE].decode('utf-8','replace') + u'.'
                pcount += 1
            # but suppress inf if within an omnium
            if self.inomnium:
                inf = None
            if self.evtype != u'handicap' and rh is not None and rh[u'ucicode']:
                inf = rh[u'ucicode']   # overwrite by force
            if plstr:	# don't emit a row for unplaced riders
                if not first:
                    sec.lines.append([plstr,rno,rname,inf, None, None])
                else:
                    sec.lines.append([plstr,rno,rname,inf, fs, None])
                    first = False
        if self.onestart:
            sec.subheading = substr
            if rcount > 0 and pcount < rcount:
                sec.subheading += u' - Provisional Result'
            else:
                sec.subheading += u' - Result'
        else:
            if substr:
                sec.subheading = substr

        ret.append(sec)

        if len(self.comments) > 0:
            sec = printing.bullet_text()
            sec.subheading = u'Decisions of the commisaires panel'
            templist = self.comments
            for temp in templist.split("!"):
                sec.lines.append([None, temp])
            ret.append(sec)
        return ret

    def destroy(self):
        """Signal race shutdown."""
        self.frame.destroy()

    def show(self):
        """Show race window."""
        self.frame.show()
  
    def hide(self):
        """Hide race window."""
        self.frame.hide()

    def __init__(self, meet, event, ui=True):
        """Constructor.

        Parameters:

            meet -- handle to meet object
            event -- event object handle
            ui -- display user interface?

        """
        self.meet = meet
        self.event = event
        self.evno = event[u'evid']
        self.evtype = event[u'type']
        self.series = event[u'seri']
        self.configpath = meet.event_configfile(self.evno)

        self.log = logging.getLogger('race')
        self.log.setLevel(logging.DEBUG)        # config may override?
        self.log.debug('Creating new event: ' + str(self.evno))
        self.results = []

        self.readonly = not ui
        self.comments = []
        self.onestart = False
        self.runlap = None
        self.lastrunlap = None
        self.start = None
        self.lstart = None
        self.finish = None
        self.winopen = ui	# window 'open' on proper load- or consult edb
        self.timerwin = False
        self.timerstat = 'idle'
        self.distance = None
        self.units = 'laps'
        self.timetype = 'start/finish'
        self.startplace = 0	# offset to first place in this race (hack)
        self.autospec = event[u'auto']	# automatic startlist
        self.inomnium = False
        self.seedsrc = None
        self.doscbplaces = True  # auto show result on scb
        self.reorderflag = 0
        self.startchan = timy.CHAN_START
        self.finchan = timy.CHAN_FINISH

        self.riders = gtk.ListStore(gobject.TYPE_STRING, # 0 bib
                                    gobject.TYPE_STRING, # 1 first name
                                    gobject.TYPE_STRING, # 2 last name
                                    gobject.TYPE_STRING, # 3 club
                                    gobject.TYPE_STRING, # 4 xtra info
                                    gobject.TYPE_BOOLEAN,# 5 DNF/DNS
                                    gobject.TYPE_STRING) # 6 placing

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'race.ui'))

        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # info pane
        self.info_expand = b.get_object('info_expand')
        b.get_object('race_info_evno').set_text(self.evno)
        self.showev = b.get_object('race_info_evno_show')
        self.prefix_ent = b.get_object('race_info_prefix')
        self.prefix_ent.connect('changed', self.editent_cb,u'pref')
        self.prefix_ent.set_text(self.event[u'pref'])
        self.info_ent = b.get_object('race_info_title')
        self.info_ent.connect('changed', self.editent_cb,u'info')
        self.info_ent.set_text(self.event[u'info'])

        self.time_lbl = b.get_object('race_info_time')
        self.time_lbl.modify_font(pango.FontDescription("monospace bold"))
        self.type_lbl = b.get_object('race_type')
        self.type_lbl.set_text(self.event[u'type'].capitalize())

        # ctrl pane
        self.stat_but = b.get_object('race_ctrl_stat_but')
        self.ctrl_places = b.get_object('race_ctrl_places')
        self.ctrl_action_combo = b.get_object('race_ctrl_action_combo')
        self.ctrl_action = b.get_object('race_ctrl_action')
        self.action_model = b.get_object('race_action_model')

        # riders pane
        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_enable_search(False)
        t.set_rules_hint(True)

        # riders columns
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'First Name', COL_FIRSTNAME,
                               self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Last Name', COL_LASTNAME,
                               self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Club', COL_CLUB, self.editcol_db)
        uiutil.mkviewcoltxt(t, 'Info', COL_INFO, self.editcol_cb)
        uiutil.mkviewcolbool(t, 'DNF', COL_DNF, self.dnf_cb)
        uiutil.mkviewcoltxt(t, 'Place', COL_PLACE, self.editcol_cb,
                                halign=0.5, calign=0.5)
        t.show()
        b.get_object('race_result_win').add(t)

        # start timer and show window
        if ui:
            # connect signal handlers
            b.connect_signals(self)
