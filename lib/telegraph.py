
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

"""IRC Telegraph Class

This module provides a telegraph endpoint for IPC over IRC

TODO:
	- Test complex fragmented JSON samples
	- Test interaction with remote servers
	- Simplify multi-channel setup
	- Add client-client facility for file xfer (DCC?)
"""

import threading
import Queue
import logging
import socket
import random
import time
import glib
import json
import zlib
import metarace

from metarace import unt4
from metarace import tod
from metarace import strops

# IRC Limits
IRC_MAXMSGLEN = 512		# RFC1459 (enforced by hybrid)
IRC_FAKEHOSTLEN = 60		# fake len till server sends the real one
IRC_PRIVHDRLEN = 14		# :nick!user@spoof PRIVMSG #chan :payload \r\n
				# ': PRIVMSG  :\r\n' len == 14

# Global Defaults
TELEGRAPH_USER=u'yak'		# default username root part
TELEGRAPH_HOST=u''		# default is "not present"
TELEGRAPH_PORT=6667
TELEGRAPH_USERNAME=u'tclient'	# default username
TELEGRAPH_FULLNAME=u'telegraph client'	# default ircname
TELEGRAPH_FAKEHOST=u'o'*IRC_FAKEHOSTLEN
TELEGRAPH_CHANNEL=u'#agora'	# default channel
TELEGRAPH_PRIVATESERVERS=[u'127.0.0.1',u'localhost',
                          u'192.168.95.64',u'192.168.96.64']
TELEGRAPH_OPERNAME=u'operator'
TELEGRAPH_OPERPASS=u'password'

# dispatch thread queue commands
# temp: RMSG is 'raw' message for new-style json commands
TCMDS = ('EXIT', 'PORT', 'MSG', 'RMSG')

# command read queue timeout value
QUEUE_TIMEOUT = 2

# client initiated ping interval - helps to break a network error
PING_INTERVAL = tod.tod('30')

def parse_portstr(portstr=''):
    """Read a port string and split into defaults."""
    user = TELEGRAPH_USER
    host = TELEGRAPH_HOST
    port = TELEGRAPH_PORT

    # overwrite code defaults with system defaults
    if metarace.sysconf.has_option(u'telegraph',u'user'):
        user = metarace.sysconf.get(u'telegraph',u'user')
    if metarace.sysconf.has_option(u'telegraph',u'host'):
        host = metarace.sysconf.get(u'telegraph',u'host')
    if metarace.sysconf.has_option(u'telegraph',u'port'):
        port = metarace.sysconf.get(u'telegraph',u'port')

    # strip off nickname
    ar = portstr.rsplit('@', 1)
    if len(ar) == 2:
        nick = ar[0][0:9]	# limit nick to 9 char
        portstr = ar[1]
    else:
        # append random element to default user
        nick = user[0:5] + unicode(random.randint(1000,9999))
        portstr = ar[0]

    # read host:port
    ar = portstr.split(':')
    if len(ar) > 1:
        host = ar[0]
        if ar[1].isdigit():
            port = int(ar[1])
    else:
        if ar[0]:
            host = ar[0]

    return (host, port, nick)

