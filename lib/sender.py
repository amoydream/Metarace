
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

"""DISC/DHI sender class.

This module provides a thread object which collects, queues
and dispatches all messages intended for a galactica scoreboard
with DHI. A calling application should use the sender methods
for all low-level scb drawing.

SCB messages are stored in a Queue object and written out to
the DHI using blocking I/O.

"""

import threading
import Queue
import logging
import socket
import metarace
import serial

from metarace import unt4
from metarace import strops

# Default Galactica encoding is cp1252 (ansi)
ENCODING = 'cp1252'
LINELEN = 24
PAGELEN = 8

# protocol to object mappings
PROTOCOLS = {
}

# dispatch thread queue commands
TCMDS = ('EXIT', 'PORT', 'MSG')

class serialport(object):
    """Scoreboard communication port object."""
    def __init__(self, addr, protocol):
        """Constructor.

        Parameters:

          addr -- socket style 2-tuple (host, port)
          protocol -- one of socket.SOCK_STREAM or socket.SOCK_DGRAM

        """
        self.__s = serial.Serial(addr,38400)
        self.send = self.__s.write  # local cache the send() method
        self.read = self.__s.read
        self.running = True

    def sendall(self, buf):
        """Send all of buf to port."""
        msglen = len(buf)
        sent = 0
        while sent < msglen:
            out = self.send(buf[sent:])
            #junk = self.read(1024)
            #if out == 0:
                #raise socket.error("Serial port write error")
            sent += len(buf)	# LAME
        pass

    def close(self):
        """Shutdown socket object."""
        self.running = False
        try:
            self.__s.close()
        except:
            pass	# error here should not leak out

class scbport(object):
    """Scoreboard communication port object."""
    def __init__(self, addr, protocol):
        """Constructor.

        Parameters:

          addr -- socket style 2-tuple (host, port)
          protocol -- one of socket.SOCK_STREAM or socket.SOCK_DGRAM

        """
        self.__s = socket.socket(socket.AF_INET, protocol)
        if protocol is socket.SOCK_STREAM:
            # set the TCP 'no delay' option
            self.__s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        else:	# assume Datagram (UDP)
            # set all scb packets to look like 'EF' VoIP packets
            #self.__s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)
            # enable broadcast send
            self.__s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.__s.connect(addr)
        self.send = self.__s.send  # local cache the send() method
        self.running = True

    def sendall(self, buf):
        """Send all of buf to port."""
        msglen = len(buf)
        sent = 0
        while sent < msglen:
            out = self.send(buf[sent:])
            if out == 0:
                raise socket.error("DHI command socket broken")
            sent += out
        pass

    def close(self):
        """Shutdown socket object."""
        self.running = False
        try:
            self.__s.shutdown(socket.SHUT_RDWR)
        except:
            pass	# error here should not leak out

def mkport(port=None):
    """Create a new scbport socket object.

    port is a string specifying the address as follows:

        [PROTOCOL:]ADDRESS[:PORT]

    Where:

        PROTOCOL :: TCP or UDP	(optional)
        ADDRESS :: hostname or IP address
        PORT :: port name or number (optional)

    """
    nprot = socket.SOCK_DGRAM	# default is UDP
    naddr = u'localhost'	# default is localhost
    nport = 5060		# default is 'sip' scbport

    # import system defaults if required
    if metarace.sysconf.has_option(u'sender',u'portspec'):
        if not port or port == u'DEFAULT':
            port = metarace.sysconf.get(u'sender',u'portspec')
    
    if port == u'DEBUG': # force use of the hardcoded UDP endpoint
        pass
    else:
        vels = [u'UDP', u'localhost', u'sip']
        aels = port.translate(strops.PRINT_UTRANS).strip().split(u':')
        if len(aels) == 3:
            vels[0] = aels[0].upper()
            vels[1] = aels[1]
            vels[2] = aels[2]
        elif len(aels) == 2:
            if aels[0].upper() in [u'TCP', u'UDP']:
                # assume PROT:ADDR
                vels[0] = aels[0].upper()
                vels[1] = aels[1]
            else:
                vels[1] = aels[0]
                vels[2] = aels[1]
        elif len(aels) == 1:
            vels[1] = aels[0]
        else:
            raise socket.error('Invalid port specification string')

        # 'import' the vels...
        if vels[0] == u'TCP':
            nprot = socket.SOCK_STREAM
            nport = 16372
        elif vels[0] == u'UDP':
            nprot = socket.SOCK_DGRAM
            nport = 5060
        else:
            raise socket.error('Invalid protocol specified.')
        naddr = vels[1]
        # override port if supplied
        if vels[2].isdigit():
            nport = int(vels[2])
        else:
            nport = socket.getservbyname(vels[2])
    
    ## split port string into [PROTOCOL:]ADDR[:PORT]
    if u'/dev/' in naddr:
        return serialport(naddr, nprot)
    else:
        return scbport((naddr, nport), nprot)

