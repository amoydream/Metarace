
# Metarace : Cycle Race Abstractions
# Copyright (C) 2016  Nathan Fraser
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

"""Race Result USB Decoder 'DEBUG' interface"""
import threading
import decimal
import Queue
import serial
import logging
import glib
import time	# for ipcompletion, this is a problem!

import metarace
from metarace import tod

# Serial baudrate
RRU_BAUD = 19200

# GTK Priority for timing message callbacks sent to main loop
RRU_PRIORITY = glib.PRIORITY_HIGH

# Photofinish threshold - ~20cm based on tests at DISC,
# Activator period is ~20ms... with a single active
# loop, all reads within 20ms must be considered same time.
PHOTOTHRESH = tod.tod('0.03')

# thread queue commands -> private to thread
TCMDS = ('EXIT', 'PORT', 'MSG', 'TRIG', 'SYNC', 'SANE', 'REPL')

RFID_LOG_LEVEL = 16     # lower so not in status and on-screen logger.
logging.addLevelName(RFID_LOG_LEVEL, 'RFID')

class rru(threading.Thread):
    """Race Result USB Active thread object class."""
    def __init__(self, port=None, name='rru'):
        """Construct thread object.

        Named parameters:

          port -- serial port
          name -- text identifier for use in log messages

        """
        threading.Thread.__init__(self) 
        self.name = name
        self.port = None
        self.looppower = 20
        self.loopid = 1
        self.unitno = u'rru'
        self.loopchannel = 2
        self.lastsync = tod.tod(u'now')
        self.lastts = 0
        self.error = False
        self.errstr = ''
        self.cqueue = Queue.Queue()	# command queue
        self.log = logging.getLogger(self.name)
        self.log.setLevel(logging.DEBUG)
        self.__rdbuf = ''
        self.setcb()
        if port is not None:
            self.setport(port)

    def photothresh(self):
        """Return the relevant photo finish threshold."""
        return PHOTOTHRESH		# allow override perhaps?

    def __defcallback(self, evt=None):
        """Default callback is a tod log entry."""
        self.log.debug(str(evt))
        return False

    def getcb(self):
        """Return the current callback function."""
        return self.__cb

    def setcb(self, func=None):
        """Set or clear the event callback."""
        # if func is not callable, gtk mainloop will catch the error
        if func is not None:
            self.__cb = func
        else:
            self.__cb = self.__defcallback

    def write(self, msg=None):
        """Queue a raw command string to attached decoder."""
        self.cqueue.put_nowait(('MSG', msg))

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False
        self.cqueue.put_nowait(('EXIT', msg)) # "Prod" command thread

    def setport(self, device=None):
        """Request (re)opening port as specified."""
        self.cqueue.put_nowait(('PORT', device))

    def sync(self):
        """Roughly synchronise Decoder to host PC clock.
           For RRU this does not change the decoder - it just fetches the
           current clock offset estimate
        """
        self.cqueue.put_nowait(('SYNC', None))

    def sane(self):
        """Request sanity check in decoder thread."""
        self.cqueue.put_nowait(('SANE', None))

    def __sane(self):
        """Just override settings for now."""
        self.write(u'DETECT')
        self.write(u'RESETPASSINGSBUFFER')
        self.write(u'POWER={0:02d}'.format(self.looppower))
        self.write(u'LOOPID=' + unicode(self.loopid))
        self.write(u'CHANNEL=' + unicode(self.loopchannel))
        self.write(u'BEEP=0')

    def trig(self, timeval='now', index='FAKE', chan='MAN',
                   refid='0', sourceid=None):
        """Create a fake timing event.

	   Generate a new tod object to mimic a message as requested
           and pipe it to the command thread. Default tod is the
	   'now' time in the calling thread.

        """
        src=str(self.loopid)
        if sourceid is not None:
           src=sourceid
        t = tod.tod(timeval, index, chan, refid.lstrip('0'), source=src)

        self.cqueue.put_nowait(('TRIG', t))

    def start_session(self):
        """Send a depart command to decoder."""
        pass

    def stop_session(self):
        """Send a stop command to decoder."""
        pass

    def status(self):
        """Request status message from decoder."""
        self.write(u'GETCONFIG')

    def get_config(self):
        """Request decoder configuration."""
        self.write(u'GETCONFIG')

    def replay(self, filename=''):
        """Read passings from file and process."""
        self.cqueue.put_nowait(('REPL', filename))

    def wait(self):
        """Suspend calling thread until the command queue is empty."""
        self.cqueue.join()

    def tstotod(self, ts):
        """Convert a race result timestamp to time of day."""
        ret = None
        try:
            # get offset from last sync beacon in device units
            ti = int(ts) - self.lastts

            # convert offset to seconds
            tsec = decimal.Decimal(ti//256)+decimal.Decimal(ti%256)/256

            # create a new time of day object, truncated to thous
            ntv = self.lastsync.timeval + tsec
            ret = tod.tod(ntv).truncate(3)
        except Exception as e:
            self.log.debug(u'Error converting Ts: ' + repr(e))
        return ret

    def __parse_message(self, msg, ack=True):
        """Return tod object from timing msg or None."""
        ret = None
        if len(msg) > 4:
            mvec = msg.split()
            if len(mvec) > 10 and mvec[0] == 'pass':
                stored = False
                if len(mvec) > 11: # there's extra info
                    if 'STORE' in mvec[11]:
                        stored = True
                self.log.debug(u'RRU: ' + repr(mvec))
                t = self.tstotod(mvec[3])
                if t is not None:
                    loopid = '0'
                    if len(mvec[8]) == 3:
                        loopid = mvec[8][1]
                    chanstr = u'BOX'
                    tagid = mvec[1].replace('ID=','')
                    if tagid == '_____127':
                        chanstr = u'MAN'
                        tagid = u'255'  # translate trigger to mr trig flag
                    t.index = mvec[2]
                    t.chan = chanstr
                    t.refid = tagid
                    t.source = loopid
                    if stored:
                        self.log.info(u'Stored active passing: ' + repr(t))
                    else:
                        glib.idle_add(self.__cb, t, priority=RRU_PRIORITY)
                    self.log.log(RFID_LOG_LEVEL, ' ' + str(t))
                else:
                    self.log.error(u'Invalid passing: ' + repr(mvec))
            elif len(mvec) == 1 and 'TS=' in mvec[0]:
                # assume timestamp update
                nts = mvec[0].split('=')[-1]
                self.log.debug(u'TS for ' + self.lastsync.rawtime(4) + u': ' + repr(nts))
                self.lastts = int(nts)
            else:
                # some other report - just log to info
                self.log.info(u' '.join(mvec))
        else:        
            self.log.info('Short message: ' + repr(msg))
        return ret

    def __read(self):
        """Read messages from the decoder until a timeout condition."""
        ch = self.port.read(1)
        while ch != '':
            if ch == '\n' and len(self.__rdbuf) > 0:
                # linefeed ends the current 'message'
                self.__rdbuf += ch	# include trailing newline
                t = self.__parse_message(self.__rdbuf.lstrip('\0'))
                if t is not None:
                    glib.idle_add(self.__cb, t, priority=RRU_PRIORITY)
                self.__rdbuf = ''
            elif len(self.__rdbuf) > 100:
                # Drop malformed
                self.__rdbuf = ''
            elif ch != '\n':
                self.__rdbuf += ch
            ch = self.port.read(1)

    def __readline(self, l):
        """Try to extract passing information from lines in a file."""
        t = self.__parse_message(l, False)
        if t is not None:
            glib.idle_add(self.__cb, t, priority=glib.PRIORITY_LOW)

    def __mkport(self, pstr):
        """Try to guess port type."""
        ret = serial.Serial(pstr, RRU_BAUD, rtscts=False, timeout=0.2)
        return ret

    def run(self):
        """Called via threading.Thread.start()."""
        running = True
        self.log.debug('Starting')
        while running:
            try:
                # Read phase
                if self.port is not None:
                    try:
                        self.__read()
                    except Exception as e:
                        self.log.debug(u'EXCEPT timeout: ' + repr(e))
                    m = self.cqueue.get_nowait()
                else:
                    # when no read port avail, block on read of command queue
                    m = self.cqueue.get()
                self.cqueue.task_done()
                
                # Write phase
                if type(m) is tuple and type(m[0]) is str and m[0] in TCMDS:
                    if m[0] == 'MSG' and self.port and not self.error:
                        cmd = m[1]+ '\n'
                        self.log.debug('Sending rawmsg ' + repr(cmd))
                        self.port.write(cmd)
                    elif m[0] == 'TRIG':
                        if type(m[1]) is tod.tod:
                            self.log.log(RFID_LOG_LEVEL, str(m[1]))
                            glib.idle_add(self.__cb, m[1],
                                          priority=RRU_PRIORITY)
                    elif m[0] == 'SANE':
                        self.log.debug('Checking config.')
                        self.__sane()
                    elif m[0] == 'SYNC':
                        # not the assumption is that 'now' is close to the
                        # time that the Unit processses the message. It 
                        # should not then matter how long it takes to get
                        # the reply, we capture the reference time here.
                        self.port.write('GETTIMESTAMP\n')
                        self.lastsync = tod.tod('now')
                        self.lastts = 0	# error passings in transition
                    elif m[0] == 'REPL':
                        self.log.info('Replay passings from: ' + repr(m[1]))
                        with open(m[1], 'rb') as f:
                            for l in f:
                                self.__readline(l)
                        self.log.info('Replay complete.')
                    elif m[0] == 'EXIT':
                        self.log.debug('Request to close : ' + str(m[1]))
                        running = False	# This may already be set
                    elif m[0] == 'PORT':
                        if self.port is not None:
                            self.port.close()
                            self.port = None
                        if m[1] is not None and m[1] != '' and m[1] != 'NULL':
                            self.log.debug('Re-Connect port : ' + str(m[1]))
                            self.port = self.__mkport(m[1])
                            self.error = False
                        else:
                            self.log.debug('Not connected.')
                            self.error = True
                    else:
                        pass
                else:
                    self.log.warn(u'Unknown message: ' + repr(m))
            except Queue.Empty:
                pass
            except serial.SerialException as e:
                if self.port is not None:
                    self.port.close()
                    self.port = None
                self.errstr = 'Serial Port error.'
                self.error = True
                self.log.error('Closed port: ' + str(type(e)) + str(e))
            except Exception as e:
                self.log.error('Exception: ' + str(type(e)) + str(e))
                raise
                #self.errstr = str(e)
                #self.error = True
        if self.port is not None:
            self.port.close()
            self.port = None
        self.setcb()	# make sure callback is unrefed
        self.log.info('Exiting')

def printtag(t):
    print(t.refid + u'\t' + t.rawtime(2) + '\t' + repr(t.source))
    return False

def doconfig(t):
    t.loopchannel = 4
    t.loopid = 4
    t.looppower = 5
    t.sane()
    return False

def dosync(t):
    t.sync()
    return False

if __name__ == "__main__":
    import metarace
    import gtk
    import time
    import random
    import json
    metarace.init()
    t = rru()
    lh = logging.StreamHandler()
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                      "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    t.log.addHandler(lh)
    try:
        t.start()
        t.setport(u'/dev/ttyUSB0')

        t.setcb(printtag)
        glib.timeout_add_seconds(2, doconfig, t)
        glib.timeout_add_seconds(3, dosync, t)
        metarace.mainloop()
    except:
        t.wait()
        t.exit('Exception')
        t.join()
        raise
