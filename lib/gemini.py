
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

"""Gemini/DHI sender class.

This module provides a thread object which collects, queues
and dispatches all messages intended for the scoreboard to
the DHI. A calling application should use the sender methods
for all low-level scb drawing.

SCB messages are stored in a Queue object and written out to
gem using blocking I/O

"""

import threading
import Queue
import logging
import serial
import sys

from metarace import unt4
from metarace import tod
from metarace import strops

# dispatch thread queue commands
TCMDS = ('EXIT', 'PORT', 'MSG')

# Character encoding
ENCODING = 'ascii'

class scbport(object):
    """Scoreboard communication port object."""
    def __init__(self, port='/dev/ttyUSB1'):
        """Constructor.
        """
        self.__s = serial.Serial(port, 9600, rtscts=0, timeout=0.2)
        self.running = True

    def sendall(self, buf):
        """Send all of buf to port."""
        self.__s.write(buf.encode(ENCODING,'replace'))

    def close(self):
        """Shutdown socket object."""
        self.running = False
        try:
            self.__s.close()
        except:
            pass	# error here should not leak out

def mkport(port):
    """Create a new scbport socket object."""
    return scbport(port)

GEMHEAD = chr(unt4.SOH) + chr(unt4.DC4)
GEMHOME = chr(unt4.STX) + chr(unt4.HOME)
GEMFOOT = chr(unt4.EOT)
class gemini(threading.Thread):
    """Gemini sender thread."""

    def clear(self):
        """Clear scb."""
        self.bib = ''
        self.bib1 = ''
        self.rank = ''
        self.time = ''
        self.time1 = ''
        self.lap = ''
        self.lmsg = ''
        self.write(unt4.GENERAL_CLEARING.pack())

    def send_msg(self, msg, mtype='S', charoff='0', msg1=None):
        msg0 = msg
        if msg1 is None:
            msg1 = msg
        nmsg = (GEMHEAD		# front straight
                   + mtype + '0' + charoff
                   + GEMHOME
                   + msg0
                   + GEMFOOT 
                   + GEMHEAD		# back straight
                   + mtype + '0' + charoff
                   + GEMHOME
                   + chr(unt4.LF)	# line 2
                   + msg1
                   + GEMFOOT)
        if nmsg != self.lmsg:
            self.write(nmsg)
            self.lmsg = nmsg
        #self.log.debug(u'GEM: ' + repr(nmsg))

    def reset_fields(self):
        """Clear out the gemini state fields."""
        self.bib = ''
        self.bib1 = ''
        self.rank = ''
        self.rank1 = ''
        self.time = ''
        self.time1 = ''

    def show_lap(self):
        self.lmsg = ''	# always write out lap to allow redraw
        lstr = strops.truncpad(self.lap, 3, 'r')
        msg = (lstr + chr(unt4.STX) + lstr[0:2] + ':' + lstr[2]
                    + lstr[0] + ':' + lstr[1:3] + '.  ')
        self.send_msg(msg)
     
    def show_brt(self):
        msg = (strops.truncpad(self.bib, 3)
               + chr(unt4.STX)
               + strops.truncpad(str(self.rank),1)
               + '  '	# the 'h:' padding
               + self.time.rjust(5))
        self.send_msg(msg)

    def show_dual(self):
        line0 = (strops.truncpad(self.bib, 3)
               + chr(unt4.STX)
               + strops.truncpad(self.time, 12, 'r', elipsis=False))
        line1 = (strops.truncpad(self.bib1, 3)
               + chr(unt4.STX)
               + strops.truncpad(self.time1, 12, 'r', elipsis=False))
        #self.send_msg(line0, 'R', '2',msg1=line1)	# xxx-5:02.6-x
        self.send_msg(line0, 'R', '3',msg1=line1)	# xxx-5:02.6-x

    def set_rank(self, rank):
        if rank.isdigit() and len(rank) <= 1:
            self.rank = rank
        else:
            self.rank = ''

    def set_bib(self, bib, lane=False):	# True/1/something -> lane 1
        if lane:
            self.bib1 = bib
        else:
            self.bib = bib

    def set_time(self, time, lane=False):
        if lane:
            self.time1 = time
        else:
            self.time = time

    def show_clock(self):
        msg = ('   '	# bib padding
               + chr(unt4.STX)
               + strops.truncpad(self.time, 12, 'r', elipsis=False))
        self.send_msg(msg, 'R')		# -2:34:56xxxx

    def rtick(self, ttod, places=3):
        """Convenience wrapper on set time/show runtime."""
        self.set_time(ttod.omstr(places))
        self.show_runtime()

    def dtick(self, ttod, places=3, lane=False):
        """Convenience wrapper on set time/show dualtime."""
        self.set_time(ttod.omstr(places), lane)
        self.show_dual()

    def ctick(self, ttod):
        """Convenience wrapper on set time/show clock."""
        self.set_time(ttod.omstr(0))
        self.show_clock()

    def set_lap(self, lap=''):
        """Set and show the provided lap."""
        self.lap = str(lap)
        self.show_lap()

    def show_runtime(self):
        msg = (strops.truncpad(self.bib, 3)
               + chr(unt4.STX)
               + strops.truncpad(self.time, 12, 'r', elipsis=False))
        self.send_msg(msg, 'R', '2')	# xxx-5:02.6-x

    def __init__(self, port=None):
        """Constructor."""
        threading.Thread.__init__(self) 
        self.name = 'gemini'
        self.port = None
        self.ignore = False
        self.queue = Queue.Queue()
        self.log = logging.getLogger('gemini')
        self.log.setLevel(logging.DEBUG)
        self.running = False
        self.bib = ''
        self.bib1 = ''
        self.rank = ''
        self.rank1 = ''
        self.time = ''
        self.time1 = ''
        self.lap = ''
        self.lmsg = ''
        if port is not None:
            self.setport(port)

    def write(self, msg=None):
        """Send the provided msg to the DHI."""
        self.queue.put_nowait(('MSG', msg))

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False
        self.queue.put_nowait(('EXIT', msg))

    def wait(self):             # NOTE: Do not call from cmd thread
        """Suspend calling thread until cqueue is empty."""
        self.queue.join()

    def setport(self, port=None):
        """Dump command queue content and (re)open port."""
        try:
            while True:
                self.queue.get_nowait()
                self.queue.task_done()
        except Queue.Empty:
            pass 
        self.queue.put_nowait(('PORT', port))

    def set_ignore(self, ignval=False):
        """Set or clear the ignore flag.

        While the ignore flag is set commands will be read, but
        no packets will be sent to the DHI.

        """
        self.ignore = bool(ignval)

    def connected(self):
        """Return true if SCB connected."""
        return self.port is not None and self.port.running

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        self.log.debug('Starting')
        while self.running:
            m = self.queue.get()
            self.queue.task_done()
            try:
                if m[0] == 'MSG' and not self.ignore and self.port:
                    #self.log.debug('Sending: ' + repr(m[1]))
                    self.port.sendall(m[1])
                elif m[0] == 'EXIT':
                    self.log.debug('Request to close : ' + str(m[1]))
                    self.running = False
                elif m[0] == 'PORT':
                    if self.port is not None:
                        self.port.close()
                        self.port = None
                    if m[1] is not None and m[1] != '' and m[1] != 'NULL':
                        self.log.debug('Re-Connect port: ' + str(m[1]))
                        self.port = mkport(m[1])
                    else:
                        self.log.debug('Not connected.')

            except IOError as e:
                self.log.error('IO Error: ' + str(type(e)) + str(e))
                if self.port is not None:
                    self.port.close()
                self.port = None
            except Exception as e:
                self.log.error('Exception: ' + str(type(e)) + str(e))
        if self.port is not None:
            self.port.close()
        self.log.info('Exiting')

