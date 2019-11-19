
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

"""Road Mass-start race module.

This module provides a class 'rms' which implements the race
interface and manages data, timing and scoreboard for generic
road race events:

 - Criterium
 - Kermesse
 - Road race

"""

import gtk
import glib
import gobject
import pango
import os
import logging
import threading
import bisect

import metarace
from metarace import tod
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import uiutil
from metarace import printing
from metarace import jsonconfig

# Model columns

# basic infos
COL_BIB = 0
COL_NAMESTR = 1
COL_SHORTNAME = 2
COL_CAT = 3
COL_COMMENT = 4
COL_INRACE = 5		# boolean in the race
COL_PLACE = 6		# Place assigned in result
COL_LAPS = 7		# Incremented if inrace and not finished

# timing infos
COL_RFTIME = 8		# one-off finish time by rfid
COL_CBUNCH = 9		# computed bunch time	-> derived from rftime
COL_MBUNCH = 10		# manual bunch time	-> manual overrive
COL_STOFT = 11		# start time 'offset' - only reported in result
COL_BONUS = 12
COL_PENALTY = 13
COL_PREPASS = 14
COL_RFSEEN = 15		# list of tods this rider 'seen' by rfid

# listview column nos
CATCOLUMN = 2
COMCOLUMN = 3
INCOLUMN = 4
LAPCOLUMN = 5
STARTCOLUMN = 6

# rider commands
RIDER_COMMANDS_ORD = [ 'add', 'del', 'que', 'dns', 'otl',
                       'wd', 'dnf', 'dsq', 'com', 'ret', 'man',
                       '', 'fin']	# then intermediates...
RIDER_COMMANDS = {'dns':'Did not start',
                   'otl':'Outside time limit',
                   'dnf':'Did not finish',
                   'wd':'Withdraw',
                   'dsq':'Disqualify',
                   'add':'Add starters',
                   'del':'Remove starters',
                   'que':'Query riders',
                   'fin':'Final places',
                   'com':'Add comment',
                   'ret':'Return to race',
                   'man':'Manual passing',
                   '':'',
                   }

RESERVED_SOURCES = ['fin',	# finished stage
                    'reg',	# registered to stage
                    'start']	# started stage
				# additional cat finishes added in loadconfig

DNFCODES = ['otl', 'hd', 'wd', 'dsq', 'dnf', 'dns']
GAPTHRESH = tod.tod('1.12')
MINPASSTIME = tod.tod('30')	# minimum pass interval if not configured

# timing keys
key_announce = 'F4'
key_armstart = 'F5'
key_armlap = 'F6'
key_placesto = 'F7'	# fill places to selected rider
key_appendplace = 'F8'	# append sepected rider to places
key_armfinish = 'F9'
key_raceover = 'F10'

# extended fn keys	(ctrl + key)
key_abort = 'F5'
key_clearfrom = 'F7'	# clear places on selected rider and all following
key_clearplace = 'F8'	# clear rider from place list
key_undo = 'Z'

# config version string
EVENT_ID = 'roadrace-2.0'

def key_bib(x):
    """Sort on bib field of aux row."""
    return strops.riderno_key(x[1])

def sort_bib(x, y):
    """Rider bib sorter."""
    return cmp(strops.riderno_key(x[1]),
               strops.riderno_key(y[1]))

def sort_tally(x, y):
    """Points tally sort using countback struct."""
    if x[0] == y[0]:
        return cmp(y[3], x[3])
    else:
        return cmp(y[0], x[0])

