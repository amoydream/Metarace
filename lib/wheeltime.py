
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

"""Times-7 Wheeltime helper.

This module provides a thread object which interfaces with the
Times-7 Wheeltime RFID system. Once connected, the thread will
collect RFID events from the wheeltime and deliver them to a calling
thread via a specified callback.

TCP/IP communicaton with an attached wheeltime unit is handled
by blocking I/O in a sub thread.

  Messages are returned as TOD objects:

        index:          power:batt      99:0 or '' for trig/sts
        chan:           BOX     rfid
                        STA     n/a
                        MAN     manual trigger
                        STS     status n/a
        timeval:        time of day of passing/trig/status
        refid:          transponder id with leading zeros stripped
			and T7 prefix stripped

  Sent to mainloop via glib.idle_add of the provided callback


"""

import threading
import Queue
import logging
import socket
import time
import glib

from metarace import tod

# System defaults
TAGPREFIX = '058001'		# Wheeltime rfid prefix
DEFPORT = '192.168.95.32'	# CV wheeltime IP
WHEELRAWPORT = 10000		# TCP Port for raw tag stream
WHEELFSPORT = 10200		# TCP Port for FS/LS filtered stream (normal)
WHEELCMDPORT = 8999		# TCP Wheeltime command port (defunct?)
WHEELPHOTOTHRESH = tod.tod('0.1')	# Wheeltime confidence ~0.1s

# thread queue commands -> private to timy thread
TCMDS = ('RFID', 'EXIT', 'PORT', 'TRIG', 'REPL', 'MSG')

# Logging defaults
RFID_LOG_LEVEL = 16	# lower so not in status and on-screen logger.
logging.addLevelName(RFID_LOG_LEVEL, 'RFID')

adder = lambda sum, ch: sum + ord(ch)

def ipico_lrc(ipxstr='', el=34):
    """Return the so-called 'LRC' character sum from IPX module."""
    return reduce(adder, ipxstr[2:el], 0) & 0xff

def sendall(s, buf):
    """Send all of buf to socket s."""
    msglen = len(buf)
    sent = 0
    while sent < msglen:
        out = s.send(buf[sent:])
        if out == 0:
            raise socket.error("Wheeltime command socket broken")
        sent += out
        
class wtio(threading.Thread):
    """Wheeltime I/O Helper Thread.

    wtio provides a simple helper object thread class to
    perform blocking reads from an inet socket and deliver
    parsed Time of Day event objects back to a wheeltime
    thread object through the command queue.

    """
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
                self.log.info('Wheeltime I/O connection broken')
                self.close()
            else:
                self.rdbuf += inb
            idx = self.rdbuf.find('\n')
        if idx >= 0:
            ret = self.rdbuf[0:idx+1]
            self.rdbuf = self.rdbuf[idx+1:]
        return ret

    def procmsg(self, msg):
        """Read IPX wheeltime event and insert as tod into command queue."""
        s = msg.strip()
        if (len(s) == 36 or len(s) == 38) and s[1] == 'a':
            sum = ipico_lrc(s)
            lrc = int(s[34:36], 16)
            if sum == lrc:
                #tagid=s[4:16]	## NOTE: Using 'shortform' tag ids
                if s[4:10] == TAGPREFIX:	# match id prefix
                    tagid=(s[10:16]).lower()
                    timestr = '{0}:{1}:{2}.{3:02}'.format(s[26:28], s[28:30],
                                   s[30:32], int(s[32:34], 16))
                    istr = '00:0'	# emulate THbC info, but zero for WT
                    chanstr = 'BOX'
                    t = tod.tod(timestr, index=istr, chan=chanstr,
                                         refid=tagid, source=self.srcid)
                    if self.cb is not None:
                        glib.idle_add(self.cb, t)
                    self.cqueue.put_nowait(('RFID', t))	# notify only now
                else:
                    self.log.warn('Spurious tag id: ' + s[4:10] + ' :: ' 
                                    + s[10:16])
            else:
                self.log.warn('Incorrect char sum message skipped: ' 
                               + hex(sum) + ' != ' + hex(lrc))
        elif len(s) == 30 and s[0:8] == 'ab010a2c':
            # Process a trigger event
            sum = ipico_lrc(s, 28)
            lrc = int(s[28:30], 16)
            if sum == lrc:
                timestr = '{0}:{1}:{2}.{3:02}'.format(s[16:18], s[18:20],
                               s[20:22], int(s[22:24], 16))
                istr = '00:0'
                chanstr = 'MAN'
                tagid = ''	# emulate THbC manual trigger (lstripped)
                t = tod.tod(timestr, index=istr, chan=chanstr,
                                     refid=tagid, source=self.srcid)
                if self.cb is not None:
                    glib.idle_add(self.cb, t)
                self.cqueue.put_nowait(('RFID', t))	# notify only now
            else:
                self.log.warn('Incorrect char sum message skipped: ' 
                               + hex(sum) + ' != ' + hex(lrc))
        else:
            self.log.debug('Non RFID message: ' + repr(msg))

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.connect((self.addr, WHEELFSPORT))
            while self.running:
                try:
                    m = self.readline(s)
                    if m is not None:
                        self.procmsg(m)
                except socket.timeout:
                    pass
            s.shutdown(socket.SHUT_RDWR)
            s.close()
        except Exception as e:
            self.running = False
            self.log.error('WTIO Exception: ' + repr(e))

