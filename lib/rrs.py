
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

"""Race Result System Helper

"""

import threading
import Queue
import logging
import socket
import time
import glib
#impoer serial
import datetime

#import metarace	# for configs
from metarace import tod

# Serial baudrate
RRS_BAUD = 38400

# GTK Priority for timing message callbacks sent to main loop
RRS_PRIORITY = glib.PRIORITY_HIGH

# PORT number on decoder
RRS_TCP_PORT = 3601

# Photo threshold
PHOTOTHRESH = tod.tod('0.03')

# thread queue commands -> private to timy thread
TCMDS = ('RFID', 'EXIT', 'PORT', 'TRIG', 'SANE', 'REPL', 'MSG')

# Logging defaults
RFID_LOG_LEVEL = 16	# lower so not in status and on-screen logger.
logging.addLevelName(RFID_LOG_LEVEL, 'RFID')

def sendall(s, buf):
    """Send all of buf to socket s."""
    msglen = len(buf)
    sent = 0
    while sent < msglen:
        out = s.send(buf[sent:])
        if out == 0:
            raise socket.error("Command socket broken")
        sent += out
        
class rrio(threading.Thread):
    """I/O Helper Thread."""
    def __init__(self, addr=None, cqueue=None, log=None, srcid=None):
        """Construct wheeltime I/O thread.

        Named parameters:

          addr -- tcp address or hostname of Wheeltime unit
          cqueue -- wheeltime thread command queue object
          log -- wheeltime thread log object

        """
        threading.Thread.__init__(self)
        self.daemon = True	# daemon so doesn't hold up main proc
        self.cqueue = cqueue
        self.wqueue = Queue.Queue()	# write queue
        self.log = log
        self.addr = addr
        self.rdbuf = ''
        self.cb = None
        self.srcid = srcid
        self.running = False

    def setcb(self, cb=None):
        self.cb = cb

    def close(self):
        """Signal thread for termination."""
        self.running = False

    def readline(self, s=None):
        """Return whole lines from socket.

        This function works on an input buffer, returning one complete
        line per call or None if one is not yet fully received.

        """
        ret = None
        idx = self.rdbuf.find('\n')
        if idx < 0:
            inb = s.recv(512)
            if inb == '':
                self.log.info('I/O connection broken')
                self.close()
            else:
                self.rdbuf += inb
            idx = self.rdbuf.find('\n')
        if idx >= 0:
            ret = self.rdbuf[0:idx+1]
            self.rdbuf = self.rdbuf[idx+1:]
        return ret

    def procmsg(self, msg):
        """Read reply and handle appropriately."""
        s = msg.strip()
        self.log.debug(u'RECV: ' + repr(s))
        if len(s) > 3 and s[0:3] == '#P;':	# Pushed passing
            s = s[3:]
        if len(s) > 26 and s[0].isdigit():	# now just passing
            p = s.split(u';')
            if len(p) > 3:
                istr = p[0]
                tagid = p[1]
                today = datetime.date.today().isoformat()
                date = p[2]
                timestr = (p[3])
                store = u'0'
                activestore = False
                loopid = u'1'
                if len(p) > 11:
                    loopid = p[10]	# check
                    store = p[11]	# from stored passing?
                if len(p) > 15 and p[15].isdigit():
                    activestore = (int(p[15])&0x40) == 0x40
                chanstr = 'BOX'
                if tagid == u'99999':
                    chanstr = u'MAN'
                    tagid = u'255'	# translate trigger to mr trig flag
                t = tod.str2tod(timestr)
                if t is not None:
                    t.index = istr
                    t.chan = chanstr
                    t.refid = tagid
                    t.source = loopid
                    #t = tod.tod(timestr, index=istr, chan=chanstr,
                                     #refid=tagid, source=loopid)
                    if store == u'0' and (date == u'0000-00-00' or date == today):
                        if activestore:
                            self.log.info(u'Stored active passing: ' + 
                             u';'.join([istr, tagid, date, timestr, loopid]))
                        else: 
                            if self.cb is not None:
                                glib.idle_add(self.cb, t)
                    else:
                        self.log.info(u'Stored passing: ' + 
                             u';'.join([istr, tagid, date, timestr, loopid]))
                    self.cqueue.put_nowait(('RFID', t))	# notify only now
                else:
                    self.log.warn(u'Invalid time field: ' + repr(timestr))
        else:
            self.log.info('RRS: ' + repr(msg))

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(1.0)	# read timeout for command interjection
            s.connect((self.addr, RRS_TCP_PORT))
            while self.running:
                try:
                    m = self.readline(s)
                    if m is not None:
                        self.procmsg(m)
                except socket.timeout:
                    pass
                # poll write queue for message and then read
                try:
                    m = self.wqueue.get_nowait()
                    self.wqueue.task_done()
                    sendall(s, m.encode('ascii'))
                except:
                    pass
            s.shutdown(socket.SHUT_RDWR)
            s.close()
        except Exception as e:
            self.running = False
            self.log.error('RRIO Exception: ' + repr(e))