def mksender(port=None, protocol=None):
    nprot = u'dhi'
    if metarace.sysconf.has_option(u'sender', u'protocol'):
        look = metarace.sysconf.get(u'sender', u'protocol').lower()
        if look in PROTOCOLS:
            nprot = look
    if protocol is not None and protocol in PROTOCOLS:
        nprot = protocol
    cols = LINELEN
    rows = PAGELEN
    encoding = ENCODING
    if metarace.sysconf.has_option(u'sender', u'linelen'):
        cols = metarace.sysconf.get(u'sender', u'linelen')
    if metarace.sysconf.has_option(u'sender', u'pagelen'):
        rows = metarace.sysconf.get(u'sender', u'pagelen')
    if metarace.sysconf.has_option(u'sender', u'encoding'):
        encoding = metarace.sysconf.get(u'sender', u'encoding')
    ret = PROTOCOLS[nprot](port)
    ret.linelen = cols
    ret.pagelen = rows
    ret.encoding = encoding
    return ret

class sender(threading.Thread):
    """Galactica DHI sender thread.

    sender provides a helper object thread class to aid
    delivery of UNT4 message packets into an Omega Galactica
    DHI scoreboard 'database'. The class also provides basic
    text drawing primitives, overlay control and clearing.

    Scoreboard DHI database is assumed to comprise 20 rows
    (lines) of 32 ansi (cp1252) characters (columns). Mapping of
    database rows to overlay screens is outlined in the attached
    DISC DHI documentation.

    """

    def clrall(self, chan=None):
        """Clear all lines in DHI database."""
        self.sendmsg(unt4.GENERAL_CLEARING)

    def clrline(self, line, chan=None):
        """Clear the specified line in DHI database."""
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),erl=True))

    def setline(self, line, msg, chan=None):
        """Set the specified DHI database line to msg."""
        # set line should also clear, used to have ERL, but not now?
        msg = strops.truncpad(msg,self.linelen,'l',False)
        #msg = msg[0:self.linelen].ljust(self.linelen)
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),text=msg))

    def flush(self, chan=None):
        """Send a flush packet if possible (or required?)."""
        pass

    def linefill(self, line, char=u'_', chan=None):
        """Use char to fill the specified line."""
        #msg = char * self.linelen
        msg = char * 100
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),text=msg))

    def postxt(self, line, oft, msg, chan=None):
        """Position msg at oft on line in DHI database."""
        # TODO: Handle negative offsets? From right?
        #if oft < self.linelen:
        if True:
            #msg = msg[0:(self.linelen-oft)]
            self.sendmsg(unt4.unt4(xx=int(oft),yy=int(line),text=msg))

    def setoverlay(self, newov, chan=None):
        """Request overlay newov to be displayed on the scoreboard."""
        if self.curov != newov:
            self.sendmsg(newov)
            self.curov = newov

    def __init__(self, port=None):
        """Constructor."""
        threading.Thread.__init__(self) 
        self.name = u'sender'
        self.port = None
        self.linelen = LINELEN
        self.pagelen = PAGELEN
        self.encoding = ENCODING	# set by mksender
        self.ignore = False
        self.curov = None
        self.queue = Queue.Queue()
        self.log = logging.getLogger('sender')
        self.log.setLevel(logging.DEBUG)
        self.running = False
        if port is not None:
            self.setport(port)

    def sendmsg(self, unt4msg=None, chan=None):
        """Pack and send a unt4 message to the DHI."""
        self.queue.put_nowait(('MSG', unt4msg.pack()))

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
        """Dump command queue content and (re)open DHI port.

        Specify hostname and port for TCP connection as follows:

            tcp:hostname:16372

        Or use system defaults:

		DISC  -- TCP:192.168.96.32:16372
		DEBUG -- UDP:localhost:5060

        """
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
                    self.port.sendall(m[1].encode(self.encoding,'replace'))
                elif m[0] == 'EXIT':
                    self.log.debug(u'Request to close : ' + str(m[1]))
                    self.running = False
                elif m[0] == 'PORT':
                    if self.port is not None:
                        self.port.close()
                        self.port = None
                    if m[1] not in [None, '', 'none', 'NULL']:
                        self.log.debug(u'Re-Connect port: ' + str(m[1]))
                        self.port = mkport(m[1])
                        self.curov = None
                    else:
                        self.log.debug(u'Not connected.')

            except IOError as e:
                self.log.error(u'IO Error: ' + str(type(e)) + str(e))
                if self.port is not None:
                    self.port.close()
                self.port = None
            except Exception as e:
                self.log.error(u'Exception: ' + str(type(e)) + str(e))
        if self.port is not None:
            self.port.close()
        self.log.info(u'Exiting')