class telegraph(threading.Thread):
    """Metarace telegraph server thread.

       If irclib is not present, this module reverts to a disconnected
       'black hole' sender.

    """

    ### GTK main thread methods
    def add_channel(self, channel):
        """Add channel to the set of joined channels on reconnect."""
        if channel:
            self.addchans.add(channel)
            if self.connected():
                self.queue.put_nowait(('ADDCHAN', channel))

    def clrall(self, chan=None):
        """Clear the live announce screen."""
        self.sendmsg(unt4.GENERAL_CLEARING, chan)

    def clrline(self, line, chan=None):
        """Clear the specified line in DHI database."""
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),erl=True), chan)

    def rcv_obj(self, mtxt):
        """Unpack the provided message to python object."""
        ## convenience function
        ret = None
        try:
            ret = json.loads(mtxt)
        except Exception as e:
            self.log.error(u'Error receiving object: ' + repr(e))
        return ret

    def message_sig(self, msg):
        """Return a 'signature' for supplied message."""
        return str(zlib.adler32(msg) & 0xffffffff)

    def send_rep(self, obj=None, chan=None):
        """Pack the provided object into JSON and send."""
        msg = None
        try:
            msg = json.dumps(obj, separators=(',',':'))
            #self.log.info(u'dumped value is of type: ' + repr(type(msg)))
            mlen = str(len(msg))
            msig = self.message_sig(msg)  
            self.queue.put_nowait(('RMSG',
                                   ' '.join(['J', msig, mlen, msg]),
                                   chan))
        except Exception as e:
            self.log.error(u'Error serialising report: ' + repr(e))
        
    def send_obj(self, hdr=None, obj=None, chan=None):
        """Pack the provided object into JSON and send as a new style cmd."""
        try:
            self.send_cmd(hdr,json.dumps(obj),chan)
        except Exception as e:
            self.log.error(u'Error telegraphing object: ' + repr(e))

    def send_cmd(self, hdr=None, txt=None, chan=None):
        """Send the provided new style command."""
        self.sendmsg(unt4.unt4(header=hdr,text=txt), chan)

    def send_cmdlist(self, cmdlist, chan=None):
        """Send the provided new/old style command."""
        encmsg = u' '.join(cmdlist).encode('utf-8','ignore')
        self.queue.put_nowait(('RMSG', encmsg, chan))

    def set_title(self, line, chan=None):
        """Update the announcer's title line."""
        self.sendmsg(unt4.unt4(header='title',text=line), chan)

    def set_time(self, tstr, chan=None):
        """Update the announcer's time."""
        self.sendmsg(unt4.unt4(header='time',text=tstr), chan)

    def set_start(self, stod, chan=None):
        """Update the announcer's relative start time."""
        self.sendmsg(unt4.unt4(header='start',text=stod.rawtime()), chan)

    def set_gap(self, tstr, chan=None):
        """Update the announcer's gap time (if relevant)."""
        self.sendmsg(unt4.unt4(header='gap',text=tstr), chan)

    def set_avg(self, tstr, chan=None):
        """Update the announcer's average speed."""
        self.sendmsg(unt4.unt4(header='average',text=tstr), chan)

    def add_rider(self, rvec, header_txt='rider', chan=None):
        """Send a rider vector to the announcer."""
        self.sendmsg(unt4.unt4(header=header_txt,
                               text=chr(unt4.US).join(rvec)),chan)

    def gfx_overlay(self, newov, chan=None):
        """Update graphic channel overlay."""
        self.queue.put_nowait(('MSG', 
                   unt4.unt4(header=u'overlay', text=unicode(newov)).pack(),
                       chan))

    def gfx_clear(self, chan=None):
        """Update graphic channel overlay."""
        self.queue.put_nowait(('MSG', unt4.GENERAL_CLEARING, chan))

    def gfx_set_title(self, title, chan=None):
        """Update graphic channel title."""
        self.queue.put_nowait(('MSG', 
                    unt4.unt4(header=u'set_title', text=title).pack(),
                       chan))

    def gfx_add_row(self, rvec, chan=None):
        """Update graphic channel title."""
        ovec = []
        for c in rvec:	# replace nulls and empties
            nc = u''
            if c:	# but assume strings?
                nc = c
            ovec.append(nc)
        self.queue.put_nowait(('MSG', 
                       unt4.unt4(header=u'add_row', 
                                 text=chr(unt4.US).join(ovec)).pack(),
                       chan))

    def setline(self, line, msg, chan=None):
        """Set the specified DHI database line to msg."""
        msg = msg[0:self.linelen].ljust(self.linelen)
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),text=msg),chan)

    def flush(self):
        """Send a flush packet if possible (or required?)."""
        ##TODO
        pass

    def linefill(self, line, char='_', chan=None):
        """Use char to fill the specified line."""
        ## Why was this set to 100?
        #msg = char * 100
        msg = char * self.linelen
        self.sendmsg(unt4.unt4(xx=0,yy=int(line),text=msg), chan)

    def postxt(self, line, oft, msg, chan=None):
        """Position msg at oft on line in DHI database."""
        if oft >= 0:
            self.sendmsg(unt4.unt4(xx=int(oft),yy=int(line),text=msg), chan)

    def setoverlay(self, newov, chan=None):
        """Request overlay newov to be displayed on the scoreboard."""
        if self.curov != newov:
            self.sendmsg(newov, chan)
            self.curov = newov

    def sendmsg(self, unt4msg=None, chan=None):
        """Pack and send a unt4 message."""
        self.queue.put_nowait(('MSG', unt4msg.pack(),chan))

    def write(self, msg=None, chan=None):
        """Send the provided raw text msg."""
        self.queue.put_nowait(('MSG', msg, chan))

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False
        self.queue.put_nowait(('EXIT', msg))

    def wait(self):             # NOTE: Do not call from cmd thread
        """Suspend calling thread until cqueue is empty."""
        self.queue.join()

    def rejoin_channel(self, newchan=''):
        """Request another attempt to join channel."""
        self.queue.put_nowait(('CHAN', newchan))

    ### IRC protocol methods

    def irc_event_cb(self, c, e):
        """Debug method to collect all IRC events."""
        self.log.debug(str(e.eventtype()) + ' :: '
                         + str(e.source()) + '->' + str(e.target()) + ' :: '
                         + '/'.join(map(str, e.arguments())))

    def server_join_cb(self, c, e):
        """Register server join."""
        self.log.debug(u'Connected to server:' + repr(c) + repr(e))
        self.queue.put_nowait(('CHAN', ''))
        for chan in self.addchans:
            self.queue.put_nowait(('ADDCHAN', chan))

    def channel_join_cb(self, c, e):
        """Register channel join."""
        source = self.il.nm_to_n(e.source()).lower()
        dest = e.target().lower()
        if source == self.nick.lower() and dest == self.channel:
            self.chanstatus = True
            self.connect_pending = False # flags queue processing ok
            self.log.debug(u'Joined channel ' + str(e.target()))
            self.dumbcnt = 0
        else:
            self.log.debug(u'Channel join: ' + repr(source)
                             + u'/' + repr(e.target()))

    def channel_part_cb(self, c, e):
        """Register channel part."""
        tg = e.target().lower()
        if (len(e.arguments()) > 0 and tg == self.channel
            and e.arguments()[0].lower() == self.nick.lower()):
            self.chanstatus = False
            self.log.debug('Left channel ' + str(e.target()))

    def disconnect_cb(self, c, e):
        """Handle server disconnect."""
        # remove any IO handlers
        for cksock in self.iohandle:
            ret = glib.source_remove(self.iohandle[cksock])
            self.log.debug(u'Source remove on ' + cksock 
                             + ' returned: ' + repr(ret))

    def set_pub_cb(self, cb=None):
        """Set the public message callback function."""
        self.pub_cb = cb

    def mode_cb(self, c, e):
        """Catch the MODE command to register server spoofed addr"""
        # This is required in order to determine the maximum message length
        # without having to fudge the server-assigned user@host portion.
        # If the server does not issue MODE, this is assumed to be an
        # arbitrartily long string, long enough to catch most cases.
        src = self.il.nm_to_n(e.source()).lower()
        dest = e.target().lower()
        if src == self.nick.lower():
            self.srvid = e.source()
            self.log.debug(u'Registering server id: ' + repr(self.srvid)
                             + u' :: ' + repr(src) + u'->' 
                             + repr(dest) + u' :: '
                             + ''.join(e.arguments()).decode('utf-8'))

    def msg_cb(self, c, e):
        """Handle privmsg."""
        source = self.il.nm_to_n(e.source()).lower()
        dest = e.target().lower()
        body = ''.join(e.arguments())	# RCV bytes!
        # determine receive stream and append to appropriate buffer
        if self.pub_cb is not None:
            # add buffer if required
            if source not in self.rdbuf:
                self.rdbuf[source] = {}
            sbuf = self.rdbuf[source]
            if dest not in sbuf:
                sbuf[dest] = ''
            sbuf[dest] += body	# append payload to relevant buffer
            while len(sbuf[dest]) > 0:
                # attempt to process current buffer content
                if sbuf[dest][0] == 'J':
                    # process the buffer as a JSON report
                    joft = sbuf[dest].find('{')
                    blen = len(sbuf[dest])
                    if joft > 6:	# implies blen > 0
                        # enough length for sig and buflen, start decode
                        mvec = sbuf[dest].split(None, 3)
                        if len(mvec) == 4:
                            dlen = strops.confopt_posint(mvec[2])
                            if len(mvec[3]) >= dlen:
                                data = mvec[3][0:dlen]
                                dsig = self.message_sig(data)
                                if dsig == mvec[1]:
                                    try:
                                        o = json.loads(data)
                                        glib.idle_add(self.pub_cb,o,source,dest)
                                    except Exception as e:
                                        self.log.error(u'Error reading ob: '
                                                       + repr(e))
                                else:
                                    self.log.info(u'Invalid signature: '
                                                  + repr(sbuf[dest]))
                                nstart = joft+dlen
                                sbuf[dest] = sbuf[dest][nstart:]
                            else:
                                # awaiting more data
                                break
                        else:
                            self.log.info(u'Skipping malformed report packet: '
                                           + repr(sbuf[dest]))
                            sbuf[dest]=sbuf[dest][1:]	# chop off marker
                            # terminal
                    else:
                        if len(sbuf[dest]) > 40:
                            self.log.info(u'Skipping malformed report packet: '
                                           + repr(sbuf[dest]))
                        sbuf[dest]=sbuf[dest][1:]	# chop off marker
                elif sbuf[dest][0] == '<':
                    data = unt4.decode(sbuf[dest])
                    # attempt to decode as UNT4 encoded list
                    oidx = data.find(chr(unt4.SOH))
                    eidx = data.find(chr(unt4.EOT))
                    # process one chunk and then unravel
                    if oidx >= 0 and eidx >= 0 and eidx > oidx:
                        msgtxt = data[oidx:eidx+1]
                        glib.idle_add(self.pub_cb,
                            unt4.unt4(unt4str=msgtxt.decode('utf-8','replace')),
                            source, dest)
                        datlen = len(unt4.encode(data[0:eidx+1]))
                        sbuf[dest] = sbuf[dest][datlen:]
                    elif eidx >= 0: # discard partial msg (not yet complete)
                        datlen = len(unt4.encode(data[0:eidx+1]))
                        sbuf[dest] = sbuf[dest][datlen:]
                    else:
                        # awaiting more data
                        break
                else:
                    # assume buffer contains a complete utf8 command list
                    glib.idle_add(self.pub_cb,
                                  sbuf[dest].decode('utf-8','ignore').split(),
                                  source,dest)
                    sbuf[dest] = ''	# truncate buffer

        
    def __init__(self, linelen=28):
        """Constructor."""
        threading.Thread.__init__(self) 
        self.running = False
        self.debug = False
        self.il = None
        self.localsrv = False
        self.rdbuf = {}	# source -> dest -> buf[]
        self.pub_cb = None
        self.iohandle = {}
        self.addchans = set()

        ## HACK
        self.linelen = linelen
        self.pagelen = 7
        self.encoding = 'utf-8'


        self.log = logging.getLogger('telegraph')
        self.log.setLevel(logging.DEBUG)

        try:
            import irclib
            # CHECK: 16.2.9. "all import attempts must be completed
            # before the interpreter starts shutting itself down."
            self.ih = irclib.IRC(fn_to_add_socket=self._addsock,
                                 fn_to_remove_socket=self._delsock)
            self.il = irclib
        except ImportError:
            self.log.warn(u'irclib not present: Telegraph will not function.')
            self.ih = fakeirc()
        self.ic = self.ih.server()
        self.ping_interval = PING_INTERVAL
        self.np = tod.tod('now') + self.ping_interval

        self.name = 'telegraph'
        self.chanstatus = False
        self.nick = TELEGRAPH_USER + unicode(random.randint(1000,9999))
        self.host = TELEGRAPH_HOST
        self.port = TELEGRAPH_PORT
        self.username = TELEGRAPH_USERNAME
        self.fullname = TELEGRAPH_FULLNAME
        self.channel = TELEGRAPH_CHANNEL
        self.srvid = TELEGRAPH_FAKEHOST
        self.privateservers = TELEGRAPH_PRIVATESERVERS
        self.opername = TELEGRAPH_OPERNAME
        self.operpass = TELEGRAPH_OPERPASS
        self.doreconnect = False
        self.connect_pending = False
        self.dumbcnt = 0
        self.curov = None	# allow use as a sender
        #self.linelen = linelen
        self.queue = Queue.Queue()
        # check system config for overrides
        if metarace.sysconf.has_option(u'telegraph', u'username'):
            self.username = metarace.sysconf.get(u'telegraph', u'username')
        if metarace.sysconf.has_option(u'telegraph', u'fullname'):
            self.fullname = metarace.sysconf.get(u'telegraph', u'fullname')
        if metarace.sysconf.has_option(u'telegraph', u'channel'):
            self.channel = metarace.sysconf.get(u'telegraph', u'channel')
        if metarace.sysconf.has_option(u'telegraph', u'privateservers'):
            self.privateservers = metarace.sysconf.get(u'telegraph',
                                                       u'privateservers')
        if metarace.sysconf.has_option(u'telegraph', u'opername'):
            self.opername = metarace.sysconf.get(u'telegraph', u'opername')
        if metarace.sysconf.has_option(u'telegraph', u'operpass'):
            self.operpass = metarace.sysconf.get(u'telegraph', u'operpass')
        if metarace.sysconf.has_option(u'sender', u'pagelen'):
            self.pagelen = strops.confopt_posint(metarace.sysconf.get(
                                  u'sender', u'pagelen'),self.pagelen)
        if metarace.sysconf.has_option(u'telegraph', u'ping_interval'):
            ck = tod.str2tod(metarace.sysconf.get(u'telegraph',
                                                    u'ping_interval'))
            if ck is not None:
                self.ping_interval = ck

    def set_portstr(self, portstr=u'', channel=u'', force=False):
        """Set irc connection by a port string."""
        if channel == u'' or channel[0] != u'#':
            self.log.debug(u'Invalid channel specified: '
                           + repr(channel) 
                           + u', using default: ' 
                           + repr(TELEGRAPH_CHANNEL))
            channel = TELEGRAPH_CHANNEL
        (host, port, nick) = parse_portstr(portstr)
        self.set_port(host, port, channel, nick, reconnect=force)

    def set_port(self, host=None, port=None, channel=None,
                       nick=None, reconnect=False):
        """Request change in irc connection."""
        if host is not None and host != self.host:
            self.host = host
            reconnect = True
        if port is not None and port != self.port:
            self.port = port
            reconnect = True
        if channel is not None and channel != self.channel:
            self.channel = channel
            reconnect = True
        if nick is not None:	# This overrides the nick in init
            self.nick = nick
            reconnect = True
        if reconnect:
            if self.ic.is_connected():
                osock = self.ic.socket
                self.ic.disconnect()
                if osock:	# irclib has an error that stops delsock call
                    self._delsock(osock)
            if self.host in self.privateservers:
                self.localsrv = True	# try metalan oper if private server
            try:		# dump queue before reconnect
                while True:	# QUERY: is this necessary?
                    self.queue.get_nowait()
                    self.queue.task_done()
            except Queue.Empty:
                pass 
            self.queue.put_nowait(('PORT', ''))

    def connected(self):
        """Return true if connected and in channel."""
        return self.ic.is_connected() and self.chanstatus

    def _addsock(self, addsock):
        self.log.debug(u'addsocket called: ' + repr(addsock))
        self.iohandle[repr(addsock)] = glib.io_add_watch(addsock,
                                glib.IO_IN,
                                      self._iocallback)

    def _iocallback(self, socket, condition, data=None):
        try:
            self.ih.process_data([socket])
        except Exception as e:
            self.log.error(u'Exception: ' + str(type(e)) + str(e))
        return True

    def _delsock(self, delsock):
        self.log.debug(u'delsocket called: ' + repr(delsock))
        cksock = repr(delsock)
        if cksock in self.iohandle:
            ret = glib.source_remove(self.iohandle[cksock])
            self.log.debug(u'Source remove on ' + cksock 
                             + ' returned: ' + repr(ret))
            del(self.iohandle[cksock])
        else:
            self.log.debug(u'Socket ' + cksock + ' not found in iohandles.')

    def _addchan(self, adchan):
        self.ic.join(adchan)

    def _rechan(self):
        # TODO: allow flag for oper connections
        if self.localsrv:
            self.log.debug(u'Sending OPER: ' + repr(self.opername))
            self.ic.oper(self.opername, self.operpass)
        self.ic.join(self.channel)

    def _reconnect(self):
        if not self.connect_pending:
            self.log.debug('Connecting to '
                            + self.host + ':' + str(self.port))
            self.connect_pending = True
            self.ic.connect(server=self.host,
                            port=self.port,
                            nickname=self.nick,
                            username=self.username,
                            ircname=self.fullname)

    def __streammsg(self, chan, msg):
        """Output the message in as many blocks as are required."""
        # :NICK!USER@SPOOF PRIVMSG #CHAN :payload \r\n
        maxlen = IRC_MAXMSGLEN - (IRC_PRIVHDRLEN + len(chan) + len(self.srvid))
        pos = 0
        while pos < len(msg):
            self.ic.privmsg(chan, msg[pos:pos+maxlen])
            pos += maxlen
        if not self.localsrv:
            # try to avoid channel flooding by sleep here
            time.sleep(0.3+0.8*random.random())

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        self.log.debug('Starting')
        self.ic.add_global_handler('pubmsg', self.msg_cb, -10)
        self.ic.add_global_handler('privmsg', self.msg_cb, -10)
        self.ic.add_global_handler('welcome', self.server_join_cb, -10)
        self.ic.add_global_handler('join', self.channel_join_cb, -10)
        self.ic.add_global_handler('umode', self.mode_cb, -10)
        self.ic.add_global_handler('part', self.channel_part_cb, -10)
        self.ic.add_global_handler('kick', self.channel_part_cb, -10)
        self.ic.add_global_handler('disconnect', self.disconnect_cb, -10)
        if self.debug:
            self.ic.add_global_handler('all_events', self.irc_event_cb, 0)
        while self.running:
            try:
                if self.host not in [None, '', 'none', 'NULL']:
                    # irc process phase
                    if not self.connected() or self.doreconnect:
                        self.doreconnect = False
                        if not self.connect_pending:
                            self.chanstatus = False
                            self._reconnect()    

                    # keepalive ping
                    now = tod.tod('now')
                    if now > self.np:
                        self.ic.ctcp('PING', self.nick,
                                     str(int(time.time())))
                        self.np = now + PING_INTERVAL

                # queue process phase - queue empty exception breaks loop
                while True:
                    m = self.queue.get(timeout=QUEUE_TIMEOUT)
                    self.queue.task_done()
                    if m[0] == 'RMSG' and self.host not in [None,
                                                   '', 'none', 'NULL']:
                        chan = self.channel
                        if len(m) > 2 and m[2]:
                            chan = m[2]
                        self.__streammsg(chan,m[1])
                    elif m[0] == 'MSG' and self.host not in [None,
                                                   '', 'none', 'NULL']:
                        chan = self.channel
                        if len(m) > 2 and m[2]:
                            chan = m[2]
                        self.__streammsg(chan,
                                         unt4.encode(m[1]).encode('utf-8',
                                                                  'replace'))
                    elif m[0] == 'CHAN':
                        if m[1] != '':
                            self.channel = m[1]
                        self._rechan()
                    elif m[0] == 'ADDCHAN':
                        if m[1] != '':
                            self._addchan(m[1])
                    elif m[0] == 'EXIT':
                        self.log.debug('Request to close : ' + str(m[1]))
                        self.running = False
                    elif m[0] == 'PORT':
                        if not self.connect_pending:
                            self.doreconnect = True
            except Queue.Empty:
                pass
            except Exception as e:
                self.log.error(u'Exception: ' + str(type(e)) + str(e))
                self.connect_pending = False
                self.dumbcnt += 1
                if self.dumbcnt > 2:
                    self.host = ''
                    self.log.debug('Not connected.')
                time.sleep(2.0)
        self.log.info('Exiting')

