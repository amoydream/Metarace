
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

"""Timing and data handling application wrapper for road events."""

import pygtk
pygtk.require("2.0")

import gtk
import glib
import pango
import gobject

import os
import sys
import csv
import logging
import random

import metarace

from metarace import ucsv
from metarace import jsonconfig
from metarace import tod
from metarace import eventdb
from metarace import riderdb
from metarace import thbc
from metarace import rrs
from metarace import rru
from metarace import timy
from metarace import telegraph
from metarace import unt4
from metarace import strops
from metarace import loghandler
from metarace import uiutil
from metarace import printing
from metarace import rsync
from metarace import namebank

LOGHANDLER_LEVEL = logging.DEBUG
ROADRACE_TYPES = {u'irtt':u'Road Time Trial',
                  u'billy':u'Time Trial',
                  u'rms':u'Road Race',
                  u'trtt':u'Team Road Time Trial',
                  u'crit':u'Criterium/Kermesse',
                  u'rhcp':u'Handicap',
                  u'sportif':u'Sportif Ride',
                  u'crosslap':u'Cross Laps',
                  u'cross':u'Cyclocross Race'}
CONFIGFILE = u'config.json'
LOGFILE = u'event.log'
ROADMEET_ID = u'roadmeet_2.0'  # configuration versioning
DEFAULT_RFID_HANDLER = u'thbc'
RFID_HANDLERS = {u'thbc':thbc.thbc,
                 u'rrs':rrs.rrs,
                 u'rru':rru.rru}
                 #u'wheeltime':wheeltime.wheeltime}
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

def rfid_device(devstr=u''):
    """Return a pair: (device, port) for the provided device string."""
    (a, b, c) = devstr.partition(u':')
    devtype = DEFAULT_RFID_HANDLER
    if b:
        a = a.lower()
        if a in RFID_HANDLERS:
            devtype = a
        a = c	# shift port into a
    devport = a
    return((devtype, devport))

class registerdlg(object):
    def __init__(self, meet=None):
        self.rdb = meet.rdb
        self.log = meet.log
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'transponder_reg.ui'))
        self.dlg = b.get_object('tagregister')
        self.dlg.set_transient_for(meet.window)
        self.rfidval = b.get_object('rfid_val')
        self.bibent = b.get_object('bib_entry')
        self.serent = b.get_object('series_entry')
        self.riderval = b.get_object('rider_val')
        self.autoinc = b.get_object('autoinc_but')
        self.autotrack = b.get_object('autotrack_but')
        self.storeid = None     # TODO: track scanned rfid with changing bib
        b.connect_signals(self)

    def series_entry_changed_cb(self, entry, data=None):
        bib = self.bibent.get_text().decode('utf-8')
        if bib:
            self.bib_entry_changed_cb(self.bibent)

    def bib_entry_changed_cb(self, entry, data=None):
        bib = entry.get_text().decode('utf-8')
        ser = self.serent.get_text().decode('utf-8')
        r = self.rdb.getrider(bib, ser)
        if r is not None:
            first = self.rdb.getvalue(r, riderdb.COL_FIRST)
            last = self.rdb.getvalue(r, riderdb.COL_LAST)
            club = self.rdb.getvalue(r, riderdb.COL_CLUB)
            cat = self.rdb.getvalue(r, riderdb.COL_CAT)
            refid = self.rdb.getvalue(r, riderdb.COL_REFID)
            self.riderval.set_text(u'{0} {1} ({2}) / {3}'.format(first,
                                      last, club, cat))
            if refid:
                self.rfidval.set_text(refid)
            else:
                self.rfidval.set_text(u'')
        else:
            self.riderval.set_text(u'[new rider]')
            self.rfidval.set_text(u'')

    def rfid_val_activate_cb(self, entry, data=None):
        """Activate on rfid val updates rider record."""
        bib = self.bibent.get_text().decode('utf-8')
        ser = self.serent.get_text().decode('utf-8')
        nrid = self.rfidval.get_text().decode('utf-8')
        r = self.rdb.getrider(bib, ser)
        if r is not None:
            self.rdb.editrider(r, refid=nrid)
            self.log.info(u'Updated refid on rider ' + repr(bib)
                          + u' to ' + repr(nrid))

    def bib_entry_activate_cb(self, entry, data=None):
        """Activate on bib adds new rider, unless it exists."""
        bib = entry.get_text().decode('utf-8')
        ser = self.serent.get_text().decode('utf-8')
        r = self.rdb.getrider(bib, ser)
        if r is None:
            self.rdb.addempty(bib, ser)
            self.log.debug(u'Added new rider ' + repr(bib) + u':' + repr(ser))

    def run(self):
        return self.dlg.run()

    def destroy(self):
        self.dlg.destroy()

    def increment_rider(self):
        cbib = self.bibent.get_text().decode('utf-8').strip()
        if cbib.isdigit():
            if self.autotrack.get_active():
                nbib = self.rdb.nextriderin(cbib,
                                  self.serent.get_text().decode('utf-8'))
                if nbib is not None:
                    self.bibent.set_text(unicode(nbib))
                #self.bibent.activate()	# no need to acitvate on extant no
            else:
                nbib = int(cbib) + 1
                self.bibent.set_text(unicode(nbib))
                self.bibent.activate()
        return False

    def register_tag(self, e):
        if e.refid:
            r = self.rdb.getrefid(e.refid)
            if r is not None:
                # rider already assined to tag?
                bib = self.rdb.getvalue(r, riderdb.COL_BIB)
                ser = self.rdb.getvalue(r, riderdb.COL_SERIES)
                self.rfidval.set_text(e.refid)
                self.bibent.set_text(bib)
                self.serent.set_text(ser)
                #self.bibent.grab_focus()
            else:
                # rider ok to assign or new rider
                bib = self.bibent.get_text().decode('utf-8')
                ser = self.serent.get_text().decode('utf-8')
                self.bibent.activate()	# required?
                r = self.rdb.getrider(bib, ser)
                if r is not None:
                    # check for existing tag allocation
                    orefid = self.rdb.getvalue(r, riderdb.COL_REFID)
                    if not orefid:
                        nrefid = e.refid.lower()
                        self.rdb.editrider(r, refid=nrefid)
                        self.log.warn(u'Assigned tag ' + repr(nrefid)
                               + u' to rider ' + repr(bib) + u':' + repr(ser))
                        self.rfidval.set_text(nrefid)
                        if self.autoinc.get_active():
                            glib.idle_add(self.increment_rider)
                        else:
                            self.bibent.grab_focus()
                    else:
                        self.log.error(u'Rider already assigned.')

