
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

"""VOLA Display Console"""

import pygtk
pygtk.require("2.0")

import gtk
import glib

import os
import sys
import logging
import serial

import metarace

from metarace import jsonconfig
from metarace import tod
from metarace import telegraph
from metarace import unt4	## todo remove
from metarace import strops

LOGHANDLER_LEVEL = logging.DEBUG
CONFIGFILE = u'voladisp.json'
LOGFILE = u'voladisp.log'
APP_ID = u'voladisp_1.0'  # configuration versioning
ENCODING = 'iso8859-15'
VOLA_BAUD = 9600

STX=0x02
LF=0x0a
BRIGHTVALS = [0x31, 0x32, 0x33]
ELAPOFT = tod.tod('0.05')

class voladisp:
    """VOLA Display control console application."""

    def quit_cb(self, menuitem, data=None):
        """Quit the application."""
        self.running = False

    def uscb_activate_cb(self, menuitem, data=None):
        """Request a re-connect to the uSCBsrv IRC connection."""
        if self.uscbport:
            self.log.info(u'Requesting re-connect to announcer: ' + 
                           repr(self.uscbport) + repr(self.uscbchan))
        else:
            self.log.info(u'Announcer not configured.')
            # but still call into uscbsrv...
        self.scb.set_portstr(portstr=self.uscbport,
                             channel=self.uscbchan,
                             force=True)

    def timeout(self):
        """Update status."""
        # 1: Terminate?
        if not self.running:
            return False
        # 2: Process?
        try:
            ntime = tod.tod(u'now')
            ntod = ntime.truncate(0)
            if ntime >= self.nc.truncate(1):
                self.tod = ntod
                self.process_timeout()
                # and advance one second
                self.nc += tod.ONE
            else:
                self.log.debug(u'Timeout called early: ' + ntime.rawtime())
                # no need to advance, desired timeout not yet reached
        except Exception as e:
            self.log.error(u'Timeout: ' + repr(e))

        # 3: Re-Schedule
        tt = tod.tod(u'now')+tod.tod(u'0.01')
        count = 0
        while self.nc < tt:     # ensure interval is positive
            if tod.MAX - tt < tod.ONE:
                self.log.debug(u'Midnight rollover.')
                break
            self.log.debug(u'May have missed an interval, catching up.')
            self.nc += tod.ONE  # 0.01 allows for processing delay
            count += 1
            if count > 10:
                break
        ival = int(1000.0 * float((self.nc - tod.tod(u'now')).timeval))
        if ival < 0 or ival > 10000:
            ival = 1000	# assume host time change
        glib.timeout_add(ival, self.timeout)

        # 4: Return False
        return False    # must return False

    def process_timeout(self):
        """Perform required timeout activities."""
        # Check run state variables iff remote on:
        if self.remote_enable:
            if self.elapstart is not None:
                estr = u''
                dstr = u''
                if self.elapfin is not None:
                    # Race over - show elap and down if poss
                    elap = (self.elapfin - self.elapstart).truncate(0)
                    estr = elap.rawtime(0)
                    if self.timerstat != u'finished':
                        if self.tod > self.elapfin:
                            downtod = self.tod - self.elapfin
                            target = self.maxlaptime
                            if self.timelimit is not None:
                                target = (self.timelimit 
                                          - elap) + tod.tod(u'30')
                            if downtod > tod.ZERO:
                                if downtod < target:
                                #if downtod < target:
                                    dstr = u'+' + downtod.rawtime(0)
                            else: 
                                dstr = u'+0'	# prepare for arrival
                        else:
                            dstr = u'+0'	# prepare for arrival
                else:
                    # race in progress, show run time and distance or lap
                    elap = self.tod - (self.elapstart+ELAPOFT)
                    if elap > tod.MAXELAP:
                        elap = tod.ZERO
                    estr = elap.rawtime(0)
                    if self.distance is not None and self.distance > 1.9:
                        dstr = u'{0:1.1f}km'.format(self.distance)
                    if self.lapfin is not None:
                        # lap down time overwrites dist, but only if valid
                        laptm = self.tod - self.lapfin
                        if laptm > tod.ZERO and laptm < self.maxlaptime:
                            dstr = u'+' + laptm.rawtime(0)
                    elif self.timerstat == u'armfinish':
                        dstr = u'+0'
                self.set_left(estr)
                self.set_right(dstr)
            elif self.ttno:
                nostr = u''
                if self.ttno:
                    nostr = self.ttno
                nostr = nostr.rjust(4)
                #nmstr = u''
                #if self.ttname:
                    #nmstr = self.ttname
                #nmstr = nmstr.ljust(12)
                #topline = nostr + u' ' + nmstr
                rstr = u''
                if self.ttrank:
                    rstr = u'(' + self.ttrank + u')' + ' '
                rstr = rstr.ljust(6)
                tstr = u''
                if self.tttime:
                    tstr = self.tttime
                    if u'.' not in tstr:
                        tstr += '   '
                tstr = tstr.rjust(10)
                self.set_right(tstr)	# HhMM:SS.DC
                self.set_left(rstr+nostr)	# (rr)  nnn
            elif self.timeofday:
                self.set_left(u'')
                dstr = u''
                if self.distance is not None and self.distance > 1.9:
                    dstr = u'{0:1.1f}km'.format(self.distance)
                else:
                    dstr = self.tod.meridian(secs=False)
                self.set_right(dstr)

        # check connection status
        if not self.remote.connected():
            self.failcount += 1
            if self.failcount > self.failthresh:
                self.remote.set_portstr(force=True)
                self.failcount = 0
            self.log.debug(u'Telegraph connection failed, count = '
                           + repr(self.failcount))
        else:
            self.failcount = 0

    def app_destroy_cb(self, window, msg=u''):
        """Handle destroy signal and exit application."""
        self.log.removeHandler(self.sh)
        self.log.info(u'App destroyed. ' + msg)
        glib.idle_add(self.app_destroy_handler)

    def app_destroy_handler(self):
        if self.started:
            self.shutdown()	# threads are joined in shutdown
        # close event and remove main log handler
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
        self.running = False
        # flag quit in main loop
        gtk.main_quit()
        return False

    def shutdown(self, msg=''):
        """Shutdown threads and close application."""
        self.started = False
        self.remote.exit(msg)
        self.close_display()
        print (u'Waiting for remote to exit...')
        self.remote.join()

    def start(self):
        if not self.started:
            self.log.debug(u'App startup.')
            self.remote.start()
            self.started = True

    def set_left(self, msg):
        msg = strops.truncpad(msg, 8, align='r', elipsis=False)
        self.obuf[0] = msg
        self.setline(0)

    def set_right(self, msg):
        msg = strops.truncpad(msg, 8, align='r', elipsis=False)
        self.obuf[1] = msg
        self.setline(1)
    
    def set_remote_enable(self):
        self.clearlines()	# refresh cache
        self.remote_enable = True

    def reset(self):
        """Reset run state."""
        self.elapstart = None
        self.elapfin = None
        self.timerstat = u'running'	# default assume run state
        self.distance = None
        self.lapfin = None

    def serialwrite(self, cmd):
        """Output command blocking."""
        try:
            if self.scb:
                self.scb.write(cmd)
        except Exception as e:
            self.log.error(u'Writing to scoreboard: ' + repr(e))

    def set_brightness(self, val):
        """Update local brightness."""
        nv = 0x31
        try:
            nv = int(val)
            if nv > 0 and nv < 4:
                nv = 0x30 + nv  # specified as 1, 2, 3
        except:
            self.log.info('Ignored invalid brightness: ' + repr(val))
        if nv in BRIGHTVALS:
            self.brightness = nv
        else:
            self.brightness = 0x31

    def remote_cb(self, cmd, nick, chan):
        """Handle unt message from remote (in main loop)."""
        if self.remoteuser and self.remoteuser.lower() != nick.lower():
            return False

        if cmd.header == u'start':
            self.elapstart = tod.str2tod(cmd.text)
        elif cmd.header == u'finish':
            self.elapfin = tod.str2tod(cmd.text)
        elif cmd.header == u'redraw' and cmd.text == u'timer':
            # fake a timeout in the cb
            self.process_timeout()
        elif cmd.header == u'brightness':
            self.set_brightness(cmd.text)
        elif cmd.header == u'lapfin':
            self.lapfin = tod.str2tod(cmd.text)
        elif cmd.header == u'finpanel':
            fpvec = cmd.text.split(unichr(unt4.US))
            self.ttrank = None
            self.ttno = None
            self.ttname = None
            self.ttcat = None
            self.tttime = None
            if len(fpvec) > 4:  # rank/no/name/cat/time
                if fpvec[0]:
                    self.ttrank = fpvec[0]
                if fpvec[1]:
                    self.ttno = fpvec[1]
                if fpvec[2]:
                    self.ttname = fpvec[2]
                if fpvec[3]:
                    self.ttcat = fpvec[3]
                if fpvec[4]:
                    self.tttime = fpvec[4]
        elif cmd.header == u'distance':
            self.distance = None
            try:
                a = float(cmd.text)
                if a > 0.1:
                    self.distance = a
            except:
                self.log.debug(u'Invalid distance: ' + repr(cmd.text))
        elif cmd.header == u'timerstat':
            self.timerstat = cmd.text
        elif cmd.header == u'timelimit':
            self.timelimit = tod.str2tod(cmd.text)
        elif cmd.header == u'clearall':
            self.reset()
            self.clearlines()
        elif cmd.erp:	# general clearing
            self.reset()
        return False

    def setline(self, line):
        """Copy line to display."""
        cmd = (chr(STX) + chr(0x31 + line) + chr(self.brightness)
                + self.obuf[line].encode(ENCODING,'replace') + chr(LF))
        #print(repr(line)+u'::'+repr(cmd))
        self.serialwrite(cmd)

    def clearlines(self):
        """Empty local caches."""
        for j in range(0,2):
            self.obuf[j] = u''.ljust(10)

    def close_display(self):
        """Close serial port."""
        if self.scb is not None:
            self.log.info(u'Closing serial port.')
            self.scb.close()	# serial port close
            self.scb = None		# release handle

    def reconnect_display(self):
        """Re-connect to display serial port."""
        self.close_display()
        self.log.debug(u'Connecting serial port: ' + repr(self.port))
        try:
            self.scb = serial.Serial(self.port, VOLA_BAUD, timeout=0.2)
        except Exception as e:
            self.log.error(u'Opening serial port: ' + repr(e))
            self.scb = None

    def saveconfig(self):
        # N/A
        pass

    def loadconfig(self):
        """Load app config from disk."""
        cr = jsonconfig.config({u'voladisp':{
               u'id':'',
               u'port':u'',
               u'brightness':u'1',
               u'remoteport':u'',
               u'remotechan':u'',
               u'remoteuser':u'',
               u'loglevel':unicode(logging.INFO),
               u'maxlaptime':u'4:00'
              }})
        cr.add_section(u'voladisp')
        cwfilename = metarace.default_file(CONFIGFILE)
        cr.merge(metarace.sysconf, u'voladisp')

        # re-set log file
        if self.loghandler is not None:
            self.log.removeHandler(self.loghandler)
            self.loghandler.close()
            self.loghandler = None
        self.loghandler = logging.FileHandler(
                             os.path.join(self.configpath, LOGFILE))
        self.loghandler.setLevel(LOGHANDLER_LEVEL)
        self.loghandler.setFormatter(logging.Formatter(
                       '%(asctime)s %(levelname)s:%(name)s: %(message)s'))
        self.log.addHandler(self.loghandler)

        # check for config file
        try:
            with open(cwfilename, 'rb') as f:
                cr.read(f)
        except Exception as e:
            self.log.error(u'Reading app config: ' + repr(e))

        # set uSCBsrv connection
        self.remoteuser = cr.get(u'voladisp', u'remoteuser')
        self.remotechan = cr.get(u'voladisp', u'remotechan')
        self.remoteport = cr.get(u'voladisp', u'remoteport')
        self.remote.set_portstr(portstr=self.remoteport,
                             channel=self.remotechan)
        if self.remoteuser:
            self.log.info(u'Enabled remote control by: '
                          + repr(self.remoteuser))
        else:
            self.log.info(u'Promiscuous remote control enabled.')

        # set display serial port
        self.port = cr.get(u'voladisp', u'port')
        self.reconnect_display()

        # check the maximum lap time field
        mlap = tod.str2tod(cr.get(u'voladisp',u'maxlaptime'))
        if mlap is not None:
            self.maxlaptime = mlap

        # set display birightness
        self.set_brightness(strops.confopt_posint(cr.get(u'voladisp',
                             u'brightness'), 1))

        cid = cr.get(u'voladisp', u'id')
        if cid and cid != APP_ID:
            self.log.error(u'Meet configuration mismatch: '
                           + repr(cid) + u' != ' + repr(APP_ID))

    def __init__(self, configpath=None):
        """App constructor."""
        # logger and log handler
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        self.loghandler = None	# set in loadconfig to meet dir

        # meet configuration path and options
        if configpath is None:
            configpath = u'.'	# None assumes 'current dir'
        self.configpath = configpath
        self.loglevel = logging.INFO	# UI log window

        # hardware connections
        self.remote = telegraph.telegraph()
        self.remoteuser = u''		# match against remote nick
        self.remoteport = u''		# only connect if requested
        self.remotechan = u'#announce'
        self.remote.set_pub_cb(self.remote_cb)
        self.port = u''			# re-set in loadconfig
        self.scb = None
        self.remote_enable = True
        self.obuf = []			# current output buffer
        for j in range(0,2):
            self.obuf.append(u''.ljust(10))
   
        self.set_remote_enable()

        # run state
        self.running = True
        self.started = False
        self.tod = tod.tod(u'now').truncate(0)
        self.nc = self.tod + tod.tod(u'1.22') # set interval a little off mark
        self.maxlaptime = tod.tod('2:00') # default maximum lap time

        # animation variables
        self.ttrank = None
        self.ttno = None
        self.ttname = None
        self.ttcat = None
        self.tttime = None
        self.elapstart = None
        self.elapfin = None
        self.timerstat = u'running'	# default assume run state
        self.timelimit = None
        self.distance = None
        self.lapfin = None
        self.timeofday = True  # show timeofday on bottom line?
        self.failcount = 0
        self.failthresh = 30    # connect timeout ~30sec

        # start timer
        self.log.debug(u'Starting clock intervals at: ' + self.nc.rawtime(3))
        glib.timeout_add(2000, self.timeout)

def main():
    """Run the application."""
    configpath = None

    # expand configpath on cmd line to realpath _before_ doing chdir
    if len(sys.argv) > 2:
        print(u'usage: voladisp [configdir]\n')
        sys.exit(1)
    elif len(sys.argv) == 2:
        rdir = sys.argv[1]
        if not os.path.isdir(rdir):
            rdir = os.path.dirname(rdir)
        configpath = os.path.realpath(rdir)

    metarace.init()
    if configpath is not None:
        os.chdir(configpath)
    app = voladisp(configpath)
    app.loadconfig()
    app.start()
    try:
        metarace.mainloop()
    except:
        app.shutdown(u'Exception from main loop.')
        raise

if __name__ == '__main__':
    main()