class fakeirc(object):
    """Relacement dummy class for when irclib is not present."""
    def server(self):
        return self

    def process_once(self, delay=None):
        pass

    def is_connected(self):
        return False

    def disconnect(self):
        pass

    def connect(self, host=None, port=None, nick=None, data=None):
        """Fake an IOError to effectively shut down object."""
        raise IOError('IRC library not present.')

    def close(self, data=None):
        pass

    def oper(self, user=None, pword=None, data=None):
        pass

    def join(self, chan=None, data=None):
        pass

    def mode(self, chan=None, mode=None, data=None):
        pass

    def topic(self, chan=None, topic=None, data=None):
        pass

    def add_global_handler(self, sig=None, cb=None, arg=None, data=None):
        pass

    def ctcp(self, cmd=None, nick=None, ts=None, data=None):
        pass

    def privmsg(self, chan=None, msg=None, data=None):
        pass
    
if __name__ == '__main__':
    def recon(te):
        te.set_portstr(force=True)
        return False

    cvec = []
    for i in range(0,18):
        cvec.append(i)

    def delaycmd(te):
        obj = {u'type':u'test',
               u'xoft':0,
               u'yoft':10,
               u'len':1024,
               u'compt': cvec,
               u'string':u' pksdjh sdkjh sfkjh sfjkhsf kj sfh jksfh jksf hksf hjksf hk sfhjk sfhjk sfhjk sfhjk sfhjklain old unicode \u2764',
               u'vector':[u'asdfghfdsasdfghgfdsasdfghgfdsasdfghfdsasdf',
                          u'543wu8s0du09ns', u'qw9idpoudpiuoiwed',
                          u'tyhgfdsertyhjnfderthvdsthjhgfdsrtgh',
                          u'2tyujnvdsertyjnhgoidfsjfogijfsdoihsodighusf',
                          0,1,2,3,4],
               u'hash':{u'a':u'adfg',u'b':u'ire'},
               u'Null':None}
        te.send_rep(obj)

    logging.basicConfig()
    metarace.init()
    t = telegraph()
    def msgcb(cmd, nick, chan):
        print(u'Telegraph: ' + repr(nick) + u'/' + repr(chan)
                       + u' :: ' + repr(type(cmd)) + u'/' + repr(cmd))
        return False

    #t.debug = True
    try:
        t.start()
        t.add_channel('#otherone')
        t.set_portstr(force=True)
        t.set_pub_cb(msgcb)
        glib.timeout_add_seconds(15, delaycmd, t)
        metarace.mainloop()
    except:
        t.exit()
        t.join()
        raise