class fakemeet:
    """Road meet placeholder for external event manipulations."""
    def __init__(self, edb, rdb, path):
        self.edb = edb
        self.rdb = rdb
        self.configpath = path
        self.timer = thbc.thbc()
        self.alttimer = timy.timy()
        self.stat_but = gtk.Button()
        self.scb = telegraph.telegraph()
        self.title_str = ''
        self.date_str = ''
        self.organiser_str = ''
        self.commissaire_str = ''
        self.logo = ''
        self.distance = None
        self.docindex = 0
        self.linkbase = u'.'
        self.writehtml = True
        self.bibs_in_results = True
    def get_distance(self):
        return self.distance

    def report_strings(self, rep):
        """Copy the meet strings into the supplied report."""

        ## this is a copy of meet.print_report()
        rep.strings[u'title'] = self.title_str
        rep.strings[u'subtitle'] = self.subtitle_str
        rep.strings[u'docstr'] = self.document_str
        rep.strings[u'datestr'] = strops.promptstr(u'Date:', self.date_str)
        rep.strings[u'commstr'] = strops.promptstr(u'Chief Commissaire:',
                                                  self.commissaire_str)
        rep.strings[u'orgstr'] = strops.promptstr(u'Organiser:',
                                                  self.organiser_str)
        if self.distance:
            rep.strings[u'diststr'] = strops.promptstr(u'Distance:',
                                           unicode(self.distance) + u' km')
        else:
            rep.strings[u'diststr'] = u''

    def announce_cmd(self, ctype=None, cmsg=None):
        pass
    def announce_time(self, data=None):
        pass
    def announce_clear(self):
        pass
    def announce_start(self, data=None):
        pass
    def announce_rider(self, data=None):
        pass
    def announce_gap(self, data=None):
        pass
    def announce_avg(self, data=None):
        pass
    def loadconfig(self):
        """Load meet config from disk."""
        cr = jsonconfig.config({u'roadmeet':{
               u'title':u'',
               u'shortname':u'',
               u'subtitle':u'',
               u'document':u'',
               u'date':u'',
               u'organiser':u'',
               u'commissaire':u'',
               u'logo':u'',
               u'distance':u'',
               u'docindex':u'0',
               u'resultnos':u'Yes',
               u'uscbport':u'',
               u'linkbase':u'.',
               u'writehtml':u'True',
               u'id':u''}})
        cr.add_section(u'roadmeet')
        cr.merge(metarace.sysconf, u'roadmeet')
        cwfilename = os.path.join(self.configpath, CONFIGFILE)
        # check for config file
        try:
            with open(cwfilename, 'rb') as f:
                cr.read(f)
        except Exception as e:
            print(u'Error reading meet config: ' + unicode(e))
        # set meet meta, and then copy into text entries
        self.shortname = cr.get(u'roadmeet', u'shortname')
        self.title_str = cr.get(u'roadmeet', u'title')
        self.subtitle_str = cr.get(u'roadmeet', u'subtitle')
        self.document_str = cr.get(u'roadmeet', u'document')
        self.date_str = cr.get(u'roadmeet', u'date')
        self.organiser_str = cr.get(u'roadmeet', u'organiser')
        self.commissaire_str = cr.get(u'roadmeet', u'commissaire')
        self.linkbase = cr.get(u'roadmeet', u'linkbase')
        self.writehtml = strops.confopt_bool(cr.get(u'roadmeet',
                                                    u'writehtml'))
        self.logo = cr.get(u'roadmeet', u'logo')
        self.distance = strops.confopt_float(cr.get(u'roadmeet', u'distance'))
        self.docindex = strops.confopt_posint(cr.get(u'roadmeet', u'docindex'), 0)
        self.bibs_in_results = strops.confopt_bool(cr.get(u'roadmeet',
                                                          u'resultnos'))
    def event_configfile(self, evno):
        """Return a config filename for the given event no."""
        return os.path.join(self.configpath, u'event_' 
                                     + unicode(evno) + u'.json')
    def default_template(self):
        tfile = os.path.join(self.configpath, u'template.json')
        if not os.path.exists(tfile):
            tfile = None
        return tfile

