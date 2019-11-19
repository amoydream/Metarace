
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

"""Omnium 'aggregate' model.

This module provides a class 'omnium' which implements the 'race' interface
and manages data, timing and scoreboard for omnium style aggregates.

Notes:

  - Event state is rebuilt on load of model.
  - Model does not (at this stage) provide methods for altering stages
  - dnf/dns is handled only crudely and inferred by reading stage result
 
"""

import os
import logging
import csv
import ConfigParser
import gtk
import glib
import gobject

import metarace
from metarace import scbwin
from metarace import tod
from metarace import uiutil
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import printing

# config version string
EVENT_ID = 'trackomnium-1.3'

# Model columns
COL_BIB = 0
COL_FIRST = 1
COL_LAST = 2
COL_CLUB = 3
COL_COMMENT = 4
COL_TOTAL = 5
COL_TIME = 6
COL_PLACE = 7
COL_POINTS = 8		# array of points for display

PENALTY = 10		# fixed fallback penalty for mass start

# scb function key mappings
key_reannounce = 'F4'	# (+CTRL)
key_abort = 'F5'	# (+CTRL)
key_startlist = 'F3'
key_results = 'F4'

class omnium(object):
    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        cr = ConfigParser.ConfigParser({'startlist':'',
                                        'id':EVENT_ID,
                                        'showinfo':'No',
                                        'events':'',
                                        'autospec':'',
                                        'evnicks':''})
        cr.add_section('event')
        cr.add_section('riders') # no need fr omnium??

        if os.path.isfile(self.configpath):
            self.log.debug('Attempting to read config from '
                               + repr(self.configpath))
            cr.read(self.configpath)
        #self.autospec = cr.get('event', 'autospec')
        for r in cr.get('event', 'startlist').split():
            self.addrider(r)
            ## TODO : load/save comment for rider

        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))

        self.events = strops.reformat_bibserlist(cr.get('event', 'events'))
        self.nicknames = cr.get('event', 'evnicks').split()
        self.recalculate()
        if not self.onestart:
           if self.autospec:
                self.meet.autostart_riders(self, self.autospec)

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get('event', 'id')
        if eid and eid != EVENT_ID:
            self.log.error('Event configuration mismatch: '
                           + repr(eid) + ' != ' + repr(EVENT_ID))
            #self.readonly = True

    def startlist_report(self, program=False):
        """Return a startlist report."""
        ret = []
        sec = printing.section()
        headvec = [u'Event', self.evno, u':', self.event[u'pref'],
                         self.event[u'info']]
        if not program:
            headvec.append(u'- Start List')
        sec.heading = u' '.join(headvec)
        sec.lines = []
        for r in self.riders:
            rno = r[COL_BIB]
            rname = strops.resname(r[COL_FIRST], r[COL_LAST],
                                       r[COL_CLUB])
            sec.lines.append([None, rno, rname, None, None, None])
        ret.append(sec)
        return ret

    def get_startlist(self):
        """Return a list of bibs in the rider model."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ' '.join(ret)

    def saveconfig(self):
        """Save race to disk."""
        if self.readonly:
            self.log.error('Attempt to save readonly ob.')
            return
        cw = ConfigParser.ConfigParser()
        cw.add_section('event')
        cw.set('event', 'startlist', self.get_startlist())
        cw.set('event', 'events', self.events)
        cw.set('event', 'evnicks', ' '.join(self.nicknames))
        cw.set('event', 'showinfo', self.info_expand.get_expanded())
        cw.set('event', 'autospec', self.autospec)
        cw.set('event', 'id', EVENT_ID)
        self.log.debug('Saving race config to: ' + self.configpath)
        with open(self.configpath, 'wb') as f:
            cw.write(f)

    def result_gen(self):
        """Generator function to export a final result."""
        for r in self.riders:
            bib = r[COL_BIB]
            rank = None
            info = ''
            if r[COL_PLACE] != '':
                if r[COL_PLACE].isdigit():
                    rank = int(r[COL_PLACE])
                    info = r[COL_PLACE]
                else:
                    # TODO: allow for 'dnf'/'dns' here, propagates into event
                    rank = r[COL_PLACE]
                    info = None		# no seeding info available
            time = None

            yield [bib, rank, time, info]

    def result_report(self, recurse=True):
        """Return a list of printing sections containing the race result."""
        ret = []
        sec = printing.section(u'omnium')
        sec.heading = 'Event ' + self.evno + ': ' + ' '.join([
                         self.event[u'pref'], self.event[u'info']]).strip()
        sec.subheading = self.standingstr()
        sec.lines = []
        aggtm = None
        prevline = None

        for r in self.riders:
            rno = r[COL_BIB].decode('utf-8')
            rh = self.meet.newgetrider(rno, self.series)
            rname = u''
            if rh is not None:
                rname = rh[u'namestr']
            rcat = None
            if rh is not None and rh[u'ucicode']:
                rcat = rh[u'ucicode']   # overwrite by force
            rank = ''
            if r[COL_PLACE] != '':
                rank = r[COL_PLACE]
                if rank.isdigit():
                    rank += '.'
            ptsstr = ''
            if r[COL_TOTAL] > 0:
                ptsstr = str(r[COL_TOTAL])
            tmstr = ''
            if r[COL_TIME] != tod.ZERO:
                tmstr = r[COL_TIME].rawtime(2)
            nrow = [rank, rno, rname, rcat, ptsstr, None, tmstr]
            if prevline:
                if rank and prevline[4] == ptsstr:	# same pts
                    prevline[5] = prevline[6]	# patch tmstr
                    nrow[5] = tmstr
                    aggtm = 'Time'
                prevline[6] = None
                sec.lines.append(prevline)
            prevline = nrow

        if prevline is not None:
            sec.lines.append(prevline)
        sec.colheader = [None, None, None, None, 'Points', aggtm]
        ret.append(sec)
        if recurse:
            for eno in self.events.split():
                if eno != self.evno:
                    lastev = eno
                    r = self.meet.get_event(eno, False)
                    if r is None:
                        self.log.error('Invalid Omnium config: aborting.')
                        return
                    r.loadconfig()
                    if r.onestart:
                        ret.extend(r.result_report())
                    else:
                        ret.extend(r.startlist_report())
                    r.destroy()
        return ret

    def addrider(self, bib='', info=None):
        """Add specified rider to race model."""
        nr=[bib, '', '', '', '', 0, tod.tod(0), '', []]
        if bib == '' or self.getrider(bib) is None:
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,5):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            return self.riders.append(nr)
        else:
            return None

    def getrider(self, bib):
        """Return temporary reference to model row."""
        ret = None
        for r in self.riders:
            if r[COL_BIB] == bib:
                ret = r         ## DANGER- Leaky ref
                break
        return ret

    def delrider(self, bib):
        """Remove the specified rider from the model."""
        i = self.getiter(bib)
        if i is not None:
            self.riders.remove(i)

    def getiter(self, bib):
        """Return temporary iterator to model row."""
        i = self.riders.get_iter_first()
        while i is not None:
            if self.riders.get_value(i, COL_BIB) == bib:
                break
            i = self.riders.iter_next(i)
        return i

    def clearplaces(self):
        """Zero internal model for recalculate."""
        for r in self.riders:
            r[COL_PLACE] = ''
            r[COL_TOTAL] = 0
            r[COL_TIME] = tod.tod(0)
            r[COL_POINTS] = []

    def standingstr(self):
        """Return an event status string for reports and scb."""
        ret = u''
        if self.curevent is not None and self.lastevent is not None:
            if self.curevent != self.lastevent:
                ev = self.meet.edb[self.curevent]
                if ev is not None:
                    if self.curevent in self.eventstatusmap:
                        if self.eventstatusmap[self.curevent] in ['running']:
                            # force virtual for race in progress
                            self.resultvirtual = True
                    if self.resultvirtual:
                        ret = u'Virtual Standings'
                    else:
                        ret = u'Standings after ' + ev[u'info']
                else:
                    ret = u'Standings'               
            else:
                ev = self.meet.edb[self.curevent]
                if ev is not None:
                    if ev[u'type'] == u'points':
                        if self.curevent in self.eventstatusmap:
                            if self.eventstatusmap[self.curevent] =='running':
                                # force virtual for race in progress
                                self.resultvirtual = True
                # TODO check for completion
                if self.resultvirtual:
                    ret = u'Virtual Standings'
                else:
                    ret = u'Final Result'
        return ret

    def sortomnium(self, x, y):
        """Sort results according to omnium rules."""

        # Comparison vecs: [idx, bib, rcnt, dnf, r[COL_TOTAL], r[COL_TIME]
        if x[3] == y[3]:	# Both dnf or not
            if x[2] == y[2]:	# Same number of results so far
                if x[3] == 'dnf':
                    return cmp(strops.riderno_key(x[1]),
                               strops.riderno_key(y[1]))# revert to bib
                else:
                    if x[4] == y[4]:			# same pts
                        if x[5] == y[5]:		# same aggregate time
                            return cmp(strops.riderno_key(x[1]),
                                       strops.riderno_key(y[1]))# revert to bib
                        else:
                            return cmp(x[5], y[5])
                    else:
                        return cmp(x[4], y[4])
            else:
                return cmp(y[2], x[2]) # Sort descending on rcount
        else:
                return cmp(x[3], y[3])		# In then DNF

    def recalculate(self):
        """Update internal model."""
        self.clearplaces()
        curev = None
        lastev = None

        # Pass one: Fill in points for all events
        self.resultvirtual = False # unless rescount differs for any riders
        self.eventstatusmap = {}
        rescount = {}
        penalty = len(self.riders)
        if penalty == 0:
            penalty = PENALTY
        self.log.debug(u'Set dnf penalty to: ' + repr(penalty))
        ecnt = 0
        for eno in self.events.split():
            if eno != self.evno:
                lastev = eno
                r = self.meet.get_event(eno, False)
                if r is None:
                    self.log.error('Invalid Omnium config: aborting.')
                    return
                r.loadconfig()
                self.eventstatusmap[eno] = r.timerstat
                if r.evtype == u'points': # Override for point score
                    sstr = r.standingstr().lower()
                    if sstr == u'result':	# only finished reliably
                        self.eventstatusmap[eno] = u'finished'
                    elif u'provisional' in sstr or u'standing' in sstr:
                        self.eventstatusmap[eno] = u'running'
                lastrank = 0	# used to track dnfs
                for res in r.result_gen():
                    bib = res[0]
                    lr = self.getrider(bib)
                    if lr is not None:
                        while len(lr[COL_POINTS]) < ecnt:
                            self.log.debug('Filling in rider ' + repr(bib)
                                          + ', missing from event ' + repr(eno))
                            lr[COL_POINTS].append('')
                        if len(lr[COL_POINTS]) == ecnt:
                            self.log.debug('res[1] = ' + repr(res[1]))
                            if res[1] is not None and lr[COL_PLACE] != 'dnf':
                                # track highest evt here
                                curev = eno
                                self.onestart = True
                                if type(res[1]) is int:
                                    lr[COL_TOTAL] += res[1]
                                    if bib not in rescount:
                                        rescount[bib] = 1
                                    else:
                                        rescount[bib] += 1
                                    lastrank = res[1]
                                elif res[1] == 'dnf':	# DNF gets pos+penalty
                                    lastrank += 1
                                    lr[COL_TOTAL] += lastrank + penalty
                                    if bib not in rescount:
                                        rescount[bib] = 1
                                    else:
                                        rescount[bib] += 1
                                elif res[1] in ['dsq', 'dns']:
                                    lr[COL_PLACE] = 'dnf'
                                lr[COL_POINTS].append(str(res[1]))

                                if type(res[2]) is tod.tod:
                                    lr[COL_TIME] = lr[COL_TIME] + res[2]
                            else:
                                lr[COL_POINTS].append('')
                                # this rider is not yet placed
                                if lastev == curev:
                                    self.resultvirtual = True
                                    # omnium race in progress
                                else:
                                    # race probably not yet started, ignore
                                    pass
                        else:
                            self.log.error('Ignoring duplicate result for rider ' + repr(bib) + ' in event ' + repr(eno))
                    else:
                        self.log.warn('Result for rider not in aggregate: ' + repr(bib) + ' in event ' + repr(eno))
                r.destroy()
            else:	# serious problem... but ignore for now
                self.log.error('Ignoring self in list of aggregate events.')
            ecnt += 1

        # record event handles for current and last event
        self.curevent = None
        if curev:
            self.curevent = curev
        self.lastevent = None
        if lastev:
            self.lastevent = lastev

        # Pass 2: Create aux map, check virtual and sort model
        rescheck = None 
        auxtbl = []
        idx = 0
        for r in self.riders:
            bib = r[COL_BIB]
            rcnt = 0
            if bib in rescount:
                rcnt = rescount[bib]
            dnf = False
            if r[COL_PLACE] != '':	# dnf
                dnf = True
            if not dnf:
                if rescheck is not None and rcnt != rescheck and rcnt != 0:
                    self.log.info(u'Force Virtual: ' + repr(rcnt) + u' != ' + repr(rescheck))
                    self.resultvirtual = True
                rescheck = rcnt
            arow = [idx, bib, rcnt, dnf, r[COL_TOTAL], r[COL_TIME]]
            auxtbl.append(arow)
            idx += 1
        if len(auxtbl) > 1:
            auxtbl.sort(self.sortomnium)
            self.riders.reorder([a[0] for a in auxtbl])

        # Pass 3: Fill in places
        idx = 0
        place = 0
        lp = 0
        lt = tod.tod(0)
        for r in self.riders:
            if r[COL_TOTAL] != lp or r[COL_TIME] > lt:
                place = idx + 1
            if r[COL_PLACE] != '':
                break
            if place > 0:
                r[COL_PLACE] = place
            idx += 1
            lp = r[COL_TOTAL]
            lt = r[COL_TIME]

    def key_event(self, widget, event):
        """Race window key press handler."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_abort or key == key_reannounce:
                    # override ctrl+f5
                    self.recalculate()
                    glib.idle_add(self.delayed_announce)
                    return True
            elif key[0] == 'F':
                if key == key_startlist:
                    self.do_startlist()
                    return True
                elif key == key_results:
                    self.do_places()
                    return True
        return False

    def delayed_announce(self):
        """Initialise the announcer's screen after a delay."""
        if self.winopen:
            self.meet.announce.clrall()

            self.meet.ann_title(' '.join([
                  'Event', self.evno, ':',
                  self.event[u'pref'], self.event[u'info'],
                  self.standingstr()]))
            self.meet.announce.linefill(1, '_')
            ha = [ '   ', '  #', 'Rider'.ljust(25), ' Pt ']
            for n in self.nicknames:	# configurable error?
                ha.append(strops.truncpad(n, 4, 'r'))
            ha.append('Tot Time'.rjust(10))
            self.meet.announce.setline(3, ' '.join(ha))

            l = 4
            for r in self.riders:
                plstr = ''
                if r[COL_PLACE] != '':
                    plstr = r[COL_PLACE]
                    if plstr.isdigit():
                        plstr += '.'
                plstr = strops.truncpad(plstr, 3, 'l')
                bibstr = strops.truncpad(r[COL_BIB], 3, 'r')
                clubstr = ''
                if r[COL_CLUB] != '':
                    clubstr = ' (' + r[COL_CLUB] + ')'
                namestr = strops.truncpad(strops.fitname(r[COL_FIRST],
                              r[COL_LAST], 25-len(clubstr))+clubstr, 25)
                ptsstr = '    '
                if r[COL_TOTAL] > 0:
                    ptsstr = strops.truncpad(str(r[COL_TOTAL]), 4, 'r')
                ol = [plstr, bibstr, namestr, ptsstr]
                for c in range(0, len(self.nicknames)):
                    if len(r[COL_POINTS]) > c:
                        ol.append(strops.truncpad(r[COL_POINTS][c], 4, 'r'))
                    else:
                        ol.append('    ')
                if r[COL_TIME] != tod.ZERO:
                    ol.append(strops.truncpad(r[COL_TIME].rawtime(3), 10, 'r'))
                else:
                    ol.append('          ')
                self.meet.announce.setline(l, ' '.join(ol))
                l += 1

        return False

    def do_startlist(self):
        """Show startlist on scoreboard."""
        self.meet.scbwin = None
        self.timerwin = False
        startlist = []
        name_w = self.meet.scb.linelen - 8
        fmt = [(3, u'r'), u' ', (name_w,u'l'), (4,u'r')]
        for r in self.riders:
            startlist.append([r[COL_BIB],
                              strops.fitname(r[COL_FIRST], r[COL_LAST],
                                             name_w),
                                  r[COL_CLUB]])
        evtstatus = u'Startlist'
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                                     head=self.meet.racenamecat(self.event),
                                     subhead=evtstatus,
                                     coldesc=fmt, rows=startlist)
        self.meet.scbwin.reset()

    def do_places(self):
        """Show race result on scoreboard."""
        resvec = []
        name_w = self.meet.scb.linelen - 10
        fmt = [(3,'l'),(3,'r'),' ',(name_w,'l'),(3,'r')]
        for r in self.riders:
            if r[COL_PLACE] is not None and r[COL_PLACE] != '':
                plstr = r[COL_PLACE].decode('utf-8')
                if plstr.isdigit():
                    if int(plstr) > 20:
                        break
                    plstr = plstr + u'.'
                resvec.append([plstr,
                    r[COL_BIB].decode('utf-8'),
                    strops.fitname(r[COL_FIRST].decode('utf-8'),
                                   r[COL_LAST].decode('utf-8'),
                                        name_w),
                    str(r[COL_TOTAL])])
        ## TODO: detect standings after nn/provisonal/final
        evtstatus = self.standingstr()
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                                    head=self.meet.racenamecat(self.event),
                                    subhead=evtstatus, coldesc=fmt,
                                    rows=resvec)
        self.meet.scbwin.reset()
        return False

    def shutdown(self, win=None, msg='Exiting'):
        """Terminate race object."""
        self.log.debug('Race shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def rftimercb(self, e):
        """Handle rftimer event."""
        return False

    def timercb(self, e):
        """Handle a timer event."""
        return False

    def timeout(self):
        """Update scoreboard and respond to timing events."""
        if not self.winopen:
            return False
        return True

    def do_properties(self):
        """Run race properties dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'omnium_properties.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.meet.window)
        se = b.get_object('race_series_entry')
        se.set_text(self.series)
        re = b.get_object('race_events_entry')
        re.set_text(self.events)
        ne = b.get_object('race_nicks_entry')
        ne.set_text(' '.join(self.nicknames))
        as_e = b.get_object('auto_starters_entry')
        as_e.set_text(self.autospec)
        response = dlg.run()
        if response == 1:       # id 1 set in glade for "Apply"
            self.log.debug('Updating race properties.')
            # update event config
            self.events = re.get_text()
            self.nicknames = ne.get_text().split()

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
                if not self.onestart:
                    if self.autospec:
                        self.meet.autostart_riders(self, self.autospec)

            # xfer starters if not empty
            slist = strops.reformat_riderlist(
                          b.get_object('race_starters_entry').get_text(),
                                        self.meet.rdb, self.series).split()
            for s in slist:
                self.addrider(s)

            self.recalculate()
            glib.idle_add(self.delayed_announce)
        else:
            self.log.debug('Edit race properties cancelled.')

        # if prefix is empty, grab input focus
        if self.prefix_ent.get_text() == '':
            self.prefix_ent.grab_focus()
        dlg.destroy()

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

    def update_expander_lbl_cb(self):
        """Update race info expander label."""
        self.info_expand.set_label('Race Info : '
                    + self.meet.racenamecat(self.event, 64))

    def editent_cb(self, entry, col):
        """Shared event entry update callback."""
        if col == u'pref':
            self.event[u'pref'] = entry.get_text().decode('utf-8', 'replace')
        elif col == u'info':
            self.event[u'info'] = entry.get_text().decode('utf-8', 'replace')
        self.update_expander_lbl_cb()

    def todstr(self, col, cr, model, iter, data=None):
        """Format tod into text for listview."""
        at = model.get_value(iter, COL_TIME)
        if at is not None and at != tod.ZERO:
            cr.set_property('text', at.timestr(3))
        else:
            cr.set_property('text', '')

    def editcol_db(self, cell, path, new_text, col):
        """Cell update with writeback to meet."""
        new_text = new_text.strip()
        self.riders[path][col] = new_text.strip()
        glib.idle_add(self.meet.rider_edit, self.riders[path][COL_BIB],
                                           self.series, col, new_text)

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
        # clear has no real meaning in omnium
        glib.idle_add(self.delayed_announce)

    def tod_context_edit_activate_cb(self, menuitem, data=None):
        """Run edit time dialog."""
        # no edit time for now in omnium
        glib.idle_add(self.delayed_announce)

    def tod_context_del_activate_cb(self, menuitem, data=None):
        """Delete selected row from race model."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter
            if self.riders.remove(i):
                pass    # re-select?
            glib.idle_add(self.delayed_announce)

    def __init__(self, meet, event, ui=True):
        """Constructor."""
        self.meet = meet
        self.event = event      # Note: now a treerowref
        self.evno = event[u'evid']
        self.evtype = event[u'type']
        self.series = event[u'seri']
        self.configpath = meet.event_configfile(self.evno)
        self.log = logging.getLogger('omnium')
        self.log.setLevel(logging.DEBUG)
        self.log.debug('opening event: ' + str(self.evno))
        self.curevent = None
        self.lastevent = None
        self.resultvirtual = False
        self.eventstatusmap = {}

        # race run time attributes
        self.onestart = False
        self.readonly = not ui
        self.winopen = True
        self.events = ''
        self.nicknames = []
        self.autospec = event[u'auto']      # automatic startlist


        self.riders = gtk.ListStore(gobject.TYPE_STRING, # 0 bib
                                    gobject.TYPE_STRING, # 1 first name
                                    gobject.TYPE_STRING, # 2 last name
                                    gobject.TYPE_STRING, # 3 club
                                    gobject.TYPE_STRING, # 4 comment
                                    gobject.TYPE_INT,    # 5 total
                                    gobject.TYPE_PYOBJECT, # 6 time total
                                    gobject.TYPE_STRING, # 7 place
                                    gobject.TYPE_PYOBJECT) # event points

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'omnium.ui'))

        self.frame = b.get_object('omnium_vbox')
        self.frame.connect('destroy', self.shutdown)

        # info pane
        self.info_expand = b.get_object('info_expand')
        b.get_object('omnium_info_evno').set_text(self.evno)
        self.showev = b.get_object('omnium_info_evno_show')
        self.prefix_ent = b.get_object('omnium_info_prefix')
        self.prefix_ent.connect('changed', self.editent_cb,u'pref')
        self.prefix_ent.set_text(self.event[u'pref'])
        self.info_ent = b.get_object('omnium_info_title')
        self.info_ent.connect('changed', self.editent_cb,u'info')
        self.info_ent.set_text(self.event[u'info'])

        self.update_expander_lbl_cb()

        # riders pane
        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_enable_search(False)
        t.set_rules_hint(True)
        t.connect('button_press_event', self.treeview_button_press)

        # riders columns
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'First Name', COL_FIRST,
                                self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Last Name', COL_LAST,
                                self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Club', COL_CLUB, self.editcol_db)
        uiutil.mkviewcoltxt(t, 'Points', COL_TOTAL, calign=1.0)
        uiutil.mkviewcoltod(t, 'Time', cb=self.todstr)
        uiutil.mkviewcoltxt(t, 'Rank', COL_PLACE,
                                halign=0.5, calign=0.5)
        t.show()
        b.get_object('omnium_result_win').add(t)

        self.context_menu = None
        if ui:
            # connect signal handlers
            b.connect_signals(self)
            b = gtk.Builder()
            b.add_from_file(os.path.join(metarace.UI_PATH, 'tod_context.ui'))
            self.context_menu = b.get_object('tod_context')
            b.connect_signals(self)