class rms(object):
    """Road race handler."""

    def hidecolumn(self, target, visible=False):
        tc = self.view.get_column(target)
        if tc:
            tc.set_visible(visible)

    def loadcats(self, cats=u''):
        self.cats = []	# clear old cat list
        catlist = cats.split()
        if u'AUTO' in catlist:	# ignore any others and re-load from rdb
            self.cats = self.meet.rdb.listcats()
            self.autocats = True
        else:
            self.autocats = False
            for cat in catlist:
                if cat != u'':
                    cat = cat.upper()
                    self.cats.append(cat)
        self.cats.append(u'')	# always include one empty cat
        self.log.debug(u'Result category list updated: ' + repr(self.cats))

    def loadconfig(self):
        """Load event config from disk."""
        self.riders.clear()
        self.resettimer()
        self.cats = []

        cr = jsonconfig.config({u'event':{u'start':u'',
                                        u'id':EVENT_ID,
                                        u'finish':u'',
                                        u'finished':u'No',
                                        u'places':u'',
                                        u'comment':[],
                                        u'hidecols':[],	# TODO: default hides
                                        u'categories':[],
                                        u'intermeds':[],
                                        u'allowspares': False,
                                        u'contests':[],
                                        u'tallys':[],
                                        u'lapstart':u'',
                                        u'dofastestlap':False,
                                        u'minlap':None,
                                        u'lapfin':u'',
                                        u'curlap':-1,
                                        u'passlabels': {},
                                        u'catonlap': {},
                                        u'gapthresh': None,
                                        u'preloop': None,
                                        u'totlaps':None,
                                        u'sprintlaps':[],
                                        u'passingsource':[],
                                        u'laptimes':[],
                                        u'nolaps':u'False',
                                        u'timelimit':None,
                                        u'startlist':u'',
                                        u'autoexport':False,
                                        u'resultcats':u'False'}})
        cr.add_section(u'event')
        cr.add_section(u'riders')
        # sections for commissaire awarded bonus/penalty
        cr.add_section(u'stagebonus')
        cr.add_section(u'stagepenalty')
        cr.add_section(u'tkpass')

        # check for config file
        try:
            with open(self.configpath, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading event config: ' + unicode(e))

        # load _result_ categories
        catlist = cr.get(u'event', u'categories')
        if u'AUTO' in catlist:	# ignore any others and re-load from rdb
            self.cats = self.meet.rdb.listcats()
            self.autocats = True
        else:
            self.autocats = False
            for cat in catlist:
                if cat != u'':
                    cat = cat.upper()
                    self.cats.append(cat)
        self.cats.append(u'')	# always include one empty cat

        # amend reserved sources with any cats
        if len(self.cats) > 1:
            for cat in self.cats:
                if cat:
                    srcid = cat.lower() + 'fin'
                    RESERVED_SOURCES.append(srcid)
                    self.catplaces[srcid] = cat

        # fake intermed for riders in break
        #self.intermeds.append(u'brk')
        #self.intermap[u'brk'] = {u'descr':u'Riders in break', u'places':u''}
        self.passlabels = cr.get(u'event', u'passlabels')
        self.catonlap = cr.get(u'event', u'catonlap')
        self.preloop = cr.get(u'event', u'preloop')
        self.passingsource = []
        for source in cr.get(u'event', u'passingsource'):
            self.passingsource.append(source.lower()) # force lower case

        # fetch time gap threshold of configured
        ngt = tod.str2tod(cr.get(u'event', u'gapthresh'))
        if ngt is not None:
            self.gapthresh = ngt
            if self.gapthresh != GAPTHRESH:
                self.log.warn(u'Set time gap threshold to: ' + self.gapthresh.rawtime(2))

        # restore intermediates
        for i in cr.get(u'event', u'intermeds'):
            if i in RESERVED_SOURCES:
                self.log.info(u'Ignoring reserved intermediate: ' + repr(i))
            else:
                crkey = u'intermed_' + i
                descr = u''
                places = u''
                km = None
                abbr = u''
                if cr.has_option(crkey, u'descr'):
                    descr = cr.get(crkey, u'descr')
                if cr.has_option(crkey, u'dist'):
                    km = strops.confopt_float(cr.get(crkey, u'dist'), None)
                if cr.has_option(crkey, u'abbr'):
                    abbr = cr.get(crkey, u'abbr')
                if cr.has_option(crkey, u'places'):
                    places = strops.reformat_placelist(
                                 cr.get(crkey, u'places'))
                if i not in self.intermeds:
                    self.log.debug(u'Adding intermediate: '
                                    + repr(i) + u':' + descr
                                    + u':' + places)
                    self.intermeds.append(i)
                    self.intermap[i] = {u'descr':descr,
                                        u'places':places,
                                        u'abbr':abbr,
                                        u'dist':km}
                else:
                    self.log.info(u'Ignoring duplicate intermediate: '
                                   + repr(i))

        # load contest meta data
        tallyset = set()
        for i in cr.get(u'event', u'contests'):
            if i not in self.contests:
                self.contests.append(i)
                self.contestmap[i] = {}
                crkey = u'contest_' + i
                tally = u''
                if cr.has_option(crkey, u'tally'):
                    tally = cr.get(crkey, u'tally')
                    if tally:
                        tallyset.add(tally)
                self.contestmap[i][u'tally'] = tally
                descr = i
                if cr.has_option(crkey, u'descr'):
                    descr = cr.get(crkey, u'descr')
                    if descr == u'':
                        descr = i
                self.contestmap[i][u'descr'] = descr
                labels = []
                if cr.has_option(crkey, u'labels'):
                    labels = cr.get(crkey, u'labels').split()
                self.contestmap[i][u'labels'] = labels
                source = i
                if cr.has_option(crkey, u'source'):
                    source = cr.get(crkey, u'source')
                    if source == u'':
                        source = i
                self.contestmap[i][u'source'] = source
                bonuses = []
                if cr.has_option(crkey, u'bonuses'):
                    for bstr in cr.get(crkey, u'bonuses').split():
                        bt = tod.str2tod(bstr)
                        if bt is None:
                            self.log.info(u'Invalid bonus ' + repr(bstr)
                              + u' in contest ' + repr(i))
                            bt = tod.ZERO
                        bonuses.append(bt)
                self.contestmap[i][u'bonuses'] = bonuses
                points = []
                if cr.has_option(crkey, u'points'):
                    pliststr = cr.get(crkey, u'points').strip()
                    if pliststr and tally == u'': # no tally for these points!
                        self.log.error(u'No tally for points in contest: '
                                        + repr(i))
                        tallyset.add(u'')	# add empty placeholder
                    for pstr in pliststr.split():
                        pt = 0
                        try:
                            pt = int(pstr)
                        except:
                            self.log.info(u'Invalid points ' + repr(pstr)
                              + u' in contest ' + repr(i))
                        points.append(pt)
                self.contestmap[i][u'points'] = points
                allsrc = False		# all riders in source get same pts
                if cr.has_option(crkey, u'all_source'):
                    allsrc = strops.confopt_bool(cr.get(crkey, u'all_source'))
                self.contestmap[i][u'all_source'] = allsrc
                self.contestmap[i][u'category'] = 0
                if cr.has_option(crkey, u'category'):	# for climbs
                    self.contestmap[i][u'category'] = strops.confopt_posint(
                                                    cr.get(crkey, u'category'))
            else:
                self.log.info(u'Ignoring duplicate contest: ' + repr(i))

            # check for invalid allsrc
            if self.contestmap[i][u'all_source']:
                if (len(self.contestmap[i][u'points']) > 1 
                    or len(self.contestmap[i][u'bonuses']) > 1):
                    self.log.info(u'Ignoring extra points/bonus for '
                                  + u'all source contest ' + repr(i))
     
        # load points tally meta data
        tallylist = cr.get('event', 'tallys')
        # append any 'missing' tallys from points data errors
        for i in tallyset:
            if i not in tallylist:
                self.log.debug(u'Adding missing tally to config: ' + repr(i))
                tallylist.append(i)
        # then scan for meta data
        for i in tallylist:
            if i not in self.tallys:
                self.tallys.append(i)
                self.tallymap[i] = {}
                self.points[i] = {}	 # redundant, but ok
                self.pointscb[i] = {}
                crkey = u'tally_' + i
                descr = u''
                if cr.has_option(crkey, u'descr'):
                    descr = cr.get(crkey, u'descr')
                self.tallymap[i][u'descr'] = descr
                keepdnf = False
                if cr.has_option(crkey, u'keepdnf'):
                    keepdnf = strops.confopt_bool(cr.get(crkey, u'keepdnf'))
                self.tallymap[i][u'keepdnf'] = keepdnf
            else:
                self.log.info(u'Ignoring duplicate points tally: ' + repr(i))


        starters = cr.get(u'event', u'startlist').split()
        self.allowspares = strops.confopt_bool(cr.get(u'event', u'allowspares'))
        dolaps = not strops.confopt_bool(cr.get(u'event', u'nolaps'))
        for r in starters:
            self.addrider(r)
            if cr.has_option(u'riders', r):
                nr = self.getrider(r)
                # bib = comment,in,laps,rftod,mbunch,rfseen...
                ril = cr.get(u'riders', r)	# rider op is vec
                lr = len(ril)
                if lr > 0:
                    nr[COL_COMMENT] = ril[0]
                if lr > 1:
                    nr[COL_INRACE] = strops.confopt_bool(ril[1])
                if lr > 2:
                    nr[COL_LAPS] = strops.confopt_posint(ril[2])
                if lr > 3:
                    nr[COL_RFTIME] = tod.str2tod(ril[3])
                if lr > 4:
                    nr[COL_MBUNCH] = tod.str2tod(ril[4])
                if lr > 5:
                    nr[COL_STOFT] = tod.str2tod(ril[5])
                if dolaps and lr > 6:
                    for i in range(6, lr):
                        laptod = tod.str2tod(ril[i])
                        if laptod is not None:
                            nr[COL_RFSEEN].append(laptod)
            # record any extra bonus/penalty to rider model
            if cr.has_option(u'stagebonus', r):
                nr[COL_BONUS] = tod.str2tod(cr.get(u'stagebonus', r))
            if cr.has_option(u'stagepenalty', r):
                nr[COL_PENALTY] = tod.str2tod(cr.get(u'stagepenalty', r))
            if cr.has_option(u'prepass', r):
                nr[COL_PREPASS] = tod.str2tod(cr.get(u'prepass', r))

        self.laptimes = []
        ltin = cr.get(u'event', u'laptimes')
        for ts in ltin:
            nlt = tod.str2tod(ts)
            if nlt is not None:
                self.laptimes.append(nlt)

        self.set_start(cr.get(u'event', u'start'))
        self.set_finish(cr.get(u'event', u'finish'))
        self.lapstart = tod.str2tod(cr.get(u'event', u'lapstart'))
        self.lapfin = tod.str2tod(cr.get(u'event', u'lapstart'))
        self.minlap = tod.str2tod(cr.get(u'event', u'minlap'))
        self.curlap = cr.get(u'event', u'curlap')
        self.totlaps = cr.get(u'event', u'totlaps')
        self.sprintlaps = cr.get(u'event', u'sprintlaps')
        self.timelimit = cr.get(u'event',u'timelimit')	# save as str
        self.places = strops.reformat_placelist(cr.get(u'event', u'places'))
        self.comment = cr.get(u'event', u'comment')	# comments are vec
        self.dofastestlap = strops.confopt_bool(cr.get(u'event',
                                                       u'dofastestlap'))
        self.autoexport = strops.confopt_bool(cr.get(u'event',
                                                       u'autoexport'))
        if strops.confopt_bool(cr.get(u'event', u'finished')):
            self.set_finished()
        self.recalculate()

        for col in cr.get(u'event', u'hidecols'):
            target = strops.confopt_posint(col)
            if target is not None:
                self.hidecolumn(target)
        #if self.totlaps is None:	# NO should be config'ble
                #self.hidecolumn(LAPCOLUMN)

        self.load_cat_starts()

        if self.curlap is not None and self.curlap >= 0:
            self.lapentry.set_text(unicode(self.curlap))
        else:
            self.lapentry.set_text(u'')
        if self.totlaps is not None:
            self.totlapentry.set_text(unicode(self.totlaps))

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get(u'event', u'id')
        if eid and eid != EVENT_ID:
            self.log.error(u'Event configuration mismatch: '
                           + repr(eid) + u' != ' + repr(EVENT_ID))
            self.readonly = True

    def get_ridercmdorder(self):
        """Return rider command list order."""
        ret = RIDER_COMMANDS_ORD[0:]
        for i in self.intermeds:
            ret.append(i)
        return ret

    def get_ridercmds(self):
        """Return a dict of rider bib commands for container ui."""
        ret = {}
        for k in RIDER_COMMANDS:
            ret[k] = RIDER_COMMANDS[k]
        for k in self.intermap:
            descr = k
            if self.intermap[k]['descr']:
                descr = k + u' : ' + self.intermap[k]['descr']
            ret[k] = descr
        return ret

    def get_startlist(self):
        """Return a list of all rider numbers 'registered' to event."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ' '.join(ret)

    def get_starters(self):
        """Return a list of riders that 'started' the race."""
        ret = []
        for r in self.riders:
            if r[COL_COMMENT] != 'dns' or r[COL_INRACE]:
                ret.append(r[COL_BIB])
        return ' '.join(ret)

    def checkpoint_model(self):
        """Write the current rider model to an undo buffer."""
        self.undomod.clear()
        self.placeundo = self.places
        for r in self.riders:
            self.undomod.append(r)
        self.canundo = True

    def undo_riders(self):
        """Roll back rider model to last checkpoint."""
        if self.canundo:
            self.riders.clear()
            for r in self.undomod:
                self.riders.append(r)
            self.places = self.placeundo
            self.canundo = False
          
    def get_catlist(self):
        """Return the ordered list of categories."""
        rvec = []
        for cat in self.cats:
            if cat != '':
                rvec.append(cat)
        return rvec

    def ridercat(self, cat):
        """Return a category from the result for the riders cat."""
        ret = ''        # default is the 'None' category - uncategorised
        checka = cat.upper()
        if checka in self.cats:
            ret = checka
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
        if self.lapstart is not None:
            cw.set(u'event', u'lapstart', self.lapstart.rawtime())
        if self.lapfin is not None:
            cw.set(u'event', u'lapfin', self.lapfin.rawtime())
        if self.minlap is not None:
            cw.set(u'event', u'minlap', self.minlap.rawtime())
        cw.set(u'event', u'gapthresh', self.gapthresh.rawtime())
        cw.set(u'event', u'finished', self.timerstat == 'finished')
        cw.set(u'event', u'places', self.places)
        cw.set(u'event', u'curlap', self.curlap)
        cw.set(u'event', u'totlaps', self.totlaps)
        cw.set(u'event', u'sprintlaps', self.sprintlaps)
        cw.set(u'event', u'allowspares', self.allowspares)
        cw.set(u'event', u'timelimit', self.timelimit)
        cw.set(u'event', u'passlabels', self.passlabels)
        cw.set(u'event', u'catonlap', self.catonlap)
        cw.set(u'event', u'preloop', self.preloop)
        cw.set(u'event', u'dofastestlap', self.dofastestlap)
        cw.set(u'event', u'autoexport', self.autoexport)
        cw.set(u'event', u'passingsource', self.passingsource)
        ltout = []
        for lt in self.laptimes:
            ltout.append(lt.rawtime())
        cw.set(u'event', u'laptimes', ltout)

        # save intermediate data
        opinters = []
        for i in self.intermeds:
            if i != u'brk':
                crkey = u'intermed_' + i
                cw.add_section(crkey)
                cw.set(crkey, u'descr', self.intermap[i][u'descr'])
                cw.set(crkey, u'places', self.intermap[i][u'places'])
                if u'dist' in self.intermap[i]:
                    cw.set(crkey, u'dist', self.intermap[i][u'dist'])
                if u'abbr' in self.intermap[i]:
                    cw.set(crkey, u'abbr', self.intermap[i][u'abbr'])
                opinters.append(i)
        cw.set(u'event', u'intermeds', opinters)

        # save contest meta data
        cw.set(u'event', u'contests', self.contests)
        for i in self.contests:
            crkey = u'contest_' + i
            cw.add_section(crkey)
            cw.set(crkey, u'tally', self.contestmap[i][u'tally'])
            cw.set(crkey, u'source', self.contestmap[i][u'source'])
            cw.set(crkey, u'all_source', self.contestmap[i][u'all_source'])
            if u'category' in self.contestmap[i]:
                cw.set(crkey, u'category', self.contestmap[i][u'category'])
            blist = []
            for b in self.contestmap[i][u'bonuses']:
                blist.append(b.rawtime(0))
            cw.set(crkey, u'bonuses', u' '.join(blist))
            plist = []
            for p in self.contestmap[i][u'points']:
                plist.append(str(p))
            cw.set(crkey, u'points', u' '.join(plist))
        # save tally meta data
        cw.set(u'event', u'tallys', self.tallys)
        for i in self.tallys:
            crkey = u'tally_' + i
            cw.add_section(crkey)
            cw.set(crkey, u'descr', self.tallymap[i][u'descr'])
            cw.set(crkey, u'keepdnf', self.tallymap[i][u'keepdnf'])

        # save riders
        cw.set(u'event', u'startlist', self.get_startlist())    
        if self.autocats:
            cw.set(u'event', u'categories', [u'AUTO'])
        else:
            cw.set(u'event', u'categories', self.get_catlist())
        cw.set(u'event', u'comment', self.comment)
        hides = []
        idx = 0
        for c in self.view.get_columns():
            if not c.get_visible():
                hides.append(str(idx))
            idx += 1
        if len(hides) > 0:
            cw.set(u'event', u'hidecols', hides)
            
        cw.add_section(u'riders')
        # sections for commissaire awarded bonus/penalty
        cw.add_section(u'stagebonus')
        cw.add_section(u'stagepenalty')
        cw.add_section(u'tkpass')
        for r in self.riders:
            rt = u''
            if r[COL_RFTIME] is not None:
                rt = r[COL_RFTIME].rawtime()	# Don't truncate this!
            mb = u''
            if r[COL_MBUNCH] is not None:
                mb = r[COL_MBUNCH].rawtime(0)	# But bunch is to whole sec
            sto = u''
            if r[COL_STOFT] is not None:
                sto = r[COL_STOFT].rawtime()
            # bib = comment,in,laps,rftod,mbunch,rfseen...
            bib = r[COL_BIB].decode('utf-8')
            slice = [r[COL_COMMENT].decode('utf-8'), r[COL_INRACE],
                     r[COL_LAPS], rt, mb, sto]
            for t in r[COL_RFSEEN]:
                if t is not None:
                    slice.append(t.rawtime())	# retain 'precision' here too
            cw.set(u'riders', bib, slice)
            if r[COL_BONUS] is not None:
                cw.set(u'stagebonus', bib, r[COL_BONUS].rawtime())
            if r[COL_PENALTY] is not None:
                cw.set(u'stagepenalty', bib, r[COL_PENALTY].rawtime())
            if r[COL_PREPASS] is not None:
                cw.set(u'prepass', bib, r[COL_PREPASS].rawtime())
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

    def title_close_clicked_cb(self, button, entry=None):
        """Close and save the race."""
        self.meet.close_event()

    def set_titlestr(self, titlestr=None):
        """Update the title string label."""
        if titlestr is None or titlestr == '':
            titlestr = 'Mass Start Road Race'
        self.title_namestr.set_text(titlestr)

    def destroy(self):
        """Emit destroy signal to race handler."""
        if self.context_menu is not None:
            self.context_menu.destroy()
        self.frame.destroy()

    def get_results(self):
        """Extract results in flat mode (not yet implemented)."""
        return []

    def points_report(self):
        """Return the points tally report."""
        ret = []
        aux = []
        cnt = 0
        for tally in self.tallys:
            sec = printing.section('points')
            sec.heading = tally.upper() + u' ' + self.tallymap[tally]['descr']
            sec.units = 'pt'
            tallytot = 0
            aux = []
            for bib in self.points[tally]:
                r = self.getrider(bib)
                tallytot += self.points[tally][bib]
                aux.append([self.points[tally][bib], strops.riderno_key(bib),
                           [None, r[COL_BIB], r[COL_NAMESTR],
                           strops.truncpad(str(self.pointscb[tally][bib]), 10,
                                                   elipsis=False),
                             None, str(self.points[tally][bib])],
                           self.pointscb[tally][bib]
                        ])
            aux.sort(sort_tally)
            for r in aux:
                sec.lines.append(r[2])
            sec.lines.append([None, None, None])
            sec.lines.append([None, None, 'Total Points: ' + str(tallytot)])
            ret.append(sec)
        
        if len(self.bonuses) > 0:
            sec = printing.section('bonus')
            sec.heading = 'Stage Bonuses'
            sec.units = 'sec'
            aux = []
            for bib in self.bonuses:
                r = self.getrider(bib)
                aux.append([self.bonuses[bib], strops.riderno_key(bib),
                       [None,r[COL_BIB],r[COL_NAMESTR],None,None,
                        str(int(self.bonuses[bib].truncate(0).timeval))],
                        0, 0, 0])
            aux.sort(sort_tally)
            for r in aux:
                sec.lines.append(r[2])
            ret.append(sec)            
        return ret

    def reorder_startlist(self):
        """Reorder riders for a startlist."""
        self.calcset = False
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append([cnt, r[COL_BIB]])
            cnt += 1
        if len(aux) > 1:
            aux.sort(key=key_bib)
            self.riders.reorder([a[0] for a in aux])
        return cnt

    def signon_report(self):
        """Return a signon report."""
        ret = []
        self.reorder_startlist()
        if len(self.cats) > 1:
            for c in self.cats:
                c = self.ridercat(c)
                self.log.info(u'Preparing signon for cat: ' + repr(c))
                if c:
                    sec = printing.signon_list('signon')
                    sec.heading = c
                    dbr = self.meet.rdb.getrider(c,u'cat')
                    if dbr is not None:
                        sec.heading = self.meet.rdb.getvalue(dbr,
                                          riderdb.COL_FIRST)
                        sec.subheading = self.meet.rdb.getvalue(dbr,
                                          riderdb.COL_LAST)
                    for r in self.riders:
                      rcat = r[COL_CAT].decode('utf-8').upper()
                      if rcat == c:
                        cmt = None
                        if not r[COL_INRACE]:
                            cmt = r[COL_COMMENT].decode('utf-8')
                        sec.lines.append([cmt,r[COL_BIB].decode('utf-8'),
                                               r[COL_NAMESTR].decode('utf-8')])
                        
                    ret.append(sec)
                    ret.append(printing.pagebreak(threshold=0.1))
        else:
            sec = printing.signon_list('signon')
            for r in self.riders:
                cmt = None
                if not r[COL_INRACE]:
                    cmt = r[COL_COMMENT].decode('utf-8')
                sec.lines.append([cmt,r[COL_BIB].decode('utf-8'),
                                       r[COL_NAMESTR].decode('utf-8')])
            ret.append(sec)
        return ret

    def startlist_report(self):
        """Return a startlist report."""
        ret = []
        self.reorder_startlist()
        if len(self.cats) > 1:
            for c in self.cats:
                self.log.info(u'Preparing startlist for cat: ' + repr(c))
                if c:
                    ret.extend(self.startlist_report_gen(c))
                    # ret.append(printing.pagebreak(0.85))
                    ret.append(printing.pagebreak(0.02))
        else:
            ret = self.startlist_report_gen()
        return ret

    def load_cat_starts(self):
        self.catstarts = {}
        self.catlaps = {}
        for c in self.cats:
            cs = None	# default start offset is None
            ls = None
            dbr = self.meet.rdb.getrider(c,u'cat')
            if dbr is not None:
                ct = tod.str2tod(self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE))
                if ct is not None:
                    cs = ct
                lt = strops.confopt_posint(self.meet.rdb.getvalue(dbr, riderdb.COL_CAT))
                if lt:
                    ls = lt
            self.catstarts[c] = cs
            self.catlaps[c] = ls
    
    def startlist_report_gen(self, cat=None):
        catname = u''
        subhead = u''
        if cat is not None:
            dbr = self.meet.rdb.getrider(cat,u'cat')
            if dbr is not None:
                catname = self.meet.rdb.getvalue(dbr,
                                          riderdb.COL_FIRST)
                subhead = self.meet.rdb.getvalue(dbr,
                                          riderdb.COL_LAST)
        else:
            cat = u''	# match all riders

        catcache = {u'':None}
        if cat == u'':
            for c in self.meet.rdb.listcats(self.series):
                if c != u'':
                    catnm = c
                    dbr = self.meet.rdb.getrider(c,u'cat')
                    if dbr is not None:
                        catnm = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_FIRST)
                    catcache[c] = catnm
        ret = []
        sec = None
        if len(self.riders) > 36:
            sec = printing.twocol_startlist('startlist')
        else:
            sec = printing.section('startlist')
        sec.heading = 'Startlist'
        if catname:
            sec.heading += u': ' + catname
            sec.subheading = subhead
        rcnt = 0
        cat = self.ridercat(cat)
        for r in self.riders:
            rcat = r[COL_CAT].decode('utf-8').upper()
            if cat == u'' or rcat == cat:
                ucicode = None
                name = r[COL_NAMESTR].decode('utf-8')
                dbr = self.meet.rdb.getrider(r[COL_BIB].decode('utf-8'),
                                             self.series)
                if dbr is not None:
                    ucicode = self.meet.rdb.getvalue(dbr,
                                             riderdb.COL_UCICODE)
                    # suppress club/team for twocol
                    name = strops.resname(
                             self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST).decode('utf-8'),
                             self.meet.rdb.getvalue(dbr, riderdb.COL_LAST).decode('utf-8'),
                             self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB).decode('utf-8'))
                    # hack for W23 flagged riders in national champs
                    #if ucicode and rcat:
                        #name += u' ' + rcat
                if not ucicode and cat == u'':
                    if rcat in catcache:
                        ucicode = catcache[rcat] # try and fill cat
                comment = u''
                if not r[COL_INRACE]:
                    cmt = r[COL_COMMENT].decode('utf-8')
                    if cmt == u'dns':
                        comment = cmt
                riderno = r[COL_BIB].decode('utf-8').translate(strops.INTEGER_UTRANS)
                sec.lines.append([comment,
                                  riderno,
                                  name,
                                  ucicode])
                ## and look up pilots?
                if cat in [u'MB', u'WB']:
                    # lookup pilot 
                    dbr = self.meet.rdb.getrider(r[COL_BIB],u'pilot')
                    if dbr is not None:
                        puci = self.meet.rdb.getvalue(dbr,
                                                      riderdb.COL_UCICODE)
                        pnam = strops.listname(
                           self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                           self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                           self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB))
                        sec.lines.append([u'', u'', pnam, puci])

                rcnt += 1
        #if rcnt > 120:	# Hack for multiple pages?
            #nsec = printing.section('startlist')
            #nsec.heading = 'Startlist'
            #if catname:
                #nsec.heading += u': ' + catname
                #nsec.subheading = subhead
            #nsec.lines = sec.lines
            #sec = nsec
        if rcnt > 1:
            sec.footer = 'Total riders: ' + unicode(rcnt)
        ret.append(sec)
        #if rcnt > 1:
            #sec = printing.bullet_text('startcount')
            #sec.lines.append([u'', 'Total riders: ' + unicode(rcnt)])
            #ret.append(sec)
            
        return ret

    def camera_report(self):
        """Return the judges (camera) report."""
        ret = []
        self.recalculate()	# fill places and bunch info
        pthresh = self.meet.timer.photothresh()
        totcount = 0
        dnscount = 0
        dnfcount = 0
        fincount = 0
        lcomment = ''
        insertgap = True
        if self.timerstat != 'idle':
            sec = printing.judgerep('judging')
            #sec = printing.section('judging')
            sec.heading = u'Judges Report'
            sec.colheader = ['','no','rider','lap','finish',
                                'rftime','passings']
            if self.start is not None:
                sec.start = self.start
            if self.finish is not None:
                sec.finish = self.maxfinish+tod.tod(u'0.1')
                #sec.finish = self.finish
            sec.laptimes = self.laptimes
            first = True
            ft = None
            lt = None
            lrf = None
            lplaced = None
            ltimed = None
            for r in self.riders:
                totcount += 1
                marker = ' '
                es = ''
                bs = ''
                pset = False
                placed = False
                timed = False
                catstart = None
                photo = False
                if r[COL_INRACE]:
                    rcat = self.get_ridercat(r[COL_BIB])
                    if rcat in self.catstarts:
                        catstart = self.catstarts[rcat]
                    comment = unicode(totcount)
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if bt is not None:
                        timed = True
                        fincount += 1
                        if r[COL_PLACE] != '':
                           comment = r[COL_PLACE] + '.'
                           placed = True
                           pset = True

                        # format 'elapsed' rftime
                        if r[COL_RFTIME] is not None:
                            if not pset and lrf is not None:
                                if r[COL_RFTIME]-lrf < pthresh:
                                    photo = True
                                    if not sec.lines[-1][7]:	# not placed
                                        #sec.lines[-1][0] = '[pf]__'
                                        sec.lines[-1][8] = True
                            if self.start is not None:
                                es =  (r[COL_RFTIME]-self.start).rawtime(2)
                            else:
                                es = r[COL_RFTIME].rawtime(2)
                            lrf = r[COL_RFTIME]
                        else:
                            lrf = None

                        # format 'finish' time
                        if ft is None:
                            ft = bt
                            bs = ft.rawtime(0)
                        else:
                            if bt > lt:
                                # New bunch
                                sec.lines.append([None, None, None])
                                bs = "+" + (bt - ft).rawtime(0)
                            else:
                                # Same time
                                pass
                        lt = bt
                        # sep placed and unplaced
                        insertgap = False 
                        if lplaced and placed != lplaced:
                            sec.lines.append([None, None, None])
                            sec.lines.append([None, None,
                                              u'Riders not placed on camera.'])
                            insertgap = True
                        lplaced = placed
                    else:
                        if r[COL_COMMENT].strip() != '':
                            comment = r[COL_COMMENT].strip()
                        else:
                            comment = u'____'

                    # sep timed and untimed
                    if not insertgap and ltimed and ltimed != timed:
                        sec.lines.append([None, None, None])
                        sec.lines.append([None, None,
                                          u'Riders not seen at finish.'])
                        insertgap = True
                    ltimed = timed
                    sec.lines.append([comment, r[riderdb.COL_BIB],
                                     r[COL_NAMESTR], str(r[COL_LAPS]),
                                     bs, es, r[COL_RFSEEN], placed,
                                     photo, catstart])
                else:
                    comment = r[COL_COMMENT]
                    if comment == '':
                        comment = 'dnf'
                    if comment != lcomment:
                        sec.lines.append([None, None, None])
                    lcomment = comment
                    if comment == 'dns':
                        dnscount += 1
                    else:
                        dnfcount += 1
                    sec.lines.append([comment, r[riderdb.COL_BIB],
                                     r[COL_NAMESTR], str(r[COL_LAPS]),
                                     None, None, r[COL_RFSEEN], True, False])
                first = False

            ret.append(sec)
            sec = printing.section('judgesummary')
            sec.lines.append([None,None,'Total riders: ' + str(totcount)])
            sec.lines.append([None,None,'Did not start: ' + str(dnscount)])
            sec.lines.append([None,None,'Did not finish: ' + str(dnfcount)])
            sec.lines.append([None,None,'Finishers: ' + str(fincount)])
            residual = totcount - (fincount + dnfcount + dnscount)
            if residual > 0:
                sec.lines.append([None,None,
                                  'Unaccounted for: ' + str(residual)])
            if len(sec.lines) > 0:
                ret.append(sec)
        else:
            # nothing to report...
            pass
        return ret

    def catresult_report(self):
        """Return a categorised race result report."""
        self.log.info(u'Result report in category path')
        self.recalculate()
        ret = []
        for cat in self.cats:
            if not cat:
                continue	# ignore empty and None cat
            ret.extend(self.single_catresult(cat))

        # show all intermediates here
        for i in self.intermeds:
            im = self.intermap[i]
            if im[u'places']:
                ret.extend(self.int_report(i))
                
        if len(self.comment) > 0:
            s = printing.bullet_text('comms')
            s.heading = u'Decisions of the commissaires panel'
            for comment in self.comment:
                s.lines.append([None, comment])
            ret.append(s)
        return ret

    def single_catresult(self, cat):
        ret = []
        catname = cat	# fallback emergency
        subhead = u''
        distance = self.meet.get_distance()
        dbr = self.meet.rdb.getrider(cat,u'cat')
        minlap = tod.tod(u'30.0')
        if self.minlap is not None:
            minlap = self.minlap
        doflap = self.dofastestlap
        if self.start is None:
            doflap = False	# don't do laps unless start is set
        flap = None
        fno = None
        fcnt = None
        if dbr is not None:
            catname = self.meet.rdb.getvalue(dbr,
                                      riderdb.COL_FIRST) # already decode
            subhead = self.meet.rdb.getvalue(dbr,
                                      riderdb.COL_LAST)
            dist = self.meet.rdb.getvalue(dbr,
                                      riderdb.COL_REFID)
            try:
                distance = float(dist)
            except:
                self.log.warn(u'Invalid distance: ' + repr(dist)
                               + u' for cat ' + repr(cat))
        sec = printing.section(cat)

        wt = None
        lt = None
        first = True
        lcomment = u''
        lp = None
        lsrc = None
        rcnt = 0
        plcnt = 1
        totcount = 0
        dnscount = 0
        dnfcount = 0
        hdcount = 0
        fincount = 0
        for r in self.riders:
            rcat = self.ridercat(r[COL_CAT].decode('utf-8'))
            if cat == rcat:	# rider in cat
                totcount += 1
                sof = None	# all riders have a start time offset
                if r[COL_STOFT] is not None:
                    sof = r[COL_STOFT]
                elif rcat in self.catstarts:
                    sof = self.catstarts[rcat]
                bstr = r[COL_BIB].decode('utf-8')
                nstr = r[COL_NAMESTR].decode('utf-8')
                pstr = u''
                tstr = u''	# tstr not used in catresult
                dstr = u''	# time/gap
                cstr = u''	# todo? check for ucicode here !
                dbr = self.meet.rdb.getrider(bstr, self.series)
                if dbr is not None:
                    cstr = self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE)
                placed = False		# placed at finish
                timed = False		# timed at finish
                comment = None
                if r[COL_INRACE]:
                    psrc = r[COL_PLACE].decode('utf-8')
                    if psrc != u'':
                        placed = True
                        if lsrc != psrc:	# previous total place differs
                            lp = unicode(plcnt)
                        else:
                            pass		# dead heat in cat
                        lsrc = psrc
                    else:
                        lp = u''
                    plcnt += 1
                    pstr = u''
                    if lp is not None and lp != u'':
                        pstr = lp + u'.'
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if bt is not None:
                        fincount += 1 # for accounting, use bunch time
                        timed = True
                        # compute elapsed
                        et = bt
                        if sof is not None:
                            # apply a start offset
                            et = bt - sof
                        if wt is None:	# first finish time
                            wt = et
                        if not first:
                            dstr = u'+' + (et - wt).rawtime(0)
                        else:
                            dstr = wt.rawtime(0)
                        first = False
                    lt = bt
                else:
                    # Non-finishers dns, dnf. hd, dsq
                    placed = True	# for purpose of listing
                    comment = r[COL_COMMENT].decode('utf-8')
                    if comment == u'':
                        comment = u'dnf'
                    if comment != lcomment:
                        sec.lines.append([None, None, None])# new bunch
                    lcomment = comment
                    # account for special cases
                    if comment == u'dns':
                        dnscount += 1
                    elif comment == u'hd' or comment == u'otl':
                        # otl special case: also show down time if possible
                        bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                        if bt is not None:
                            if not first and wt is not None:
                                et = bt
                                sof = None
                                if r[COL_STOFT] is not None:
                                    sof = r[COL_STOFT]
                                elif rcat in self.catstarts:
                                    sof = self.catstarts[rcat]
                                if sof is not None:
                                    # apply a start offset
                                    et = bt - sof
                                dstr = u'+' + (et - wt).rawtime(0)
                        hdcount += 1
                    else:
                        dnfcount += 1
                    pstr = comment
                if placed or timed:
                    sec.lines.append([pstr, bstr, nstr, cstr, tstr, dstr])
                    ## and look up pilots?
                    if cat in [u'MB', u'WB']:
                        # lookup pilot 
                        dbr = self.meet.rdb.getrider(r[COL_BIB],u'pilot')
                        if dbr is not None:
                            puci = self.meet.rdb.getvalue(dbr,
                                                          riderdb.COL_UCICODE)
                            pnam = strops.listname(
                               self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                               self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                               self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB))
                            sec.lines.append([u'', u'pilot', pnam, puci, u'', u''])
                if doflap and comment != u'dns':
                    if len(r[COL_RFSEEN]) > 0:
                        # only consider laps between stime and ftime
                        stime = self.start + sof
                        ftime = tod.tod(u'now')
                        if r[COL_RFTIME] is not None:
                            ftime = r[COL_RFTIME]
                        ls = stime
                        lt = None
                        lc = 0
                        self.log.debug(u'looking for laps on ' + repr(r[COL_RFSEEN]))
                        self.log.debug(u'stime : ' + str(ls) + u' , ftime = ' + str(ftime))
 
                        for p in r[COL_RFSEEN]:
                            if ls >= ftime:
                                break	# lap starts after end of region
                            if p < ls:
                                continue # passing before start of region
                            else:
                                lt = p - ls
                                if lt > minlap:
                                    lc += 1	# consider this a legit lap
                                    if flap is None or lt < flap: # new fastest
                                        flap = lt
                                        fno = r[COL_BIB]
                                        fcnt = lc
                                else:
                                    pass
                                    # short lap
                                ls = p
                rcnt += 1
            else:
                # not in this category.
                pass
        if self.timerstat == 'finished':
            sec.heading = u'Result'
        elif self.timerstat in ['running', 'ready', 'armfinish']:
            sec.heading = u'Race in Progress'
        else:
            sec.heading = u'Provisional Result'
        ret.append(sec)
        rsec = sec
        # Race metadata / UCI comments
        sec = printing.bullet_text('uci'+cat)
        if wt is not None:
            #sec.lines.append([None, u"Winner's time: " + wt.rawtime(0)])
            if distance is not None:
                sec.lines.append([None, u'Average speed of the winner: '
                                    + wt.speedstr(1000.0*distance)])
        if doflap and fno is not None:
            ftr = self.getrider(fno)
            fts = u''
            if ftr is not None:
                #ftcat = ftr[COL_CAT].decode('utf-8')
                #if ftcat.upper() in catcache:
                    #ftcat = catcache[ftcat.upper()]
                fts = ftr[COL_SHORTNAME].decode('utf-8')
            ftstr = flap.rawtime(0)
            if flap < tod.tod(60):
                ftstr += u' sec'
            fmsg = (u'Fastest lap: ' + fno + u' ' + fts + 
                    u' ' + ftstr + u' on lap ' + unicode(fcnt))
            sec.lines.append([None, fmsg])

        sec.lines.append([None,
                          u'Number of starters: '
                          + unicode(totcount-dnscount)])
        if hdcount > 0:
            sec.lines.append([None,
                          u'Riders finishing out of time limits: '
                          + unicode(hdcount)])
        if dnfcount > 0:
            sec.lines.append([None,
                          u'Riders abandoning the race: '
                          + unicode(dnfcount)])
        residual = totcount - (fincount + dnfcount + dnscount + hdcount)
        if residual > 0:
            self.log.info(cat + u' unaccounted for: ' + unicode(residual))
        else:	 # everyone accounted for, change stat if prov
            if self.timerstat in ['running', 'ready', 'armfinish']:
                rsec.heading = u'Provisional Result'
        ret.append(sec)

        # finish report title manipulation
        if catname:
            rsec.heading += u': ' + catname
            rsec.subheading = subhead
        ret.append(printing.pagebreak())
        return ret

    def result_report(self):
        """Return a race result report."""

        # check if a categorised report is required
        # if self.event[u'type'] != u'rhcp' and len(self.cats) > 1:
        if len(self.cats) > 1:
            return self.catresult_report()

        self.log.info(u'Result report in uncat/handicap path')
        self.recalculate()
        ret = []
        wt = None
        we = None
        dofastest = False	# ftime for handicap races
        fastest = None
        vfastest = None
        curelap = None
        if self.start is not None:	# virtual bunch time
            curelap = (tod.tod('now') - self.start).truncate(0)
        fastestbib = None
        totcount = 0
        dnscount = 0
        dnfcount = 0
        hdcount = 0
        fincount = 0
        lcomment = ''
        gapcount = 0
        catcache = {u'':None}
        for c in self.meet.rdb.listcats(self.series):
            if c != u'':
                catnm = c
                dbr = self.meet.rdb.getrider(c,u'cat')
                if dbr is not None:
                    catnm = self.meet.rdb.getvalue(dbr,
                                     riderdb.COL_FIRST)
                catcache[c] = catnm
        lt = None
        if self.timerstat != 'idle':
            sec = printing.section('result')
            if self.event[u'type'] == u'rhcp':
                sec.colheader = [None, None, None, None,
                                 u'Elapsed', u'Time/Gap']
            if self.racestat == u'final':
                sec.heading = u'Result'
            elif self.racestat == u'provisional':
                sec.heading = u'Provisional Result'
            else:
                sec.heading = u'Race In Progress'
            
            first = True
            for r in self.riders:
                totcount += 1
                bstr = r[COL_BIB].decode('utf-8')	# 'bib'
                nstr = r[COL_NAMESTR].decode('utf-8')	# 'name'
                cstr = r[COL_CAT].decode('utf-8')	# 'cat'
                rcat = self.ridercat(cstr)
                if cstr.upper() in catcache:
                    cstr = catcache[cstr.upper()]
                ## UCI!! REMOVE ME
                dbr = self.meet.rdb.getrider(bstr, self.series)
                if dbr is not None:
                    cstr = self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE)
                pstr = u''				# 'place'
                tstr = u''				# 'elap' (hcp only)
                dstr = u''				# 'time/gap'
                placed = False				# placed at finish
                timed = False				# timed at finish
                if r[COL_INRACE]:
                    psrc = r[COL_PLACE].decode('utf-8')
                    if psrc != u'':
                        pstr = psrc + u'.'
                        placed = True
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if bt is not None:
                        timed = True
                        fincount += 1	# for accounting, use bunch time
                        if wt is None:	# first finish time
                            wt = bt
                            first = False
                            # for uncat/hcap first time is always race time
                            dstr = wt.rawtime(0)
                        else:
                            # for uncat/handicap, time gap is always
                            # down on winner's uncorrected time
                            dstr = '+' + (bt - wt).rawtime(0)

                        # compute elapsed, or corrected time
                        if self.event[u'type'] == u'rhcp':
                            # always show elap for hcp, even if no stof*
                            tstr = bt.rawtime(0)
                        et = bt
                        sof = None
                        if r[COL_STOFT] is not None:	# apply a start offset
                            sof = r[COL_STOFT]
                        elif rcat in self.catstarts:
                            sof = self.catstarts[rcat]
                        if sof is not None:
                            dofastest = True	# will need to report!
                            et = bt - sof
                            # *but, adjust tstr if a start offset is present
                            tstr = et.rawtime(0)
                            if we is None:
                                we = et
                        if fastest is None or et < fastest:
                            fastest = et
                            fastestbib = r[COL_BIB]
                    else:	# check virtual finish time
                        sof = None
                        if r[COL_STOFT] is not None:
                            sof = r[COL_STOFT]
                        elif rcat in self.catstarts and self.catstarts[rcat] != tod.ZERO:
                            sof = self.catstarts[rcat]
                        if sof is not None:
                            vt = curelap - sof
                            if vfastest is None or vt < vfastest:
                                vfastest = vt
                    lt = bt
                else:
                    # Non-finishers dns, dnf. hd, dsq
                    placed = True	# for purpose of listing
                    comment = r[COL_COMMENT].decode('utf-8')
                    if comment == u'':
                        comment = u'dnf'
                    if comment != lcomment:
                        sec.lines.append([None, None, None])# new bunch
                    lcomment = comment
                    # account for special cases
                    if comment == u'dns':
                        dnscount += 1
                    elif comment == u'hd' or comment == u'otl':
                        # otl special case: also show down time if possible
                        bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                        if bt is not None:
                            if not first and wt is not None:
                                et = bt
                                sof = None
                                if r[COL_STOFT] is not None:
                                    sof = r[COL_STOFT]
                                elif rcat in self.catstarts:
                                    sof = self.catstarts[rcat]
                                if sof is not None:
                                    # apply a start offset
                                    et = bt - sof
                                dstr = u'+' + (et - wt).rawtime(0)
                        hdcount += 1
                    else:
                        dnfcount += 1
                    pstr = comment
                if placed or timed:
                    sec.lines.append([pstr, bstr, nstr, cstr, tstr, dstr])
            ret.append(sec)

            # Race metadata / UCI comments
            sec = printing.bullet_text('resultuci')
            if wt is not None:
                sec.lines.append([None, u'Race time: ' + wt.rawtime(0)])
                if we is None:
                    we = wt
                dval = self.meet.get_distance()
                if dval is not None:
                    sec.lines.append([None, u'Average speed of the winner: '
                                        + we.speedstr(1000.0*dval)])
            if dofastest:
                if vfastest and vfastest < fastest:
                    self.log.info(u'Fastest time not yet available.')
                else:
                    ftr = self.getrider(fastestbib)
                    fts = u''
                    if ftr is not None:
                        #ftcat = ftr[COL_CAT].decode('utf-8')
                        #if ftcat.upper() in catcache:
                            #ftcat = catcache[ftcat.upper()]
                        fts = ftr[COL_SHORTNAME].decode('utf-8')
                    fmsg = (u'Fastest time: ' + fastest.rawtime(0)
                            + u'  ' + fastestbib + u' ' + fts)
                    smsg = (u'Fastest time - ' + fts + u' '
                            + fastest.rawtime(0))
                    sec.lines.append([None, fmsg])
                    if not self.readonly:	# in a ui window?
                        self.meet.announce_cmd(u'resultmsg', fmsg)
                        self.meet.announce_cmd(u'scrollmsg', smsg)

            sec.lines.append([None,
                              u'Number of starters: '
                              + unicode(totcount-dnscount)])
            if hdcount > 0:
                sec.lines.append([None,
                              u'Riders finishing out of time limits: '
                              + unicode(hdcount)])
            if dnfcount > 0:
                sec.lines.append([None,
                              u'Riders abandoning the race: '
                              + unicode(dnfcount)])
            residual = totcount - (fincount + dnfcount + dnscount + hdcount)
            if residual > 0:
                self.log.info(u'Unaccounted for: ' + unicode(residual))
            ret.append(sec)
            
            # Intermediates
            #if self.event[u'type'] == u'rhcp':
                #for imed in self.intermeds:
                    #ret.extend(self.int_report(imed))

            # Decisions of commissaires panel
            if len(self.comment) > 0:
                sec = printing.bullet_text('comms')
                sec.heading = u'Decisions of the Commissaires Panel'
                for cl in self.comment:
                    sec.lines.append([None, cl.strip()])
                ret.append(sec)
        else:
            self.log.info(u'Result report skipped - not started yet.')
        return ret

    def stat_but_clicked(self, button=None):
        """Deal with a status button click in the main container."""
        self.log.info('Stat button clicked.')

    def ctrl_change(self, acode='', entry=None):
        """Notify change in action combo."""
        if acode == 'fin':
            if entry is not None:
                entry.set_text(self.places)
        elif acode in self.intermeds:
            if entry is not None:
                entry.set_text(self.intermap[acode]['places'])
        else:
            if entry is not None:
                entry.set_text('')

    def race_ctrl(self, acode='', rlist=''):
        """Apply the selected action to the provided bib list."""
        self.checkpoint_model()
        if acode in self.intermeds:
            if acode == u'brk':
                rlist = strops.reformat_riderlist(rlist)
                self.intsprint(acode, rlist)
            else:
                rlist = strops.reformat_placelist(rlist)
                if self.checkplaces(rlist, dnf=False):
                    self.intermap[acode]['places'] = rlist
                    self.recalculate()
                    self.intsprint(acode, rlist)
                    self.log.info('Intermediate ' + repr(acode) + ' == '
                                   + repr(rlist))
                else:
                    self.log.error('Intermediate ' + repr(acode) + ' not updated.')
            return False
        elif acode == 'fin':
            rlist = strops.reformat_placelist(rlist)
            if self.checkplaces(rlist):
                self.places = rlist
                self.recalculate()
                self.finsprint(rlist)
                return False
            else:
                self.log.error('Places not updated.')
                return False
        elif acode == 'dnf':
            self.dnfriders(strops.reformat_biblist(rlist))
            return True
        elif acode == 'dsq':
            self.dnfriders(strops.reformat_biblist(rlist), 'dsq')
            return True
        elif acode == 'hd' or acode == 'otl':
            self.dnfriders(strops.reformat_biblist(rlist), 'otl')
            return True
        elif acode == 'wd':
            self.dnfriders(strops.reformat_biblist(rlist), 'wd')
            return True
        elif acode == 'dns':
            self.dnfriders(strops.reformat_biblist(rlist), 'dns')
            return True
        elif acode == 'ret':
            self.retriders(strops.reformat_biblist(rlist))
            return True
        elif acode == 'man':
            # crude hack tool for now
            self.manpassing(strops.reformat_bibserlist(rlist))
            return True
        elif acode == 'del':
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
            if rlist != '':
                for bib in rlist.split():
                    self.query_rider(bib)
            return True
        elif acode == 'com':
            self.add_comment(rlist)
            return True
        else:
            self.log.error('Ignoring invalid action.')
        return False

    def add_comment(self, comment=''):
        """Append a race comment."""
        self.comment.append(comment.strip())
        self.log.info('Added race comment: ' + repr(comment))

    def query_rider(self, bib=None):
        """List info on selected rider in the scratchpad."""
        self.log.info('Query rider: ' + repr(bib))
        r = self.getrider(bib)
        if r is not None:
            ns = strops.truncpad(r[COL_NAMESTR] + ' ' + r[COL_CAT], 30)
            bs = ''
            bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
            if bt is not None:
                bs = bt.timestr(0)
            ps = r[COL_COMMENT]
            if r[COL_PLACE] != '':
                ps = strops.num2ord(r[COL_PLACE])
            self.log.info(' '.join([bib, ns, bs, ps]))
            lt = None
            if len(r[COL_RFSEEN]) > 0:
                for rft in r[COL_RFSEEN]:
                    nt = rft.truncate(0)
                    ns = rft.timestr(1)
                    ls = ''
                    if lt is not None:
                        ls = (nt - lt).timestr(0)
                    self.log.info(' '.join(['\t', ns, ls]))
                    lt = nt
            if r[COL_RFTIME] is not None:
                self.log.info(' '.join([' Finish:',
                                          r[COL_RFTIME].timestr(1)]))
        else:
            self.log.info(bib.ljust(4) + ' ' + 'Not in startlist.')

    def startlist_gen(self, cat=''):
        """Generator function to export a startlist."""
        mcat = self.ridercat(cat)
        self.reorder_startlist()
        for r in self.riders:
            if mcat == '' or mcat == self.ridercat(r[COL_CAT]):
                start = ''
                if r[COL_STOFT] is not None and r[COL_STOFT] != tod.ZERO:
                    start = r[COL_STOFT].rawtime(0)
                bib = r[COL_BIB]
                series = self.series
                name = r[COL_NAMESTR]
                cat = r[COL_CAT]
                firstxtra = ''
                lastxtra = ''
                clubxtra = ''
                dbr = self.meet.rdb.getrider(r[COL_BIB],self.series)
                if dbr is not None:
                    firstxtra = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_FIRST).capitalize()
                    lastxtra = self.meet.rdb.getvalue(dbr, 
                                         riderdb.COL_LAST).upper()
                    clubxtra = self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB)
                yield [start, bib, series, name, cat,
                       firstxtra, lastxtra, clubxtra]

    def lifexport(self):
        """Export lif for bruno."""
        self.recalculate()
        st = tod.ZERO
        if self.start is not None:
            st = self.start
        sno = u'1'
        if self.meet.mirrorpath:
            sno = self.meet.mirrorfile
        rdx = 1
        odat = [[sno,u'1',u'1',self.meet.subtitle_str,u'',u'',u'',u'',u'',u'',u'',u'']]
        
        for r in self.riders:
            bib = r[COL_BIB]
            if r[COL_INRACE]:
                if r[COL_RFTIME]:
                    last = u''
                    first = u''
                    team = u''
                    ucicode = u''
                    dbr = self.meet.rdb.getrider(r[COL_BIB],self.series)
                    if dbr is not None:
                        first = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_FIRST).capitalize()
                        last = self.meet.rdb.getvalue(dbr,
                                         riderdb.COL_LAST).upper()
                        team = self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB)
                        ucicode = self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE)

                    rftime = u'0'
                    if r[COL_RFTIME] is not None:
                        rftime = (r[COL_RFTIME]-st).rawtime(2,hoursep=':')
                    bunch = u''
                    if r[COL_CBUNCH] is not None:
                        bunch = r[COL_CBUNCH].rawtime(0,hoursep=':')
                    # rider with time
                    odat.append([unicode(rdx),bib,bib,last,first,team,rftime,ucicode,bunch,u'',u'',u''])
                    rdx += 1
        return odat

    def result_gen(self, cat=''):
        """Generator function to export a final result."""
        self.recalculate()	# fix up ordering of rows
        mcat = self.ridercat(cat)
        rcount = 0
        lrank = None
        lcrank = None
        for r in self.riders:
            rcat = self.ridercat(r[COL_CAT])
            if mcat == '' or mcat == rcat:
                rcount += 1
                bib = r[COL_BIB]
                crank = None
                rank = None
                bonus = None
                ft = None
                if r[COL_INRACE]:
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    ft = bt
                    sof = None
                    if r[COL_STOFT] is not None:
                        sof = r[COL_STOFT]
                    elif rcat in self.catstarts:
                        sof = self.catstarts[rcat]
                    if sof is not None and bt is not None:
                        if self.event[u'type'] != u'rhcp':
                            ft = bt - sof
                        else:
                            # for handicap, time is stage time, bonus
                            # carries the start offset, elapsed is:
                            # stage - bonus
                            ft = bt
                            bonus = sof

                if r[COL_PLACE].isdigit():
                    rank = int(r[COL_PLACE])
                    if rank != lrank: 
                        crank = rcount
                    else:
                        crank = lcrank
                    lcrank = crank
                    lrank = rank
                else:
                    crank = r[COL_COMMENT]
                if self.event[u'type'] != u'rhcp' and (
                         bib in self.bonuses or r[COL_BONUS] is not None):
                    bonus = tod.ZERO
                    if bib in self.bonuses:
                        bonus += self.bonuses[bib]
                    if r[COL_BONUS] is not None:
                        bonus += r[COL_BONUS]
                penalty = None
                if r[COL_PENALTY] is not None:
                    penalty = r[COL_PENALTY]
                if ft is not None:
                    ft = ft.truncate(0)	# force whole second for bunch times
                yield [crank, bib, ft, bonus, penalty]

    def clear_results(self):
        """Clear all data from event model."""
        self.resetplaces()
        self.places = ''
        self.log.debug('Cleared event result.')

    def getrider(self, bib):
        """Return reference to selected rider no."""
        ret = None
        for r in self.riders:
            if r[COL_BIB] == bib:
                ret = r
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

    def delrider(self, bib='', series=''):
        """Remove the specified rider from the model."""
        i = self.getiter(bib)
        if i is not None:
            self.riders.remove(i)
        # and remove from places? (for hack handicapcatrace)
        oplaces = self.places.split()
        if bib in oplaces:
            oplaces.remove(bib)
        self.places = ' '.join(oplaces)

    def starttime(self, start=None, bib='', series=''):
        """Adjust start time for the rider."""
        if series == self.series:
            r = self.getrider(bib)
            if r is not None:
                r[COL_STOFT] = start

    def addrider(self, bib='', series=None):
        """Add specified rider to race model."""
        if series is not None and series != self.series:
            self.log.debug('Ignoring non-series starter: '
                            + repr(strops.bibser2bibstr(bib, series)))
            return None
        if bib == '' or self.getrider(bib) is None:
            nr = [bib, '', '', '', '', True, '', 0, None, None, None, None, 
                           None, None, None, []]
            dbr = self.meet.rdb.getrider(bib, self.series)
            if dbr is not None:
                nr[COL_NAMESTR] = strops.listname(
                      self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                      self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                      self.meet.rdb.getvalue(dbr, riderdb.COL_CLUB))
                nr[COL_SHORTNAME] = strops.listname(
                      self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                      self.meet.rdb.getvalue(dbr, riderdb.COL_LAST))
                nr[COL_CAT] = self.meet.rdb.getvalue(dbr, riderdb.COL_CAT)
            return self.riders.append(nr)
        else:
            return None

    def resettimer(self):
        """Reset race timer."""
        self.meet.alttimer.dearm(1)
        self.set_finish()
        self.set_start()
        self.clear_results()
        self.timerstat = 'idle'
        self.meet.announce_cmd(u'timerstat', u'idle')
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Idle')
        self.meet.stat_but.set_sensitive(True)
        self.curlap = -1
        self.live_announce = True
        
    def armstart(self):
        """Process an armstart request."""
        if self.timerstat == 'idle':
            self.timerstat = 'armstart'
            self.meet.announce_cmd(u'timerstat', u'armstart')
            uiutil.buttonchg(self.meet.stat_but,
                             uiutil.bg_armint, 'Arm Start')
            self.resetcatonlaps()
        elif self.timerstat == 'armstart':
            self.timerstat = 'idle'
            self.meet.announce_cmd(u'timerstat', u'idle')
            uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Idle') 
        elif self.timerstat == 'ready':
            self.set_running()

    def resetcatonlaps(self):
        onechange = False
        for cat in self.catlaps: # for each category with a defined target
            target = self.catlaps[cat]
            if target:
                self.catonlap[cat] = 0	# start at 0
                onechange = True
        if onechange:
            self.announcecatlap()

    def armfinish(self):
        """Process an armfinish request."""
        if self.timerstat in ['ready', 'running', 'finished']:
            if self.curlap and self.totlaps and self.curlap < self.totlaps:
                # assume lap was not armed
                self.armlap()
            elif self.totlaps == 0:
                self.armlap()	# unbound lap count
            self.timerstat = 'armfinish'
            self.meet.announce_cmd(u'timerstat', u'armfinish')
            uiutil.buttonchg(self.meet.stat_but,
                             uiutil.bg_armfin, 'Arm Finish')
            self.meet.stat_but.set_sensitive(True)
            self.meet.alttimer.armlock(True)
            self.meet.alttimer.arm(1)
        elif self.timerstat == 'armfinish':
            self.meet.alttimer.dearm(1)
            self.timerstat = 'running'
            self.meet.announce_cmd(u'timerstat', u'running')
            uiutil.buttonchg(self.meet.stat_but,
                             uiutil.bg_armstart, 'Running')

    def last_rftime(self):
        """Find the last rider with a RFID finish time set."""
        ret = None
        for r in self.riders:
            if r[COL_RFTIME] is not None:
                ret = r[COL_BIB]
        return ret
        
    def armlap(self, data=None):
        ## announce text handling...
        self.log.info(u'Arming Lap.')
        if self.curlap is None or self.curlap < 0:
            self.curlap = 0	# manual override lap counts
        self.scratch_tot = 0
        self.scratch_map = {}
        self.scratch_ord = []
        titlestr = self.title_namestr.get_text()
        if self.live_announce:
            self.meet.announce_clear()
        if self.timerstat in ['idle', 'armstart', 'armfinish']:
            self.meet.announce_cmd(u'finstr', self.meet.get_short_name())
            if self.timerstat in ['idle', 'armstart']:
                self.reannounce_times()	# otherwise not called
                self.meet.announce_title(titlestr)	# enforce
                return True	# no arm till event underway	(messy)
        if self.curlap <= 0 or self.lapfin is not None:
            self.curlap += 1	# increment
            
            if self.totlaps and self.curlap > self.totlaps:
                self.log.info('Too many laps.')
                self.curlap = self.totlaps

            # sanity check onlap
            # once arm lap is done, curlap and onlap _should_ be same
            if self.onlap != self.curlap:
                self.log.info('Cur/On lap mismatch - fixed.')
                self.onlap = self.curlap
                self.meet.announce_cmd(u'onlap', unicode(self.onlap))

        # update curlap entry whatever happened
        self.lapentry.set_text(unicode(self.curlap))

        # Write lap time fields
        lapstr = None
        if self.timerstat not in ['armfinish', 'finished']:
            self.meet.announce_cmd(u'finstr', None)
            ## Step 1: lap time handling
            if self.lapfin:
                # roll over to lap start
                self.lapstart = self.lapfin
            elif self.lapstart:# assume still waiting for same lap
                pass
            else:		# at start?
                self.lapstart = self.start
            if self.totlaps is not None:
                if self.totlaps > 0:
                    #lapstr = u''
                    lapstr = (u'Lap ' +  unicode(self.onlap)
                                  + u'/' + unicode(self.totlaps))
                    #lapstr = (u'Lap ' +  unicode(self.curlap)
                                  #+ u'/' + unicode(self.totlaps))
                else:	# 0 flags unknown total
                    lapstr = u''
                    passkey = unicode(self.curlap)
                    if passkey in self.passlabels:
                        lapstr = u'At ' + self.passlabels[passkey]
                    else:
                        lapstr = (u'Lap ' +  unicode(self.onlap))
                self.totlapentry.set_text(unicode(self.totlaps))
                self.meet.announce_cmd(u'laplbl', lapstr)
                if self.curlap in self.sprintlaps:
                    self.meet.announce_cmd(u'laptype',u'SPRINT')
                else:
                    self.meet.announce_cmd(u'laptype',None)
            else:
                # make sure something is displayed in this path
                self.meet.announce_cmd(u'laplbl', None)
                self.meet.announce_cmd(u'finstr', self.meet.get_short_name())
            self.lapfin = None
        if self.live_announce:
            if lapstr:
                self.meet.announce_title(titlestr + ' ' + lapstr)
            else:
                self.meet.announce_title(titlestr)	# enforce
        self.reannounce_times()
        # in case of idle/delay
        return False

    def key_event(self, widget, event):
        """Handle global key presses in event."""
        if event.type == gtk.gdk.KEY_PRESS:
            key = gtk.gdk.keyval_name(event.keyval) or 'None'
            if event.state & gtk.gdk.CONTROL_MASK:
                if key == key_abort:    # override ctrl+f5
                    self.resettimer()
                    return True
                elif key == key_announce: # re-send current announce vars
                    self.reannounce_times()
                    return True
                elif key == key_clearfrom: # clear all places from selected
                    self.clear_places_from_selection()
                    return True
                elif key == key_clearplace: # clear selected rider from places
                    self.clear_selected_place()
                    return True	
                elif key.upper() == key_undo:	# Undo model change if possible
                    self.undo_riders()
                    return True
            if key[0] == 'F':
                if key == key_announce:
                    if self.places:
                        self.finsprint(self.places)
                    else:
                        self.reannounce_lap()
                    return True
                elif key == key_placesto:
                    self.fill_places_to_selected()
                    return True
                elif key == key_appendplace:
                    self.append_selected_place()
                    return True
        return False

    def append_selected_place(self):
        sel = self.view.get_selection()
        sv = sel.get_selected()
        if sv is not None:
            i = sv[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            self.log.info(u'Confirmed next place: '
                           + repr(selbib) + u'/' + repr(selpath))
            oplaces = self.places.split()
            if selbib in oplaces:
                oplaces.remove(selbib)
            oplaces.append(selbib)
            self.checkpoint_model()
            self.places = ' '.join(oplaces)
            # recalculate in this path
            self.recalculate()
            # advance selection
            j = self.riders.iter_next(i)
            if j is not None:
                # note: set by selection doesn't adjust focus
                self.view.set_cursor_on_cell(self.riders.get_path(j))

    def fill_places_to_selected(self):
        """Update places to match ordering up to selected rider."""
        sel = self.view.get_selection()
        sv = sel.get_selected()
        if sv is not None:
            # fill places and recalculate
            i = sv[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            self.log.info(u'Confirmed places to: '
                           + repr(selbib) + u'/' + repr(selpath))
            self.fill_places_to(selbib)
            # advance selection
            j = self.riders.iter_next(i)
            if j is not None:
                # note: set by selection doesn't adjust focus
                self.view.set_cursor_on_cell(self.riders.get_path(j))

    def clear_places_from_selection(self):
        """Clear all places from riders following the current selection."""
        if self.places.find('-') > 0:
            self.log.warn('Clear place with dead heat not implemented.')
            return
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            self.log.info(u'Cleared places from: '
                           + repr(selbib) + u'/' + repr(selpath))
            oplaces = self.places.split()
            nplaces = []
            for p in oplaces:
                if p == selbib:
                    break
                nplaces.append(p)
            self.checkpoint_model()
            self.places = ' '.join(nplaces)
            # recalculate in this path
            self.recalculate()

    def clear_selected_place(self):
        """Remove the selected rider from places."""
        if self.places.find('-') > 0:
            self.log.warn('Clear place with dead heat not implemented.')
            return

        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            self.log.info(u'Cleared rider from places: '
                           + repr(selbib) + u'/' + repr(selpath))
            oplaces = self.places.split()
            if selbib in oplaces:
                oplaces.remove(selbib)
            self.checkpoint_model()
            self.places = ' '.join(oplaces)
            # recalculate in this path
            self.recalculate()

    def setrftime(self, bib, rank, rftime, bonus=None):
        """Override rider result from CSV data."""
        r = self.getrider(bib)
        if r is not None:
            # first: set the rider's manual bunch time
            r[COL_RFTIME] = rftime
            if bonus is not None:
                r[COL_BONUS] = bonus
            oldplaces = self.places.split()
            if bib in oldplaces:
                oldplaces.remove(bib)
            oldplaces.insert(rank-1, bib)
            self.places = u' '.join(oldplaces)
            self.__dorecalc = True

    def setriderval(self, bib, rank, bunch, bonus=None):
        """Override rider result from LIF data."""
        if u'-' in self.places:
            self.log.debug(u'Dead heat in places: LIF data rejected.')
            return None

        r = self.getrider(bib)
        if r is not None:
            # first: set the rider's manual bunch time
            r[COL_MBUNCH] = bunch
            if bonus is not None:
                r[COL_BONUS] = bonus
            oldplaces = self.places.split()
            if bib in oldplaces:
                oldplaces.remove(bib)
            oldplaces.insert(rank-1, bib)
            self.places = u' '.join(oldplaces)
            self.__dorecalc = True
        
    def dnfriders(self, biblist='', code='dnf'):
        """Remove each rider from the race with supplied code."""
        recalc = False
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                if code != 'wd':
                    r[COL_INRACE] = False
                r[COL_COMMENT] = code
                recalc = True
                self.log.info('Rider ' + str(bib) 
                               + ' did not finish with code: ' + code)
            else:
                self.log.warn('Unregistered Rider ' + str(bib) + ' unchanged.')
        if recalc:
            self.recalculate()
        return False

    def manpassing(self, biblist=''):
        """Register a manual passing, preserving order."""
        for bib in biblist.split():
            # NOTE: All bibs are sent into trigger interface so that 
            #       timing master will propagate to slaves, race module
            #       will process as if timing came from attached decoder.
            rno,rser = strops.bibstr2bibser(bib)
            if not rser:	# allow series manual override
                rser = self.series
            bibstr = strops.bibser2bibstr(rno,rser)
            self.meet.timer.trig(refid=u'riderno:'+bibstr)
            self.log.debug(u'Manual Passing: ' + bibstr)

    def retriders(self, biblist=''):
        """Return all listed riders to the race."""
        recalc = False
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                r[COL_INRACE] = True
                r[COL_COMMENT] = ''
                recalc = True
                self.log.info('Rider ' + str(bib) 
                               + ' returned to race.')
            else:
                self.log.warn('Unregistered Rider ' + str(bib) + ' unchanged.')
        if recalc:
            self.recalculate()
        return False
  
    def shutdown(self, win=None, msg='Race Sutdown'):
        """Close event."""
        self.log.debug('Event shutdown: ' + msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def starttrig(self, e):
        """Process a 'start' trigger signal."""
        self.meet.announce_cmd(u'decodertrig', e.rawtime())
        self.log.info(u'Trigger: ' + e.rawtime(2))
        if self.timerstat == 'armstart':
            self.set_start(e)
            self.resetcatonlaps()		# but only here?
            if self.event[u'type'] == u'crit':	# auto arm lap for real crit
                glib.timeout_add_seconds(10, self.armlap)
        return False

    def set_lap_finish(self, e):
        """Write lap time into model and emit changed state."""
        self.laptimes.append(e)
        self.lapfin = e
        self.onlap += 1	# increment rider onlap
        self.meet.announce_cmd(u'onlap',unicode(self.onlap))
        self.meet.announce_cmd(u'laptype',None)
        self.reannounce_times()

    def timertrig(self, e):
        """Record timer message from alttimer."""
        self.meet.announce_timer(e, self.meet.alttimer)
        if self.timerstat == 'armfinish':
            self.log.info(u'Bunch Trigger: ' + e.rawtime(0))

    def rfidstat(self, e):
        """Handle RFID status message."""
        self.log.info(u'Decoder ' + e.source + u': ' + e.refid)
        return False

    def announcecatlap(self):
        """Emit a category lap scoreboard message."""
        # take only the first two cats?
        tlbl = u''
        tnum = u''
        blbl = u''
        bnum = u''
        for cat in self.cats:	# scan ordered list first
            if cat:		# ignore empty cat
                if cat in self.catonlap:
                    count = self.catonlap[cat]
                    target = None
                    togo = None
                    if cat in self.catlaps:
                        target = self.catlaps[cat]
                    if target is not None and count is not None and count <= target: 
                        togo = target - count
                        prompt = cat.upper()
                        dbr = self.meet.rdb.getrider(cat,u'cat')
                        if dbr is not None:
                            prompt = self.meet.rdb.getvalue(dbr,
                                                      riderdb.COL_CLUB)
                        value = str(togo)
                        if tnum != u'':
                            blbl = prompt
                            bnum = value
                        else:
                            tlbl = prompt
                            tnum = value
            if bnum != u'':	# scan completed early
                break
        # always announce even if there's noting to display
        self.meet.announce_cmd(u'catlaps',unichr(0x1f).join(
                                [tlbl, tnum, blbl, bnum]))
        self.log.debug(u'Category onlaps: ' + repr(self.catonlap))

    def rfidtrig(self, e):
        """Process rfid event."""
        # bounce message onto announcer: HACK
        self.meet.announce_timer(e, self.meet.timer)

        if e.refid in ['', '255']:	# got a trigger
            return self.starttrig(e)
        elif e.chan == u'STS':	# status message
            return self.rfidstat(e)

        # else assume this is a passing
        r = self.meet.rdb.getrefid(e.refid)
        if r is None:
            self.log.info('Unknown tag: ' + e.refid 
                          + '@' + e.rawtime(2) + u'/' + repr(e.source))
            return False

        bib = self.meet.rdb.getvalue(r, riderdb.COL_BIB)
        ser = self.meet.rdb.getvalue(r, riderdb.COL_SERIES)
        if ser != self.series:
            self.log.error('Ignored non-series rider: ' + bib + '.' + ser)
            return False

        cat = self.meet.rdb.getvalue(r, riderdb.COL_CAT)	# db cat
        rcat = self.get_ridercat(bib)				# race cat

        # if passing for a spare bike, and spares allowed, add it in
        if self.allowspares and cat == u'SPARE':
            self.log.warning(u'Spare Bike: ' + bib)
            self.addrider(bib)

        # at this point should always have a valid source rider vector
        lr = self.getrider(bib)
        if lr is None:
            self.log.warn('Ignoring non starter: ' + bib
                          + '@' + e.rawtime(2) + u'/' + repr(e.source))
            return False

        # if this passing is for pre-finish, log
        if self.preloop is not None and e.source == self.preloop:
            if lr[COL_PREPASS] is not None:
                self.log.warn(u'Rider already pre-passed.')
            else:
                lr[COL_PREPASS] = e
                self.log.info(u'Prepass: ' + bib
                          + '@' + e.rawtime(2) + u'/' + repr(e.source))
            return False

        # if there's a source filter set, discard any unknown sources
        if len(self.passingsource) > 0:
            if e.source.lower() not in self.passingsource:
                self.log.info(u'Source not configured: ' + bib
                                + '@' + e.rawtime(2) + u'/' + repr(e.source))
                return False
                
        # save minimum lap time for re-use
        passthresh = MINPASSTIME
        if self.minlap is not None:
            passthresh = self.minlap

        if not lr[COL_INRACE]:
            self.log.warn('Withdrawn rider: ' + lr[COL_BIB]
                          + ' @ ' + e.rawtime(2))
            # but continue anyway just in case it was not correct?
        else:
            self.log.info('Saw: ' + bib + ' @ ' + e.rawtime(2))

        # check run state
        if self.timerstat in ['idle', 'ready', 'armstart']:
            return False

        # check for start and minlap
        st = tod.ZERO
        catstart = tod.ZERO
        if rcat in self.catstarts and self.catstarts[rcat] is not None:
            catstart = self.catstarts[rcat]
        if self.start is not None:
            st = self.start + catstart + passthresh
        # ignore all passings from before first allowed time
        if e <= st:
            self.log.info(u'Ignored early passing: '
                              + bib + ' < ' + st.rawtime(2))
            return False
        else:
            # if available, grab previous passing
            lastchk = None
            ipos = bisect.bisect_right(lr[COL_RFSEEN], e)
            if ipos == 0:	# 'first' in-race passing, accept and move on
                lr[COL_RFSEEN].insert(ipos,e)
            else:		# always one to the 'left' of e
                # First check one to the 'left': previous passing
                lastseen = lr[COL_RFSEEN][ipos-1]
                nthresh = lastseen + passthresh
                if e <= nthresh:
                    self.log.info(u'Ignored short lap: '
                              + bib + ' < ' + nthresh.rawtime(2))
                    return False
                # if insertion is out of order, also check passing to right
                if len(lr[COL_RFSEEN]) > ipos:
                    npass = lr[COL_RFSEEN][ipos]
                    delta = npass - e
                    if delta <= passthresh:
                        self.log.info(u'Ignored spurious pre-passing: '
                                  + bib + ' ' + e.rawtime(2) + ' < ' + npass.rawtime(2))
                        return False

            # insert this passing in order into pass records
            lr[COL_RFSEEN].insert(ipos,e)

        catfinish = False
        targetlap = None
        if rcat in self.catlaps and self.catlaps[rcat]:
            targetlap = self.catlaps[rcat]
            if lr[COL_LAPS] >= targetlap - 1:
                catfinish = True	# arm just this rider

        ## finishing rider path
        if self.timerstat == 'armfinish' or catfinish:
            # in finish mode, all passings are considered on the finish 'lap'
            if self.finish is None:
                # Announce the first finish to scoreboard. Will be corrected
                # if necessary later in recalc.
                self.set_finish(e)
                # send a flush command to SCB for snappy output
                self.meet.announce_cmd(u'redraw',u'timer')
            if lr[COL_RFTIME] is None:
                if lr[COL_COMMENT] != 'wd':
                    if lr[COL_PLACE] == '':
                        lr[COL_RFTIME] = e
                        self.__dorecalc = True
                    else:
                        self.log.error(u'Placed rider seen at finish: ' + bib
                                  + ' @ ' + e.rawtime(2))
                    if lr[COL_INRACE]:
                        if not catfinish and self.curlap is not None and self.curlap > 0:
                            lr[COL_LAPS] = self.curlap # on last lap
                        else:
                            lr[COL_LAPS] += 1
                            if rcat in self.catonlap and lr[COL_LAPS] > self.catonlap[rcat]:
                                self.catonlap[rcat] = lr[COL_LAPS]
                                self.announcecatlap()
                        if self.lapfin is None:
                            self.set_lap_finish(e)
                        self.announce_rider('', bib, lr[COL_NAMESTR],
                                        lr[COL_CAT], e)
            else:
                # log this case, but is it required in main log?
                self.log.info('Duplicate finish rider ' + bib
                                  + ' @ ' + e.rawtime(2))
        ## end finishing rider path

        ## lapping rider path
        elif self.timerstat in ['running']:
            ## Need to check lapping mode and cat lap count
            self.__dorecalc = True	# cleared in timeout, from same thread
            if lr[COL_INRACE]:
                onlap = False
                if targetlap is not None: # treat target lapping differently
                    self.log.debug(u'Targetlap is: ' + repr(targetlap))
                    lr[COL_LAPS] += 1	# increment indiv lap
                    catlap = 0
                    if rcat in self.catonlap:
                        catlap = self.catonlap[rcat] 
                    else:
                        self.catonlap[rcat] = 0	# init

                    if lr[COL_LAPS] > catlap:	# catch up even if discont
                        self.catonlap[rcat] = lr[COL_LAPS]
                        self.announcecatlap()
                    else:
                        if lr[COL_LAPS] >= catlap:
                            onlap = True

                elif self.lapfin is None:
                    # lap finish armed, the first rider with laps == curlap-1
                    # will be considered the leader, otherwise they are dropped
                    # NOTE: this overrides lap time guards in all cases
                    if lr[COL_LAPS] == self.curlap-1:
                        self.set_lap_finish(e)
                        # send a flush command to SCB for snappy output
                        self.meet.announce_cmd(u'redraw',u'timer')
                        lr[COL_LAPS] = self.curlap
                        onlap = True
                    else:
                        lr[COL_LAPS] += 1 # boost them onto the next lap
                else:
                    ## check that this passing is in 'this' lap
                    if self.lapfin is not None:
                        curlapstart = self.lapfin
                        if e < curlapstart:
                            # passing is for a previous lap, 
                            onlap = False
                            self.log.info(u'Passing from previous lap: ' + bib + u' ' + e.rawtime(2) + u' < ' + curlapstart.rawtime(2))
                        elif e < curlapstart+passthresh:
                            # passing cannot be for a NEW lap yet
                            # put everyone in this category ON lead lap 
                            # regardless of the current lap total
                            lr[COL_LAPS] = self.curlap
                            onlap = True
                        else:	
                            # If rider's lap count is behind current lap,
                            # push them on to this lap, even if more than
                            # one minlap behind leader
                            if lr[COL_LAPS] < self.curlap:
                                lr[COL_LAPS] = self.curlap
                                onlap = True
                            else:
                                # otherwise we have a new lap leader,
                                # if the lap numbers match, record start
                                # of new lap and move on
                                if lr[COL_LAPS] == self.curlap:
                                    self.armlap()
                                    self.set_lap_finish(e)
                                    # send a flush command to SCB for snappy output
                                    self.meet.announce_cmd(u'redraw',u'timer')
                                    lr[COL_LAPS] = self.curlap
                                    onlap = True
                                else:
                                    # lap number inconsistency, log and ignore
                                    self.log.info(u'Rider lap number mismatch: ' + bib + u' ' + repr(lr[COL_LAPS]) + u' <=> ' + repr(self.curlap))

                ## announce only riders on the current lap
                if onlap:
                    self.announce_rider('', bib, lr[COL_NAMESTR],
                                            lr[COL_CAT], e)
        ## end lapping rider path
        return False	# called from glib_idle_add

    def announce_rider(self, place, bib, namestr, cat, rftime):
        """Log a rider in the lap and emit to announce."""
        if bib not in self.scratch_map:
            self.scratch_map[bib] = rftime
            self.scratch_ord.append(bib)
        if self.live_announce:
            glib.idle_add(self.meet.announce_rider, [place,bib,namestr,
                                     cat,rftime.rawtime()])

    def lapentry_activate_cb(self, entry, data=None):
        """Transfer lap entry string into model if possible."""
        try:
            self.curlap = int(entry.get_text().decode('utf-8'))
        except:
            self.log.warn(u'Ignored invalid lap count.')
        if self.curlap >= 0:
            self.lapentry.set_text(unicode(self.curlap))
        else:
            self.lapentry.set_text(u'')

    def totlapentry_activate_cb(self, entry, data=None):
        """Transfer total lap entry string into model if possible."""
        try:
            nt = entry.get_text().decode('utf-8')
            if nt:	# not empty
                self.totlaps = int(entry.get_text().decode('utf-8'))
            else:
                self.totlaps = None
        except:
            self.log.warn(u'Ignored invalid total lap count.')
        if self.totlaps is not None:
            self.totlapentry.set_text(unicode(self.totlaps))
        else:
            self.totlapentry.set_text(u'')

    def finsprint(self, places):
        """Display a final sprint 'provisional' result."""

        self.live_announce = False
        self.meet.announce_clear()
        scrollmsg = u'Provisional - '
        titlestr = self.title_namestr.get_text()
        if self.racestat == u'final':
            scrollmsg = u'Result - '
            self.meet.announce_title(titlestr + ': Final Result')
        else:
            self.meet.announce_title(titlestr + ': Provisional')
        self.meet.announce_cmd(u'bunches',u'final')
        placeset = set()
        idx = 0
        st = tod.tod('0')
        if self.start is not None:
            st = self.start
        # result is sent in weird hybrid units TODO: fix the amb
        wt = None
        lb = None
        scrollcnt = 0
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        ft = self.vbunch(r[COL_CBUNCH],
                                         r[COL_MBUNCH])
                        fs = ''
                        if ft is not None:
                            # temp -> just use the no-blob style to correct
                            fs = ft.rawtime()
                            if wt is None:
                                wt = ft
                            lb = ft
                        if scrollcnt < 5:
                            scrollmsg += (u' ' +
                                         r[COL_PLACE] + u'. ' 
                                         + r[COL_SHORTNAME].decode('utf-8')
                                         + u' ')
                            scrollcnt += 1
                        glib.idle_add(self.meet.announce_rider,
                                                 [r[COL_PLACE]+'.',
                                                 bib,
                                                 r[COL_NAMESTR],
                                                 r[COL_CAT], fs])
                    idx += 1
        self.meet.announce_cmd(u'scrollmsg',scrollmsg)
        if wt is not None:
            if self.start:
                self.meet.announce_cmd(u'start',self.start.rawtime())
            if self.finish:
                self.meet.announce_cmd(u'finish',self.finish.rawtime())
            else:
                self.log.info('No valid times available.')

    def int_report(self, acode):
        """Return report sections for the named intermed."""
        ret = []
        if acode not in self.intermeds:
            self.log.debug('Attempt to read non-existent intermediate: '
                              + repr(acode))
            return ret
        descr = acode
        if self.intermap[acode]['descr']:
            descr = self.intermap[acode]['descr']
        places = self.intermap[acode]['places']
        lines = []
        placeset = set()
        idx = 0
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        lines.append([str(curplace)+'.',bib,r[COL_NAMESTR],
                                                 r[COL_CAT], None, None])
                    idx += 1
                else:
                    self.log.warn('Duplicate no. = ' + str(bib) + ' in places.')
        if len(lines) > 0:
            sec = printing.section(u'inter'+acode)
            sec.heading = descr
            sec.lines = lines
            ret.append(sec) 
        return ret

    def intsprint(self, acode='', places=''):
        """Display an intermediate sprint 'provisional' result."""

        ## TODO : Fix offset time calcs - too many off by ones
        if acode not in self.intermeds:
            self.log.debug('Attempt to display non-existent intermediate: '
                              + repr(acode))
            return
        descr = acode
        if self.intermap[acode]['descr']:
            descr = self.intermap[acode]['descr']
        self.live_announce = False
        self.meet.announce_clear()
        self.reannounce_times()
        self.meet.announce_title(descr)
        scrollmsg = descr + u' - '
        placeset = set()
        idx = 0
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        scrollmsg += (u' ' +
                                     str(curplace) + u'. ' 
                                     + r[COL_SHORTNAME].decode('utf-8')
                                     + u' ')
                        rank = u''
                        if acode != u'brk':
                            rank = str(curplace)+'.'
                        glib.idle_add(self.meet.announce_rider,
                                                [rank,
                                                 bib,
                                                 r[COL_NAMESTR],
                                                 r[COL_CAT], ''])
                    idx += 1
                else:
                    self.log.warn('Duplicate no. = ' + str(bib) + ' in places.')
        if acode != u'brk':
            self.meet.announce_cmd(u'scrollmsg',scrollmsg)
        else:
            self.meet.announce_cmd(u'breakriders',places)
        glib.timeout_add_seconds(25, self.reannounce_lap)

    def todempty(self, val):
        if val is not None:
            return val.rawtime()
        else:
            return u''

    def reannounce_times(self):
        """Re-send the current timing values."""
        self.meet.announce_cmd(u'timerstat', self.timerstat)
        self.meet.announce_cmd(u'start', self.todempty(self.start))
        self.meet.announce_cmd(u'finish', self.todempty(self.finish))
        self.meet.announce_cmd(u'lapstart', self.todempty(self.lapstart))
        self.meet.announce_cmd(u'lapfin', self.todempty(self.lapfin))
        totlaps = None
        if self.totlaps:	 #may be zero, but don't show that case
            totlaps = unicode(self.totlaps)
## announce the onlap and curlap?
        curlap = None
        if self.curlap is not None:
            curlap = unicode(self.curlap)
        onlap = None
        if self.onlap is not None:
            onlap = unicode(self.onlap)
        self.meet.announce_cmd(u'onlap',unicode(onlap))
        self.meet.announce_cmd(u'curlap',unicode(curlap))
        self.meet.announce_cmd(u'totlaps', totlaps)
        # Write lap time fields
        lapstr = None
        if self.timerstat not in ['armfinish', 'finished']:
            self.meet.announce_cmd(u'finstr', None)
            if self.totlaps is not None:
                if self.totlaps > 0:
                    #lapstr = u''
                    lapstr = (u'Lap ' +  unicode(self.onlap)
                                  + u'/' + unicode(self.totlaps))
                    #lapstr = (u'Lap ' +  unicode(self.curlap)
                                  #+ u'/' + unicode(self.totlaps))
                else:	# 0 flags unknown total
                    lapstr = u''
                    passkey = unicode(self.curlap)
                    if passkey in self.passlabels:
                        lapstr = u'At ' + self.passlabels[passkey]
                    else:
                        lapstr = (u'Lap ' +  unicode(self.onlap))
                self.totlapentry.set_text(unicode(self.totlaps))
                self.meet.announce_cmd(u'laplbl', lapstr)
                if self.curlap in self.sprintlaps:
                    self.meet.announce_cmd(u'laptype',u'SPRINT')
                else:
                    self.meet.announce_cmd(u'laptype',None)
            else:
                # make sure something is displayed in this path
                self.meet.announce_cmd(u'laplbl', None)
                self.meet.announce_cmd(u'finstr', self.meet.get_short_name())

        if self.timerstat == u'idle':
            self.meet.announce_cmd(u'finstr', self.meet.get_short_name())
        return False

    def reannounce_lap(self):
        """Re-send current lap passing data."""
        self.meet.announce_clear()
        self.meet.announce_cmd(u'scrollmsg', None)
        self.reannounce_times()
        self.live_announce = False
        if self.timerstat == 'armfinish':
            self.meet.announce_title('Finish')
        else:
            self.meet.announce_title(self.title_namestr.get_text())
        for bib in self.scratch_ord:
            r = self.getrider(bib)
            if r is not None:
                glib.idle_add(self.meet.announce_rider,
                                        ['',bib,r[COL_NAMESTR],r[COL_CAT],
                                         self.scratch_map[bib].rawtime()])
        self.live_announce = True
        return False

    def timeout(self):
        """Update elapsed time and recalculate if required."""
        if not self.winopen:
            return False
        if self.__dorecalc:
            self.__dorecalc = False
            self.recalculate()
            if self.autoexport and self.timerstat in ['armfinish']:
                glib.idle_add(self.meet.menu_data_results_cb,None);
        et = self.get_elapsed()
        if et is not None:
            self.elaplbl.set_text(et.rawtime(0))
        else:
            self.elaplbl.set_text(u'')
        return True

    def set_start(self, start=''):
        """Set the start time."""
        self.lapstart = None
        self.lapfin = None
        self.onestart = False
        if type(start) is tod.tod:
            self.start = start
        else:
            self.start = tod.str2tod(start)
        if self.start is not None:
            self.onestart = True
            self.meet.announce_cmd(u'start', self.start.rawtime())
            self.last_scratch = self.start
            self.curlap = -1	# reset lap count at start
            self.onlap = 1	# leaders are 'onlap'
            self.meet.announce_cmd(u'onlap',unicode(self.onlap))
            if self.finish is None:
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
            self.meet.announce_cmd(u'finish', self.finish.rawtime())
            if self.start is None:
                self.set_start('0')
            else:
                elap = self.finish-self.start
                dval = self.meet.get_distance()
                if dval is not None:
                    self.meet.announce_cmd(u'average',
                                           elap.rawspeed(1000.0*dval))

    def get_elapsed(self):
        """Hack mode - always returns time from start."""
        ret = None
        if self.start is not None and self.timerstat != 'finished':
            ret = (tod.tod('now') - self.start).truncate(0)
        return ret

    def set_ready(self):
        """Update event status to ready to go."""
        # MOD Apr 2013: No need for ready now that minlap is provided.
        self.timerstat = 'ready'
        self.meet.announce_cmd(u'timerstat', u'ready')
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_armint, 'Ready')

    def set_running(self):
        """Update event status to running."""
        self.timerstat = 'running'
        self.meet.announce_cmd(u'timerstat', u'running')
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_armstart, 'Running')

    def set_finished(self):
        """Update event status to finished."""
        self.timerstat = 'finished'
        self.meet.announce_cmd(u'timerstat', u'finished')
        self.meet.announce_cmd(u'laplbl', None)
        self.meet.announce_cmd(u'laptype', None)
        uiutil.buttonchg(self.meet.stat_but, uiutil.bg_none, 'Finished')
        self.meet.stat_but.set_sensitive(False)
        if self.finish is None:
            self.set_finish(tod.tod('now'))

    def title_place_xfer_clicked_cb(self, button, data=None):
        """Transfer current rider list order to place entry."""
        nplaces = ''
        lplace = None
        for r in self.riders:
            if r[COL_INRACE] and r[COL_PLACE] != '':
                if lplace == r[COL_PLACE] and r[COL_PLACE] != '':
                    nplaces += '-' + r[COL_BIB] # dead heat riders
                else:
                    nplaces += ' ' + r[COL_BIB]
                    lplace = r[COL_PLACE]
        self.places = strops.reformat_placelist(nplaces)
        self.meet.action_combo.set_active(10)	# 'fin'
        self.meet.action_entry.set_text(self.places)
        
    def fill_places_to(self, bib=None):
        """Fill in finish places up to the nominated bib."""
        if self.places.find('-') > 0:
            self.log.warn('Fill places with dead heat not yet implemented.')
            return
        oplaces = self.places.split()	# only patch if no dead heats
        nplaces = []
        for r in self.riders:
            if r[COL_INRACE]:
                if r[COL_BIB] in oplaces:
                    oplaces.remove(r[COL_BIB])	# remove from old list
                nplaces.append(r[COL_BIB])      # add to new list
            else:
                if r[COL_BIB] in oplaces:
                    oplaces.remove(r[COL_BIB])	# strip out DNFed riders
            if r[COL_BIB] == bib:		# break after to get sel rider
                break
        nplaces.extend(oplaces)
        self.checkpoint_model()
        self.places = ' '.join(nplaces)
        # recalculate in this path
        self.recalculate()

    def info_time_edit_clicked_cb(self, button, data=None):
        """Run an edit times dialog to update race time."""
        st = ''
        if self.start is not None:
            st = self.start.rawtime(2)
        ft = ''
        if self.finish is not None:
            ft = self.finish.rawtime(2)
        ret = uiutil.edit_times_dlg(self.meet.window, stxt=st, ftxt=ft)
        if ret[0] == 1:
            self.set_start(ret[1])
            self.set_finish(ret[2])
            self.log.info('Adjusted race times.')

    def editcol_cb(self, cell, path, new_text, col):
        """Edit column callback."""
        new_text = new_text.strip()
        self.riders[path][col] = new_text

    def editlap_cb(self, cell, path, new_text, col):
        """Edit the lap field if valid."""
        new_text = new_text.strip()
        if new_text.isdigit():
            self.riders[path][col] = int(new_text)
        else:
            self.log.error('Invalid lap count.')

    def resetplaces(self):
        """Clear places off all riders."""
        for r in self.riders:
            r[COL_PLACE] = ''
        self.bonuses = {}	# bonuses are global to stage
        for c in self.tallys:	# points are grouped by tally
            self.points[c] = {}
            self.pointscb[c] = {}
            
    def sortrough(self, x, y):
        # aux cols: ind, bib, in, place, rftime, laps, lastpass
        #             0    1   2      3       4     5         6
        if x[2] != y[2]:		# in the race?
            if x[2]:
                return -1
            else:
                return 1
        else:
            if x[3] != y[3]:		# places not same?
                if y[3] == '':
                    return -1
                elif x[3] == '':
                    return 1
                if int(x[3]) < int(y[3]):
                    return -1
                else:
                    return 1
            else:
                if x[4] == y[4]:	# same time?
                    if x[5] == y[5]:	# same laps?
                        return cmp(x[6], y[6])	# oldest pass on same lap
                    else:
                        if x[5] > y[5]:
                            return -1
                        else:
                            return 1
                else:
                    if y[4] is None:
                        return -1
                    elif x[4] is None:
                        return 1
                    elif x[4] < y[4]:
                        return -1
                    else:
                        return 1
        return 0

    # do final sort on manual places then manual bunch entries
    def sortvbunch(self, x, y):
        # aux cols: ind, bib, in, place, vbunch, comment
        #             0    1   2      3       4        5
        if x[2] != y[2]:		# IN compares differently,
            if x[2]:			# return in rider first
                return -1
            else:
                return 1
        else:
            if x[2]:			# in the race...
                if x[3] != y[3]:		# places not same?
                    if y[3] == '':
                        return -1
                    elif x[3] == '':
                        return 1
                    if int(x[3]) < int(y[3]):
                        return -1
                    else:
                        return 1
                else:
                    if x[4] == y[4]:	# same time?
                        return 0
                    else:
                        if y[4] is None:
                            return -1
                        elif x[4] is None:
                            return 1
                        elif x[4] < y[4]:
                            return -1
                        else:
                            return 1
            else:			# not in the race
                if x[5] != y[5]:
                    return strops.cmp_dnf(x[5], y[5]) # sort by code
                else:
                    return cmp(strops.riderno_key(x[1]), 
                               strops.riderno_key(y[1])) # sort on no
        return 0

    def vbunch(self, cbunch=None, mbunch=None):
        """Switch to return best choice bunch time."""
        ret = None
        if mbunch is not None:
            ret = mbunch
        elif cbunch is not None:
            ret = cbunch
        return ret

    def showstart_cb(self, col, cr, model, iter, data=None):
        """Draw start time offset in rider view."""
        st = model.get_value(iter, COL_STOFT)
        otxt = ''
        if st is not None:
            otxt = st.rawtime(0)
        else:
            rcat = self.ridercat(model.get_value(iter, COL_CAT))
            if rcat in self.catstarts and self.catstarts[rcat] is not None:
                otxt = self.catstarts[rcat].rawtime(0)
        cr.set_property('text', otxt)

    def edit_event_properties(self, window, data=None):
        """Edit race specifics."""
        self.log.info(u'going on prop ' + repr(window))
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, u'generic_props.ui'))
        dlg = b.get_object('properties')
        dlg.set_transient_for(window)
        tbl = b.get_object('props_table')
###
        rows = len(DEFAULT_COLUMN_ORDER)
        tbl.resize(rows, 3)
        entmap = {}
        rof = 0
        for i in DEFAULT_COLUMN_ORDER:
            prompt = EVENT_COLUMNS[i]
            ne = uiutil.mktextentry(prompt, rof, tbl)
            ne.set_text(unicode(ref[i]))
            #if i == u'evid':
                #ne.set_sensitive(False)
            entmap[i] = ne
            rof += 1
        response = dlg.run()

        if response == 1:       # id 1 set in glade for "Apply"
            self.log.info(u'Updating event.')
            for i in DEFAULT_COLUMN_ORDER:
                nv = entmap[i].get_text().decode('utf-8', 'ignore')
                if i in EVENT_COLUMN_CONVERTERS:
                    nv = EVENT_COLUMN_CONVERTERS[i](nv)
                ov = ref[i]
                if ov != nv:
                    ref.set_value(i, nv)        # update without notify
                    do_notify = True
        else:
            self.log.info(u'Edit event cancelled.')
        dlg.destroy()

    def showbunch_cb(self, col, cr, model, iter, data=None):
        """Update bunch time on rider view."""
        cb = model.get_value(iter, COL_CBUNCH)
        mb = model.get_value(iter, COL_MBUNCH)
        if mb is not None:
            cr.set_property('text', mb.rawtime(0))
            cr.set_property('style', pango.STYLE_OBLIQUE)
        else:
            cr.set_property('style', pango.STYLE_NORMAL)
            if cb is not None:
                cr.set_property('text', cb.rawtime(0))
            else:
                seen = model.get_value(iter, COL_RFSEEN)
                if len(seen) > 0:
                    if self.start:
                        et = seen[-1] - self.start
                    else:
                        et = seen[-1]
                    cr.set_property('text', u'[' + et.rawtime(1) + u']')
                    cr.set_property('style', pango.STYLE_OBLIQUE)
                else:
                    cr.set_property('text', '')

    def editstart_cb(self, cell, path, new_text, col=None):
        """Edit start time on rider view."""
        newst = tod.str2tod(new_text)
        if newst:
            newst = newst.truncate(0)
        self.riders[path][COL_STOFT] = newst

    def editbunch_cb(self, cell, path, new_text, col=None):
        """Edit bunch time on rider view."""
        # NOTE: This is the cascading bunch time editor, 
        new_text = new_text.strip()
        dorecalc = False
        if new_text == '':	# user request to clear RFTIME?
            self.riders[path][COL_RFTIME] = None
            self.riders[path][COL_MBUNCH] = None
            self.riders[path][COL_CBUNCH] = None
            dorecalc = True
        else:
            # get 'current bunch time'
            omb = self.vbunch(self.riders[path][COL_CBUNCH],
                              self.riders[path][COL_MBUNCH])

            # assign new bunch time
            nmb = None
            if u'+' in new_text:	# assume a down time
                oft = tod.ZERO 
                if self.winbunch is not None:
                    oft = self.winbunch
                nmb = tod.str2tod(new_text.replace(u'+',''))
                if nmb is not None:
                    nmb += oft
            else:
                nmb = tod.str2tod(new_text)
            if self.riders[path][COL_MBUNCH] != nmb:
                self.riders[path][COL_MBUNCH] = nmb
                dorecalc = True
            if nmb is not None:
                i = int(path)+1
                tl = len (self.riders)
                # until next rider has mbunch set OR place clear assign new bt
                while i < tl:
                    ivb = self.vbunch(self.riders[i][COL_CBUNCH], 
                                      self.riders[i][COL_MBUNCH])
                    if (self.riders[i][COL_PLACE] != ''
                          and (ivb is None
                              or ivb == omb)):
                        self.riders[i][COL_MBUNCH] = nmb
                        dorecalc = True
                    else:
                        break
                    i += 1
        if dorecalc:
            self.recalculate()
    
    def checkplaces(self, rlist='', dnf=True):
        """Check the proposed places against current race model."""
        ret = True
        placeset = set()
        for no in strops.reformat_biblist(rlist).split():
            if no != 'x':
                # repetition? - already in place set?
                if no in placeset:
                    self.log.error('Duplicate no in places: ' + repr(no))
                    ret = False
                placeset.add(no)
                # rider in the model?
                lr = self.getrider(no)
                if lr is None:
                    self.log.error('Non-starter in places: ' + repr(no))
                    ret = False
                else:
                    # rider still in the race?
                    if not lr[COL_INRACE]:
                        self.log.info('DNF/DNS rider in places: ' + repr(no))
                        if dnf:
                            ret = False
            else:
                # placeholder needs to be filled in later or left off
                self.log.info('Placeholder in places.')
        return ret

    def recalculate(self):
        """Recalculator, acquires lock and then continues."""
        if not self.recalclock.acquire(False):
            self.log.warn('Recalculate already in progress.')
            return None	# allow only one entry
        try:
            self.__recalc()
        finally:
            self.recalclock.release()

    def get_ridercat(self, bib):
        """Return the primary category for the specified rider."""
        ret = None	# unknown
        r = self.getrider(bib)
        if r is not None:
            ret = self.ridercat(r[COL_CAT].decode('utf-8').upper())
        return ret

    def get_cat_placesr(self, cat):
        """Return a normalised place str for a cat within main places."""
        placestr = self.places
        pgroups = []
        lp = None
        ng = []
        for placegroup in placestr.split():
            cg = []
            for bib in placegroup.split('-'):
                if self.get_ridercat(bib) == cat:
                    cg.append(bib)
            if len(cg) > 0:	# >= one cat rider in this group 
                pgroups.append(u'-'.join(cg))

        #for r in self.result_gen(cat):
            #if type(r[0]) is not int:
                #pgroups.append(u'-'.join(ng))
                #break
            #if lp != r[0]:	# new place group
                #if len(ng) > 0:
                    #pgroups.append(u'-'.join(ng))
                #ng = []
            #ng.append(r[1]) # same place group
            #lp = r[0]
            
        ret = u' '.join(pgroups)
        self.log.debug('Cat ' + repr(cat) + ' finish: ' + repr(ret))
        return ret

    def assign_finish(self):
        """Transfer finish line places into rider model."""
        placestr = self.places
        placeset = set()
        idx = 0
        for placegroup in placestr.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is None:
                        self.addrider(bib)
                        r = self.getrider(bib)
                    if r[COL_INRACE]:
                        idx += 1
                        r[COL_PLACE] = str(curplace)
                    else:
                        self.log.warn('DNF Rider ' + str(bib)
                                       + ' in finish places.')
                else:
                    self.log.warn('Duplicate no. = ' + str(bib) +
                                   ' in finish places.')

    def assign_places(self, contest):
        """Transfer points and bonuses into the named contest."""
        # fetch context meta infos
        src = self.contestmap[contest]['source']
        if src not in RESERVED_SOURCES and src not in self.intermeds:
            self.log.info('Invalid intermediate source: ' + repr(src)
                           + ' in contest: ' + repr(contest))
            return
        countbackwinner = False	# for stage finish only track winner in cb
        category = self.contestmap[contest]['category']
        tally = self.contestmap[contest]['tally']
        bonuses = self.contestmap[contest]['bonuses']
        points = self.contestmap[contest]['points']
        allsrc = self.contestmap[contest]['all_source']
        allpts = 0
        allbonus = tod.ZERO
        if allsrc:
            if len(points) > 0:
                allpts = points[0]
            if len(bonuses) > 0:
                allbonus = bonuses[0]
        placestr = ''
        if src == 'fin':
            placestr = self.places
            if tally in [u'sprint', u'crit']:	# really only for sprints/crits
                countbackwinner = True
        elif src == 'reg':
            placestr = self.get_startlist()
        elif src == 'start':
            placestr = self.get_starters()
        elif src in self.catplaces: # ERROR -> cat climb tally needs type?
            placestr = self.get_cat_placesr(self.catplaces[src])
            countbackwinner = True
        else:
            placestr = self.intermap[src]['places']
        placeset = set()
        idx = 0
        for placegroup in placestr.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is None:
                        self.addrider(bib)
                        r = self.getrider(bib)
                    idx += 1
                    if allsrc:	# all listed places get same pts/bonus..
                        if allbonus is not tod.ZERO:
                            if bib in self.bonuses:
                                self.bonuses[bib] += allbonus 
                            else:
                                self.bonuses[bib] = allbonus
                        if tally and tally in self.points and allpts != 0:
                            if bib in self.points[tally]:
                                self.points[tally][bib] += allpts
                            else:
                                self.points[tally][bib] = allpts
                                self.pointscb[tally][bib] = strops.countback()
                            # No countback for all_source entries
                    else:	# points/bonus as per config
                        if len(bonuses) >= curplace:	# bonus is vector
                            if bib in self.bonuses:
                                self.bonuses[bib] += bonuses[curplace-1]
                            else:
                                self.bonuses[bib] = bonuses[curplace-1]
                        if tally and tally in self.points:
                            if len(points) >= curplace: # points vector
                                if bib in self.points[tally]:
                                    self.points[tally][bib] += points[curplace-1]
                                else:
                                    self.points[tally][bib] = points[curplace-1]
                            if bib not in self.pointscb[tally]:
                                self.pointscb[tally][bib] = strops.countback()
                            if countbackwinner:	# stage finish
                                if curplace == 1:	# winner only at finish
                                    self.pointscb[tally][bib][0] += 1
                            else:			# intermediate/other
                                if tally == u'climb': # climbs countback on category winners only
                                    if curplace == 1:
                                        self.pointscb[tally][bib][category] += 1
                                else:
                                    self.pointscb[tally][bib][curplace] += 1
                else:
                    self.log.warn('Duplicate no. = ' + str(bib) + ' in '
                                    + repr(contest) + ' places.')


    def decode_limit(self, limitstr, elap=None):
        """Decode a limit and finish time into raw bunch time."""
        ret = None
        if limitstr:
            limit = None
            down = False
            if u'+' in limitstr:
                down = True
                limitstr = limitstr.replace(u'+',u'')
            if u'%' in limitstr:
                down = True
                if elap is not None:
                    try:
                        frac = 0.01*float(limitstr.replace(u'%',u''))
                        limit = tod.tod(int(frac * float(elap.as_seconds())))
                    except:
                        pass
            else:	# assume tod without sanity check
                limit = tod.str2tod(limitstr)
                if limit is not None:
                    if elap is not None and limit < elap:
                        down = True # assume a time less than winner is down
                    else:    # assume raw bunch time, ignore elap
                        pass

            # assign limit discovered above, if possible
            if limit is not None:
                if down:
                    if elap is not None:
                        ret = elap + limit	# down time on finish
                        ret = ret.truncate(0)
                else:
                    ret = limit.truncate(0)	# raw bunch time
            if ret is None:
                self.log.warn(u'Unable to decode time limit: '
                              + repr(limitstr))
        return ret

    def __recalc(self):
        """Internal 'protected' recalculate function."""
        # if readonly and calc set - skip recalc
        if self.readonly and self.calcset:
            self.log.debug('Cached Recalculate.')
            return False

        self.log.debug('Recalculate model.')
        # pass one: clear off old places and bonuses
        self.resetplaces()

        # pass two: assign places
        self.assign_finish()
        for c in self.contests:
            self.assign_places(c)

        # pass three: do rough sort on in, place, rftime -> existing
        auxtbl = []
        idx = 0
        for r in self.riders:
            lastpass = tod.ZERO	# earliest?
            if len(r[COL_RFSEEN]) > 0:
                lastpass = r[COL_RFSEEN][-1]
            # aux cols: ind, bib, in, place, rftime, laps, last passing
            auxtbl.append([idx, r[COL_BIB], r[COL_INRACE], r[COL_PLACE],
                           r[COL_RFTIME], r[COL_LAPS], lastpass])
            idx += 1
        if len(auxtbl) > 1:
            auxtbl.sort(self.sortrough)
            self.riders.reorder([a[0] for a in auxtbl])

        # pass four: compute cbunch values on auto time gaps and manual inputs
        #            At this point all riders are assumed to be in finish order
        self.maxfinish = tod.ZERO
        racefinish = None
        ft = None	# the finish or first bunch time
        lt = None	# the rftime of last competitor across line
        bt = None	# the 'current' bunch time
        if self.start is not None:
            for r in self.riders:
                if r[COL_INRACE] or r[COL_COMMENT] == u'otl':
                    if r[COL_MBUNCH] is not None:
                        bt = r[COL_MBUNCH]	# override with manual bunch
                        r[COL_CBUNCH] = bt
                        if ft is None:
                            ft = bt
                        lt = bt	# problematic for gaps, but only on
                                        # manual override anyhow
                    elif r[COL_RFTIME] is not None:
                        # establish elapsed, but allow subsequent override
                        if r[COL_RFTIME] > self.maxfinish:
                            self.maxfinish = r[COL_RFTIME]
                        et = r[COL_RFTIME] - self.start
    
                        # establish bunch time
                        if ft is None:
                            racefinish = r[COL_RFTIME] # save race finish
                            ft = et.truncate(0)	# compute first time
                            bt = ft
                        else:
                            if et < lt or et - lt < self.gapthresh: #NTG!
                                # same time
                                pass
                            else:
                                bt = et.truncate(0)

                        # assign and continue
                        r[COL_CBUNCH] = bt
                        lt = et
                    else:
                        # empty rftime with non-empty rank implies no time gap
                        if r[COL_PLACE] != '':
                            r[COL_CBUNCH] = bt	# use current bunch time
                        else: r[COL_CBUNCH] = None
                
        # if racefinish defined, call set finish
        if racefinish:
            self.set_finish(racefinish)

        # pass five: resort on in,vbunch (todo -> check if place cmp reqd)
        #            at this point all riders will have valid bunch time
        auxtbl = []
        idx = 0
        for r in self.riders:
            # aux cols: ind, bib, in, place, vbunch
            auxtbl.append([idx, r[COL_BIB], r[COL_INRACE], r[COL_PLACE],
                           self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH]),
                           r[COL_COMMENT]])
            idx += 1
        if len(auxtbl) > 1:
            auxtbl.sort(self.sortvbunch)
            self.riders.reorder([a[0] for a in auxtbl])

        # Pass six - Scan model to determine racestat and time limits
        if self.timerstat != 'idle':
            limit = None
            if ft is not None and self.timelimit is not None:
                limit = self.decode_limit(self.timelimit, ft)
                if limit is not None:
                    self.log.info(u'Time limit: ' + self.timelimit 
                                   + u' = ' + limit.rawtime(0)
                                   + u', +' + (limit-ft).rawtime(0))
                    # and export to announce
                    self.meet.announce_cmd(u'timelimit', limit.rawtime(0))
            tot = 0
            placed = 0
            handled = 0
            ft = None
            for r in self.riders:
                tot += 1
                if r[COL_INRACE]:
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if ft is None:
                        ft = bt
                    if r[COL_PLACE]:
                        placed += 1
                        handled += 1
                    else:
                        if limit is not None:
                            if bt > limit:
                                r[COL_COMMENT] = u'otl'
                            else:	# and clear if not
                                if r[COL_COMMENT] == u'otl':
                                    r[COL_COMMENT] = u''
                else:
                    handled += 1
            if ft is not None:
                self.winbunch = ft
            if self.timerstat == 'finished' or handled == tot:
                self.racestat = u'final'
            else:
                if placed >= 10 or (placed > 0 and tot < 20):
                    self.racestat = u'provisional'
                else:
                    self.racestat = u'virtual'
        else:
            self.racestat = u'prerace'
        self.log.debug(u'Race status: ' + self.racestat)
        self.calcset = True
        return False	# allow idle add

    def new_start_trigger(self, rfid):
        """Collect a RFID trigger signal and apply it to the model."""
        if self.newstartdlg is not None and self.newstartent is not None:
            et = tod.str2tod(self.newstartent.get_text())
            if et is not None:
                st = rfid - et
                self.set_start(st)
                self.newstartdlg.response(1)
                self.newstartdlg = None	# try to ignore the 'up' impulse
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

    def time_context_menu(self, widget, event, data=None):
        """Popup menu for result list."""
        self.context_menu.popup(None, None, None, event.button,
                                event.time, selpath)

    def treerow_selected(self, treeview, path, view_column, data=None):
        """Select row, DANGER - only confirm selected place!"""
        # filter on running/armfinish
        if self.timerstat not in ['idle', 'armstart', 'finished']:
            if self.finish is not None:
                rbib = self.riders[path][COL_BIB]
                self.log.info(u'Confirmed next place by tree selection to: '
                                + repr(rbib) + u'/' + repr(path))
                self.append_selected_place()
                #self.fill_places_to(rbib)

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

    def rms_context_swap_activate_cb(self, menuitem, data=None):
        """Swap a spare bike out with another rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            srcbib = self.riders.get_value(sel[1], COL_BIB)

        self.log.info(u'Swap rider: ' + srcbib)

    def rms_context_edit_activate_cb(self, menuitem, data=None):
        """Edit rider start/finish/etc."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            stx = u''
            ftx = u''
            btx = u''
            ptx = u''
            st = self.riders.get_value(sel[1], COL_STOFT)
            ft = self.riders.get_value(sel[1], COL_RFTIME)
            bt = self.riders.get_value(sel[1], COL_BONUS)
            pt = self.riders.get_value(sel[1], COL_PENALTY)
            if st:
                stx = st.rawtime()
            if ft:
                ftx = ft.rawtime()
            if bt:
                btx = bt.rawtime()
            if pt:
                ptx = pt.rawtime()
            tvec = uiutil.edit_times_dlg(self.meet.window,
                                         stx, ftx, btx, ptx,
                                         True, True) # enable bon+pen
            if len(tvec) > 4 and tvec[0] == 1:
                self.riders.set_value(sel[1], COL_STOFT,
                                              tod.str2tod(tvec[1]))
                self.riders.set_value(sel[1], COL_RFTIME,
                                              tod.str2tod(tvec[2]))
                self.riders.set_value(sel[1], COL_BONUS,
                                              tod.str2tod(tvec[3]))
                self.riders.set_value(sel[1], COL_PENALTY,
                                              tod.str2tod(tvec[4]))
                bib = self.riders.get_value(sel[1], COL_BIB)
                nst = u'-'
                st = self.riders.get_value(sel[1], COL_STOFT)
                if st:
                    nst = st.rawtime(0)
                nft = u'-'
                ft = self.riders.get_value(sel[1], COL_RFTIME)
                if ft:
                    nft = ft.rawtime(2)
                self.log.info(u'Adjust rider ' + bib + u' start: '
                  + nst
                  + u' raw finish: '
                  + nft)
                self.recalculate()

    def rms_context_clear_activate_cb(self, menuitem, data=None):
        """Clear finish time for selected rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            self.log.info(u'Clear raw finish time for: ' + repr(selbib))
            self.riders.set_value(i, COL_RFTIME, None)
            self.riders.set_value(i, COL_MBUNCH, None)
            self.recalculate()

    def rms_context_del_activate_cb(self, menuitem, data=None):
        """Remove selected rider from event."""
        sel = self.view.get_selection().get_selected()
        bib = None
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            self.log.info(u'Delete rider: ' + repr(selbib))
            self.delrider(selbib)

    def rms_context_refinish_activate_cb(self, menuitem, data=None):
        """Try to automatically re-finish rider from last passing."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            splits = self.riders.get_value(i, COL_RFSEEN)
            if splits is not None and len(splits) > 0:
                nf = splits[-1]
                self.log.info(u'Set raw finish for rider '
                              + repr(selbib) + ' to last passing: '
                              + nf.rawtime(2))
                self.riders.set_value(i, COL_RFTIME, nf)
                self.recalculate()

    def __init__(self, meet, event, ui=True):
        self.meet = meet
        self.event = event
        self.evno = event[u'evid']
        self.series = event[u'seri']
        self.configpath = meet.event_configfile(self.evno)

        self.log = logging.getLogger('roadrace')
        self.log.setLevel(logging.DEBUG)
        self.log.debug(u'opening event: ' + unicode(self.evno))

        self.recalclock = threading.Lock()
        self.__dorecalc = False

        # race property attributes

        # race run time attributes
        self.readonly = not ui
        self.calcset = False
        self.start = None
        self.finish = None
        self.maxfinish = None
        self.winbunch = None	# bunch time of winner (overall race time)
        self.winopen = True
        self.timerstat = 'idle'
        self.racestat = u'prerace'
        self.onestart = False
        self.places = ''
        self.laptimes = []
        self.comment = []
        self.cats = []
        self.passingsource = []	# list of decoders we accept passings from
        self.catlaps = {}	# cache of cat lap counts
        self.catstarts = {}	# cache of cat start times
        self.catplaces = {}
        self.autocats = False
        self.bonuses = {}
        self.points = {}
        self.pointscb = {}
        self.dofastestlap = False
        self.autoexport = False
        self.timelimit = None
        self.preloop = None
        self.passlabels = {}	# sector labels for mult passings
        self.catonlap = {}	# onlap per category
        self.gapthresh = GAPTHRESH	# time gap to set new time
					# NOTE: .12 usually added to account
					# for front wheel measurements

        # intermediates
        self.intermeds = []	# sorted list of intermediate keys
        self.intermap = {}	# map of intermediate keys to results
        self.contests = []	# sorted list of contests
        self.contestmap = {}	# map of contest keys
        self.tallys = []	# sorted list of points tallys
        self.tallymap = {}	# map of tally keys

        # Scratch pad status variables - check if needed?
        self.last_scratch = None
        self.scratch_start = None
        self.scratch_last = None
        self.scratch_count = 0
        self.scratch_tot = 0
        self.curlap = -1
        self.onlap = 1
        self.sprintlaps = []
        self.totlaps = None
        self.lapstart = None
        self.lapfin = None
        self.lapmap = {}	# temp lap tracker
        self.minlap = None	# minimum lap/elap time if relevant
 
        # lap tracking
        self.scratch_map = {}
        self.scratch_ord = []
        self.live_announce = True

        # new start dialog
        self.newstartent = None
        self.newstartdlg = None

        self.riders = gtk.ListStore(gobject.TYPE_STRING, # BIB = 0
                                    gobject.TYPE_STRING, # NAMESTR = 1
                                    gobject.TYPE_STRING, # SHORTNAME = 2
                                    gobject.TYPE_STRING, # CAT = 3
                                    gobject.TYPE_STRING, # COMMENT = 4
                                    gobject.TYPE_BOOLEAN, # INRACE = 5
                                    gobject.TYPE_STRING,  # PLACE = 5
                                    gobject.TYPE_INT,  # LAP COUNT = 7
                                    gobject.TYPE_PYOBJECT, # RFTIME = 8
                                    gobject.TYPE_PYOBJECT, # CBUNCH = 9
                                    gobject.TYPE_PYOBJECT, # MBUNCH =10 
                                    gobject.TYPE_PYOBJECT, # STOFT = 11
                                    gobject.TYPE_PYOBJECT, # BONUS = 12
                                    gobject.TYPE_PYOBJECT, # PENALTY = 13
                                    gobject.TYPE_PYOBJECT, # PREPASS = 14
                                    gobject.TYPE_PYOBJECT) # RFSEEN = 15
        self.undomod = gtk.ListStore(gobject.TYPE_STRING, # BIB = 0
                                    gobject.TYPE_STRING, # NAMESTR = 1
                                    gobject.TYPE_STRING, # SHORTNAME = 2
                                    gobject.TYPE_STRING, # CAT = 3
                                    gobject.TYPE_STRING, # COMMENT = 4
                                    gobject.TYPE_BOOLEAN, # INRACE = 5
                                    gobject.TYPE_STRING,  # PLACE = 6
                                    gobject.TYPE_INT,  # LAP COUNT = 7
                                    gobject.TYPE_PYOBJECT, # RFTIME = 8
                                    gobject.TYPE_PYOBJECT, # CBUNCH = 9
                                    gobject.TYPE_PYOBJECT, # MBUNCH = 10
                                    gobject.TYPE_PYOBJECT, # STOFT = 11
                                    gobject.TYPE_PYOBJECT, # BONUS = 12
                                    gobject.TYPE_PYOBJECT, # PENALTY = 13
                                    gobject.TYPE_PYOBJECT, # PREPASS = 14
                                    gobject.TYPE_PYOBJECT) # RFSEEN = 15
        self.canundo = False
        self.placeundo = None

        # !! does this need a builder? perhaps make directly...
        b = gtk.Builder()
        b.add_from_file(os.path.join(metarace.UI_PATH, 'rms.ui'))

        # !! destroy??
        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # meta info pane
        self.shortname = None
        self.title_namestr = b.get_object('title_namestr')
        self.set_titlestr()
        self.elaplbl = b.get_object('time_lbl')
        #self.elaplbl.modify_font(pango.FontDescription("monospace bold"))
        self.lapentry = b.get_object('lapentry')
        self.totlapentry = b.get_object('totlapentry')

        # results pane
        t = gtk.TreeView(self.riders)
        t.set_reorderable(True)
        t.set_rules_hint(True)
        t.show()
        self.view = t
        uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
        uiutil.mkviewcoltxt(t, 'Rider', COL_NAMESTR, expand=True,maxwidth=500)
        uiutil.mkviewcoltxt(t, 'Cat', COL_CAT)
        uiutil.mkviewcoltxt(t, 'Com', COL_COMMENT, cb=self.editcol_cb)
        uiutil.mkviewcolbool(t, 'In', COL_INRACE, width=50)
        uiutil.mkviewcoltxt(t, 'Lap', COL_LAPS, width=40, cb=self.editlap_cb)
        uiutil.mkviewcoltod(t, 'Start', cb=self.showstart_cb, width=50,
                                editcb=self.editstart_cb)
        uiutil.mkviewcoltod(t, 'Bunch', cb=self.showbunch_cb,
                                editcb=self.editbunch_cb,
                                width=50)
        uiutil.mkviewcoltxt(t, 'Place', COL_PLACE, calign=0.5, width=50)
        b.get_object('race_result_win').add(t)
        self.context_menu = None

        if ui:
            # connect signal handlers
            b.connect_signals(self)
            b = gtk.Builder()
            b.add_from_file(os.path.join(metarace.UI_PATH, 'rms_context.ui'))
            self.context_menu = b.get_object('rms_context')
            self.view.connect('button_press_event', self.treeview_button_press)
            self.view.connect('row-activated', self.treerow_selected)
            b.connect_signals(self)
            self.meet.timer.setcb(self.rfidtrig)
            self.meet.alttimer.setcb(self.timertrig)