class roadmeet:
    """Road meet application class."""

    ## Meet Menu Callbacks

    def menu_meet_open_cb(self, menuitem, data=None):
        """Open a new meet."""
        if self.curevent is not None:
            self.close_event()

        dlg = gtk.FileChooserDialog(u'Open new road meet', self.window,
            gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER, (gtk.STOCK_CANCEL,
            gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dlg.set_current_folder(self.configpath)
        response = dlg.run()
        if response == gtk.RESPONSE_OK:
            self.configpath = dlg.get_filename()
            try:
                self.loadconfig()
                self.log.info(u'Meet data loaded from'
                               + repr(self.configpath) + u'.')
            except Exception as e:
                self.log.error(u'Open event: ' + repr(e))
        else:
            self.log.debug(u'Load new meet cancelled.')
        dlg.destroy()

    def menu_meet_save_cb(self, menuitem, data=None):
        """Save current all meet data to config."""
        self.saveconfig()
        self.log.info(u'Meet data saved to ' + repr(self.configpath) + u'.')

    def get_short_name(self):
        """Return the <= 16 char shortname."""
        return self.shortname

    def cat_but_auto_clicked(self, but, entry, data=None):
        """Lookup cats and write them into the supplied entry."""
        entry.set_text(' '.join(self.rdb.listcats()))
        
    def menu_race_properties_activate_cb(self, menuitem, data=None):
        """Edit race specific properties."""
        if self.curevent is not None:
            self.log.debug(u'Editing race properties.')
            self.curevent.edit_event_properties(self.window)

    ## TODO: call into curevent and fetch configurables + use generic props
    def menu_meet_properties_cb(self, menuitem, data=None):
        """Edit meet properties."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'roadmeet_props.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.window)

        # setup the type entry
        tcombo = b.get_object('type_combo')
        tmodel = b.get_object('type_model')
        tlbl = self.etype
        dotype = False
        if self.etype in ROADRACE_TYPES:
            tlbl = ROADRACE_TYPES[self.etype]
        if self.etype in [u'rms', u'rhcp', u'crit']:
            # make the combo so switcheroo is possible
            dotype = True
            cnt = 0
            for t in [u'rms', u'rhcp', u'crit']:
                tmodel.append([t, ROADRACE_TYPES[t]])
                if t == self.etype:
                    tcombo.set_active(cnt)
                cnt += 1
            tcombo.set_sensitive(True)
        else:
            tcombo.set_sensitive(False)
            tmodel.append([self.etype,tlbl])

        # fetch event result categories
        cat_ent = b.get_object('cat_entry')
        if self.curevent is not None:
            clist = u' '.join(self.curevent.get_catlist())
            cat_ent.set_text(clist)
            cba = b.get_object('cat_but_auto')
            cba.connect('clicked', self.cat_but_auto_clicked, cat_ent)

        # fill text entries
        t_ent = b.get_object('title_entry')
        t_ent.set_text(self.title_str)
        st_ent = b.get_object('subtitle_entry')
        st_ent.set_text(self.subtitle_str)
        doc_ent = b.get_object('document_entry')
        doc_ent.set_text(self.document_str)
        d_ent = b.get_object('date_entry')
        d_ent.set_text(self.date_str)
        o_ent = b.get_object('organiser_entry')
        o_ent.set_text(self.organiser_str)
        c_ent = b.get_object('commissaire_entry')
        c_ent.set_text(self.commissaire_str)
        di_ent = b.get_object('distance_entry')
        if self.distance is not None:
            di_ent.set_text(str(self.distance))
        upe = b.get_object('uscbsrv_host_entry')
        upe.set_text(self.uscbport)
        uce = b.get_object('uscbsrv_channel_entry')
        uce.set_text(self.uscbchan)
        upb = b.get_object('scb_port_dfl')
        upb.connect('clicked', 
                    lambda b: upe.set_text('dhm@localhost'))
        uoc = b.get_object('uscbsrv_option_check')
        uoc.set_active(self.remote_enable)
        mte = b.get_object('timing_main_entry')
        mte.set_text(self.timer_port)
        mtb = b.get_object('timing_main_dfl')
        mtb.connect('clicked',
                    lambda b: mte.set_text(u'thbc:' + thbc.DEFPORT))
        ate = b.get_object('timing_alt_entry')
        ate.set_text(self.alttimer_port)
        atb = b.get_object('timing_alt_dfl')
        atb.connect('clicked',
                    lambda b: ate.set_text(timy.BACKUPPORT))
        response = dlg.run()
        if response == 1:	# id 1 set in glade for "Apply"
            self.log.debug(u'Updating meet properties.')
            self.title_str = t_ent.get_text().decode('utf-8')
            self.subtitle_str = st_ent.get_text().decode('utf-8')
            self.document_str = doc_ent.get_text().decode('utf-8')
            self.date_str = d_ent.get_text().decode('utf-8')
            self.organiser_str = o_ent.get_text().decode('utf-8')
            self.commissaire_str = c_ent.get_text().decode('utf-8')
            self.distance = strops.confopt_float(
                                di_ent.get_text().decode('utf-8'))
            self.remote_enable = uoc.get_active()
            self.set_title()

            nchan = uce.get_text().decode('utf-8')
            nport = upe.get_text().decode('utf-8')
            if nport != self.uscbport or nchan != self.uscbchan:
                self.uscbport = nport
                self.uscbchan = nchan
                self.scb.set_portstr(portstr=self.uscbport,
                                     channel=self.uscbchan)
            self.remote_reset()
            self.set_timer(mte.get_text())
            nport = ate.get_text()
            if nport != self.alttimer_port:
                self.alttimer_port = nport
                self.alttimer.setport(nport)

            if self.curevent is not None:
                self.curevent.loadcats(cat_ent.get_text().decode('utf-8'))

            if dotype:
                # check for type change
                nt = tmodel.get_value(tcombo.get_active_iter(), 0)
                if nt != self.etype:
                    event = self.edb.getfirst()
                    event[u'type'] = nt
                    self.log.info(u'Changing event type from ' +
                                  repr(self.etype) + u' to ' + repr(nt))
                    self.etype = nt
                    # is a reload required?
            self.log.debug(u'Properties updated.')
        else:
            self.log.debug(u'Edit properties cancelled.')
        dlg.destroy()

    def menu_meet_fullscreen_toggled_cb(self, button, data=None):
        """Update fullscreen window view."""
        ## get current state:
        #gtk.gdk.WINDOW_STATE_FULLSCREEN
        if button.get_active():
            self.window.fullscreen()
        else:
            self.window.unfullscreen()

    def print_report(self, sections=[]):
        """Print the pre-formatted sections in a standard report."""
        tfile = self.default_template()
        rep = printing.printrep(template=tfile, path=self.configpath)
        rep.strings[u'title'] = self.title_str
        rep.strings[u'subtitle'] = self.subtitle_str
        rep.strings[u'docstr'] = self.document_str
        rep.strings[u'datestr'] = strops.promptstr(u'Date:', self.date_str)
        rep.strings[u'commstr'] = strops.promptstr(u'Chief Commissaire:',
                                                  self.commissaire_str)
        rep.strings[u'orgstr'] = strops.promptstr(u'Organiser:',
                                                  self.organiser_str)
        if self.distance:
            rep.strings[u'diststr'] = strops.promptstr(u'Distance:',
                                           unicode(self.distance) + u' km')
        else:
            rep.strings[u'diststr'] = u''

        for sec in sections:
            rep.add_section(sec)
        print_op = gtk.PrintOperation()
        print_op.set_allow_async(True)
        print_op.set_print_settings(self.printprefs)
        print_op.set_default_page_setup(self.pageset)
        print_op.connect("begin_print", self.begin_print, rep)
        print_op.connect("draw_page", self.draw_print_page, rep)
        res = print_op.run(gtk.PRINT_OPERATION_ACTION_PREVIEW,
                               None)
        #res = print_op.run(gtk.PRINT_OPERATION_ACTION_PREVIEW,
                               #self.window)
        if res == gtk.PRINT_OPERATION_RESULT_APPLY:
            self.printprefs = print_op.get_print_settings()
            self.log.debug(u'Updated print preferences.')
        elif res == gtk.PRINT_OPERATION_RESULT_IN_PROGRESS:
            self.log.debug(u'Print operation running asynchronously.')
        self.docindex += 1

        ofile = os.path.join(self.configpath, u'output.pdf')
        with open(ofile, 'wb') as f:
            rep.output_pdf(f)
        ofile = os.path.join(self.configpath, u'output.xls')
        with open(ofile, 'wb') as f:
            rep.output_xls(f)
        ofile = os.path.join(self.configpath, u'output.txt')
        with open(ofile, 'wb') as f:
            rep.output_text(f)
        return False

    def begin_print(self,  operation, context, rep):
        """Set print pages and units."""
        rep.start_gtkprint(context.get_cairo_context())
        operation.set_use_full_page(True)
        operation.set_n_pages(rep.get_pages())
        operation.set_unit('points')

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

    def menu_meet_quit_cb(self, menuitem, data=None):
        """Quit the application."""
        self.running = False
        self.window.destroy()

    ## Race Menu Callbacks
    def menu_race_run_activate_cb(self, menuitem=None, data=None):
        """Open the event handler."""
        eh = self.edb.getfirst() # only one event
        if eh is not None:
            self.open_event(eh)
            self.set_title()

    def menu_race_close_activate_cb(self, menuitem, data=None):
        """Close callback - disabled in roadrace."""
        self.close_event()
    
    def menu_race_abort_activate_cb(self, menuitem, data=None):
        """Close the currently open event without saving."""
        if self.curevent is not None:
            self.curevent.readonly = True
        self.close_event()

    def menu_race_armstart_activate_cb(self, menuitem, data=None):
        """Default armstart handler."""
        self.log.info(u'Arm Start')
        try:
            self.curevent.armstart()
        except Exception as e:
            self.log.error('Arm start - ' + repr(e))

    def menu_race_armlap_activate_cb(self, menuitem, data=None):
        """Default armlap handler."""
        self.log.info(u'Arm Lap')
        try:
            self.curevent.armlap()
        except Exception as e:
            self.log.error('Arm lap - ' + repr(e))

    def menu_race_armfin_activate_cb(self, menuitem, data=None):
        """Default armfin handler."""
        self.log.info(u'Arm Finish')
        try:
            self.curevent.armfinish()
        except Exception as e:
            self.log.error('Arm finish - ' + repr(e))

    def menu_race_finished_activate_cb(self, menuitem, data=None):
        """Default finished handler."""
        self.log.info(u'Finished')
        try:
            self.curevent.set_finished()
        except Exception as e:
            self.log.error('Set finished - ' + repr(e))

    def open_event(self, eventhdl=None):
        """Open provided event handle."""
        if eventhdl is not None:
            self.close_event()
            if self.etype == u'irtt':
                from metarace import irtt
                self.curevent = irtt.irtt(self, eventhdl, True)
            elif self.etype == u'billy':
                from metarace import billy
                self.curevent = billy.billy(self, eventhdl, True)
            elif self.etype == u'trtt':
                from metarace import trtt
                self.curevent = trtt.trtt(self, eventhdl, True)
            elif self.etype == u'cross':
                from metarace import cross
                self.curevent = cross.cross(self, eventhdl, True)
            elif self.etype == u'crosslap':
                from metarace import crosslap
                self.curevent = crosslap.crosslap(self, eventhdl, True)
            elif self.etype == u'sportif':
                from metarace import sportif
                self.curevent = sportif.sportif(self, eventhdl, True)
            else:	# default is fall back to road mass start 'rms'
                from metarace import rms
                self.curevent = rms.rms(self, eventhdl, True)
            
            assert(self.curevent is not None)
            self.curevent.loadconfig()
            self.race_box.add(self.curevent.frame)

            # re-populate the rider command model.
            cmdo = self.curevent.get_ridercmdorder()
            cmds = self.curevent.get_ridercmds()
            if cmds is not None:
                self.action_model.clear()
                for cmd in cmdo:
                    self.action_model.append([cmd, cmds[cmd]])
                self.action_combo.set_active(0)

            self.menu_race_close.set_sensitive(True)
            self.menu_race_abort.set_sensitive(True)
            starters = eventhdl[u'star']
            if starters is not None and starters != u'':
                self.curevent.race_ctrl('add', starters) # add listed starters
                eventhdl[u'star'] = u''	# and clear
            self.curevent.show()

    def close_event(self):
        """Close the currently opened race."""
        if self.curevent is not None:
            self.curevent.hide()
            self.race_box.remove(self.curevent.frame)
            self.curevent.destroy()
            self.menu_race_close.set_sensitive(False)
            self.menu_race_abort.set_sensitive(False)
            self.curevent = None
            uiutil.buttonchg(self.stat_but, uiutil.bg_none, 'Closed')
            self.stat_but.set_sensitive(False)

    ## Reports menu callbacks.
    def menu_reports_startlist_activate_cb(self, menuitem, data=None):
        """Generate a startlist."""
        if self.curevent is not None:
            sections = self.curevent.startlist_report()
            if sections:
                self.print_report(sections)
            else:
                self.log.info('Startlist - Nothing to print.')
            if not self.curevent.readonly:
                # emit starters to announcer
                self.scb.send_cmd(u'startlist', self.curevent.get_startlist())

    def menu_reports_signon_activate_cb(self, menuitem, data=None):
        """Generate a sign on sheet."""
        if self.curevent is not None:
            sections = self.curevent.signon_report()
            if sections:
                self.print_report(sections)
            else:
                self.log.info('Sign-on - Nothing to print.')

    def menu_reports_camera_activate_cb(self, menuitem, data=None):
        """Generate the camera operator report."""
        if self.curevent is not None:
            sections = self.curevent.camera_report()
            if sections:
                self.print_report(sections)

    def race_results_points_activate_cb(self, menuitem, data=None):
        """Generate the points tally report."""
        if self.curevent is not None:
            sections = self.curevent.points_report()
            if sections:
                self.print_report(sections)

    def menu_reports_result_activate_cb(self, menuitem, data=None):
        """Generate the race result report."""
        if self.curevent is not None:
            sections = self.curevent.result_report()
            if sections:
                self.print_report(sections)

    ### TODO: if reports have options, this is the dialog, saved to meet
    def menu_reports_prefs_activate_cb(self, menuitem, data=None):
        """Run the report preferences dialog."""
        self.log.info(u'Report preferences not implemented.')

    ## Data menu callbacks.

    ## TODO: launch rego dlg with card swiper -> namebank hooks
    def menu_data_rego_activate_cb(self, menuitem, data=None):
        """Open rider registration dialog."""
        rdlg = registerdlg(self)
        ocb = self.timer.getcb()
        self.log.debug(u'Read ocb == ' + repr(ocb))
        try:
            self.timer.setcb(rdlg.register_tag)
            rdlg.run()
        finally:
            self.timer.setcb(ocb)
            self.log.debug(u'Restored ocb == ' + repr(ocb))
        rdlg.destroy()

    def menu_data_uscb_activate_cb(self, menuitem, data=None):
        """Reload rider db from disk."""
        self.rdb.clear()
        self.rdb.load(os.path.join(self.configpath, u'riders.csv'))
        self.log.info(u'Reloaded riders from disk.')
        self.menu_race_run_activate_cb()

    def menu_import_riders_activate_cb(self, menuitem, data=None):
        """Add riders to database."""
        sfile = uiutil.loadcsvdlg(u'Select rider file to import',
                                   self.window, self.configpath)
        if sfile is not None:
            with namebank.namebank() as n:            
                self.rdb.load(sfile, namedb=n, overwrite=True)
        else:
            self.log.debug(u'Import riders cancelled.')

    def menu_import_chipfile_activate_cb(self, menuitem, data=None):
        """Import a transponder chipfile."""
        sfile = uiutil.loadcsvdlg(u'Select chipfile to import',
                                   self.window, self.configpath)
        if sfile is not None:
            self.rdb.load_chipfile(sfile)
        else:
            self.log.debug(u'Import chipfile cancelled.')

    # NOTE: Assumes snippet is suitably edited and then smashes in data
    def menu_import_replay_activate_cb(self, menuitem, data=None):
        """Replay an RFID logfile snippet."""
        if self.curevent is None:
            self.log.info(u'No event open.')
            return

        sfile = uiutil.loadcsvdlg(u'Select logfile to import',
                                  self.window, self.configpath)
        if sfile and os.path.isfile(sfile):
            self.timer.replay(sfile)
        else:
            self.log.debug(u'Replay RF data cancelled.')

    def menu_import_lif_activate_cb(self, menuitem, data=None):
        """Import a LIF file."""
        if self.curevent is None:
            self.log.info(u'No event open to import to.')
            return
        sfile = uiutil.loadcsvdlg(u'Select LIF file to import',
                                  self.window, self.configpath)
        if os.path.isfile(sfile):
            with open(sfile, 'rb') as f:
                cr = ucsv.UnicodeReader(f)
                ltime = None
                count = 0
                for r in cr:
                    ## TEMP: Race Result Import Override
                    if len(r) > 5:
                        rrank = strops.confopt_posint(r[0].split(u'.')[0],None)
                        rno = r[1]
                        rtim = tod.str2tod(r[5])
                        rbon = tod.str2tod(r[4])
                        if rrank is not None:
                            #self.curevent.setrftime(rno, rrank, rtim, rbon)
                            self.curevent.setriderval(rno, rrank, rtim, rbon)
                        
                    #if len(r) > 8:
                        #if r[3][0:5] != u'Etape':
                            #if r[1].isalnum() and r[1] != u'No.':
                                #bib = r[1].strip().lower()
                                #rank = strops.confopt_posint(r[0], None)
                                #if bib and rank is not None:
                                    #rftime = tod.str2tod(r[2])
                                    #rtime = tod.str2tod(r[6])
                                    #dtime = tod.str2tod(r[8])
                                    #if rftime is not None:
                                        #self.curevent.setrftime(bib, rank, rftime)
                                    #elif dtime is not None:
                                        ## this is a new bunch
                                        #ltime = rtime
                                        #self.curevent.setriderval(bib, rank, ltime)
                                    #count += 1
                self.log.info(u'Loaded ' + unicode(count) + u' riders from '
                                    + repr(sfile))
        else:
            self.log.debug(u'Import startlist cancelled.')

    ## TODO: repair the start time import for all event types
    ##       fix the series ambiguity in all event types
    def menu_import_startlist_activate_cb(self, menuitem, data=None):
        """Import a startlist."""
        if self.curevent is None:
            self.log.info(u'No event open to add starters to.')
            return
        count = 0
        sfile = uiutil.loadcsvdlg(u'Select startlist file to import',
                                  self.window, self.configpath)
        if os.path.isfile(sfile):
            with open(sfile, 'rb') as f:
                cr = ucsv.UnicodeReader(f)
                for r in cr:
                    if len(r) > 1 and r[1].isalnum() and r[1] != u'No.':
                        bib = r[1].strip().lower()
                        series = u''
                        if len(r) > 2:
                            series = r[2].strip()
                        self.curevent.addrider(bib, series)
                        start = tod.str2tod(r[0])
                        if start is not None:
                            self.curevent.starttime(start, bib, series)
                        count += 1
            self.log.info(u'Loaded ' + unicode(count) + u' riders from '
                           + repr(sfile))
        else:
            self.log.debug(u'Import startlist cancelled.')

    # no export support yet
    def menu_export_rftimes_activate_cb(self, menuitem, data=None):
        """Export raw rf timing data."""
        self.log.info(u'Export of raw rf timing data not implemented.')

    def menu_export_riders_activate_cb(self, menuitem, data=None):
        """Export rider database."""
        sfile = uiutil.savecsvdlg(u'Select file to export riders to',
                                  self.window,u'riders_export.csv',
                                  self.configpath)
        if sfile is not None:
            self.rdb.save(sfile)
        else:
            self.log.debug(u'Export riders cancelled.')

    def menu_export_chipfile_activate_cb(self, menuitem, data=None):
        """Export transponder chipfile from rider model."""
        sfile = uiutil.savecsvdlg(u'Select file to export refids to',
                                self.window, u'chipfile.csv', self.configpath)
        if sfile is not None:
            self.rdb.save_chipfile(sfile)
        else:
            self.log.debug(u'Export chipfile cancelled.')

    def menu_export_result_activate_cb(self, menuitem, data=None):
        """Export raw result to disk."""
        if self.curevent is None:
            self.log.info(u'No event open.')
            return
    
        rfilename = uiutil.savecsvdlg(u'Select file to save results to.',
                                       self.window,
                                       u'results.csv', self.configpath)
        if rfilename is not None:
            with open(rfilename , 'wb') as f:
                cw = ucsv.UnicodeWriter(f)
                cw.writerow([u'Rank', u'No.', u'Time', u'Bonus', u'Penalty'])
                for r in self.curevent.result_gen(u''):
                    opr = [u'',u'',u'',u'',u'']
                    for i in range(0,2):
                        if r[i]:
                            opr[i] = unicode(r[i])
                    for i in range(2,5):
                        if r[i]:
                            opr[i] = unicode(r[i].timeval)
                    cw.writerow(opr)
            self.log.info(u'Exported meet results to ' + repr(rfilename))
            lifdat = self.curevent.lifexport()
            if len(lifdat) > 0:
                with open(u'lifexport.lif', u'wb') as f:
                    cw = ucsv.UnicodeWriter(f, quoting=csv.QUOTE_MINIMAL)
                    for r in lifdat:
                        cw.writerow(r)

    def menu_export_startlist_activate_cb(self, menuitem, data=None):
        """Extract startlist from current event."""
        if self.curevent is None:
            self.log.info(u'No event open.')
            return
    
        rfilename = uiutil.savecsvdlg(u'Select file to save startlist to.',
                                       self.window,
                                       u'startlist.csv', self.configpath)
        if rfilename is not None:
            with open(rfilename , 'wb') as f:
                cw = ucsv.UnicodeWriter(f)
                cw.writerow([u'Start', u'No.', u'Series', u'Name', u'Cat'])
                for r in self.curevent.startlist_gen():
                    cw.writerow(r)
            self.log.info(u'Exported startlist to ' + repr(rfilename))

    def default_template(self):
        tfile = os.path.join(self.configpath, u'template.json')
        if not os.path.exists(tfile):
            tfile = None
        return tfile

    def export_result_maker(self):
        # 1: make report with meta from event and meet
        tfile = self.default_template()
        rep = printing.printrep(template=tfile, path=self.configpath)
        rep.strings[u'title'] = self.title_str
        rep.strings[u'subtitle'] = self.subtitle_str
        rep.strings[u'docstr'] = self.document_str
        rep.strings[u'datestr'] = strops.promptstr(u'Date:', self.date_str)
        rep.strings[u'commstr'] = strops.promptstr(u'Chief Commissaire:',
                                                  self.commissaire_str)
        rep.strings[u'orgstr'] = strops.promptstr(u'Organiser:',
                                                  self.organiser_str)
        if self.distance:
            rep.strings[u'diststr'] = strops.promptstr(u'Distance:',
                                             unicode(self.distance) + u' km')
        else:
            rep.strings[u'diststr'] = u''

        # 2: ensure export path valid
        exportpath = os.path.join(self.configpath, u'export')
        if not os.path.exists(exportpath):
            os.mkdir(exportpath)
 
        # 3: set provisional status	# TODO: other tests for prov flag?
        if self.curevent.timerstat != 'finished':
            rep.set_provisional(True)
        else:
            rep.reportstatus = u'final'	# TODO: write in other phases

        # 4: call into curevent to get result sections
        ## TODO: if status is 'new' write out a startlist
        ##       else result, but use catresult if flag in config
        dostart = False	# assume not exporting startlist
        if self.etype == u'irtt':
            if not self.curevent.onestart:
                dostart = True
        else:
            if self.curevent.timerstat in ['idle', 'ready']:
                dostart = True

        if dostart:	# emit startlist(s) instead of result
            for sec in self.curevent.startlist_report():
                rep.add_section(sec)
        else:
            for sec in self.curevent.result_report():
                rep.add_section(sec)

        # 5: write out files
        if self.mirrorfile:
            filebase = self.mirrorfile
        else:
            filebase = os.path.basename(self.configpath)
        if filebase in [u'', u'.']:
            filebase = u'result'
            self.log.warn(u'Using default filename for export: result')
        else:
            pass
            #filebase += u'_result'

        ofile = os.path.join(exportpath, filebase+u'.pdf')
        lb = os.path.join(self.linkbase, self.mirrorpath, filebase)
        rep.canonical = u'.'.join([lb, u'pdf'])
        with open(ofile, 'wb') as f:
            rep.output_pdf(f)
        ofile = os.path.join(exportpath, filebase+u'.xls')
        with open(ofile, 'wb') as f:
            rep.output_xls(f)
        ofile = os.path.join(exportpath, filebase+u'.json')
        with open(ofile, 'wb') as f:
            rep.output_json(f)
        lt = [u'pdf', u'xls', u'txt', u'json']
        ofile = os.path.join(exportpath, filebase+u'.txt')
        with open(ofile, 'wb') as f:
            rep.output_text(f, linkbase=lb, linktypes=lt)
        if self.writehtml:
            ofile = os.path.join(exportpath, filebase+u'.html')
            with open(ofile, 'wb') as f:
                rep.output_html(f, linkbase=lb, linktypes=lt)

    def menu_data_results_cb(self, menuitem, data=None):
        """Create live result report and export to MR"""
        self.saveconfig()
        self.log.info(u'Meet data saved to ' + repr(self.configpath) + u'.')
        if self.curevent is None:
            return

        if False:	# save current lif with export 
            lifdat = self.curevent.lifexport()
            if len(lifdat) > 0:
                with open(u'lifexport.lif', u'wb') as f:
                    cw = ucsv.UnicodeWriter(f, quoting=csv.QUOTE_MINIMAL)
                    for r in lifdat:
                        cw.writerow(r)

        # only build reports if the race is really stand-alone
        if not u'mk' in self.mirrorcmd:
            if self.etype in [u'sportif'] or self.mirrorcmd != u'echo':
                self.export_result_maker()

        glib.idle_add(self.mirror_start)
        self.log.info(u'Race info export.')

    ## Directory utilities
    def event_configfile(self, evno):
        """Return a config filename for the given event no."""
        return os.path.join(self.configpath, u'event_' 
                                     + unicode(evno) + u'.json')

    ## Timing menu callbacks
    def menu_timing_edit_activate_cb(self, menuitem, data=None):
        """Call into the event and run the timing edit if possible."""
        if self.curevent is not None:
            self.curevent.info_time_edit_clicked_cb(None)

    def menu_timing_subtract_activate_cb(self, menuitem, data=None):
        """Run the time of day subtraction dialog."""
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'tod_subtract.ui'))
        ste = b.get_object('timing_start_entry')
        ste.modify_font(pango.FontDescription("monospace"))
        fte = b.get_object('timing_finish_entry')
        fte.modify_font(pango.FontDescription("monospace"))
        nte = b.get_object('timing_net_entry')
        nte.modify_font(pango.FontDescription("monospace"))
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

    def menu_timing_start_activate_cb(self, menuitem, data=None):
        """Manually set race elapsed time via RFU trigger."""
        if self.curevent is None:
            self.log.info('No event open to set elapsed time on.')
        else:
            self.curevent.elapsed_dlg()

    def entry_set_now(self, button, entry=None):
        """Enter the 'now' time in the provided entry."""
        entry.set_text(tod.tod('now').timestr())
        entry.activate()

    def menu_timing_recalc(self, entry, ste, fte, nte):
        """Update the net time entry for the supplied start and finish."""
        st = tod.str2tod(ste.get_text())
        ft = tod.str2tod(fte.get_text())
        if st is not None and ft is not None:
            ste.set_text(st.timestr())
            fte.set_text(ft.timestr())
            nte.set_text((ft - st).timestr())

    def menu_timing_clear_activate_cb(self, menuitem, data=None):
        """Clear memory in attached timing devices."""
        self.log.info(u'Clear timer memory disabled.')

    def menu_timing_sync_activate_cb(self, menuitem, data=None):
        """Roughly synchronise decoder."""
        self.timer.sync()
        self.log.info(u'Rough sync decoder clock.')

    def menu_timing_reconnect_activate_cb(self, menuitem, data=None):
        """Reconnect timer and re-start."""
        (cdev, cport) = rfid_device(self.timer_port)
        self.log.debug(u'REPORT')
        self.timer.setport(cport)	# force reconnect
        self.log.debug(u'STOP')
        self.timer.stop_session()
        self.log.debug(u'SANE')
        self.timer.sane()
        self.log.debug(u'START')
        self.timer.start_session()
        self.alttimer.setport(self.alttimer_port)
        self.alttimer.sane()	#!! sets delay to 1.0s!
        ## TODO: move this and the others into method, and include
        ## all road-ish events: 'rhcp', 'rms', None
        if self.etype == 'rms':	# road race finish requires 1.0s delay
            self.alttimer.write('DTF01.00')
        else:
            self.alttimer.delaytime('0.01')	# irtt likes no delay
        self.log.info(u'Re-connect/re-start attached timer.')

    def restart_decoder(self, data=None):
        """Request re-start of decoder."""
        self.log.debug(u'START')
        self.timer.start_session()
        return None

    def menu_timing_configure_activate_cb(self, menuitem, data=None):
        """Attempt to re-configure the attached decoder from saved config."""
        if not uiutil.questiondlg(self.window,
                                  u'Re-configure Decoder Settings?',
                                  u'Note: Passings will not be registered while decoder is being configured. Connection must be serial to update IP config.'):
            self.log.debug(u'Config aborted.')
            return False

        self.log.debug(u'STOP')
        self.timer.stop_session()
        self.log.debug(u'SANE')
        self.timer.sane()
        self.log.debug(u'IPCONFIG')
        glib.timeout_add_seconds(60, self.restart_decoder)
        self.timer.ipconfig()
        return None

    ## Help menu callbacks
    def menu_help_docs_cb(self, menuitem, data=None):
        """Display program help."""
        metarace.help_docs(self.window)

    def menu_help_about_cb(self, menuitem, data=None):
        """Display metarace about dialog."""
        metarace.about_dlg(self.window)

    ## Race Control Elem callbacks
    def race_stat_but_clicked_cb(self, button, data=None):
        """Call through into event if open."""
        if self.curevent is not None:
            self.curevent.stat_but_clicked(button)

    def race_stat_entry_activate_cb(self, entry, data=None):
        """Pass the chosen action and bib list through to curevent,"""
        action = self.action_model.get_value(
                       self.action_combo.get_active_iter(), 0)
        if self.curevent is not None:
            if self.curevent.race_ctrl(action,
                     self.action_entry.get_text().decode('utf-8')):
                self.action_entry.set_text('')
   
    ## Menu button callbacks
    def race_action_combo_changed_cb(self, combo, data=None):
        """Notify curevent of change in combo."""
        aiter = self.action_combo.get_active_iter()
        if self.curevent is not None and aiter is not None:
            action = self.action_model.get_value(aiter, 0)
            self.curevent.ctrl_change(action, self.action_entry)

    def menu_rfustat_clicked_cb(self, button, data=None):
        self.timer.status()
        self.alttimer.status()

    def menu_clock_clicked_cb(self, button, data=None):
        """Handle click on menubar clock."""
        self.log.info(u'PC ToD: ' + self.clock_label.get_text())

    ## 'Slow' Timer callback - this is the main event routine
    def timeout(self):
        """Update status buttons and time of day clock button."""
        if self.running:
            # update pc ToD label
            self.clock_label.set_text(tod.tod('now').meridian())

            # call into race timeout handler
            if self.curevent is not None:
                self.curevent.timeout()

            # check for completion in the rsync module
            if self.mirror is not None:
                if not self.mirror.is_alive():
                    self.mirror = None
        else:
            return False
        return True

    ## Window methods
    def set_title(self, extra=u''):
        """Update window title from meet properties."""
        typepfx = u''
        title = self.title_str.strip()
        if self.etype in ROADRACE_TYPES:
            typepfx = ROADRACE_TYPES[self.etype] + u' :: '
        self.window.set_title(typepfx + title)
        if self.curevent is not None:
            self.curevent.set_titlestr(self.subtitle_str)

    def meet_destroy_cb(self, window, msg=u''):
        """Handle destroy signal and exit application."""
        self.close_event()
        self.log.removeHandler(self.sh)
        self.log.removeHandler(self.lh)
        self.window.hide()
        metarace.unlockpath(self.configpath)
        self.log.info(u'Meet destroyed. ' + msg)
        glib.idle_add(self.meet_destroy_handler)

    def meet_destroy_handler(self):
        if self.started:
            self.saveconfig()
            self.shutdown()	# threads are joined in shutdown
        # close event and remove main log handler
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
        self.running = False
        # flag quit in main loop
        gtk.main_quit()
        return False

    def key_event(self, widget, event):
        """Collect key events on main window and send to race."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                key = key.lower()
                if key in ['0','1']:
                    self.timer.trig(chan='MAN', refid=key)
                    return True
                elif key in ['2','3','4','5','6','7','8','9']:
                    self.alttimer.trig(chan=key)
                    return True
            if self.curevent is not None:
                return self.curevent.key_event(widget, event)
        return False

    def shutdown(self, msg=''):
        """Cleanly shutdown threads and close application."""
        if self.curevent is not None:
            self.curevent.destroy()	# necessary here?

        self.started = False

        self.window.hide()
        self.timer.exit(msg)
        self.alttimer.exit(msg)
        self.scb.exit(msg)
        print (u'Waiting for workers to exit...')
        if self.mirror is not None:
            print(u'\tlive result mirror.')
            self.mirror.join()
            self.mirror = None
        print(u'\tmain timer.')
        self.timer.join()
        print(u'\talt timer.')
        self.alttimer.join()
        print(u'\tannouncer.')
        self.scb.join()

    def start(self):
        """Start the timer and rfu threads."""
        if not self.started:
            self.log.debug(u'Meet startup.')
            self.scb.start()
            self.timer.start()
            self.alttimer.start()
            self.started = True

    ## Roadmeet functions
    def saveconfig(self):
        """Save current meet data to disk."""
        if self.curevent is not None and self.curevent.winopen:
            self.curevent.saveconfig()
        cw = jsonconfig.config()
        cw.add_section(u'roadmeet')
        cw.set(u'roadmeet', u'id', ROADMEET_ID)
        cw.set(u'roadmeet', u'uscbport', self.uscbport)
        cw.set(u'roadmeet', u'uscbchan', self.uscbchan)
        cw.set(u'roadmeet', u'uscbopt', self.remote_enable)
        cw.set(u'roadmeet', u'timer', self.timer_port)
        #cw.set(u'roadmeet', u'rfidlvl', self.rfidlvl)
        cw.set(u'roadmeet', u'alttimer', self.alttimer_port)

        cw.set(u'roadmeet', u'shortname', self.shortname)
        cw.set(u'roadmeet', u'linkbase', self.linkbase)
        cw.set(u'roadmeet', u'writehtml', self.writehtml)
        cw.set(u'roadmeet', u'title', self.title_str)
        cw.set(u'roadmeet', u'subtitle', self.subtitle_str)
        cw.set(u'roadmeet', u'document', self.document_str)
        cw.set(u'roadmeet', u'date', self.date_str)
        cw.set(u'roadmeet', u'organiser', self.organiser_str)
        cw.set(u'roadmeet', u'commissaire', self.commissaire_str)
        cw.set(u'roadmeet', u'logo', self.logo)

        cw.set(u'roadmeet', u'resultnos', self.bibs_in_results)
        cw.set(u'roadmeet', u'distance', self.distance)
        cw.set(u'roadmeet', u'docindex', self.docindex)
        cw.set(u'roadmeet', u'loglevel', self.loglevel)
        cw.set(u'roadmeet', u'mirrorpath', self.mirrorpath)
        cw.set(u'roadmeet', u'mirrorcmd', self.mirrorcmd)
        cw.set(u'roadmeet', u'mirrorfile', self.mirrorfile)

        cwfilename = os.path.join(self.configpath, CONFIGFILE)
        self.log.debug(u'Saving meet config to ' + repr(cwfilename))
        with open(cwfilename , 'wb') as f:
            cw.write(f)
        self.rdb.save(os.path.join(self.configpath, u'riders.csv'))
        self.edb.save(os.path.join(self.configpath, u'events.csv'))
        # save out print settings
        if self.printprefs is not None:
            self.printprefs.to_file(os.path.join(self.configpath,
                                                   u'print.prf'))

    def set_timer(self, newdevice=u''):
        """Re-set the main timer devices as specified."""
        # Step 1: is a change required?
        if newdevice != self.timer_port:
            (dev, nport) = rfid_device(newdevice)
            if type(self.timer) is RFID_HANDLERS[dev]:
                self.log.debug(u'timer thread same type - no change required.')
                self.timer.setport(nport)
            else:	# need to 'switch' to new handler
                wasalive = self.timer.is_alive()
                self.timer.exit(u'Switching devices.')
                self.timer = None	# unref
                self.timer = RFID_HANDLERS[dev](port=nport)
                if wasalive:
                    self.timer.start()
            # save change
            self.timer_port = newdevice 
        else:
            self.log.debug(u'set_timer - No change required.')

    def loadconfig(self):
        """Load meet config from disk."""
        if not metarace.lockpath(self.configpath):
            self.log.error(u'Unable to get lock on configpath.')
            raise Exception(u'Unable to get lock on configpath.')
            # but this causes LSP drama

        cr = jsonconfig.config({u'roadmeet':{
               u'shortname':None,
               u'title':u'',
               u'subtitle':u'',
               u'document':u'',
               u'date':u'',
               u'organiser':u'',
               u'commissaire':u'',
               u'logo':u'',
               u'distance':u'',
               u'docindex':u'0',
               u'timer':u'',
               #u'rfidlvl':[u'30', u'30'],
               u'alttimer':u'',
               u'resultnos':u'Yes',
               u'uscbport':u'',
               u'uscbchan':u'',
               u'linkbase':u'.',
               u'writehtml':u'True',
               u'uscbopt':u'No',
               u'mirrorpath':u'',
               u'mirrorcmd':u'echo',
               u'mirrorfile':u'',
               u'loglevel':unicode(logging.INFO),
               u'id':u''}})
        cr.add_section(u'roadmeet')
        cr.merge(metarace.sysconf, u'roadmeet')
        cwfilename = os.path.join(self.configpath, CONFIGFILE)

        # re-set log file
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

        # configurable log level on UI (does not change log file)
        self.loglevel = strops.confopt_posint(cr.get(u'roadmeet', u'loglevel'),
                                              logging.INFO)
        self.lh.setLevel(self.loglevel)
        self.log.debug(u'UI Log level: ' + repr(self.loglevel))
        self.log.debug(u'Reading event config.')

        # set uSCBsrv connection
        self.uscbchan = cr.get(u'roadmeet', u'uscbchan')
        self.uscbport = cr.get(u'roadmeet', u'uscbport')
        self.scb.set_portstr(portstr=self.uscbport,
                             channel=self.uscbchan)
        self.remote_enable = strops.confopt_bool(cr.get(u'roadmeet',
                                                        u'uscbopt'))
        self.remote_reset()

        # set timer port (decoder)
        self.set_timer(cr.get(u'roadmeet', u'timer'))
        #boxlvl = u'20'
        #stalvl = u'30'
        #nv = cr.get(u'roadmeet', u'rfidlvl')
        #if len(nv) > 1:
            #boxlvl = nv[0]
            #stalvl = nv[1]
        #self.timer.setlvl(boxlvl, stalvl)
        #self.rfidlvl = [boxlvl, stalvl]

        # set alt timer port (timy)
        nport = cr.get(u'roadmeet', u'alttimer')
        if nport != self.alttimer_port:
            self.alttimer_port = nport
            self.alttimer.setport(nport)
            self.alttimer.sane() # sane prod here is probably good idea
            if self.etype == 'rms':	# road race finish requires 1.0s delay
                self.alttimer.write('DTF01.00')

        # set meet meta, and then copy into text entries
        self.shortname = cr.get(u'roadmeet', u'shortname')
        self.title_str = cr.get(u'roadmeet', u'title')
        self.subtitle_str = cr.get(u'roadmeet', u'subtitle')
        self.document_str = cr.get(u'roadmeet', u'document')
        self.date_str = cr.get(u'roadmeet', u'date')
        self.organiser_str = cr.get(u'roadmeet', u'organiser')
        self.commissaire_str = cr.get(u'roadmeet', u'commissaire')
        self.logo = cr.get(u'roadmeet', u'logo')
        self.distance = strops.confopt_float(cr.get(u'roadmeet', u'distance'))
        self.docindex = strops.confopt_posint(cr.get(u'roadmeet', u'docindex'), 0)
        self.linkbase = cr.get(u'roadmeet', u'linkbase')
        self.writehtml = strops.confopt_bool(cr.get(u'roadmeet',
                                                    u'writehtml'))
        self.bibs_in_results = strops.confopt_bool(cr.get(u'roadmeet',
                                                          u'resultnos'))
        self.mirrorpath = cr.get(u'roadmeet', u'mirrorpath')
        self.mirrorcmd = cr.get(u'roadmeet', u'mirrorcmd')
        self.mirrorfile = cr.get(u'roadmeet', u'mirrorfile')

        # Re-Initialise rider and event databases
        self.rdb.clear()
        self.edb.clear()
        self.rdb.load(os.path.join(self.configpath, u'riders.csv'))
        self.edb.load(os.path.join(self.configpath, u'events.csv'))
        event = self.edb.getfirst()
        if event is None:	# add a new event of the right type
            event = self.edb.add_empty(evno=u'0')
            event[u'type'] = self.etype
        else:
            self.etype = event[u'type']
            self.log.debug(u'Existing event in db: ' + repr(self.etype))
        self.open_event(event) # always open on load if posible
        self.set_title()

        # timerconfig post event load
        if self.etype == 'rms':	# road race finish requires 1.0s delay
            self.alttimer.write('DTF01.00')

        # restore printer preferences
        psfilename = os.path.join(self.configpath, u'print.prf')
        if os.path.isfile(psfilename):
            try:
                self.printprefs.load_file(psfilename)
            except Exception as e:
                self.log.debug(u'Error loading print preferences: '
                                 + unicode(e))

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        cid = cr.get(u'roadmeet', u'id')
        if cid and cid != ROADMEET_ID:
            self.log.error(u'Meet configuration mismatch: '
                           + repr(cid) + u' != ' + repr(ROADMEET_ID))
        self.log.debug(u'Event config done.')

    def get_distance(self):
        """Return race distance in km."""
        return self.distance

    def announce_timer(self, evt, timer=None):
        """Send message into announce for remote control."""
        if not self.remote_enable:
            if timer is None:
                timer = self.timer
            prec = 4
            if timer is self.timer:
                prec = 3	# transponders have reduced precision
            elif u'M' in evt.chan:
                prec = 3
            source = timer.unitno
            if evt.source is not None:
                source = evt.source
            tvec = [evt.index, source, evt.chan,
                    evt.refid, evt.rawtime(prec)]
            self.scb.send_cmd(u'timer',unichr(unt4.US).join(tvec),
                                       self.timerchan)

    def announce_clear(self):
        """Clear announce panels."""
        self.announce_model.clear()
        self.scb.clrall()

    def announce_title(self, msg):
        """Set the announcer title."""
        # no local announce title?
        self.scb.set_title(msg)

    def announce_start(self, starttod=tod.ZERO):
        """Set the announce start offset."""
        if starttod != None:
            self.an_cur_start = starttod
        self.scb.set_start(starttod)

    def announce_cmd(self, ctype=None, cmsg=None):
        """Announce an generic uscbsrv command."""
        self.scb.send_cmd(ctype, cmsg)

    def announce_rider(self, rvec):
        """Announce the supplied rider vector."""
        # announce locally
        nr = [u'',u'',u'',u'',u'',u'',u'#000000',None] # error bar?
        if len(rvec) == 5:
            rftime = tod.str2tod(rvec[4])
            if rftime is not None:
                if len(self.announce_model) == 0:
                    # Case 1: Starting a new lap
                    self.an_cur_lap = (rftime-self.an_cur_start).truncate(0)
                    self.an_cur_split = rftime.truncate(0)
                    self.an_cur_bunchid = 0
                    self.an_cur_bunchcnt = 1
                    self.an_last_time = rftime
                    nr=[rvec[0],rvec[1],rvec[2],rvec[3],
                        self.an_cur_lap.rawtime(0),
                        self.an_cur_bunchcnt,
                        COLOURMAP[self.an_cur_bunchid][0],
                        rftime]
                elif (self.an_last_time is not None and
                        (rftime < self.an_last_time
                         or rftime - self.an_last_time < tod.tod('1.12'))):
                    # Case 2: Same bunch
                    self.an_last_time = rftime
                    self.an_cur_bunchcnt += 1
                    nr=[rvec[0],rvec[1],rvec[2],rvec[3],
                        (rftime-self.an_cur_start).rawtime(0),
                        self.an_cur_bunchcnt,
                        COLOURMAP[self.an_cur_bunchid][0],
                        rftime]
                else:
                    # Case 3: New bunch
                    self.announce_model.append(
                         [u'',u'',u'',u'',u'',u'',u'#fefefe',None])
                    self.an_cur_bunchid = (self.an_cur_bunchid + 1)%COLOURMAPLEN
                    self.an_cur_bunchcnt = 1
                    self.an_last_time = rftime
                    nr=[rvec[0],rvec[1],rvec[2],rvec[3],
                        (rftime-self.an_cur_start).rawtime(0),
                        self.an_cur_bunchcnt,
                        COLOURMAP[self.an_cur_bunchid][0],
                        rftime]
            else:
                # Informative non-timeline record
                nr=[rvec[0],rvec[1],rvec[2],rvec[3],
                        rvec[4], u'', '#fefefe',None]
            self.announce_model.append(nr)
        else:
            self.log.info('Ignored unknown rider announce vector')
        self.scb.add_rider(rvec)

        return False	# so can be idle-added

    def mirror_start(self):
        """Create a new rsync mirror thread unless in progress."""
        if self.mirrorpath and self.mirror is None:
            self.mirror = rsync.mirror(
                localpath = os.path.join(self.configpath,u'export',u''),
                remotepath = self.mirrorpath,
                mirrorcmd = self.mirrorcmd)
            self.mirror.start()
        return False    # for idle_add

    def remote_reset(self):
        """Reset remote control callbacks and status."""
        # TODO: Reset authentication variables
        # TODO: Private message callbacks
        self.log.debug(u'Remote control reset.')
        if self.remote_enable:
            self.log.debug(u'Enabled with:' + repr(self.remote_pub_cb))
            self.scb.set_pub_cb(self.remote_pub_cb)
        else:
            self.log.debug(u'Disabled.')
            self.scb.set_pub_cb()

    def remote_pub_cb(self, cmd, nick, chan):
        """Handle a remote control message."""
        if chan == self.timerchan and cmd.header == u'timer':
            tv = cmd.text.split(unichr(unt4.US)) # split into fields
            if len(tv) > 4:	# enough fields
                try:
                    index = 0
                    if tv[0].isdigit():
                        index = int(tv[0])	# record index
                    ## TODO: handle discontinuity
                    srcid = tv[1]
                    self.log.debug(u'Remote Timer: ' + repr(srcid)
                                   + u' index:' + repr(index) + u' '
                                   + tv[2] + u'/' + tv[3] + u'@' + tv[4])
                    if u'timy' in srcid:
                        self.alttimer.trig(timeval=tv[4], index=tv[0],
                                    chan=tv[2], refid=tv[3], sourceid=srcid)
                    else:
                        if srcid == u'3km':
                            thetime = tod.str2tod(tv[4])
                            if thetime is not None:
                                tv[4] = (thetime - tod.tod(u'30:00')).rawtime()
                        self.timer.trig(timeval=tv[4], index='REM',
                                    chan=tv[2], refid=tv[3], sourceid=srcid)
                except Exception as e:
                    self.log.error('Reading timer msg: ' + repr(e))
        else:
            if cmd.header == u'rfidstatus':
                self.menu_rfustat_clicked_cb(None)
            else:
                self.log.debug(u'Unknown remote cmd ' + repr(nick) + u': '
                       + repr(cmd.header) + u'::' + repr(cmd.text))

    def __init__(self, configpath=None, etype=None):
        """Meet constructor."""
        # logger and log handler
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        self.loghandler = None	# set in loadconfig to meet dir

        if etype not in ROADRACE_TYPES:
            etype = None	# fall back on default
        self.etype = etype

        # meet configuration path and options
        if configpath is None:
            configpath = u'.'	# None assumes 'current dir'
        self.configpath = configpath
        self.shortname = None
        self.title_str = u''
        self.subtitle_str = u''
        self.document_str = u''
        self.date_str = u''
        self.organiser_str = u''
        self.commissaire_str = u''
        self.linkbase = u'.'
        self.writehtml = True
        self.logo = u''
        self.distance = None
        self.docindex = 0
        self.bibs_in_results = True
        self.remote_enable = False
        self.loglevel = logging.INFO	# UI log window

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
        self.timerchan = '#timing'	# raw timing channel on telegraph
        self.timer = thbc.thbc()	# default is Tag, may change later
        self.timer_port = u''
        #self.rfidlvl = [u'20',u'30']
        self.alttimer = timy.timy()
        self.alttimer_port = u''
        self.uscbport = u''
        self.uscbchan = u'#announce'
        self.scb = telegraph.telegraph()
        self.scb.add_channel(self.timerchan)	# persists over server recon
        self.mirrorpath = u''
        self.mirrorcmd = u'echo'
        self.mirrorfile = u''
        self.mirror = None 

        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'roadmeet.ui'))
        self.window = b.get_object('meet')
        self.window.connect('key-press-event', self.key_event)
        self.clock = b.get_object('menu_clock')
        self.clock_label = b.get_object('menu_clock_label')
        self.menu_rfustat_img = b.get_object('menu_rfustat_img')
        self.status = b.get_object('status')
        self.log_buffer = b.get_object('log_buffer')
        self.log_view = b.get_object('log_view')
        self.log_view.modify_font(pango.FontDescription("monospace 12"))
        self.log_scroll = b.get_object('log_box').get_vadjustment()
        self.context = self.status.get_context_id('metarace meet')
        self.menu_race_close = b.get_object('menu_race_close')
        self.menu_race_abort = b.get_object('menu_race_abort')
        self.race_box = b.get_object('race_box')
        self.stat_but = b.get_object('race_stat_but')
        self.action_model = b.get_object('race_action_model')
        self.action_combo = b.get_object('race_action_combo')
        self.action_entry = b.get_object('race_action_entry')
        b.get_object('race_tab_img').set_from_file(
                                   metarace.default_file(metarace.LOGO_FILE))
        b.get_object('race_stat_hbox').set_focus_chain([self.action_combo,
                                             self.action_entry,
                                             self.action_combo])

        # prepare local scratch pad
        self.an_cur_lap = tod.ZERO
        self.an_cur_split = tod.ZERO
        self.an_cur_bunchid = 0
        self.an_cur_bunchcnt = 0
        self.an_last_time = None
        self.an_cur_start = tod.ZERO
        self.announce_model = gtk.ListStore(gobject.TYPE_STRING,  # rank
                                    gobject.TYPE_STRING,  # no.
                                    gobject.TYPE_STRING,  # namestr
                                    gobject.TYPE_STRING,  # cat/com
                                    gobject.TYPE_STRING,  # timestr
                                    gobject.TYPE_STRING,  # bunchcnt
                                    gobject.TYPE_STRING,  # colour
                                    gobject.TYPE_PYOBJECT) # rftod
        t = gtk.TreeView(self.announce_model)
        t.set_reorderable(False)
        t.set_rules_hint(False)
        t.set_headers_visible(False)
        t.set_search_column(1)
        t.modify_font(pango.FontDescription('bold 20px'))
        uiutil.mkviewcoltxt(t, 'Rank', 0,width=60)
        uiutil.mkviewcoltxt(t, 'No.', 1,calign=1.0,width=60)
        uiutil.mkviewcoltxt(t, 'Rider', 2,expand=True,fixed=True)
        uiutil.mkviewcoltxt(t, 'Cat', 3,calign=0.0)
        uiutil.mkviewcoltxt(t, 'Time', 4,calign=1.0,width=100,
                                        fontdesc='monospace bold')
        uiutil.mkviewcoltxt(t, 'Bunch', 5,width=50,bgcol=6,calign=0.5)
        t.show()
        b.get_object('notepad_box').add(t)

        b.connect_signals(self)

        # run state
        self.running = True
        self.started = False
        self.curevent = None

        # format and connect status and log handlers
        f = logging.Formatter('%(levelname)s:%(name)s: %(message)s')
        self.sh = loghandler.statusHandler(self.status, self.context)
        self.sh.setLevel(logging.INFO)	# show info+ on status bar
        self.sh.setFormatter(f)
        self.log.addHandler(self.sh)
        self.lh = loghandler.textViewHandler(self.log_buffer,
                      self.log_view, self.log_scroll)
        self.lh.setLevel(self.loglevel)
        self.lh.setFormatter(f)
        self.log.addHandler(self.lh)

        # get rider db and pack into a dialog
        self.rdb = riderdb.riderdb()
        b.get_object('riders_box').add(self.rdb.mkview(cat=True,
                                                  series=True,
                                                  refid=True,
                                                  ucicode=True,note=True))

        # select event page in notebook.
        b.get_object('meet_nb').set_current_page(1)

        # get event db -> loadconfig adds empty event if not already present
        self.edb = eventdb.eventdb([])

        # start timer
        glib.timeout_add_seconds(1, self.timeout)

def main(etype=None):
    """Run the road meet application."""
    configpath = None

    # expand configpath on cmd line to realpath _before_ doing chdir
    if len(sys.argv) > 2:
        print(u'usage: roadmeet [configdir]\n')
        sys.exit(1)
    elif len(sys.argv) == 2:
        rdir = sys.argv[1]
        if not os.path.isdir(rdir):
            rdir = os.path.dirname(rdir)
        configpath = os.path.realpath(rdir)

    metarace.init()
    if configpath is not None:
        os.chdir(configpath)
    app = roadmeet(configpath, etype)
    app.loadconfig()	# exception here ok
    app.window.show()
    app.start()
    try:
        metarace.mainloop()
    except:
        app.shutdown(u'Exception from main loop.')
        raise

if __name__ == '__main__':
    main()