if __name__ == "__main__":
    """Simple 'Hello World' example with logging."""
    import time
    h = logging.StreamHandler()
    h.setLevel(logging.DEBUG)
    s = gemini('/dev/ttyUSB0')
    s.log.addHandler(h)
    s.start()				# start thread
    print ('clear.')
    s.clear()
    s.wait()				# wait for all cmds to be proc'd


    #s.exit('hello done.')		# signal thread to end
    #s.join()
    #sys.exit(0)

    time.sleep(2)
    #time.sleep(1)
#    print ('set bib.')
#    s.set_bib('12')
#    s.show_brt()
#    time.sleep(1)
#    print ('set time.')
#    s.set_time('12.34')
#    s.show_brt()
#    time.sleep(1)
#    print ('set rank.')
#    s.set_rank('2')
#    s.show_brt()
#    time.sleep(5)
#    print ('clear.')
#    s.clear()
#    time.sleep(1)
#    print ('runtime.')
#    s.set_time(tod.tod('12:23.2345').omstr())
#    s.show_runtime()
#    time.sleep(1)
#    print ('add bib.')
#    s.set_bib('12')
#    s.show_runtime()
#    time.sleep(5)
#    s.clear()
#    print ('clock.')
#    s.set_time(tod.tod('now').omstr(0))
#    s.show_clock()
#    
#    time.sleep(3)
#
#    s.clear()
#    s.set_lap('out')
#    s.show_lap()
#
#    time.sleep(3)
#
#    time.sleep(3)
#
#    s.clear()
#    print ('Test wrappers: rtick.')
#    t = tod.tod('now')
#    tg = t + tod.tod('10.012')
#    s.rtick(tg - t, 1)
#    while t < tg:
#        time.sleep(0.01)
#        t = tod.tod('now')
#        print ('rtick...' + t.omstr())
#        s.rtick(tg - t, 1)
#    s.set_bib('23')
#    s.rtick(tod.ZERO)
#    time.sleep(3)
#
#    s.clear()
#    time.sleep(1)
    #print ('Test wrappers: ctick.')
    #tg = tod.tod('17:00:00')
    #t = tod.tod('now')
    #while t < tg:
        #s.ctick(t)
        #t = tod.tod('now')
        #print ('tick...' + t.omstr())
        #time.sleep(0.2)
    #time.sleep(3)
    #s.clear()
   # 
    #s.wait()
    #time.sleep(3)
    #s.clear()


    print(u'set time a.')
    s.set_time(tod.tod('now').omstr(3),0)
    time.sleep(1.2314)
    print(u'set time b.')
    s.set_time(tod.tod('now').omstr(3),1)
    print(u'show.')
    s.show_dual()
    s.wait()
    print(u'quit.')
    time.sleep(1)
    #if s.connected():
        #print('- sender still connected.')
    #else:
        #print('- sender not connected.')
    #time.sleep(3)

    s.exit('hello done.')		# signal thread to end
    s.join()