class altsender(sender):
    def sendmsg(self, unt4msg=None, chan=None):
        """Pack and send a unt4 message to the altscb."""
        self.queue.put_nowait(('MSG', unt4msg.altpack()))
        #self.log.debug(u'DAK: ' + repr(unt4msg.altpack()))

    def setoverlay(self, newov, chan=None):
        pass

    def flush(self):
        """Send a flush packet."""
        pass
        #self.queue.put_nowait(('MSG', unt4.ALT_ACK.altpack()+(9 * unichr(unt4.NUL))))

    def linefill(self, line, char=u'_'):
        """Use char to fill the specified line."""
        msg = char * self.linelen
        umsg = unt4.unt4(xx=0,yy=int(line),text=msg)
        self.sendmsg(umsg)

    def postxt(self, line, oft, msg, chan=None):
        """Position msg at oft on line in DHI database."""
        # TODO: Handle negative offsets? From right?
        if oft < self.linelen:
            msg = msg[0:(self.linelen-oft)]
            umsg = unt4.unt4(xx=int(oft),yy=int(line),text=msg)
            self.sendmsg(umsg)

    def clrline(self, line, chan=None):
        """Clear the specified line in DHI database."""
        umsg = unt4.unt4(xx=0,yy=int(line),text=u' '*self.linelen)
        self.sendmsg(umsg)

    def setline(self, line, msg, chan=None):
        """Set the specified DHI database line to msg."""
        msg = msg[0:self.linelen].ljust(self.linelen)
        umsg = unt4.unt4(xx=0,yy=int(line),text=msg)
        self.sendmsg(umsg)

PROTOCOLS[u'dhi']=sender
PROTOCOLS[u'dak']=altsender

if __name__ == "__main__":
    """Simple 'Hello World' example with logging."""
    h = logging.StreamHandler()
    h.setLevel(logging.DEBUG)
    s = sender('DISC')
    s.log.addHandler(h)
    s.start()				# start thread
    s.clrall()				# queue commands
    s.setline(0, u'HHH\u00e9llo W\u00fcrld')
    s.setline(1, u'HHH\u00e9llo W\u00fcrld')
    s.setline(6, u'HHH\u00e9llo W\u00fcrld')
    s.setline(7, u'HHH\u00e9llo W\u00fcrld')
    s.setline(8, u'HHH\u00e9llo W\u00fcrld')
    s.setoverlay(unt4.OVERLAY_2LINE)
    s.wait()				# wait for all cmds to be proc'd
    if s.connected():
        print('- sender still connected.')
    else:
        print('- sender not connected.')
    s.exit('hello done.')		# signal thread to end
    s.join()				# wait for thread to terminate