class wheeltime(threading.Thread):
    """Wheeltime thread object class."""
    def __init__(self, port=None, name='wheeltime'):
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
        self.setcb()
        self.running = False
        if port is not None:
            self.setport(port)

    def photothresh(self):
        """Return the relevant photo finish threshold."""
        return WHEELPHOTOTHRESH

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
        """Clear wheeltime memory."""
        self.cqueue.put_nowait(('MSG', 'clear_history\n'))

    def write(self, msg=None):
        """Queue a raw command string."""
        self.cqueue.put_nowait(('MSG', str(msg).rstrip() + '\n'))

    def exit(self, msg=None):
        """Flag control thread termination."""
        self.running = False
        self.cqueue.put_nowait(('EXIT', msg))

    def setport(self, device=None):
        """Request new wheeltime device address."""
        self.cqueue.put_nowait(('PORT', device))

    def sane(self):
        """Nothing to do with wheeltime."""
        pass

    def stop_session(self):
        pass

    def start_session(self):
        pass

    def sync(self):
        """Rough synchronise wheeltime RTC to PC."""
        t = time.localtime()
        datestr = '{0}{1:02}{2:02}{3:02}{4:02}{5:02}{6:02}00'.format(
                     str(t[0])[2:], t[1], t[2], t[6], t[3], t[4], t[5])
        self.cqueue.put_nowait(('MSG', 'ab000701' + datestr + '\n'))

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
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((self.addr, WHEELCMDPORT))
        sendall(s, command.encode('latin_1'))
        s.shutdown(socket.SHUT_RDWR)
        s.close()

    def setlvl(self, box=u'20', sta=u'20'):
        """Set the read level on box and sta channels."""
        pass

    def status(self):
        if self.connected():
            self.log.info('Wheeltime unit connected on: ' + repr(self.addr))
        else:
            self.log.info('Wheeltime not connected.')

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
                    #glib.idle_add(self.__cb, m[1])
                elif m[0] == 'TRIG':
                    if type(m[1]) is tod.tod:
                        self.log.log(RFID_LOG_LEVEL, str(m[1]))
                        if self.__cb is not None:
                            glib.idle_add(self.__cb, m[1])
                elif m[0] == 'MSG':
                    if self.connected():
                        self.command(m[1])
                    else:
                        self.log.warn('Wheeltime not connected.')
                elif m[0] == 'REPL':
                    self.log.warn('Wheeltime replay not implemented.')
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
                        self.log.debug('Re-Connect wheeltime addr: ' + str(m[1]))
                        self.io = wtio(addr=self.addr, cqueue=self.cqueue,
                                       log=self.log, srcid=self.name)
                        self.io.setcb(self.__cb)
                        self.io.start()
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
    w = wheeltime(DEFPORT)
    lh = logging.StreamHandler()
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    w.log.addHandler(lh)
    try:
        w.start()
        #w.clrmem()
        #w.sync()
        w.wait()
        metrarace.mainloop()
    except:
        w.wait()
        w.exit('Exception')
        w.join()
        raise
    w.exit('Complete')
    w.join()
