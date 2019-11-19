
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

"""Timing and data handling application for track meets at DISC."""

import pygtk
pygtk.require("2.0")

import gtk
import glib
import pango

import os
import sys
import logging
## HACK
import socket
import threading
import time

import metarace

from metarace import jsonconfig
from metarace import tod
from metarace import riderdb
from metarace import eventdb
from metarace import scbwin
from metarace import sender
from metarace import telegraph
from metarace import dbexport
from metarace import rsync
from metarace import timy
from metarace import thbc
from metarace import gemini
from metarace import unt4
from metarace import strops
from metarace import loghandler
from metarace import race
from metarace import ps
from metarace import ittt
from metarace import f200
from metarace import f200train
from metarace import flap
from metarace import sprnd
from metarace import spfin
from metarace import omnium
from metarace import classification
from metarace import printing
from metarace import uiutil
from metarace import analysis
from metarace import htlib

LOGFILE = u'event.log'
LOGHANDLER_LEVEL = logging.INFO	# check 
CONFIGFILE = u'config.json'
TRACKMEET_ID = u'trackmeet_1.9'	# configuration versioning
EXPORTPATH = u'export'
MAXREP = 10000	# communique max number

def mkrace(meet, event, ui=True):
    """Return a race object of the correct type."""
    ret = None
    etype = event[u'type']
    if etype in [u'indiv tt',
                 u'indiv pursuit', u'pursuit race',
                 u'team pursuit', u'team pursuit race']:
        ret = ittt.ittt(meet, event, ui)
    elif etype in [u'points', u'madison', u'tempo']:
        ret = ps.ps(meet, event, ui)
    elif etype in [u'omnium', u'aggregate']:
        ret = omnium.omnium(meet, event, ui)
    elif etype in [u'hotlap']:
        ret = hotlap.hotlap(meet, event, ui)
    elif etype == u'classification':
        ret = classification.classification(meet, event, ui)
    elif etype in [u'flying lap']:
        ret = flap.flap(meet, event, ui)
    elif etype in [u'flying 200']:
        ret = f200.f200(meet, event, ui)
    elif etype in [u'flying 200 training']:
		ret = f200train.f200train(meet,event,ui)
    elif etype in [u'sprint round']:
        ret = sprnd.sprnd(meet, event, ui)
    elif etype in [u'sprint final']:
        ret = spfin.spfin(meet, event, ui)
    else:
        ret = race.race(meet, event, ui)
    return ret

