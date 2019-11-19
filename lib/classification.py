
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

"""Classification/Medal meta-event

This module provides a class 'classification' which implements the 'race'
interface and manages data, timing and scoreboard for an overall
classification. Classification can also be used to aggregate a multi-part
event from other primitive elements, and then report them grouped in
result and export results.


Notes:

  - Event state is rebuilt on load of model.
 
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
EVENT_ID = 'trackclassify-1.4'

# Model columns
COL_BIB = 0
COL_FIRST = 1
COL_LAST = 2
COL_CLUB = 3
COL_COMMENT = 4
COL_PLACE = 5
COL_MEDAL = 6

# scb function key mappings
key_reannounce = 'F4'	# (+CTRL)
key_abort = 'F5'	# (+CTRL)
key_startlist = 'F3'
key_results = 'F4'

class classification(object):
    def loadconfig(self):
        """Load race config from disk."""
        cr = ConfigParser.ConfigParser({'id':EVENT_ID,
                                        'showinfo':'No',
                                        'showevents':'',
					'comment':'',
                                        'placesrc':'',
                                        'medals':'' })
        cr.add_section('event')

        if os.path.isfile(self.configpath):
            self.log.debug('Attempting to read config from '
                               + repr(self.configpath))
            cr.read(self.configpath)

        self.info_expand.set_expanded(strops.confopt_bool(
                                       cr.get('event', 'showinfo')))

        self.showevents = cr.get('event', 'showevents')
        self.placesrc = cr.get('event', 'placesrc')
        self.medals = cr.get('event', 'medals')
        self.comment = cr.get('event', 'comment')
        self.recalculate()	# model is cleared and loaded in recalc

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
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            sec.subheading = substr
        sec.lines = []
        for r in self.riders:
            rno = r[COL_BIB]
            if u't' in self.series: # Team no hack
                rno = u' '  # force name
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
        cw.set('event', 'showevents', self.showevents)
        cw.set('event', 'placesrc', self.placesrc)
        cw.set('event', 'medals', self.medals)
        cw.set('event', 'comment', self.comment)
        cw.set('event', 'showinfo', self.info_expand.get_expanded())
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
                    info = r[COL_MEDAL]
                else:
                    # TODO: allow for 'dnf'/'dns' here, propagates into event
                    rank = r[COL_PLACE]
                    info = None		# no seeding info available
            time = None

            yield [bib, rank, time, info]

    def result_report(self, recurse=True): # by default include inners
        """Return a list of printing sections containing the race result."""
        ret = []

        # start with the overall result
        sec = printing.section()
        sec.heading = u'Event ' + self.evno + u': ' + u' '.join([
                          self.event[u'pref'], self.event[u'info'] ]).strip()
        sec.lines = []
        lapstring = strops.lapstring(self.event[u'laps'])
        substr = u' '.join([lapstring, self.event[u'dist'],
                             self.event[u'prog']]).strip()
        if substr:
            sec.subheading = substr
        prevmedal = ''
        sec.lines = []
        for r in self.riders:
            rno = r[COL_BIB]
            rh = self.meet.newgetrider(rno, self.series)
            rname = u''
            plink = u''
            rcat = u''
            if u't' in self.series: # Team no hack
                rno = u' '  # force name
                if rh is not None:
                    rname = rh[u'first']
            else:
                if rh is not None:
                    rname = rh[u'namestr']
                    if rh[u'ucicode']:
                        rcat = rh[u'ucicode']   # overwrite by force

                    # consider partners here
                    if rh[u'cat'] and u'tandem' in rh[u'cat'].lower():
                        ph = self.meet.newgetrider(rh[u'note'], self.series)
                        if ph is not None:
                            plink = [u'', u'', ph[u'namestr'] + u' - Pilot', ph[
u'ucicode'], u'', u'',u'']

            rank = ''
            if r[COL_PLACE] != '':
                rank = r[COL_PLACE]
                if rank.isdigit():
                    rank += '.'

            medal = ''
            if r[COL_MEDAL] != '':
                medal = r[COL_MEDAL]
            if medal == '' and prevmedal != '':
                # add empty line
                sec.lines.append([None, None, None])
            prevmedal = medal

            nrow = [rank, rno, rname, rcat, None, medal,plink]
            sec.lines.append(nrow)
            if u't' in self.series:
                for trno in strops.reformat_riderlist(rh[u'note']).split():
                    trh = self.meet.newgetrider(trno) #!! SERIES?
                    if trh is not None:
                        trname = trh[u'namestr']
                        trinf = trh[u'ucicode']
                        sec.lines.append([None, trno, trname, trinf,
                                                 None, None, None])
        ret.append(sec)

        if recurse:
            # then append each of the specified events
            rset = set() # init graph with self
            rset.add(self.evno)
            for evno in self.showevents.split():
                if evno and evno not in rset:
                    rset.add(evno)
                    self.log.debug(repr(self.evno) + u': adding info from event ' + repr(evno))
                    r = self.meet.get_event(evno, False)
                    if r is None:
                        self.log.error(repr(self.evno) + u': Invalid ev in recurse')
                        continue
                    r.loadconfig()	# now have queryable event handle
                    if r.onestart:	# go for result
                        ret.extend(r.result_report())
                    else:		# go for startlist
                        ret.extend(r.startlist_report())
                    r.destroy()
                else:
                    self.log.info(repr(self.evno) + u': Skipping event ' + repr(evno))
        return ret

    def addrider(self, bib='', place=''):
        """Add specified rider to race model."""
        nr=[bib, '', '', '', '', '', '']
        if bib == '' or self.getrider(bib) is None:
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                for i in range(1,5):
                    nr[i] = self.meet.rdb.getvalue(dbr, i)
            nr[COL_PLACE] = place
            return self.riders.append(nr)
        else:
            self.log.info('Attempt to add duplicate rider: ' + repr(bib))
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

    def recalculate(self):
        """Update internal model."""
        self.riders.clear()

        # Pass one: Fill in riders - in order
        placeoft = 0
        for placegroup in self.placesrc.split(';'):
            self.log.debug('Place group: ' + repr(placegroup))
            specvec = placegroup.split(':')
            if len(specvec) == 2:
                evno = specvec[0].strip()
                if evno != self.evno:
                    r = self.meet.get_event(evno, False)
                    if r is None:
                        self.log.error('Invalid places config: aborting.')
                        return
                    r.loadconfig()	# now have queryable event handle
                    placeset = strops.placeset(specvec[1])
                    curevplace = 0
                    curevoft = 0
                    lrank = -1
                    for res in r.result_gen():
                        if ((type(res[1]) is int and res[1] in placeset)
                            or (r.evtype == 'pursuit race' and res[1])):
                            curevoft += 1
                            if res[1] == lrank:
                                self.log.debug('Duplicate place '
                                              + repr(res[1]) + ' in event '
                                              + repr(evno))
                            else:
                                curevplace = curevoft
                            lrank = res[1]
                            self.addrider(res[0], str(placeoft + curevplace))
                    r.destroy()
                    placeoft += len(placeset)
            else:
                self.log.warn('Ignoring erroneous autospec group: '
                               + repr(placegroup) + ' in event '
                               + repr(self.evno))
        if len(self.riders) > 0:	# got at least one result to report
            self.onestart = True

        # Pass two: Mark medals if possible
        medalmap = {}
        mcount = 1
        for m in self.medals.split():
            medalmap[mcount] = m
            mcount += 1
        for r in self.riders:
            if r[COL_PLACE].isdigit():
                rank = int(r[COL_PLACE])
                if rank in medalmap:
                    r[COL_MEDAL] = medalmap[rank]
        return

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
# TODO
        if self.winopen:
            self.meet.announce.clrall()
            self.meet.ann_title(' '.join([
                  'Event', self.evno, ':', self.event[u'pref'],
                   self.event[u'info']]))
            self.meet.announce.linefill(1, '_')
            lmedal = ''
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
                medal = r[COL_MEDAL]
                if lmedal != '' and medal == '':
                    l += 1	 # gap to medals
                lmedal = medal
                ol = [plstr, bibstr, namestr, medal]
                self.meet.announce.setline(l, ' '.join(ol))
                l += 1

        return False

    def do_startlist(self):
        """Show startlist on scoreboard."""
        return

    def do_places(self):
        """Show race result on scoreboard."""
        # Draw a 'medal ceremony' on the screen
        resvec = []
        count = 0
        teamnames = False
        name_w = self.meet.scb.linelen - 12
        fmt = [(3,u'l'), (4,u'r'), u' ',(name_w, u'l'), (4,u'r')]
        if self.series and self.series[0].lower() == u't':
            teamnames = True
            name_w = self.meet.scb.linelen - 8
            fmt = [(3,u'l'), u' ',(name_w, u'l'), (4,u'r')]

        for r in self.riders:
            plstr = r[COL_PLACE].decode('utf-8')
            if plstr.isdigit():
                #if int(plstr) > 20:
                    #break
                plstr = plstr + u'.'
            no = r[COL_BIB]
            first=r[COL_FIRST].decode('utf-8')
            last=r[COL_LAST].decode('utf-8')
            club=r[COL_CLUB].decode('utf-8')
            if not teamnames:
                resvec.append([plstr, no, strops.fitname(first,last, name_w),
                                     club])
            else:
                resvec.append([plstr, first, club])
            count += 1
        self.meet.scbwin = None
        header=self.meet.racenamecat(self.event)
        ## TODO: Flag Provisional
        evtstatus='Final Classification'.upper()
        self.meet.scbwin = scbwin.scbtable(scb=self.meet.scb,
                                  head=self.meet.racenamecat(self.event),
                                  subhead=evtstatus,
                                  coldesc=fmt, rows=resvec)
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
        b.add_from_file(os.path.join(metarace.UI_PATH,
                                     'classification_properties.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.meet.window)
        se = b.get_object('race_series_entry')
        se.set_text(self.series)
        ee = b.get_object('race_showevents_entry')
        ee.set_text(self.showevents)
        pe = b.get_object('race_placesrc_entry')
        pe.set_text(self.placesrc)
        me = b.get_object('race_medals_entry')
        me.set_text(self.medals)
        response = dlg.run()
        if response == 1:       # id 1 set in glade for "Apply"
            self.log.debug('Updating race properties.')
            # update event config
            self.placesrc = pe.get_text()
            self.medals = me.get_text()
            self.showevents = ee.get_text()

            # update series
            ns = se.get_text()
            if ns != self.series:
                self.series = ns
                self.event[u'seri'] = ns

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

    def editcol_db(self, cell, path, new_text, col):
        """Cell update with writeback to meet."""
        new_text = new_text.decode('utf-8','replace').strip()
        self.riders[path][col] = new_text
        glib.idle_add(self.meet.rider_edit,
                      self.riders[path][COL_BIB].decode('utf-8','replace'),
                                           self.series, col, new_text)

    def __init__(self, meet, event, ui=True):
        """Constructor."""
        self.meet = meet
        self.event = event      # Note: now a treerowref
        self.evno = event[u'evid']
        self.evtype = event[u'type']
        self.series = event[u'seri']
        self.autospec = event[u'auto']
        self.configpath = meet.event_configfile(self.evno)
        self.log = logging.getLogger('classification')
        self.log.setLevel(logging.DEBUG)
        self.log.debug(u'opening event: ' + str(self.evno))

        # race run time attributes
        self.onestart = True	# hack for AJTC
        self.readonly = not ui
        self.winopen = ui
        self.placesrc = ''
        self.medals = ''
        self.comment = ''

        self.riders = gtk.ListStore(gobject.TYPE_STRING, # 0 bib
                                    gobject.TYPE_STRING, # 1 first name
                                    gobject.TYPE_STRING, # 2 last name
                                    gobject.TYPE_STRING, # 3 club
                                    gobject.TYPE_STRING, # 4 comment
                                    gobject.TYPE_STRING, # 5 place
                                    gobject.TYPE_STRING) # 6 medal

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'omnium.ui'))

        self.frame = b.get_object('omnium_vbox')
        self.frame.connect('destroy', self.shutdown)

        # info pane
        self.info_expand = b.get_object('info_expand')
        b.get_object('omnium_info_evno').set_text(self.evno)
        self.showev = b.get_object('omnium_info_evno_show')
        self.prefix_ent = b.get_object('omnium_info_prefix')
        self.prefix_ent.set_text(self.event[u'pref'])
        self.prefix_ent.connect('changed', self.editent_cb,u'pref')
        self.info_ent = b.get_object('omnium_info_title')
        self.info_ent.set_text(self.event[u'info'])
        self.info_ent.connect('changed', self.editent_cb,u'info')
        self.update_expander_lbl_cb()

        # riders pane
        t = gtk.TreeView(self.riders)
        self.view = t
        t.set_rules_hint(True)

        # riders columns
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'First Name', COL_FIRST,
                                self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Last Name', COL_LAST,
                                self.editcol_db, expand=True)
        uiutil.mkviewcoltxt(t, 'Club', COL_CLUB, self.editcol_db)
        uiutil.mkviewcoltxt(t, 'Rank', COL_PLACE,
                                halign=0.5, calign=0.5)
        uiutil.mkviewcoltxt(t, 'Medal', COL_MEDAL)
        t.show()
        b.get_object('omnium_result_win').add(t)

        self.context_menu = None
        if ui:
            # connect signal handlers
            b.connect_signals(self)