class rrs(threading.Thread):
    """RRS thread object class."""
    def __init__(self, port=None, name='rrs'):
        """Construct wheeltime thread object.

        Named parameters:
 
          port -- ip address or hostname of wheeltime unit
          name -- text identifier for use in log messages

        """

        threading.Thread.__init__(self) 
        self.unitno = None
        self.name = name
        self.addr = None
        self.cqueue = Queue.Queue()	# command queue
        self.log = logging.getLogger(self.name)
        self.log.setLevel(logging.DEBUG)
        self.io = None
        self.lastpass = None
        self.setcb()
        self.running = False
        if port is not None:
            self.setport(port)

    def photothresh(self):
        """Return the relevant photo finish threshold."""
        return PHOTOTHRESH

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
        if self.io is not None:
            self.io.setcb(self.__cb)

    def clrmem(self):
        """Clear memory."""
        pass
        #self.cqueue.put_nowait(('MSG', 'clear_history\n'))

    def write(self, msg=None):
        """Queue a raw command string."""
        self.cqueue.put_nowait(('MSG', str(msg).rstrip() + '\n'))

    def exit(self, msg=None):
        """Flag control thread termination."""
        self.running = False
        self.cqueue.put_nowait(('EXIT', msg))

    def setport(self, device=None):
        """Request new device address."""
        self.cqueue.put_nowait(('PORT', device))

    def sane(self):
        """Reconnect, set protocol and request pushed passings."""
        self.cqueue.put_nowait(('MSG','SETPROTOCOL;1.5\r\n'))
        self.cqueue.put_nowait(('MSG','GETSTATUS\r\n'))
        self.cqueue.put_nowait(('MSG','PASSINGS\r\n'))
        self.cqueue.put_nowait(('MSG','SETPUSHPREWARNS;0\r\n'))
        self.cqueue.put_nowait(('MSG','SETPUSHPASSINGS;1;0\r\n'))

    def stop_session(self):
        pass

    def start_session(self):
        pass

    def sync(self):
        """Rough synchronise wheeltime RTC to PC."""
        pass

    def trig(self, timeval='now', index='FAKE', chan='MAN',
                   refid='0', sourceid=None):
        """Create a fake timing event.

           Generate a new tod object to mimic a message as requested
           and pipe it to the command thread. Default tod is the
           'now' time in the calling thread.

        """
        src=self.name
        if sourceid is not None:
           src=sourceid
        t = tod.tod(timeval, index, chan, refid.lstrip('0'), source=src)
        self.cqueue.put_nowait(('TRIG', t))

    def replay(self, filename=''):
        """Read passings from file and process."""
        self.cqueue.put_nowait(('REPL', filename))

    def wait(self):		# NOTE: Do not call from cmd thread
        """Suspend calling thread until cqueue is empty."""
        self.cqueue.join()

    def command(self, command):		# NOTE: Lazy!
        """Connect to serv and dump a command."""
        self.io.wqueue.put_nowait(command)

    def setlvl(self, box=u'20', sta=u'20'):
        """Set the read level on box and sta channels."""
        pass

    def status(self):
        if self.connected():
            self.log.info('RRS unit connected on: ' + repr(self.addr))
        else:
            self.log.info('RRS not connected.')
        self.cqueue.put_nowait(('MSG','GETSTATUS\r\n'))

    def connected(self):
        """Return True if wheeltime unit connected."""
        return self.io and self.io.running

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        self.log.debug('Starting')
        while self.running:
            try:
                # Read Phase - all msgs come through cqueue in wheeltime
                m = self.cqueue.get()
                self.cqueue.task_done()
                
                # Write phase
                if m[0] == 'RFID':
                    self.log.log(RFID_LOG_LEVEL, ' ' + str(m[1]))
                    loopid = m[1].source
                    thispass = m[1].index
                    if thispass.isdigit():
                        thispass = int(thispass)
                        if self.lastpass is not None and thispass > self.lastpass:
                            miss = thispass - self.lastpass
                            if miss != 1:
                                self.log.info(u'Index discontinuity: ' + repr(self.lastpass) + u' :: ' + repr(thispass))
                                getfrom = self.lastpass + 1
                                getcount = thispass - getfrom
                                self.cqueue.put_nowait(('MSG',str(getfrom) + ':' + str(getcount) + '\r\n'))
                            self.lastpass = thispass
                        elif self.lastpass is None:
                            self.lastpass = thispass
                elif m[0] == 'TRIG':
                    if type(m[1]) is tod.tod:
                        self.log.log(RFID_LOG_LEVEL, repr(m[1]))
                        if self.__cb is not None:
                            glib.idle_add(self.__cb, m[1])
                elif m[0] == 'MSG':
                    if self.connected():
                        self.command(m[1])
                    else:
                        self.log.warn('RRS not connected.')
                elif m[0] == 'REPL':
                    self.log.warn('RRS replay not implemented.')
                elif m[0] == 'EXIT':
                    self.running = False
                    self.log.debug('Request to close : ' + str(m[1]))
                elif m[0] == 'PORT':
                    self.addr = None
                    if self.io is not None:
                        self.io.close()
                        self.io = None
                    if m[1] is not None and m[1] != '' and m[1] != 'NULL':
                        self.unitno = m[1].split(u'.')[-1]
                        self.addr = m[1]
                        self.log.debug('Re-Connect RRS addr: ' + str(m[1]))
                        self.io = rrio(addr=self.addr, cqueue=self.cqueue,
                                       log=self.log, srcid=self.name)
                        self.io.setcb(self.__cb)
                        self.io.start()
                        # dubious - but req'd for restart
                        self.sane()
                    else:
                        self.log.info('Wheeltime not connected.')
                else:
                    self.log.warn('Unknown message: ' + repr(m))
            except Exception as e:
                self.log.error('Exception: ' + str(type(e)) + str(e))
        if self.io is not None:
            self.io.close()
        self.setcb()
        self.log.info('Exiting')

if __name__ == "__main__":
    import metarace
    import gtk
    import random
    metarace.init()
    w = rrs(u'192.168.96.203')
    lh = logging.StreamHandler()
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    w.log.addHandler(lh)
    try:
        w.start()
        w.sane()
        #w.clrmem()
        #w.sync()
        w.wait()
        metarace.mainloop()
    except:
        w.wait()
        w.exit('Exception')
        w.join()
        raise
    w.exit('Complete')
    w.join()