class trackmeet:
    """Track meet application class."""

    ## Meet Menu Callbacks
    def menu_meet_open_cb(self, menuitem, data=None):
        """Open a new meet."""
        self.close_event()

        dlg = gtk.FileChooserDialog(u'Open new track meet', self.window,
            gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER, (gtk.STOCK_CANCEL,
            gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        response = dlg.run()
        if response == gtk.RESPONSE_OK:
            self.configpath = dlg.get_filename()
            self.loadconfig()
            self.log.debug(u'Meet data loaded from'
                           + repr(self.configpath) + u'.')
        dlg.destroy()

    def get_event(self, evno, ui=False):
        """Return an event object for the given event number."""
        # NOTE: returned event will need to be destroyed
        ret = None
        eh = self.edb[evno]
        if eh is not None:
            ret = mkrace(self, eh, ui)
        return ret

    def menu_meet_save_cb(self, menuitem, data=None):
        """Save current meet data and open event."""
        self.saveconfig()

    def menu_meet_info_cb(self, menuitem, data=None):
        """Display meet information on scoreboard."""
        self.gemini.clear()
        self.clock.clicked()
        self.announce.gfx_overlay(2, self.graphicscb)

    def menu_meet_properties_cb(self, menuitem, data=None):
        """Edit meet properties."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'trackmeet_props.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.window)

        # load meet meta
        tent = b.get_object('meet_title_entry')
        tent.set_text(self.titlestr)
        stent = b.get_object('meet_subtitle_entry')
        stent.set_text(self.subtitlestr)
        dent = b.get_object('meet_date_entry')
        dent.set_text(self.datestr)
        lent = b.get_object('meet_loc_entry')
        lent.set_text(self.locstr)
        cent = b.get_object('meet_comm_entry')
        cent.set_text(self.commstr)
        oent = b.get_object('meet_org_entry')
        oent.set_text(self.orgstr)
        lo = b.get_object('meet_logos_entry')
        lo.set_text(self.logos)

        # load data/result opts
        re = b.get_object('data_showevno')
        re.set_active(self.showevno)
        cm = b.get_object('data_clubmode')
        cm.set_active(self.clubmode)
        prov = b.get_object('data_provisional')
        prov.set_active(self.provisional)
        tln = b.get_object('tracklen_total')
        tln.set_value(self.tracklen_n)
        tld = b.get_object('tracklen_laps')
        tldl = b.get_object('tracklen_lap_label')
        tld.connect('value-changed',
                    self.tracklen_laps_value_changed_cb, tldl)
        tld.set_value(self.tracklen_d)

        # scb/timing ports
        spe = b.get_object('scb_port_entry')
        spe.set_text(self.scbport)
        upe = b.get_object('uscb_port_entry')
        upe.set_text(self.annport)
        spb = b.get_object('scb_port_dfl')
        spb.connect('clicked', self.set_default, spe, u'DISC')
        mte = b.get_object('timing_main_entry')
        mte.set_text(self.main_port)
        mtb = b.get_object('timing_main_dfl')
        mtb.connect('clicked', self.set_default, mte, timy.MAINPORT)
        bte = b.get_object('timing_backup_entry')
        bte.set_text(self.backup_port)
        btb = b.get_object('timing_backup_dfl')
        btb.connect('clicked', self.set_default, bte, timy.BACKUPPORT)

        # run dialog
        response = dlg.run()
        if response == 1:	# id 1 set in glade for "Apply"
            self.log.debug(u'Updating meet properties.')

            # update meet meta
            self.titlestr = tent.get_text().decode('utf-8','replace')
            self.subtitlestr = stent.get_text().decode('utf-8','replace')
            self.datestr = dent.get_text().decode('utf-8','replace')
            self.locstr = lent.get_text().decode('utf-8','replace')
            self.commstr = cent.get_text().decode('utf-8','replace')
            self.orgstr = oent.get_text().decode('utf-8','replace')
            self.logos = lo.get_text().decode('utf-8','replace')
            self.set_title()

            self.clubmode = cm.get_active()
            self.showevno = re.get_active()
            self.provisional = prov.get_active()
            self.tracklen_n = tln.get_value_as_int()
            self.tracklen_d = tld.get_value_as_int()
            nport = spe.get_text().decode('utf-8','replace')
            if nport != self.scbport:
                # TODO: swap type handler if necessary
                self.scbport = nport
                self.scb.setport(nport)
            nport = upe.get_text().decode('utf-8','replace')
            if nport != self.annport:
                self.annport = nport
                self.announce.set_portstr(self.annport)
            nport = mte.get_text().decode('utf-8','replace')
            if nport != self.main_port:
                self.main_port = nport
                self.main_timer.setport(nport)
            nport = bte.get_text().decode('utf-8','replace')
            if nport != self.backup_port:
                self.backup_port = nport
                self.backup_timer.setport(nport)
            self.log.debug(u'Properties updated.')
        else:
            self.log.debug(u'Edit properties cancelled.')
        dlg.destroy()

    def menu_meet_fullscreen_toggled_cb(self, button, data=None):
        """Update fullscreen window view."""
        if button.get_active():
            self.window.fullscreen()
        else:
            self.window.unfullscreen()

    def tracklen_laps_value_changed_cb(self, spin, lbl):
        """Laps changed in properties callback."""
        if int(spin.get_value()) > 1:
            lbl.set_text(u' laps = ')
        else:
            lbl.set_text(u' lap = ')

    def set_default(self, button, dest, val):
        """Update dest to default value val."""
        dest.set_text(val)

    def menu_meet_quit_cb(self, menuitem, data=None):
        """Quit the track meet application."""
        self.running = False
        self.window.destroy()

    def default_template(self):
        tfile = os.path.join(self.configpath, u'pdf_template.json')
        if not os.path.exists(tfile):
            tfile = None
        return tfile

    ## Report printing support
    def print_report(self, sections=[], subtitle=u'', docstr=u'',
                           prov=False, doprint=True, exportfile=None):
        """Print the pre-formatted sections in a standard report."""
        self.log.info(u'Printing report ' + repr(subtitle) + u'\u2026')

        tfile = self.default_template()
        rep = printing.printrep(template=tfile, path=self.configpath)
        rep.set_provisional(prov)
        rep.strings[u'title'] = self.titlestr
        # subtitle should probably be property of meet
        rep.strings[u'subtitle'] = (self.subtitlestr + u' ' + subtitle).strip()
        rep.strings[u'datestr'] = strops.promptstr(u'Date:', self.datestr)
        rep.strings[u'commstr'] = strops.promptstr(u'Chief Commissaire:',
                                                  self.commstr)
        rep.strings[u'orgstr'] = strops.promptstr(u'Organiser: ', self.orgstr)
        rep.strings[u'docstr'] = docstr
        rep.strings[u'diststr'] = self.locstr
        for sec in sections:
            rep.add_section(sec)

        # write out to files if exportfile set
        if exportfile:
            ofile = os.path.join(self.exportpath, exportfile+u'.pdf')
            with open(ofile, 'wb') as f:
                rep.output_pdf(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.xls')
            with open(ofile, 'wb') as f:
                rep.output_xls(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.json')
            with open(ofile, 'wb') as f:
                rep.output_json(f)
            lb = u''
            lt = []
            if self.mirrorpath:
                lb = os.path.join(self.linkbase, self.mirrorpath, exportfile)
                lt = [u'pdf', u'xls', u'json']
            ofile = os.path.join(self.exportpath, exportfile+u'.txt')
            with open(ofile, 'wb') as f:
                rep.output_text(f, linkbase=lb, linktypes=lt)
            if self.htmlfiles:
                ofile = os.path.join(self.exportpath, exportfile+u'.html')
                with open(ofile, 'wb') as f:
                    rep.output_html(f, linkbase=lb, linktypes=lt)
        
        if not doprint:
            return False

        print_op = gtk.PrintOperation()
        print_op.set_allow_async(True)
        print_op.set_print_settings(self.printprefs)
        print_op.set_default_page_setup(self.pageset)
        print_op.connect("begin_print", self.begin_print, rep)
        print_op.connect("draw_page", self.draw_print_page, rep)
        self.log.debug('calling into print op...')
        res = print_op.run(gtk.PRINT_OPERATION_ACTION_PREVIEW,
                               None)
        if res == gtk.PRINT_OPERATION_RESULT_APPLY:
            self.printprefs = print_op.get_print_settings()
            self.log.debug(u'Updated print preferences.')
        elif res == gtk.PRINT_OPERATION_RESULT_IN_PROGRESS:
            self.log.debug(u'Print operation running asynchronously.')

        # may be called via idle_add
        return False

    def print_analysis(self, evno, sections=[], subtitle=u'', docstr=u'',
                           prov=False, doprint=True, exportfile=None):
        """Print the pre-formatted sections in a standard report."""
        exportfile = u'event_' + unicode(evno) + u'_analysis'

        tfile = self.default_template()
        rep = printing.printrep(template=tfile, path=self.configpath)
        rep.set_provisional(prov)
        rep.strings[u'title'] = self.titlestr
        # subtitle should probably be property of meet
        rep.strings[u'subtitle'] = (self.subtitlestr + u' ' + subtitle).strip()
        rep.strings[u'datestr'] = strops.promptstr(u'Date:', self.datestr)
        rep.strings[u'commstr'] = strops.promptstr(u'Chief Commissaire:',
                                                  self.commstr)
        rep.strings[u'orgstr'] = strops.promptstr(u'Organiser: ', self.orgstr)
        rep.strings[u'docstr'] = docstr
        rep.strings[u'diststr'] = self.locstr
        for sec in sections:
            rep.add_section(sec)

        # write out to files if exportfile set
        if exportfile:
            ofile = os.path.join(self.exportpath, exportfile+u'.pdf')
            with open(ofile, 'wb') as f:
                rep.output_pdf(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.xls')
            with open(ofile, 'wb') as f:
                rep.output_xls(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.json')
            with open(ofile, 'wb') as f:
                rep.output_json(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.txt')
            with open(ofile, 'wb') as f:
                rep.output_text(f)
            ofile = os.path.join(self.exportpath, exportfile+u'.html')
            with open(ofile, 'wb') as f:
                rep.output_html(f)
        return
    
    def begin_print(self,  operation, context, rep):
        """Set print pages and units."""
        rep.start_gtkprint(context.get_cairo_context())
        operation.set_n_pages(rep.get_pages())
        operation.set_unit("points")

    def draw_print_page(self, operation, context, page_nr, rep):
        """Draw to the nominated page."""
        rep.set_context(context.get_cairo_context())
        rep.draw_page(page_nr)

    def menu_meet_printprefs_activate_cb(self, menuitem=None, data=None):
        """Edit the printer properties."""
        dlg = gtk.PrintOperation()
        dlg.set_print_settings(self.printprefs)
        res = dlg.run(gtk.PRINT_OPERATION_ACTION_PRINT_DIALOG, self.window)
        if res == gtk.PRINT_OPERATION_RESULT_APPLY:
            self.printprefs = dlg.get_print_settings()
            self.log.info(u'Updated print preferences.')

    def find_communique(self, lookup):
        """Find or allocate a communique number."""
        ret = None
        cnt = 1
        noset = set()
        for c in self.commalloc:
            if c == lookup:	# previous allocation
                ret = self.commalloc[c]
                self.log.debug(u'Found allocation: ' + ret + u' -> ' + lookup)
                break
            else:
                noset.add(self.commalloc[c])
        if ret is None:		# not yet allocated
            while True:
                ret = unicode(cnt)
                if ret not in noset:
                    self.commalloc[lookup] = ret	# write back
                    self.log.debug(u'Add allocation: ' + ret + u' -> ' + lookup)
                    break
                else:
                    cnt += 1
                    if cnt > MAXREP:
                        self.log.error(u'Gave up looking for communique no.')
                        break	# safer
        return ret

    ## Event action callbacks
    def eventdb_cb(self, evlist, reptype=None):
        """Make a report containing start lists for the events listed."""
        # Note: selections via event listing override extended properties
        #       even if the selection does not really make sense, this
        #       allows for creation of reports manually crafted.
        secs = []
        reptypestr = reptype.title()
        lsess = None
        for eno in evlist:
            e = self.edb[eno]
            nsess = e['sess']
            if nsess != lsess and lsess is not None:
                secs.append(printing.pagebreak(0.1))
            lsess = nsess
            h = mkrace(self, e, False)
            h.loadconfig()
            if reptype == 'startlist':
                secs.extend(h.startlist_report())
            elif reptype == 'result':
                reptypestr = 'Results'
                # from event list only include the individual events
                secs.extend(h.result_report(recurse=False))
            elif reptype == 'program':
                reptypestr = 'Program of Events'
                secs.extend(h.startlist_report(True))	# startlist in program
            else:
                self.log.error(u'Unknown report type in eventdb calback: '
                                 + repr(reptype))
            h.destroy()
            secs.append(printing.pagebreak())
        if len(secs) > 0:
            reporthash = reptype + u', '.join(evlist)
            if self.communiques:	# prompt for communique no
                #commno = uiutil.communique_dialog(self.meet.window)

                #if commno is not None and len(commno) > 1:
                gtk.gdk.threads_enter()
                rvec = uiutil.edit_times_dlg(self.window,
                                   stxt='', ftxt='')
                gtk.gdk.threads_leave()                                                     
                if len(rvec) > 1 and rvec[0] == 1:
                    commno = self.find_communique(reporthash)
                    if rvec[1]:	# it's a revision
                        commno += rvec[1]
                    if commno is not None:
                        reptypestr = (u'Communiqu\u00e9 '
                                   + commno + ': ' + reptypestr)
                    if rvec[2]:
                        msgsec = printing.bullet_text()
                        msgsec.subheading = u'Revision ' + repr(rvec[1])
                        msgsec.lines.append([u'',rvec[2]])
                        ## signature
                        secs.append(msgsec)
            self.print_report(secs, docstr=reptypestr, exportfile=u'trackmeet_' + reptype)
				# no need to export file now, since deadlock
				# resolved with async print op
                                #exportfile=u'trackmeet_' + reptype)
        else:
            self.log.info(reptype + u' callback: Nothing to report.')
        return False

    ## Race menu callbacks.
    def menu_race_startlist_activate_cb(self, menuitem, data=None):
        """Generate a startlist."""
        sections = []
        if self.curevent is not None:
            sections.extend(self.curevent.startlist_report())
        self.print_report(sections)

    def menu_race_result_activate_cb(self, menuitem, data=None):
        """Generate a result."""
        sections = []
        if self.curevent is not None:
            sections.extend(self.curevent.result_report())
        self.print_report(sections, u'Result')

    def menu_race_make_activate_cb(self, menuitem, data=None):
        """Create and open a new race of the chosen type."""
        event = self.edb.add_empty()
        event[u'type']=data
        # Backup an existing config
        oldconf = self.event_configfile(event[u'evid'])
        if os.path.isfile(oldconf):
            bakfile = oldconf + u'.old'
            self.log.info(u'Existing config saved to: ' + repr(bakfile))
            os.rename(oldconf, bakfile)
        self.open_event(event)
        self.menu_race_properties.activate()

    def menu_race_info_activate_cb(self, menuitem, data=None):
        """Show race information on scoreboard."""
        if self.curevent is not None:
            self.scbwin = None
            eh = self.curevent.event
            if self.showevno and eh[u'type'] not in [u'break', u'session']:
                self.scbwin = scbwin.scbclock(self.scb,
                                              u'Event ' + eh[u'evid'],
                                              eh[u'pref'], eh[u'info'])
            else:
                self.scbwin = scbwin.scbclock(self.scb,
                                              eh[u'pref'], eh[u'info'])
            self.scbwin.reset()
            self.curevent.delayed_announce()
            self.announce.gfx_overlay(1, self.graphicscb)
            einf = {}
            for key in eh:
                einf[key] = eh[key]

            self.announce.send_obj(u'gfxobj',{u'overlay':u'gfxinfo',
                                    u'evid':eh[u'evid'],
                                    u'einf':einf,
                                    u'pref':eh[u'pref'],
                                    u'info':eh[u'info'],
                                    u'prog':eh[u'prog'],
                               
                                    u'title':self.titlestr,
                                    u'subtitle':self.subtitlestr
                                   }, self.graphicscb)

    def menu_race_properties_activate_cb(self, menuitem, data=None):
        """Edit properties of open race if possible."""
        if self.curevent is not None:
            self.curevent.do_properties()

    def menu_race_run_activate_cb(self, menuitem=None, data=None):
        """Open currently selected event."""
        eh = self.edb.getselected()
        if eh is not None:
            self.open_event(eh)

    def event_row_activated_cb(self, view, path, col, data=None):
        """Respond to activate signal on event row."""
        self.menu_race_run_activate_cb()

    def menu_race_next_activate_cb(self, menuitem, data=None):
        """Open the next event on the program."""
        if self.curevent is not None:
            nh = self.edb.getnextrow(self.curevent.event)
            if nh is not None:
                self.open_event(nh)
            else:
                self.log.warn(u'No next event to open.')
        else:
            eh = self.edb.getselected()
            if eh is not None:
                self.open_event(eh)
            else:
                self.log.warn(u'No next event to open.')

    def menu_race_prev_activate_cb(self, menuitem, data=None):
        """Open the previous event on the program."""
        if self.curevent is not None:
            ph = self.edb.getprevrow(self.curevent.event)
            if ph is not None:
                self.open_event(ph)
            else:
                self.log.warn(u'No previous event to open.')
        else:
            eh = self.edb.getselected()
            if eh is not None:
                self.open_event(eh)
            else:
                self.log.warn(u'No previous event to open.')

    def menu_race_close_activate_cb(self, menuitem, data=None):
        """Close currently open event."""
        self.close_event()
    
    def menu_race_abort_activate_cb(self, menuitem, data=None):
        """Close currently open event without saving."""
        if self.curevent is not None:
            self.curevent.readonly = True
        self.close_event()

    def open_event(self, eventhdl=None):
        """Open provided event handle."""
        if eventhdl is not None:
            self.close_event()
            self.curevent = mkrace(self, eventhdl)
            self.curevent.loadconfig()
            self.race_box.add(self.curevent.frame)
            self.menu_race_info.set_sensitive(True)
            self.menu_race_close.set_sensitive(True)
            self.menu_race_abort.set_sensitive(True)
            starters = eventhdl[u'star']
            if starters is not None and starters != u'':
                if u'auto' in starters:
                    spec = starters.lower().replace(u'auto', u'').strip()
                    if self.curevent.autospec:
                        self.curevent.autospec = spec
                        self.log.warn(u'Replaced autospec: ' + repr(spec)
                                    + u' on event ' + self.curevent.evno)
                    else:
                        self.curevent.autospec += spec
                        self.log.info(u'Transferred autospec ' + repr(spec)
                                    + u' to event ' + self.curevent.evno)
                else:
                    self.addstarters(self.curevent, eventhdl, # xfer starters
                                     strops.reformat_biblist(starters))
                eventhdl[u'star'] = u''
            self.timer.setcb(self.curevent.timercb)
            self.rftimer.setcb(self.curevent.rftimercb)
            self.menu_race_properties.set_sensitive(True)
            self.curevent.show()

    def addstarters(self, race, event, startlist):
        """Add each of the riders in startlist to the opened race."""
        starters = startlist.split()
        for st in starters:
            # check for category
            rlist = self.rdb.biblistfromcat(st, race.series)
            if len(rlist) > 0:
                for est in rlist.split():
                    race.addrider(est)
            else:                    
                race.addrider(st)

    def autoplace_riders(self, race, autospec=u'', infocol=None):
        """Try to fetch the startlist from race result info."""
        places = {}
        for egroup in autospec.split(u';'):
            self.log.debug(u'Autospec group: ' + repr(egroup))
            specvec = egroup.split(u':')
            if len(specvec) == 2:
                evno = specvec[0].strip()
                if evno not in self.autorecurse:
                    self.autorecurse.add(evno)
                    placeset = strops.placeset(specvec[1])
                    e = self.edb[evno]
                    if e is not None:
                        h = mkrace(self, e, False)
                        h.loadconfig()
                        for ri in h.result_gen():
                            if type(ri[1]) is int and ri[1] in placeset:
                                rank = ri[1]
                                if rank not in places:
                                    places[rank] = []
                                places[rank].append(ri[0])
                        h.destroy()
                    else:
                        self.log.warn(u'Autospec event number not found: '
                                        + repr(evno))
                    self.autorecurse.remove(evno)
                else:
                    self.log.debug(u'Ignoring loop in auto startlist: '
                                   + repr(evno))
            else:
                self.log.warn(u'Ignoring erroneous autospec group: '
                               + repr(egroup))
        ret = ''
        for place in sorted(places):
            ret += ' ' + '-'.join(places[place])
        self.log.debug(u'Place set: ' + ret)
        return ret

    def autostart_riders(self, race, autospec=u'', infocol=None):
        """Try to fetch the startlist from race result info."""
        # Dubious: infocol allows selection of seed info
        #          typical values:
        #                           1 -> timed event qualifiers
        #                           3 -> handicap
        # TODO: check default, maybe defer to None
        # TODO: IMPORTANT cache result gens for fast recall
        for egroup in autospec.split(u';'):
            self.log.debug(u'Autospec group: ' + repr(egroup))
            specvec = egroup.split(u':')
            if len(specvec) == 2:
                evno = specvec[0].strip()
                if evno not in self.autorecurse:
                    self.autorecurse.add(evno)
                    placeset = strops.placeset(specvec[1])
                    e = self.edb[evno]
                    if e is not None:
                        evplacemap = {}
                        ## load the place set map rank -> [[rider,seed],..]
                        h = mkrace(self, e, False)
                        h.loadconfig()
                        for ri in h.result_gen():
                            if type(ri[1]) is int and ri[1] in placeset:
                                rank = ri[1]
                                if rank not in evplacemap:
                                    evplacemap[rank] = []
                                seed = None
                                if infocol is not None and infocol < len(ri):
                                    seed = ri[infocol]
                                evplacemap[rank].append([ri[0],seed])
                                
                        h.destroy()
                        # maintain ordering of autospec
                        for p in placeset:
                            if p in evplacemap:
                                for ri in evplacemap[p]:
                                    if race.event[u'type'] == 'sprint final':
                                        for cr in race.contests:
                                            race.addrider(ri[0])
                                    else:
                                        race.addrider(ri[0], ri[1])
                    else:
                        self.log.warn(u'Autospec event number not found: '
                                        + repr(evno))
                    self.autorecurse.remove(evno)
                else:
                    self.log.debug(u'Ignoring loop in auto startlist: '
                                   + repr(evno))
            else:
                self.log.warn(u'Ignoring erroneous autospec group: '
                               + repr(egroup))

    def close_event(self):
        """Close the currently opened race."""
        if self.curevent is not None:
            self.timer.setcb()
            self.rftimer.setcb()
            self.curevent.hide()
            self.race_box.remove(self.curevent.frame)
            self.curevent.destroy()
            self.curevent.event[u'dirt'] = True	# mark event exportable
            self.menu_race_properties.set_sensitive(False)
            self.menu_race_info.set_sensitive(False)
            self.menu_race_close.set_sensitive(False)
            self.menu_race_abort.set_sensitive(False)
            self.curevent = None

    def race_evno_change(self, old_no, new_no):
        """Handle a change in a race number."""
        if self.curevent is not None and self.curevent.evno == old_no:
            self.log.warn(u'Ignoring change to open event: ' + repr(old_no))
            return False
        oldconf = self.event_configfile(old_no)
        if os.path.isfile(oldconf):
            newconf = self.event_configfile(new_no)
            if os.path.isfile(newconf):
                os.rename(newconf, newconf + u'.old')
            os.rename(oldconf, newconf)
        self.log.info(u'Race ' + repr(old_no) + u' changed to ' + repr(new_no))
        return True

    ## Data menu callbacks.
    def menu_data_rego_activate_cb(self, menuitem, data=None):
        """Open rider rego dialog."""
        self.log.warn(u'TODO :: Rider rego dlg...')

    def menu_data_import_activate_cb(self, menuitem, data=None):
        """Re-load event and rider info from disk."""
        if not uiutil.questiondlg(self.window,
            u'Re-load event and rider data from disk?',
            u'Note: The current event will be closed.'):
            self.log.debug(u'Re-load events & riders aborted.')
            return False

        cureventno = None
        if self.curevent is not None:
            cureventno = self.curevent.evno
            self.close_event()

        self.rdb.clear()
        self.edb.clear()
        self.edb.load(os.path.join(self.configpath, u'events.csv'))
        self.rdb.load(os.path.join(self.configpath, u'riders.csv'))
        self.reload_riders()

        if cureventno:
            if cureventno in self.edb:
                self.open_event(self.edb[cureventno])
            else:
                self.log.warn(u'Running event was removed from the event list.')

    def menu_data_result_activate_cb(self, menuitem, data=None):
        """Export final result."""
        try:
            self.finalresult()	# TODO: Call in sep thread
        except Exception as e:
            self.log.error(u'Error writing result: ' + unicode(e))
            raise

    def finalresult(self):
        provisional = self.provisional	# may be overridden below
        sections = []
        lastsess = None
        for e in self.edb:
            r = mkrace(self, e, False)
            if e[u'resu']:    # include in result
                nsess = e[u'sess']
                if nsess != lastsess:
                    sections.append(printing.pagebreak(0.10)) # force break
                    self.log.debug(u'Break between events: ' + repr(e[u'evid']) + u' with ' + repr(lastsess) + u' != ' + repr(nsess))
                lastsess = nsess
                if r.evtype in [u'break', u'session']:
                    sec = printing.section()
                    sec.heading = u' '.join([e[u'pref'], e[u'info']]).strip()
                    sec.subheading = u'\t'.join([strops.lapstring(e[u'laps']),
                                            e[u'dist'], e[u'prog']]).strip()
                    sections.append(sec)
                else:
                    r.loadconfig()
                    if r.onestart:	# in progress or done...
                        report = r.result_report()
                    else:
                        report = r.startlist_report()
                    if len(report) > 0:
                        sections.extend(report)
            r.destroy()

        filebase = os.path.basename(self.configpath)
        if filebase in [u'', u'.']:
            filebase = u'result'
        else:
            filebase += u'_result'

        self.print_report(sections, u'Results', prov=provisional,
                          doprint=False,
                          exportfile=filebase.translate(strops.WEBFILE_UTRANS))

    def printprogram(self):
        ## Temporary hack to export the a5 booklet as a report.
        ## TODO: eventually replace with a program builder tool
        ptmap = {'VIC':u'Victoria',
                'TAS':u'Tasmania',
                'WA':u'Western Australia',
                'SA':u'South Australia',
                'NT':u'Northern Territory',
                'ACT':u'Australian Capital Territory',
                'NSW':u'New South Wales',
                'QLD':u'Queensland',
                'GER':u'Germany',
                'SUI':u'Switzerland',
                'NED':u'Netherlands',
                'CAN':u'Canada',
                'NZL':u'New Zealand',
                'AUS':u'Australia'}
        tmap = {}
        rlist = {}
        rmap = {}
        catmap = {}
        for r in self.rdb.model:
            bib = r[0]
            club = r[3]
            cat = r[4]
            abbrev = r[8]
            if len(club) > 4:
                abbrev = r[3]
                club = r[8]
            name = strops.resname(r[1], r[2], club)
            if cat not in rlist:
                rlist[cat] = []
            rlist[cat].append([u' ', bib, name, None, None, None])
            if club not in tmap and abbrev:
                tmap[club] = abbrev
            rmap[bib] = name
            catmap[bib] = cat
        tlist = []
        for t in sorted(tmap):
            if t:
                if t in ptmap:
                    tlist.append([t, None, ptmap[t], None, None, None])
                else:
                    tlist.append([t, None, tmap[t], None, None, None])

        tfile = self.default_template()
        r = printing.printrep(template=tfile, path=self.configpath)
        subtitlestr = 'Program of Events'
        if self.subtitlestr:
            subtitlestr = self.subtitlestr + ' - ' + subtitlestr
        r.strings['title'] = self.titlestr
        r.strings['subtitle'] = subtitlestr
        r.strings['datestr'] = strops.promptstr('Date:', self.datestr)
        r.strings['commstr'] = strops.promptstr('Chief Commissaire:',
                                                  self.commstr)
        r.strings['orgstr'] = strops.promptstr('Organiser: ', self.orgstr)
        r.strings['docstr'] = '' # What should go here?
        r.strings['diststr'] = self.locstr

        r.set_provisional(self.provisional)

        cursess = None
        for e in self.edb:
            if e[u'prin']:	# include this event in program
                if e[u'sess']:	# add harder break for new session
                    if cursess and cursess != e[u'sess']:
                        r.add_section(printing.pagebreak(0.3))
                    cursess = e[u'sess']
                h = mkrace(self, e, False)
                h.loadconfig()
                s = h.startlist_report(True)
                for sec in s:
                    r.add_section(sec)
                h.destroy()
                
        # Add team abbrevs, if available
        s = printing.twocol_startlist()
        s.heading = 'Abbreviations'
        s.lines = tlist
        if len(s.lines) > 0:
            pass
            #r.add_section(s)

        filebase = os.path.basename(self.configpath)
        if filebase in [u'', u'.']:
            filebase = u'program'
        else:
            filebase += u'_program'
        ofile = os.path.join(self.configpath, u'export', filebase + u'.pdf')
        with open(ofile, 'wb') as f:
            r.output_pdf(f, docover=True)
            self.log.info(u'Exported pdf program to ' + repr(ofile))
        ofile = os.path.join(self.configpath, u'export', filebase + u'.txt')
        with open(ofile, 'wb') as f:
            r.output_text(f)
            self.log.info(u'Exported text program to ' + repr(ofile))
        if self.htmlfiles:
            ofile = os.path.join(self.configpath, u'export', filebase + u'.html')
            with open(ofile, 'wb') as f:
                r.output_html(f)
                self.log.info(u'Exported html program to ' + repr(ofile))
        ofile = os.path.join(self.configpath, u'export', filebase + u'.xls')
        with open(ofile, 'wb') as f:
            r.output_xls(f)
            self.log.info(u'Exported xls program to ' + repr(ofile))
        ofile = os.path.join(self.configpath, u'export', filebase + u'.json')
        with open(ofile, 'wb') as f:
            r.output_json(f)
            self.log.info(u'Exported json program to ' + repr(ofile))

    def menu_data_program_activate_cb(self, menuitem, data=None):
        """Export race program."""
        try:
            self.printprogram()	# TODO: call from sep thread
            self.updateindex()
        except Exception as e:
            self.log.error(u'Error writing report: ' + unicode(e))
            raise

    def menu_data_update_activate_cb(self, menuitem, data=None):
        """Update meet, session, event and riders in external database."""
        try:
            self.log.info(u'Exporting data:')
            self.updateindex() # TODO: push into sep thread
        except Exception as e:
            self.log.error(u'Error exporting event data: ' + unicode(e))
            raise

    def updatenexprev(self):
        self.nextlinks = {}
        self.prevlinks = {}
        prev = None
        prevl = None
        for eh in self.edb:
            if eh[u'inde'] or eh[u'resu']:     # include in index?
                evno = eh[u'evid']
                referno = None
                if eh[u'type'] not in [u'break', u'session']:
                    referno = evno
                if eh[u'refe']: # overwrite ref no, even on specials
                    referno = eh[u'refe']
                linkfile = None
                if referno:
                    linkfile = u'event_' + unicode(referno).translate(
                                                   strops.WEBFILE_UTRANS)
                    if prev is not None:
                        self.prevlinks[evno] = prevl
                        self.nextlinks[prev] = linkfile
                    prev = evno
                    prevl = linkfile

    def updateindex(self):
            self.reload_riders()	# force update from rdb !HACK
            self.updatenexprev()	# re-compute next/prev link struct
            dirty = {}
            ## riders
            self.db.execute(u'Delete FROM Cycling WHERE TypeID=%s',
                        [(u'Trackmeet'), (u'Tracksession'),
                         (u'Trackevent'), (u'Rider')])
            addset = []
            count = 1
            for series in self.ridermap:
                for bib in self.ridermap[series]:
                    rider = self.ridermap[series][bib]
                    nation = u''
                    if rider[u'ucicode']:
                        nation = rider[u'ucicode'][0:3].upper()
                    addset.append(
                      [count, u'Rider', series, rider[u'bib'],
                              rider[u'first'], rider[u'last'],
                       rider[u'club'], rider[u'cat'], rider[u'ucicode'],
                       rider[u'refid'], nation]
                    )
                    count += 1
            self.db.execute(u'INSERT INTO Cycling(SortOrder, TypeID, Series, Data0, Data1, Data2, Data3, Data4, Data5, Data6, Data7) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', addset)

            ## events
            addset = []
            count = 0
            for ev in self.edb:
                addset.append([count, ev[u'evid'], u'Trackevent', ev[u'seri'], 
                          ev[u'refe'], ev[u'pref'], ev[u'info'], ev[u'sess'],
                          ev[u'laps'], ev[u'dist'], ev[u'prog'],
                                 ev[u'reco'], ev[u'type']])
                count += 1
            self.db.execute(u'INSERT INTO Cycling(SortOrder, EventID, TypeID, Series, ClassID, Data0, Data1, Data2, Data3, Data4, Data5, Data6, Data7) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', addset)

            ## save
            self.db.commit()

            # check for prined program link
            # check for final result link
            # check for timing log link
            # build index of events report
            if self.mirrorpath:
                tfile = self.default_template()
                orep = printing.printrep(template=tfile,
                                         path=self.configpath)
                orep.strings[u'title'] = self.titlestr
                orep.strings[u'subtitle'] = self.subtitlestr
                orep.strings[u'datestr'] = strops.promptstr(u'Date:',
                                                         self.datestr)
                orep.strings[u'commstr'] = strops.promptstr(
                                           'Chief Commissaire:',
                                              self.commstr)
                orep.strings['orgstr'] = strops.promptstr('Organiser: ',
                                              self.orgstr)
                orep.strings[u'diststr'] = self.locstr
                orep.set_provisional(self.provisional)	# ! TODO 
                if self.provisional:
                    orep.reportstatus = u'provisional'
                else: 
                    orep.reportstatus = u'final'

                # check for program link
                ##TODO: function to avoid error
                pfilebase = os.path.basename(self.configpath)
                if pfilebase in [u'', u'.']:
                    pfilebase = u'program'
                else:
                    pfilebase += u'_program'
                pfile = os.path.join(self.configpath, u'export',
                                         pfilebase + u'.pdf')
                rfilebase = os.path.basename(self.configpath)
                if rfilebase in [u'', u'.']:
                    rfilebase = u'result'
                else:
                    rfilebase += u'_result'
                rfile = os.path.join(self.configpath, u'export',
                                         rfilebase + u'.pdf')

                lt = []
                lb = None
                if os.path.exists(rfile):
                    lt = [u'pdf', u'xls', u'txt', u'json']
                    lb = os.path.join(self.linkbase, self.mirrorpath, rfilebase)
                elif os.path.exists(pfile):
                    lt = [u'pdf', u'xls', u'txt', u'json']
                    lb = os.path.join(self.linkbase, self.mirrorpath, pfilebase)

                sec = printing.event_index(u'eventindex')
                sec.heading = 'Index of Events'
                #sec.subheading = Date?
                for eh in self.edb:
                    if eh[u'inde']:	# include in index?
                        evno = eh[u'evid']
                        if eh[u'type'] in [u'break', u'session']:
                            evno = None
                        referno = evno
                        if eh[u'refe']:	# overwrite ref no, even on specials
                            referno = eh[u'refe']
                        linkfile = None
                        if referno: 
                            linkfile = u'event_' + unicode(referno).translate(
                                                   strops.WEBFILE_UTRANS)
                        descr = u' '.join([eh[u'pref'], eh[u'info']]).strip()
                        extra = None	# STATUS INFO -> progress?
                        sec.lines.append([evno, None,
                                          descr, extra, linkfile])
                orep.add_section(sec)
                basename = u'program'
                ofile = os.path.join(self.exportpath, basename + u'.txt')
                with open(ofile, 'wb') as f:
                    orep.output_text(f, linkbase=lb, linktypes=lt)
                ofile = os.path.join(self.exportpath, basename +u'.pdf')
                with open(ofile, 'wb') as f:
                    orep.output_pdf(f)
                if self.htmlfiles:
                    ofile = os.path.join(self.exportpath, basename + u'.html')
                    with open(ofile, 'wb') as f:
                        orep.output_html(f, linkbase=lb, linktypes=lt)
                jbase = basename + u'.json'
                jfile = os.path.join(u'/site', self.mirrorpath, jbase)
                ofile = os.path.join(self.exportpath, jbase)
                with open(ofile, 'wb') as f:
                    orep.output_json(f)	# with links -> not yet
                dirty[u'index'] = jfile
                glib.idle_add(self.mirror_start, dirty)

    def mirror_completion(self, status, updates):
        """Send http notifies for any changed files sent after rsync."""
        # NOTE: This is called in the rsync thread, not via idle_add!
        self.log.debug(u'mirror status: ' + repr(status))
        if status == 0:
            if self.http_notify_host and updates is not None:
                for key in updates:
                    if key == u'index':
                        jfile = updates[key]
                        self.log.debug(u'http notify: (index)=>' + repr(jfile))
                        rsync.http_notify(url=self.http_notify_host,
                               user=self.http_notify_user,
                               passwd=self.http_notify_pass,
                               event=self.http_notify_event,
                               path=jfile,
                               log=self.log)
                    else:
                        subevent = key
                        jfile = updates[key]
                        self.log.debug(u'http notify: ' 
                                       + repr(key) + u'=>' + repr(jfile))
                        rsync.http_notify(url=self.http_notify_host,
                               user=self.http_notify_user,
                               passwd=self.http_notify_pass,
                               event=self.http_notify_event,
                               subevent=key,
                               path=jfile,
                               log=self.log)
        else:
            self.log.error(u'Mirror failed.')
        return False

    def mirror_start(self, dirty=None):
        """Create a new rsync mirror thread unless in progress."""
        if self.mirrorpath and self.mirror is None:
            cbfunc = self.mirror_completion
            cbdata = dirty
            self.mirror = rsync.mirror(
                callback = cbfunc,
                callbackdata = cbdata,
                localpath = os.path.join(self.exportpath,u''),
                remotepath = self.mirrorpath,
                mirrorcmd = self.mirrorcmd)
            self.mirror.start()
        return False	# for idle_add

    def menu_data_export_activate_cb(self, menuitem, data=None):
        """Export race data."""
        if not self.exportlock.acquire(False):
            self.log.info(u'Export already in progress.')
            return None # allow only one entry
        if self.exporter is not None:
            self.log.warn(u'Export in progress, re-run required.')
            return False
        try:
            self.exporter = threading.Thread(target=self.__run_data_export)
            self.exporter.start()
            self.log.info(u'Created export worker : ' + repr(self.exporter))
        finally:
            self.exportlock.release()
 
    def __run_data_export(self):
        try:
            self.log.debug('Exporting race info.')
            self.updatenexprev()	# re-compute next/prev link struct

            # determine 'dirty' events 	## TODO !!
            dmap = {}
            dord = []
            for e in self.edb:	# note - this is the only traversal
                series = e[u'seri']
                #if series not in rmap:
                    #rmap[series] = {}
                evno = e[u'evid']
                etype = e[u'type']
                prefix = e[u'pref']
                info = e[u'info']
                export = e[u'resu']
                key = evno	# no need to concat series, evno is unique
                dirty = e[u'dirt']
                if not dirty:   # check for any dependencies
                    for dev in e[u'depe'].split():
                        if dev in dmap:
                            dirty = True
                            break
                if dirty:
                    dord.append(key)	# maintains ordering
                    dmap[key] = [e, evno, etype, series, prefix, info, export]
            self.log.debug(u'Marked ' + unicode(len(dord)) + u' events dirty.')
  
            dirty = {}
            for k in dmap:	# only output dirty events
                # turn key into read-only event handle
                e = dmap[k][0]
                evno = dmap[k][1]
                etype = dmap[k][2]
                series = dmap[k][3]
                evstr = (dmap[k][4] + u' ' + dmap[k][5]).strip()
                doexport = dmap[k][6]
                e[u'dirt']=False
                r = mkrace(self, e, False)
                r.loadconfig()

                # extract result
                rset = []
                rescount = 0
                if r.onestart:		# skip this for unstarted?
                    for result in r.result_gen():
                        bib = result[0]
                        first = u''
                        last = u''
                        club = u''
                        cat = u''
                        nation = u''
                        if series in self.ridermap and bib in self.ridermap[series]:
                            rider = self.ridermap[series][bib]
                            first = rider[u'first']
                            last = rider[u'last']
                            club = rider[u'club']
                            cat = rider[u'cat']
                            if rider[u'ucicode']:
                                nation = rider[u'ucicode'][0:3].upper()
                        rank = u''
                        if result[1] is not None:
                            rank = unicode(result[1])
                        infoa = u''
                        if result[2] is not None:
                            if type(result[2]) is tod.tod:
                                infoa = result[2].rawtime(2)
                            else:
                                infoa = unicode(result[2])
                        infob = u''
                        # REQUIRED?
                        if result[3] is not None:
                            if type(result[3]) is tod.tod:
                                infob = result[3].rawtime(2)
                            else:
                                infob = str(result[3])
                        status = u'provisional'	# TODO: flag race status
                        # NO RESULT FOR NO RANK?
                        if rank:
                            rset.append( [evno, series, rescount,
                                          u'Result', u'Result', status,
                                          rank, bib, first, last, club,
                                          cat, nation, infoa, infob] )
                        rescount += 1

                # starters
                stcount = 0
                startrep = r.startlist_report()	# trigger rider model reorder
                for result in r.result_gen():
                    bib = result[0]
                    first = u''
                    last = u''
                    club = u''
                    cat = u''
                    nation = u''
                    if series in self.ridermap and bib in self.ridermap[series]:
                        rider = self.ridermap[series][bib]
                        first = rider[u'first']
                        last = rider[u'last']
                        club = rider[u'club']
                        cat = rider[u'cat']
                        if rider[u'ucicode']:
                            nation = rider[u'ucicode'][0:3].upper()
                    rank = u''
                    infoa = u''
                    infob = u''
                    status = u'provisional'
                    if etype in [u'handicap', u'sprint'] and result[3] is not None:
                        if type(result[3]) is tod.tod:
                            infob = result[3].rawtime(2)
                        else:
                            infob = str(result[3])
                    rset.append( [evno, series, stcount,
                                          u'Result', u'Startlist', status,
                                          rank, bib, first, last, club,
                                          cat, nation, infoa, infob] )
                    stcount += 1

                self.db.execute('DELETE FROM Cycling WHERE EventID=%s AND TypeID=%s',
                                [(evno,u'Result')])	# also kills starters
                self.db.execute('INSERT INTO Cycling (EventID, Series, SortOrder, TypeID, ClassID, StatusID, Data0, Data1, Data2, Data3, Data4, Data5, Data6, Data7, Data8) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                rset)
                self.db.commit()

                if self.mirrorpath and doexport:
                    tfile = self.default_template()
                    orep = printing.printrep(template=tfile,
                                             path=self.configpath)
                    orep.strings[u'title'] = self.titlestr
                    orep.strings[u'subtitle'] = evstr
                    orep.strings[u'datestr'] = strops.promptstr(u'Date:',
                                                             self.datestr)
                    orep.strings[u'diststr'] = self.locstr
                    orep.strings[u'docstr'] = evstr
                    if etype in [u'classification']:
                        orep.strings[u'docstr'] += u' Classification'
                    orep.set_provisional(self.provisional)	# ! TODO 
                    if self.provisional:
                        orep.reportstatus = u'provisional'
                    else:
                        orep.reportstatus = u'final'

                    # in page links
                    orep.indexlink = u'program'	# url to program of events
                    if evno in self.prevlinks:
                        orep.prevlink = self.prevlinks[evno]
                    if evno in self.nextlinks:
                        orep.nextlink = self.nextlinks[evno]

                    # update files and trigger mirror
                    if r.onestart:	# output result
                        outsec = r.result_report()
                        for sec in outsec:
                            orep.add_section(sec)
                    else:		# startlist
                        outsec = r.startlist_report('startlist')
                        for sec in outsec:
                            orep.add_section(sec)
                    basename = u'event_' + unicode(evno).translate(
                                                   strops.WEBFILE_UTRANS)
                    ofile = os.path.join(self.exportpath, basename + u'.txt')
                    with open(ofile, 'wb') as f:
                        orep.output_text(f)
                    lt = []
                    analysistlist = [u'flying 200 training',u'indiv tt',u'pursuit race',u'team pursuit',u'team pursuit race', u'indiv pursuit']
                    exportfile = u'event_' + evno
                    lb = os.path.join(self.linkbase, self.mirrorpath, exportfile)
                    if self.htmlfiles:
                        if etype in analysistlist:
                            ofile = os.path.join(self.exportpath, basename + u'.html')
                            with open(ofile, 'wb') as f:
                                orep.output_html(f, linkbase=lb, linktypes=lt, event=etype)
                            sections = []
                            sections.extend(r.analysis_report())
                            self.print_analysis(evno,sections)
                        else:
                            #lt = [u'txt', u'json']
                            ofile = os.path.join(self.exportpath, basename + u'.html')
                            with open(ofile, 'wb') as f:
                                orep.output_html(f, linkbase=lb, linktypes=lt, event=etype)
                    jbase = basename + u'.json'
                    jfile = os.path.join(u'/site', self.mirrorpath, jbase)
                    ofile = os.path.join(self.exportpath, jbase)
                    with open(ofile, 'wb') as f:
                        orep.output_json(f)
                    dirty[evno] = jfile
                r.destroy()
                time.sleep(0.001)	# force coop sched
            glib.idle_add(self.mirror_start, dirty)
            self.log.info(u'Race info export.')
        except Exception as e:
            self.log.error(u'Error exporting results: ' + unicode(e))
            raise	# temporary DEBUG

    ## SCB menu callbacks
    def menu_scb_enable_toggled_cb(self, button, data=None):
        """Update scoreboard enable setting."""
        if button.get_active():
            self.scb.set_ignore(False)
            self.scb.setport(self.scbport)
            self.announce.set_portstr(self.annport)
            if self.scbwin is not None:
                self.scbwin.reset()
        else:
            self.scb.set_ignore(True)

    def menu_scb_clock_cb(self, menuitem, data=None):
        """Select timer scoreboard overlay."""
        self.gemini.clear()
        self.scbwin = None
        self.announce.gfx_overlay(2, self.graphicscb)
        self.scb.clrall()
        ## hack for DAK
        for i in range(0, self.scb.pagelen+1):
            self.scb.clrline(i)

    def menu_scb_logo_activate_cb(self, menuitem, data=None):
        """Select logo and display overlay."""
        self.gemini.clear()
        self.scbwin = scbwin.logoanim(self.scb, self.logos)
        self.scbwin.reset()
        self.log.debug(u'Running scoreboard logo anim.')

    def menu_scb_blank_cb(self, menuitem, data=None):
        """Select blank scoreboard overlay."""
        self.gemini.clear()
        self.scbwin = None
        #self.scb.setoverlay(unt4.OVERLAY_BLANK)
        self.announce.gfx_overlay(0, self.graphicscb)
        self.scb.clrall()
        ## hack for DAK
        for i in range(0, self.scb.pagelen+1):
            self.scb.clrline(i)
        self.log.debug(u'Selected scoreboard blank overlay.')

    def menu_scb_result_activate_cb(self, menuitem, data=None):
        """Select result scoreboard overlay."""
        self.gemini.clear()
        self.scbwin = None
        self.announce.gfx_overlay(1, self.graphicscb)
        #self.scb.setoverlay(unt4.OVERLAY_MATRIX)
        self.log.debug(u'Selected scoreboard result overlay.')

    def menu_scb_connect_activate_cb(self, menuitem, data=None):
        """Force a reconnect to scoreboard."""
        self.scb.setport(self.scbport)
        self.announce.set_portstr(self.annport)
        self.log.debug(u'Re-connect scoreboard.')
        if self.gemport != u'':
            self.gemini.setport(self.gemport)
        if self.dbport != u'':
            self.db.setport(self.dbport)

    ## Timing menu callbacks
    def menu_timing_subtract_activate_cb(self, menuitem, data=None):
        """Run the time of day subtraction dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'tod_subtract.ui'))
        ste = b.get_object('timing_start_entry')
        fte = b.get_object('timing_finish_entry')
        nte = b.get_object('timing_net_entry')
        b.get_object('timing_start_now').connect('clicked',
                                                 self.entry_set_now, ste)
        b.get_object('timing_finish_now').connect('clicked',
                                                 self.entry_set_now, fte)
        ste.connect('activate', self.menu_timing_recalc, ste, fte, nte)
        fte.connect('activate', self.menu_timing_recalc, ste, fte, nte)
        dlg = b.get_object('timing')
        dlg.set_transient_for(self.window)
        dlg.run()
        dlg.destroy()

    def entry_set_now(self, button, entry=None):
        """Enter the 'now' time in the provided entry."""
        entry.set_text(tod.tod(u'now').timestr())
        entry.activate()

    def menu_timing_recalc(self, entry, ste, fte, nte):
        """Update the net time entry for the supplied start and finish."""
        st = tod.str2tod(ste.get_text().decode('utf-8','replace'))
        ft = tod.str2tod(fte.get_text().decode('utf-8','replace'))
        if st is not None and ft is not None:
            ste.set_text(st.timestr())
            fte.set_text(ft.timestr())
            nte.set_text((ft - st).timestr())

    def menu_timing_main_toggled_cb(self, button, data=None):
        """Update the selected primary timer."""
        if button.get_active():
            self.log.info(u'Selected main timer as race time source')
            self.timer = self.main_timer
        else:
            self.log.info(u'Selected backup timer as race time source')
            self.timer = self.backup_timer

    def menu_timing_clear_activate_cb(self, menuitem, data=None):
        """Clear memory in attached timing devices."""
        self.main_timer.clrmem()
        self.backup_timer.clrmem()
        self.log.info(u'Clear attached timer memories')

    def menu_timing_dump_activate_cb(self, menuitem, data=None):
        """Request memory dump from attached timy."""
        self.timer.dumpall()
        self.log.info(u'Dump active timer memory.')

    def menu_timing_reconnect_activate_cb(self, menuitem, data=None):
        """Reconnect timers and initialise."""
        self.main_timer.setport(self.main_port)
        if self.main_port:
            self.main_timer.sane()
        self.backup_timer.setport(self.backup_port)
        if self.backup_port:
            self.backup_timer.sane()
        self.rftimer.setport(self.rftimer_port)
        if self.rftimer_port:
            self.rftimer.stop_session()
            self.rftimer.sane()
            self.rftimer.start_session()
            self.rftimer.sync()
            self.rftimer.status()
        self.log.info(u'Re-connect and initialise attached timers.')

    ## Help menu callbacks
    def menu_help_docs_cb(self, menuitem, data=None):
        """Display program help."""
        metarace.help_docs(self.window)

    def menu_help_about_cb(self, menuitem, data=None):
        """Display metarace about dialog."""
        metarace.about_dlg(self.window)
  
    ## Menu button callbacks
    def menu_clock_clicked_cb(self, button, data=None):
        """Handle click on menubar clock."""
        (line1, line2, line3) = strops.titlesplit(self.titlestr
                                  + u' ' + self.subtitlestr, self.scb.linelen)
        self.scbwin = scbwin.scbclock(self.scb, line1, line2, line3,
                                      locstr=self.locstr)
        self.scbwin.reset()
        self.log.info(u'ToD: ' + tod.tod('now').meridian())
        self.rftimer.status()
        #self.log.debug(u'Displaying meet info and clock on scoreboard.')

    ## Directory utilities
    # !! CONVERT to event_NN.json
    def event_configfile(self, evno):
        """Return a config filename for the given event no."""
        return os.path.join(self.configpath, u'event_'+unicode(evno)+u'.ini')

    ## Timer callbacks
    def menu_clock_timeout(self):
        """Update time of day on clock button."""
    
        if not self.running:
            return False
        else:
            tt = tod.tod(u'now')
            self.clock_label.set_text(tt.meridian())

            # check for completion in the export workers
            if self.mirror is not None:
                if not self.mirror.is_alive():	# replaces join() non-blocking
                    self.mirror = None
            if self.exporter is not None:
                if not self.exporter.is_alive():# replaces join() non-blocking
                    self.log.info(u'Deleting complete exporter: ' + repr(self.exporter))
                    self.exporter = None
                else:
                    self.log.info(u'Incomplete exporter: ' + repr(self.exporter))
            #if type(self.scbwin) is scbwin.scbclock:
                #self.gemini.ctick(tt)	# dubious
            #self.announce.postxt(0,72,tt)

            if self.dbport != u'':
                self.dbwatch += 1
                if self.dbwatch % 10 == 0:
                    self.dbwatch = 0
                    if not self.db.connected():
                        self.log.warn(u'DB Watchdog - Reconnect.')
                        self.db.setport(self.dbport)

        return True

    def timeout(self):
        """Update internal state and call into race timeout."""
        if not self.running:
            return False
        try:
            if self.curevent is not None:      # this is expected to
                self.curevent.timeout()        # collect any timer events
            if self.scbwin is not None:
                self.scbwin.update()
        except Exception as e:
            self.log.error(u'Timeout: ' + unicode(e)) # use a stack trace
        return True

    ## Timy utility methods.
    def printimp(self, printimps=True):
        """Deprecated impulse print option - removed for re-print."""
        pass
        #"""Enable or disable printing of timing impulses on Timy."""
        #if self.timerprint:
             #self.main_timer.printer(printimps)
        #self.main_timer.printimp(printimps)
        #self.backup_timer.printimp(printimps)

    def timer_reprint(self, event=u'', trace=[]):
        self.main_timer.printer(True)	# turn on printer
        self.main_timer.printimp(False)	# suppress intermeds
        self.timer.printline(u'')
        self.timer.printline(u'')
        self.timer.printline(u'')
        self.timer.printline(self.titlestr)
        self.timer.printline(self.subtitlestr)
        self.timer.printline(u'')
        if event:
            self.timer.printline(event)
            self.timer.printline(u'')
        for l in trace:
            self.timer.printline(l)
        self.timer.printline(u'')
        self.timer.printline(u'')
        self.timer.printline(u'')
        self.timer.printline(u'')
        self.main_timer.printer(False)

    def delayimp(self, dtime):
        """Set the impulse delay time."""
        self.main_timer.delaytime(dtime)
        self.backup_timer.delaytime(dtime)

    def timer_log_straight(self, bib, msg, tod, prec=4):
        """Print a tod log entry on the Timy receipt."""
        ret = u'{0:3} {1: >4}: '.format(bib[0:3],
                              unicode(msg)[0:4]) + tod.timestr(prec)
        self.timer.printline(ret)
        return ret

    def timer_log_msg(self, bib, msg):
        """Print the given msg entry on the Timy receipt."""
        ret = u'{0:3} '.format(bib[0:3]) + unicode(msg)[0:20]
        self.timer.printline(ret)
        return ret

    def event_string(self, evno):
        """Switch to suppress event no in delayed announce screens."""
        ret = u''
        if self.showevno:
            ret = u'Event ' + unicode(evno)
        else:
            ret = u' '.join([self.titlestr, self.subtitlestr]).strip()
        return ret

    def racenamecat(self, event, slen=None, tail=u''):
        """Concatentate race info for display on scoreboard header line."""
        if slen is None:
            slen = self.scb.linelen
        evno = u''
        srcev = event[u'evid']
        if self.showevno and event[u'type'] not in [u'break', u'session']:
            evno = u'Ev ' + srcev
        info = event[u'info']
        prefix = event[u'pref']
        ret = u' '.join([evno, prefix, info, tail]).strip()
        if len(ret) > slen + 1:
            ret = u' '.join([evno, info, tail]).strip()
            if len(ret) > slen + 1:
                ret = u' '.join([evno, tail]).strip()
        return strops.truncpad(ret, slen, align=u'c')

    def racename(self, event):
        """Return a full event identifier string."""
        evno = u''
        if self.showevno and event[u'type'] not in [u'break', u'session']:
            evno = u'Event ' + event[u'evid']
        info = event[u'info']
        prefix = event[u'pref']
        return u' '.join([evno, prefix, info]).strip()

    ## Announcer methods
    def ann_default(self):
        self.announce.setline(0, strops.truncpad(
             u' '.join([self.titlestr, self.subtitlestr,
                               self.datestr]).strip(), 70, u'c'))

    def ann_title(self, titlestr=u''):
        self.announce.setline(0, strops.truncpad(titlestr.strip(), 70, u'c'))

    ## Window methods
    def set_title(self, extra=u''):
        """Update window title from meet properties."""
        self.window.set_title(u'trackmeet :: ' 
               + u' '.join([self.titlestr, self.subtitlestr]).strip())
        self.ann_default()

    def meet_destroy_cb(self, window, msg=u''):
        """Handle destroy signal and exit application."""
        lastevent = None
        if self.curevent is not None:
            lastevent = self.curevent.evno
            self.close_event()
        if self.started:
            self.saveconfig(lastevent)
            self.log.info(u'Meet shutdown: ' + msg)
            self.shutdown(msg)
        self.log.removeHandler(self.sh)
        self.log.removeHandler(self.lh)
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
        self.running = False
        gtk.main_quit()
        print(u'Exit.')

    def key_event(self, widget, event):
        """Collect key events on main window and send to race."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                key = key.lower()
                if key in ['0','1','2','3','4','5','6','7','8','9']:	#?uni
                    self.timer.trig(chan=key)
                    return True
            if self.curevent is not None:
                return self.curevent.key_event(widget, event)
        return False

    def shutdown(self, msg):
        """Cleanly shutdown threads and close application."""
        self.started = False
        self.db.exit(msg)
        self.gemini.exit(msg)
        self.announce.exit(msg)
        if self.scb is not self.announce:
            self.scb.exit(msg)
        self.main_timer.exit(msg)
        self.backup_timer.exit(msg)
        self.rftimer.exit(msg)
        print (u'Waiting for workers to exit...')
        if self.exporter is not None:
            print(u'\tresult exporter.')
            self.exporter.join()
            self.exporter = None
        if self.mirror is not None:
            print(u'\tlive result mirror.')
            self.mirror.join()
            self.mirror = None
        print(u'\tdatabase export.')
        self.db.join()
        print(u'\tauxilliary scoreboard.')
        self.gemini.join()
        if self.scb is not self.announce:
            print(u'\tmain scoreboard.')
            self.scb.join()
        print(u'\tuscbsrv announce.')
        self.announce.join()
        print(u'\tmain timer.')
        self.main_timer.join()
        print(u'\tbackup timer.')
        self.backup_timer.join()
        print(u'\trftimer.')
        self.rftimer.join()

    def start(self):
        """Start the timer and scoreboard threads."""
        if not self.started:
            self.log.debug(u'Meet startup.')
            self.announce.start()
            if self.scb is not self.announce:
                self.scb.start()
            self.main_timer.start()
            self.backup_timer.start()
            self.rftimer.start()
            self.gemini.start()
            self.db.start()
            self.started = True

    ## Track meet functions
    def delayed_export(self):
        """Queue an export on idle add."""
        self.exportpending = True
        glib.idle_add(self.exportcb)

    def save_curevent(self):
        """Backup and save current event."""
        ## NOTE: assumes curevent is defined, test externally

        # backup an existing config
        oldconf = self.event_configfile(self.curevent.event[u'evid'])
        if os.path.isfile(oldconf):
            os.rename(oldconf, oldconf + u'.1')	# one level of versioning?
        
        # call into event and save
        self.curevent.saveconfig()

        # mark event dirty in event db
        self.curevent.event[u'dirt'] = True

    def exportcb(self):
        """Save current event and update race info in external db."""
        if not self.exportpending:
            return False	# probably doubled up
        self.exportpending = False
        if self.curevent is not None and self.curevent.winopen:
            self.save_curevent()
        self.menu_data_export_activate_cb(None)
        return False # for idle add

    def saveconfig(self, lastevent=None):
        """Save current meet data to disk."""
        cw = jsonconfig.config()
        cw.add_section(u'trackmeet')
        cw.set(u'trackmeet', u'id', TRACKMEET_ID)
        if self.curevent is not None and self.curevent.winopen:
            self.save_curevent()
            cw.set(u'trackmeet', u'curevent', self.curevent.evno)
        elif lastevent is not None:
            cw.set(u'trackmeet', u'curevent', lastevent)
        cw.set(u'trackmeet', u'timerprint', self.timerprint)
        cw.set(u'trackmeet', u'maintimer', self.main_port)
        cw.set(u'trackmeet', u'udptimer', self.udpaddr)
        cw.set(u'trackmeet', u'backuptimer', self.backup_port)
        cw.set(u'trackmeet', u'rftimer', self.rftimer_port)
        cw.set(u'trackmeet', u'gemini', self.gemport)
        cw.set(u'trackmeet', u'dbhost', self.dbport)
        if self.timer is self.main_timer:
            cw.set(u'trackmeet', u'racetimer', u'main')
        else:
            cw.set(u'trackmeet', u'racetimer', u'backup')
        cw.set(u'trackmeet', u'scbport', self.scbport)
        cw.set(u'trackmeet', u'uscbport', self.annport)
        cw.set(u'trackmeet', u'title', self.titlestr)
        cw.set(u'trackmeet', u'subtitle', self.subtitlestr)
        cw.set(u'trackmeet', u'date', self.datestr)
        cw.set(u'trackmeet', u'location', self.locstr)
        cw.set(u'trackmeet', u'organiser', self.orgstr)
        cw.set(u'trackmeet', u'commissaire', self.commstr)
        cw.set(u'trackmeet', u'logos', self.logos)
        cw.set(u'trackmeet', u'mirrorpath', self.mirrorpath)
        cw.set(u'trackmeet', u'mirrorcmd', self.mirrorcmd)
        cw.set(u'trackmeet', u'linkbase', self.linkbase)
        cw.set(u'trackmeet', u'http_notify_host', self.http_notify_host)
        cw.set(u'trackmeet', u'http_notify_user', self.http_notify_user)
        cw.set(u'trackmeet', u'http_notify_pass', self.http_notify_pass)
        cw.set(u'trackmeet', u'http_notify_event', self.http_notify_event)
        cw.set(u'trackmeet', u'htmlfiles', self.htmlfiles)
        cw.set(u'trackmeet', u'clubmode', self.clubmode)
        cw.set(u'trackmeet', u'showevno', self.showevno)
        cw.set(u'trackmeet', u'provisional', self.provisional)
        cw.set(u'trackmeet', u'communiques', self.communiques)
        cw.set(u'trackmeet', u'commalloc', self.commalloc)	# map
        cw.set(u'trackmeet', u'tracklayout', self.tracklayout)
        cw.set(u'trackmeet', u'tracklen_n', unicode(self.tracklen_n))  # poss val?
        cw.set(u'trackmeet', u'tracklen_d', unicode(self.tracklen_d))
        cw.set(u'trackmeet', u'docindex', unicode(self.docindex))
        cw.set(u'trackmeet', u'graphicscb', self.graphicscb)
        cw.set(u'trackmeet', u'videowall', self.videowall)
        cwfilename = os.path.join(self.configpath, CONFIGFILE)
        self.log.debug(u'Saving meet config to ' + repr(cwfilename))
        with open(cwfilename , 'wb') as f:
            cw.write(f)
        self.rdb.save(os.path.join(self.configpath, u'riders.csv'))
        self.edb.save(os.path.join(self.configpath, u'events.csv'))
        # save out print settings
        self.printprefs.to_file(os.path.join(self.configpath, u'print.prf'))

    def reload_riders(self):
        # make a prelim mapped rider struct
        self.ridermap = {}
        for s in self.rdb.listseries():
            self.ridermap[s] = self.rdb.mkridermap(s)

    def loadconfig(self):
        """Load meet config from disk."""
        cr = jsonconfig.config({u'trackmeet':{u'maintimer':u'',
                                        u'timerprint':False,
                                        u'backuptimer':u'',
                                        u'racetimer':u'main',
					u'rftimer':u'',
                                        u'scbport':u'',
                                        u'uscbport':u'',
                                        u'showevno':True,
					u'resultnos':True,
                                        u'clubmode':True,
                                        u'htmlfiles':True,
                                        u'tracklen_n':250,
                                        u'tracklen_d':1,
                                        u'docindex':u'0',
                                        u'gemini':u'',
                                        u'dbhost':u'',
                                        u'title':u'',
                                        u'subtitle':u'',
                                        u'date':u'',
                                        u'location':u'',
                                        u'organiser':u'',
                                        u'commissaire':u'',
                                        u'logos':u'',
                                        u'curevent':u'',
                                        u'udptimer':None,
                                        u'mirrorcmd':u'',
                                        u'mirrorpath':u'',
                                        u'linkbase':u'.',
                                        u'http_notify_host':None,
                                        u'http_notify_user':None,
                                        u'http_notify_pass':None,
                                        u'http_notify_event':None,
                                        u'graphicscb':u'',
                                        u'tracklayout':u'',
                                        u'provisional':False,
                                        u'communiques':False,
                                        u'videowall':False,
                                        u'commalloc':{},
                                        u'id':u''}})
        cr.add_section(u'trackmeet')
        cr.merge(metarace.sysconf, u'trackmeet')
        cwfilename = os.path.join(self.configpath, CONFIGFILE)

        # re-set main log file
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
            self.loghandler.close()
            self.loghandler = None
        self.loghandler = logging.FileHandler(
                             os.path.join(self.configpath, LOGFILE))
        self.loghandler.setLevel(LOGHANDLER_LEVEL)
        self.loghandler.setFormatter(logging.Formatter(metarace.LOGFORMAT))
        self.log.addHandler(self.loghandler)

        # check for config file
        try:
            with open(cwfilename, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading meet config: ' + unicode(e))

        # set main timer port
        nport = cr.get(u'trackmeet', u'maintimer')
        if nport != self.main_port:
            self.main_port = nport
            self.main_timer.setport(nport)
            self.main_timer.sane()

        ## UDP Timer
        self.udpaddr = cr.get(u'trackmeet', u'udptimer')
        if not self.udpaddr:
            self.udpaddr = u'127.0.0.1'
        self.udptimer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # attempt to set SO_PRIORITY(12) (fails on MacOSX)
            self.udptimer.setsockopt(socket.SOL_SOCKET, 12, 6)
        except:
            pass

        # set backup timer port
        nport = cr.get(u'trackmeet', u'backuptimer')
        if nport != self.backup_port:
            self.backup_port = nport
            self.backup_timer.setport(nport)
            self.backup_timer.sane()

        # add rftimer if present
        nport = cr.get(u'trackmeet', u'rftimer')
        if nport != self.rftimer_port:
            self.rftimer_port = nport
            self.rftimer.setport(nport)
        
        # add gemini board if defined
        self.gemport = cr.get(u'trackmeet', u'gemini')
        if self.gemport != u'':
            self.gemini.setport(self.gemport)
            
        # add database export if present
        self.dbport = cr.get(u'trackmeet', u'dbhost')
        if self.dbport != u'':
            self.db.setport(self.dbport)

        # choose race timer
        if cr.get(u'trackmeet', u'racetimer') == u'main':
            if self.timer is self.backup_timer:
                self.menubut_main.activate()
        else:
            if self.timer is self.main_timer:
                self.menubut_backup.activate()

        # flag timer print in time-trial mode
        self.timerprint = strops.confopt_bool(cr.get(u'trackmeet',
                                                     u'timerprint'))

        # re-connect announce
        self.annport = cr.get(u'trackmeet', u'uscbport')
        self.announce.set_portstr(self.annport)
        self.announce.clrall()
        # choose scoreboard port
        nport = cr.get(u'trackmeet', u'scbport')
        if nport and len(nport) > 1 and nport[0] == u'#':
            # join scb through announce
            self.announce.add_channel(nport)
            self.scbport = nport
            self.scb.setport(u'none')
            self.scb.exit()
            self.scb = self.announce	# DANGER!
        else:
            if self.scbport != nport:
                self.scbport = nport
                self.scb.setport(nport)

        # set meet meta infos, and then copy into text entries
        self.titlestr = cr.get(u'trackmeet', u'title')
        self.subtitlestr = cr.get(u'trackmeet', u'subtitle')
        self.datestr = cr.get(u'trackmeet', u'date')
        self.locstr = cr.get(u'trackmeet', u'location')
        self.orgstr = cr.get(u'trackmeet', u'organiser')
        self.commstr = cr.get(u'trackmeet', u'commissaire')
        self.logos = cr.get(u'trackmeet', u'logos')
        self.mirrorpath = cr.get(u'trackmeet', u'mirrorpath')
        self.mirrorcmd = cr.get(u'trackmeet', u'mirrorcmd')
        self.linkbase = cr.get(u'trackmeet', u'linkbase')
        self.http_notify_host = cr.get(u'trackmeet', u'http_notify_host')
        self.http_notify_user = cr.get(u'trackmeet', u'http_notify_user')
        self.http_notify_pass = cr.get(u'trackmeet', u'http_notify_pass')
        self.http_notify_event = cr.get(u'trackmeet', u'http_notify_event')
        self.set_title()

        # result options (bool)
        self.htmlfiles = strops.confopt_bool(cr.get(u'trackmeet', u'htmlfiles'))
        self.clubmode = strops.confopt_bool(cr.get(u'trackmeet', u'clubmode'))
        self.showevno = strops.confopt_bool(cr.get(u'trackmeet', u'showevno'))
        self.provisional = strops.confopt_bool(cr.get(u'trackmeet', u'provisional'))
        self.communiques = strops.confopt_bool(cr.get(u'trackmeet', u'communiques'))
        self.videowall = strops.confopt_bool(cr.get(u'trackmeet', u'videowall'))
        # communique allocations -> fixed once only
        self.commalloc = cr.get(u'trackmeet', u'commalloc')

        # track length
        n = strops.confopt_posint(cr.get(u'trackmeet', u'tracklen_n'),0)
        d = strops.confopt_posint(cr.get(u'trackmeet', u'tracklen_d'),0)
        setlen = False
        if n > 0 and n < 5500 and d > 0 and d < 10: # sanity check
            self.tracklen_n = n
            self.tracklen_d = d
            setlen = True
        if not setlen:
            self.log.warn(u'Ignoring invalid track length - default used.')

        # track sector lengths
        self.tracklayout = cr.get(u'trackmeet',u'tracklayout')
        self.sectormap = timy.make_sectormap(self.tracklayout)

        # document id
        self.docindex = strops.confopt_posint(cr.get(u'trackmeet',
                                                     u'docindex'), 0)

        self.rdb.clear()
        self.edb.clear()
        self.edb.load(os.path.join(self.configpath, u'events.csv'))
        self.rdb.load(os.path.join(self.configpath, u'riders.csv'))
        self.reload_riders()

        cureventno = cr.get(u'trackmeet', u'curevent')
        if cureventno and cureventno in self.edb:
            self.open_event(self.edb[cureventno])

        # restore printer preferences
        psfilename = os.path.join(self.configpath, u'print.prf')
        if os.path.isfile(psfilename):
            try:
                self.printprefs.load_file(psfilename)
            except Exception as e:
                self.log.debug(u'Error loading print preferences: ' 
                                 + unicode(e))

        # make sure export path exists
        if not os.path.exists(self.exportpath):
            os.mkdir(self.exportpath)
            self.log.info(u'Created export path: ' + repr(self.exportpath))

        # add grpahic scb if required...
        self.graphicscb = cr.get(u'trackmeet', u'graphicscb')
        if self.graphicscb:
            glib.timeout_add_seconds(10, self.gfxscb_connect)

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        cid = cr.get(u'trackmeet', u'id')
        if cid != TRACKMEET_ID:
            self.log.error(u'Meet configuration mismatch: '
                           + repr(cid) + u' != ' + repr(TRACKMEET_ID))

    def gfxscb_connect(self):
        self.log.info('Joining gfx scb channel: ' + repr(self.graphicscb))
        self.announce.add_channel(self.graphicscb)
        return False

    def newgetrider(self, bib, series=u''):
        ret = None
        if series in self.ridermap and bib in self.ridermap[series]:
            ret = self.ridermap[series][bib]
        return ret
        
    def rider_edit(self, bib, series=u'', col=-1, value=u''):
        dbr = self.rdb.getrider(bib, series)
        if dbr is None:	# Scarmble>!!? it's bad form.
            dbr = self.rdb.addempty(bib, series)
        if col == riderdb.COL_FIRST:
            self.rdb.editrider(ref=dbr, first=value)
        elif col == riderdb.COL_LAST:
            self.rdb.editrider(ref=dbr, last=value)
        elif col == riderdb.COL_CLUB:
            self.rdb.editrider(ref=dbr, club=value)
        else:
            self.log.debug(u'Attempt to edit other rider column: ' + repr(col))
        ## lazy, full re-read. Should go the other way :/
        self.reload_riders()

    def get_clubmode(self):
        return self.clubmode

    def get_distance(self, count=None, units=u'metres'):
        """Convert race distance units to metres."""
        ret = None
        if count is not None:
            try:
                if units in [u'metres',u'meters']:
                    ret = int(count)
                elif units == u'laps':
                    ret = self.tracklen_n * int(count)
                    if self.tracklen_d != 1 and self.tracklen_d > 0:
                        ret //= self.tracklen_d
### !! Check this is correct in >=2.6 !!
            except (ValueError, TypeError, ArithmeticError), v:
                self.log.warn('Error computing race distance: ' + repr(v))
        return ret

    def __init__(self, configpath=None):
        """Meet constructor."""
        # logger and log handler
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        self.loghandler = None	# set in loadconfig to meet dir

        # meet configuration path and options
        if configpath is None:
            configpath = u'.'	# None assumes 'current dir'
        self.configpath = configpath
        self.exportpath = os.path.join(configpath, EXPORTPATH)
        self.titlestr = u''
        self.subtitlestr = u''
        self.datestr = u''
        self.locstr = u''
        self.orgstr = u''
        self.commstr = u''
        self.htmlfiles = True
        self.clubmode = True
        self.showevno = True
        self.provisional = False
        self.communiques = False
        self.videowall = False
        self.nextlinks = {}
        self.prevlinks = {}
        self.commalloc = {}
        self.timerport = None
        self.tracklen_n = 250	# numerator
        self.tracklen_d = 1	# d3nominator
        self.sectormap = {}	# map of timing channels to sector lengths
        self.tracklayout = None	# track configuration key
        self.logos = u''		# string list of logo filenames
        self.docindex = 0	# HACK: use for session number
        self.lastexport = None	# timestamp of last db dump
        self.exportpending = False
        self.mirrorpath = u''	# default rsync mirror path
        self.mirrorcmd = u''	# default rsync mirror command
        self.linkbase = u'.'
        self.http_notify_host = None
        self.http_notify_user = None
        self.http_notify_pass = None
        self.http_notify_event = None
        self.udpaddr = '192.168.0.21'

        # printer preferences
        paper = gtk.paper_size_new_custom('metarace-full',
                      'A4 for reports', 595, 842, gtk.UNIT_POINTS)
        self.printprefs = gtk.PrintSettings()
        self.pageset = gtk.PageSetup()
        self.pageset.set_orientation(gtk.PAGE_ORIENTATION_PORTRAIT)
        self.pageset.set_paper_size(paper)
        self.pageset.set_top_margin(0, gtk.UNIT_POINTS)
        self.pageset.set_bottom_margin(0, gtk.UNIT_POINTS)
        self.pageset.set_left_margin(0, gtk.UNIT_POINTS)
        self.pageset.set_right_margin(0, gtk.UNIT_POINTS)

        # hardware connections
        self.scb = sender.mksender(u'NULL')
        self.announce = telegraph.telegraph(80)
        #self.announce = telegraph.telegraph(self.scb.linelen)
        self.scbport = u'NULL'
        self.annport = u''
        self.timerprint = False	# enable timer printer?
        self.main_timer = timy.timy(u'', name=u'main')
        self.main_port = u''
        self.backup_timer = timy.timy(u'', name=u'bkup')
        self.backup_port = u''
        self.rftimer = thbc.thbc()
        self.rftimer_port = u''
        self.timer = self.main_timer
        self.gemini = gemini.gemini(u'')	# hack for Perth GP
        self.gemport = u''
        self.db = dbexport.dbexport()	# hack for CA Track Nats-> to be incl
        self.dbport = u''
        self.dbwatch = 0
        self.mirror = None	# file mirror thread
        self.exporter = None	# export worker thread... for speedy cb
        self.exportlock = threading.Lock()	# one only exporter
        self.graphicscb = u''	# no graphic scb to connect to

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'trackmeet.ui'))
        self.window = b.get_object('meet')
        self.window.connect('key-press-event', self.key_event)
        self.clock = b.get_object('menu_clock')
        self.clock_label = b.get_object('menu_clock_label')
        self.status = b.get_object('status')
        self.log_buffer = b.get_object('log_buffer')
        self.log_view = b.get_object('log_view')
        self.log_view.modify_font(pango.FontDescription("monospace 9"))
        self.log_scroll = b.get_object('log_box').get_vadjustment()
        self.context = self.status.get_context_id('metarace meet')
        self.menubut_main = b.get_object('menu_timing_main')
        self.menubut_backup = b.get_object('menu_timing_backup')
        self.menu_race_info = b.get_object('menu_race_info')
        self.menu_race_properties = b.get_object('menu_race_properties')
        self.menu_race_close = b.get_object('menu_race_close')
        self.menu_race_abort = b.get_object('menu_race_abort')
        self.race_box = b.get_object('race_box')
        self.new_race_pop = b.get_object('menu_race_new_types')
        b.connect_signals(self)

        # additional obs
        self.scbwin = None

        # run state
        self.running = True
        self.started = False
        self.curevent = None
        self.autorecurse = set()

        # format and connect status and log handlers
        f = logging.Formatter('%(levelname)s:%(name)s: %(message)s')
        self.sh = loghandler.statusHandler(self.status, self.context)
        self.sh.setLevel(logging.INFO)	# show info upon status bar
        self.sh.setFormatter(f)
        self.log.addHandler(self.sh)
        self.lh = loghandler.textViewHandler(self.log_buffer,
                      self.log_view, self.log_scroll)
        ## HACK: debug on console for rftimer testing
        #self.lh.setLevel(thbc.RFID_LOG_LEVEL)	# show info up in log view
        #self.lh.setLevel(timy.TIMER_LOG_LEVEL)	# show info up in log view
        self.sh.setLevel(logging.INFO)	# show info upon status bar
        #self.sh.setLevel(17)	# show info upon status bar
        self.lh.setFormatter(f)
        self.log.addHandler(self.lh)

        # get rider db and pack into scrolled pane
        self.rdb = riderdb.riderdb()
        self.ridermap = {}

        b.get_object('rider_box').add(self.rdb.mkview(ucicode=True,note=True))

        # get event db and pack into scrolled pane
        self.edb = eventdb.eventdb()
        self.edb.set_startlist_cb(self.eventdb_cb, 'startlist')
        self.edb.set_result_cb(self.eventdb_cb, 'result')
        self.edb.set_program_cb(self.eventdb_cb, 'program')
        b.get_object('event_box').add(self.edb.mkview())
        self.edb.set_evno_change_cb(self.race_evno_change)

	# now, connect each of the race menu types if present in builder
        for etype in self.edb.racetypes:
            lookup = u'mkrace_' + etype.replace(u' ', u'_')
            mi = b.get_object(lookup)
            if mi is not None:
                mi.connect('activate', self.menu_race_make_activate_cb, etype)

        # start timers
        glib.timeout_add_seconds(1, self.menu_clock_timeout)
        glib.timeout_add(50, self.timeout)

def main():
    """Run the trackmeet application."""
    configpath = None
    # expand config on cmd line to realpath _before_ doing chdir
    if len(sys.argv) > 2:
        print(u'usage: trackmeet [configdir]\n')
        sys.exit(1)
    elif len(sys.argv) == 2:
        configpath = os.path.realpath(os.path.dirname(sys.argv[1]))

    metarace.init()
    app = trackmeet(configpath)
    app.loadconfig()
    app.window.show()
    app.start()
    try:
        metarace.mainloop()
    except:
        app.shutdown(u'Exception from main loop.')
        raise

if __name__ == '__main__':
    main()
