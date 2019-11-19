
# Metarace : Cycle Race Abstractions
# Copyright (C) 2012-2015  Nathan Fraser
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

"""Tag Heuer/Chronelec Decoder Hub Interface

 Runs in a thread and manages connections to a related set of decoders.

"""
import threading
import Queue
import socket
import logging
import re

import metarace
from metarace import tod
from metarace import crc_algorithms

# UDP Port number for decoder replies
THBC_UDP_PORT = 2008
THBC_UDP_ADDR = u''
# Broadcast for decoder network
THBC_BROADCAST = u'192.168.96.255'

# distinguish logged data from decoders
RFID_LOG_LEVEL = 16
logging.addLevelName(RFID_LOG_LEVEL, 'RFID')

# THbC protocol messages
ESCAPE = chr(0x1b)
HELOCMD = 'MR1'
STOPCMD = ESCAPE + chr(0x13) + chr(0x5c)
REPEATCMD = ESCAPE + chr(0x12)
ACKCMD = ESCAPE + chr(0x11)

STATCMD = ESCAPE + chr(0x05)    # fetch status
CHKCMD = ESCAPE + chr(0x06)     # UNKNOWN
STARTCMD = ESCAPE + chr(0x07)   # start decoder
SETCMD = ESCAPE + chr(0x08)     # set configuration
IPCMD = ESCAPE + chr(0x09)      # set IP configuration
QUECMD = ESCAPE + chr(0x10)     # fetch configuration

NACK = chr(0x07)
CR = chr(0x0d)
LF = chr(0x0a)
SETTIME = ESCAPE + chr(0x48)
STATSTART = '['
PASSSTART = '<'

# decoder config consts
IPCONFIG_LEN = 16
CONFIG_LEN = 27
CONFIG_TOD = 0
CONFIG_GPS = 1
CONFIG_TZ_HOUR = 2
CONFIG_TZ_MIN = 3
CONFIG_485 = 4
CONFIG_FIBRE = 5
CONFIG_PRINT = 6
CONFIG_MAX = 7
CONFIG_PROT = 8
CONFIG_PULSE = 9
CONFIG_PULSEINT = 10
CONFIG_CELLSYNC = 11
CONFIG_CELLTOD_HOUR = 12
CONFIG_CELLTOD_MIN = 13
CONFIG_TONE_STA = 15
CONFIG_TONE_BOX = 17
CONFIG_TONE_MAN = 19
CONFIG_TONE_CEL = 21
CONFIG_TONE_BXX = 23
CONFIG_ACTIVE_LOOP = 14
CONFIG_STALVL = 25
CONFIG_BOXLVL = 26
CONFIG_FLAGS = {
        CONFIG_TOD: u'Time of Day',
        CONFIG_GPS: u'GPS Sync',
        CONFIG_TZ_HOUR: u'Timezone Hour',
        CONFIG_TZ_MIN: u'Timezone Min',
        CONFIG_485: u'Distant rs485',
        CONFIG_FIBRE: u'Distant Fibre',
        CONFIG_PRINT: u'Serial Print',
        CONFIG_MAX: u'Detect Max',
        CONFIG_PROT: u'Protocol',
        CONFIG_PULSE: u'Sync Pulse',
        CONFIG_PULSEINT: u'Sync Interval',
        CONFIG_CELLSYNC: u'CELL Sync',
        CONFIG_CELLTOD_HOUR: u'CELL Sync Hour',
        CONFIG_CELLTOD_MIN: u'CELL Sync Min',
        CONFIG_TONE_STA: u'STA Tone',
        CONFIG_TONE_BOX: u'BOX Tone',
        CONFIG_TONE_MAN: u'MAN Tone',
        CONFIG_TONE_CEL: u'CEL Tone',
        CONFIG_TONE_BXX: u'BXX Tone',
        CONFIG_STALVL: u'STA Level',
        CONFIG_BOXLVL: u'BOX Level',
        CONFIG_ACTIVE_LOOP: u'Active Loop',
}
DEFAULT_IPCFG = {
  u'IP':u'192.168.96.253',
  u'Netmask':u'255.255.255.0',
  u'Gateway':u'0.0.0.0',
  u'Host':u'192.168.96.255'
}
CONFIG_SANE = {
  u'Time of Day': True,
  u'GPS Sync': False,
  u'Active Loop': True,
  u'Detect Max': False,
  u'Protocol': 0,
  u'CELL Sync': False,
  u'Sync Pulse': False,
  u'Serial Print': False,
  u'Timezone Hour': 0,
  u'Timezone Min': 0,
  u'STA Level': 55,
  u'BOX Level': 56,
}

# Initialise crc algorithm for CRC-16/MCRF4XX
crc_alg = crc_algorithms.Crc(width=16, poly=0x1021,
                              reflect_in=True, xor_in=0xffff,
                              reflect_out=True, xor_out=0x0000)

# Cheap and nasty byte addition
adder = lambda sum, ch: sum + ord(ch)

def thbc_sum(msgstr=''):
    """Return sum of character values as decimal string."""
    return '{0:04d}'.format(reduce(adder, msgstr, 0))

def thbc_crc(msgstr='123456789'):
    """Return CRC-16/MCRF4XX on input string."""
    return crc_alg.table_driven(msgstr)

def val2hexval(val):
    """Convert int to decimal digit equivalent hex byte."""
    ret = 0x00
    ret |= ((val//10)&0x0f)<<4  # msd   97 -> 0x90
    ret |= (val%10)&0x0f        # lsd   97 -> 0x07
    return ret

def hexval2val(hexval):
    """Unconvert a decimal digit equivalent hex byte to int."""
    ret = 10*(hexval>>4)        # tens 0x97 -> 90
    ret += hexval&0x0f          # ones 0x97 ->  7
    return ret

class decoder(object):
    """Network decoder instance."""
    def __init__(self, ip, name, hub):
        self.ip = ip
        self.name = name
        self.unitno = None
        self.config = {}
        self.ipconfig = {}
        self.__hub = hub
        self.__cksumerr = 0

    def __v3_cmd(self, cmdstr=''):
        """Pack and send a v3 command: NOTE does not queue."""
        crc = thbc_crc(cmdstr)
        crcstr = chr(crc>>8) + chr(crc&0xff)
        self.__hub.sendto(ESCAPE + cmdstr + crcstr + '>', self.ip)

    def __serialise_config(self):
        """Convert current decoder setting into a config string"""
        obuf = bytearray(CONFIG_LEN)

        # fill in tone values
        for opt in [CONFIG_TONE_STA, CONFIG_TONE_BOX, CONFIG_TONE_MAN,
                     CONFIG_TONE_CEL, CONFIG_TONE_BXX]:
            if opt in self.config:
                obuf[opt] = val2hexval(self.config[opt]//100) # xx00
                obuf[opt+1] = val2hexval(self.config[opt]%100) # 00xx

        # fill in single byte values
        for opt in [CONFIG_TZ_HOUR, CONFIG_TZ_MIN, CONFIG_PROT,
                    CONFIG_PULSEINT, CONFIG_CELLTOD_HOUR, CONFIG_CELLTOD_MIN,
                    CONFIG_STALVL, CONFIG_BOXLVL]:
            if opt in self.config:
                obuf[opt] = val2hexval(self.config[opt]%100) # ??
        # fill in flags
        for opt in [CONFIG_TOD, CONFIG_GPS, CONFIG_485, CONFIG_FIBRE,
                    CONFIG_PRINT, CONFIG_MAX, CONFIG_PULSE,
                    CONFIG_CELLSYNC, CONFIG_ACTIVE_LOOP]:
            if opt in self.config:
                if self.config[opt]:
                    obuf[opt] = 0x01
        return str(obuf)        # !! not unicode

    def set_ipconfig(self):
        """Write IP configuration to decoder."""
        ipcfg = metarace.sysconf.get(u'thbc',u'ipconfig')
        cmd = chr(0x09) + chr(0x09)
        for i in [u'IP', u'Netmask', u'Gateway', u'Host']:
            if i not in self.ipconfig:
                self.ipconfig[i] = DEFAULT_IPCFG[i]
            cmd += socket.inet_aton(socket.gethostbyname(self.ipconfig[i]))
        self.__v3_cmd(cmd)

    def set_config(self):
        """Write configuration to decoder."""
        cmd = chr(0x08) + chr(0x08) + self.__serialise_config()
        self.__v3_cmd(cmd)

    def __parse_config(self, msg):
        """Decode and store configuration."""
        ibuf = bytearray(msg)
        self.config = {}
        for flag in sorted(CONFIG_FLAGS):       # import all
            # tone values
            if flag in [CONFIG_TONE_STA, CONFIG_TONE_BOX, CONFIG_TONE_MAN,
                     CONFIG_TONE_CEL, CONFIG_TONE_BXX]:
                self.config[flag] = 100*hexval2val(ibuf[flag])
                self.config[flag] += hexval2val(ibuf[flag+1])

            # single byte values
            elif flag in [CONFIG_TZ_HOUR, CONFIG_TZ_MIN, CONFIG_PROT,
                    CONFIG_PULSEINT, CONFIG_CELLTOD_HOUR, CONFIG_CELLTOD_MIN,
                    CONFIG_STALVL, CONFIG_BOXLVL]:
                self.config[flag] = hexval2val(ibuf[flag])
            # 'booleans'
            elif flag in [CONFIG_TOD, CONFIG_GPS, CONFIG_485, CONFIG_FIBRE,
                    CONFIG_PRINT, CONFIG_MAX, CONFIG_PULSE,
                    CONFIG_CELLSYNC, CONFIG_ACTIVE_LOOP]:
                self.config[flag] = bool(ibuf[flag])

        self.unitno = u''
        for c in msg[43:47]:
            self.unitno += unichr(ord(c)+ord('0'))
        #stalvl = hex(ord(msg[25]))      # ? question this
        #boxlvl = hex(ord(msg[26]))

        # Decoder info
        self.ipconfig[u'IP'] = socket.inet_ntoa(msg[27:31])
        self.ipconfig[u'Mask'] = socket.inet_ntoa(msg[31:35])
        self.ipconfig[u'Gateway'] = socket.inet_ntoa(msg[35:39])
        self.ipconfig[u'Host'] = socket.inet_ntoa(msg[39:43])

    def __parse_message(self, msg, ack=True, proc=True):
        """Process a decoder message."""
        ret = False
        if len(msg) > 4:
            if msg[0] == PASSSTART:     # RFID message
                idx = msg.find('>')
                if idx == 37:           # Valid length
                    data = msg[1:33]
                    msum = msg[33:37]
                    tsum = thbc_sum(data)
                    if tsum == msum:    # Valid 'sum'
                        self.__hub.log.log(RFID_LOG_LEVEL,
                                           self.name + ': ' + msg.strip())
                        pvec = data.split()
                        istr = pvec[3] + ':' + pvec[5]
                        rstr = pvec[1].lstrip('0')
                        if pvec[5] == '3': # LOW BATTERY ALERT
                            self.__hub.log.warn(repr(self.ip)
                                              + ': Low battery ' + rstr)
                        t = tod.tod(pvec[2], index=istr, chan=pvec[0],
                                      refid=rstr, source=self.name)
                        if proc:
                            self.__hub.passing(t, self.ip)
                            if ack:
                                self.__hub.ackpass(self.ip)
                            self.__cksumerr = 0
                        ret = True
                    else:
                        self.__hub.log.info(repr(self.ip)
                                              + ': Invalid checksum '
                                      + repr(tsum) + ' != ' + repr(msum)
                                      + ' :: ' + repr(msg))
                        self.__cksumerr += 1
                        if self.__cksumerr > 3:
                            # assume error on decoder, so acknowledge and
                            # continue with log
                            # NOTE: This path is triggered when serial comms
                            # fail and a tag read happens before a manual trig
                            self.__hub.log.error(repr(self.ip)
                                              + ': Bad msg from decoder.')
                            if ack:
                                self.__hub.ackpass(self.ip)
                else:
                    self.__hub.log.info(repr(self.ip)
                                      + ': Invalid message ' + repr(msg))
            elif msg[0] == STATSTART:   # Status message
                data = msg[1:22]
                pvec = data.split()
                if len(pvec) == 5:
                    # Note: this path is not immune to error in stream
                    # but in the case of exception from tod construct
                    # it will be collected by the thread 'main loop'
                    rstr = ':'.join(pvec[1:])
                    t = tod.tod(pvec[0].rstrip('"'), index='', chan='STS',
                                      refid=rstr, source=self.name)
                    if proc:
                        self.__hub.statusack(t, self.ip)
                    ret = True
                else:
                    self.log.info(repr(self.ip) 
                                  + ': Invalid status ' + repr(msg))
            elif '+++' == msg[0:3] and len(msg) > 53:
                self.__parse_config(msg[3:])
                # assume this is a correct message
                ret = True
            else:
                self.__hub.log.info(repr(self.ip)
                                    + ': Raw message ' + repr(msg))
        else:
            self.log.info(repr(self.ip) + ': ' 
                          + u'Short message ' + repr(msg))
        return ret

    def checkparse(self, msg):
        """Check a message for correct parse but don't process."""
        ret = False
        m = re.search('[\<\[\+]', msg)
        if m:	# matching start byte
            startchar = msg[m.start()]
            # discard any leading chars with partition
            (lead, sc, msg) = msg.partition(startchar)
            (met, ec, junk) = msg.partition(CR+LF)
            ret = self.__parse_message(sc + met + ec, proc=False)
        return ret

    def parse(self, msg):
        """Parse all complete lines in msg, then return the residual."""
        ret = msg
        while LF in ret:
            m = re.search('[\<\[\+]', ret)
            if m:	# matching start byte
                startchar = ret[m.start()]
                # discard any leading chars with partition
                (lead, sc, ret) = ret.partition(startchar)
                (met, ec, ret) = ret.partition(CR+LF)
                self.__parse_message(sc + met + ec)
            else:
                # LF possibly in msg but no start char found, discard
                self.__hub.log.debug(repr(self.ip) + u' Rawmsg: ' + repr(ret))
                (lead, sep, ret) = ret.partition(LF)
        return ret

class thbchub(threading.Thread):
    """Tag Heuer Elite decoder hub thread object class."""
    def __init__(self):
        threading.Thread.__init__(self) 
        self.hub = {}		# map of decoder associations
        self.port = None	# hub socket object
        self.portno = THBC_UDP_PORT
        self.ipaddr = THBC_UDP_ADDR
        self.broadcast = THBC_BROADCAST
        self.rdbuf = {}		# per host read buffers
        self.cqueue = Queue.Queue()	# command queue
        self.log = logging.getLogger(u'thub')
        self.log.setLevel(logging.DEBUG)
        self.__autofind = False
        self.__autoblacklist = set()
        self.running = False
        self.__cb = None
        self.__statuscb = None
        self.__cbdata = None

    def autofind(self, setto=True):
        """Set or clear the decoder autofind property."""
        if setto:
            self.__autofind = True
            self.__autoblacklist.clear()	# ready to re-find
        else:
            self.__autofind = False

    def add(self, ip, name):
        """Queue an add decoder command."""
        self.cqueue.put_nowait(('ADD',ip,name))

    def __add(self, ip, name):
        """Add or replace a connection to the decoder at the given IP."""
        self.__remove(ip)
        self.hub[ip] = decoder(ip, name, self)	# with link to parent
        self.log.debug(u'Added decoder: ' + repr(ip) + u' :: ' + repr(name))
        self.__write(QUECMD, ip)
        # send a hello and config request to decoder
 
    def remove(self, ip):
        """Queue a remove decoder command."""
        self.cqueue.put_nowait(('REMOVE',ip))

    def __remove(self, ip):
        """Remove the specified decider association."""
        # dump current association
        if ip in self.hub:
            del(self.hub[ip])
        if ip in self.rdbuf:
            del(self.rdbuf[ip])

    def connect(self, port=None, ipaddr=None):
        """Re-initialise the listening port."""
        try:
            if port is not None:
                self.portno = port
            if ipaddr is not None:
                self.ipaddr = ipaddr
            self.port = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.port.settimeout(0.2)
            self.port.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.port.bind((self.ipaddr, self.portno))
            self.log.debug(u'Reconnect socket: ' + repr(self.port))
        except Exception as e:
            self.log.error(u'Hub connect error: ' + repr(e))
            self.port = None
            self.running = False

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False
        self.cqueue.put_nowait(('EXIT', msg)) # "Prod" command thread

    def wait(self):
        """Suspend calling thread until the command queue is empty."""
        # note: this is not to be called by the socket thread - it is for
        #       users to ensure a command has been received by the module
        self.cqueue.join()

    def __read(self):
        """Read from the shared port."""
        (msg, addr) = self.port.recvfrom(2048)

        # append message to appropriate read buffer
        mid = addr[0]
        if mid in self.hub:
            if mid not in self.rdbuf:
                self.rdbuf[mid] = msg
            else:
                self.rdbuf[mid] += msg

            if LF in self.rdbuf[mid]:
                self.rdbuf[mid] = self.hub[mid].parse(self.rdbuf[mid])
        else:
            # danger - this is a lot of work for an unsolicited packet
            if self.__autofind and mid not in self.__autoblacklist:
                if LF in msg:
                    checkdec = decoder(mid, u'tester', self)
                    msgok = checkdec.checkparse(msg)
                    del(checkdec)
                    if msgok:
                        self.log.debug(u'Add unconfigured host: ' + repr(mid))
                        self.__add(mid, mid)
                    else:
                        self.log.debug(u'Invalid packet from unconfigured host: ' + repr(mid))
                        self.__autoblacklist.add(mid)
            else:
                pass
                #self.log.debug(u'Ignore unconfigured host: ' + repr(mid))

    def __write(self, msg, dst):
        """Write to the nominated decoder."""
        return self.port.sendto(msg, (dst, self.portno))

    def passing(self, p, ip):
        """Queue a passing record into command queue."""
        self.cqueue.put_nowait(('PASSING', p, ip))

    def pingall(self):
        """Broadcast a status request."""
        self.cqueue.put_nowait(('ALLSTAT',None, None))
        
    def statusack(self, p, ip):
        """Queue a status record into command queue."""
        self.cqueue.put_nowait(('STATUSACK', p, ip))

    def ackpass(self, ip):
        """Acknowledge a passing to the nominated decoder."""
        self.cqueue.put_nowait(('WRITE', ACKCMD, ip))

    def status(self, ip):
        """Request status from the decoder at ip."""
        self.cqueue.put_nowait(('WRITE', STATCMD, ip))

    def stopsession(self, ip):
        """Request stop session to decoder at ip."""
        self.cqueue.put_nowait(('WRITE', STOPCMD, ip))

    def startsession(self, ip):
        """Request start session to decoder at ip."""
        self.cqueue.put_nowait(('WRITE', STARTCMD, ip))

    def sync(self, ip=None):
        self.cqueue.put_nowait(('SYNC',ip))

    def sane(self, ip):
        """Call configset with a known good config."""
        self.cqueue.put_nowait(('CONFIG', CONFIG_SANE, ip))

    def configset(self, req, ip):
        """Queue config set command."""
        self.cqueue.put_nowait(('CONFIG', req, ip))

    def ipconfigset(self, req, ip):
        """Queue IP config set command."""
        self.cqueue.put_nowait(('WRITE', STOPCMD, ip))
        self.cqueue.put_nowait(('IPCONFIG', req, ip))

    def setcb(self, cbfunc, statusfunc=None, data=None):
        """Set the callback function for passing messages."""
        # callback receives:
        # ip of source decocer
        # tod record for the message
        # data if set
        self.__cb = cbfunc
        self.__statuscb = statusfunc
        self.__cbdata = data
        return None

    def __ipconfigset(self, req, ip):
        """Perform an ip config update."""
        if ip in self.hub:
            # check for existence of any key, flags population
            if u'IP' in self.hub[ip].ipconfig:
                # transfer configuration changes
                for key in self.hub[ip].ipconfig:
                    if key in req:
                        self.hub[ip].ipconfig[key] = req[key]
                # request update
                self.hub[ip].set_ipconfig()
                # check for a post update IP change
                if self.hub[ip].ipconfig[u'IP'] != ip:
                    self.log.info(u'Decoder IP Change: ' 
                       + repr(ip) + u' -> '
                       + repr(self.hub[ip].ipconfig[u'IP']))
                    self.__add(self.hub[ip].ipconfig[u'IP'],self.hub[ip].name)
                    self.__remove(ip)
                else:
                    self.log.info(u'Decoder IP did not change.')
            else:
                self.log.info(u'Decoder not connected: ' + repr(ip))
        else:
            self.log.info(u'IP Config called on unknown ip: '
                          + repr(ip))

    def __configset(self, req, ip):
        """Request update of the keys in req on decoder at ip."""
        if ip in self.hub:
            # check for existence of any key, flags population
            if CONFIG_PULSE in self.hub[ip].config:
                # transfer configuration changes
                for flag in self.hub[ip].config:
                    key = CONFIG_FLAGS[flag]
                    if key in req:
                        if req[key] != self.hub[ip].config[flag]:
                            self.log.info(u'Config updated: ' + repr(ip)
                                          + u' : ' + repr(key))
                        self.hub[ip].config[flag] = req[key]
                # request update
                self.hub[ip].set_config()
            else:
                self.log.info(u'Decoder not connected: ' + repr(ip))
        else:
            self.log.info(u'Config called on unknown ip: '
                          + repr(ip))

    def sendto(self, cmd, ip):
        """Queue the specified command to the nominated ip."""
        self.cqueue.put_nowait(('WRITE', cmd, ip))

    def __set_time_cmd(self, t):
        """Return a set time command string for the provided time of day."""
        s = int(t.timeval)
        st = chr(s%60)
        mt = chr((s//60)%60)
        ht = chr(s//3600)
        return SETTIME + ht + mt + st + chr(0x74)

    def __command(self, m):
        """Process a command out of the command queue."""
        if type(m) is tuple and type(m[0]) is str:
            if m[0] == 'WRITE':
                if len(m) == 3:
                    self.__write(m[1], m[2])
            elif m[0] == 'PASSING':
                if len(m) == 3:
                    if self.__cb is not None:
                        self.__cb(m[2], m[1], self.__cbdata)
                    else:
                        self.log.info(u'PASS: ' + repr(m[1]) + u' :: '
                                       + m[2])
            elif m[0] == 'STATUSACK':
                if len(m) == 3:
                    if self.__statuscb is not None:
                        self.__statuscb(m[2], m[1], self.__cbdata)
                    else:
                        self.log.debug(u'STATUS: ' + unicode(m[1]) + u' :: '
                                       + m[2])
            elif m[0] == 'SYNC':
                # broadcast or direct a rough sync command
                dst = self.broadcast
                if m[1] is not None:
                    dst = m[1]
                t = tod.tod('now')
                while t-t.truncate(0) > tod.tod('0.02'):
                    t = tod.tod('now')
                self.__write(self.__set_time_cmd(t), dst)
            elif m[0] == 'ALLSTAT':
                self.__write(STATCMD, self.broadcast)
            elif m[0] == 'IPCONFIG':
                if len(m) == 3:
                    self.__ipconfigset(m[1], m[2])
            elif m[0] == 'CONFIG':
                if len(m) == 3:
                    self.__configset(m[1], m[2])
            elif m[0] == 'ADD':
                if len(m) == 3:
                    self.__add(m[1], m[2])
            elif m[0] == 'REMOVE':
                if len(m) == 2:
                    self.__remove(m[1])
            else:
                pass
        else:
            self.log.warn(u'Unknown command: ' + repr(m))

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        self.log.debug('Starting')
        self.connect()
        while self.running:
            try:
                try:
                    self.__read()	# block until timeout
                except socket.timeout:
                    pass
                while True:	# until command queue empty exception
                    m = self.cqueue.get_nowait()
                    self.cqueue.task_done()
                    self.__command(m)
            except Queue.Empty:
                pass
            except socket.error as e:
                self.log.error('Network error: ' + str(type(e)) + str(e))
            except Exception as e:
                self.log.error('Exception: ' + str(type(e)) + str(e))
                raise
        self.log.info('Exiting')

from metarace import timy
sm = timy.make_sectormap('DISC')
passmap = {}
last = {}
MIN50 = tod.tod(2)
MAX50 = tod.tod(7)
MIN100 = tod.tod(4)
MAX100 = tod.tod(13)
MIN200 = tod.tod(8)
MAX200 = tod.tod(26)
MAXLAP = tod.tod(35)
MINLAP = tod.tod(10)
MAXHALF = tod.tod(20)
MINHALF = tod.tod(5)
MAXQTR = tod.tod(12)
MINQTR = tod.tod(2)

## TODO:
#	- sector processing needs to handle discontinuity
#	- always issue a report for every passing, even if there's a discon
#	- fill in out or order reports using the 'effort' data type
#	- pre-processing of sectors based on speed? (tba)
##
def dumbcb(ip, t, d):
    d.debug(u'CB: ' + u','.join([unicode(ip),t.rawtime(2),t.source,t.refid]))
    nc = timy.chan2id(t.source)
    nt = t
    refid = t.refid
    if refid not in passmap:	# initialise the map of passings
        passmap[refid] = {}
    lc = None
    lt = None
    if refid in last and refid not in [u'0', u'255']:
        (lc,lt) = last[refid]
    if lt is not None and (lc,nc) in sm:
        toft = nt - lt
        doft = sm[(lc,nc)]
        if toft < tod.tod(u'40'):
            # full lap on every timing point
            laptm = u''
            if refid in passmap and nc in passmap[refid]:
                lapoft = nt - passmap[refid][nc]
                if lapoft < MAXLAP and lapoft >= MINLAP:
                    laptm = lapoft.rawtime(2)

            ## TODO: Make this biz a function
            # 200 on finish only
            split200 = u''
            if nc == 1:
                otherloop = 4
                if refid in passmap and otherloop in passmap[refid]:
                    twooft = nt - passmap[refid][otherloop]
                    if twooft < MAX200 and twooft >= MIN200:
                        split200 = twooft.rawtime(2)

            # 100 on finish and 100 split
            split100 = u''
            if nc == 5 or nc == 1:
                otherloop = 4
                if nc == 1:
                    otherloop = 5
                if refid in passmap and otherloop in passmap[refid]:
                    oft100 = nt - passmap[refid][otherloop]
                    if oft100 < MAX100 and oft100 >= MIN100:
                        split100 = oft100.rawtime(2)

            # 50 on 200 start (entry)
            split50 = u''
            if nc == 4:
                otherloop = 1
                if refid in passmap and otherloop in passmap[refid]:
                    oft50 = nt - passmap[refid][otherloop]
                    if oft50 < MAX50 and oft50 >= MIN50:
                        split50 = oft50.rawtime(2)

            # 1/4 lap on 200 and PB
            splitqtr = u''
            if nc == 3 or nc == 4:
                otherloop = 2
                if nc == 3:
                    otherloop = 4
                if refid in passmap and otherloop in passmap[refid]:
                    qtroft = nt - passmap[refid][otherloop]
                    if qtroft < MAXQTR and qtroft >= MINQTR:
                        splitqtr = qtroft.rawtime(2)
            
            # half lap on pursuit lines
            splithalf = u''
            if nc == 2 or nc == 3:
                otherloop = 2
                if nc == 2:
                    otherloop = 3
                if refid in passmap and otherloop in passmap[refid]:
                    halfoft = nt - passmap[refid][otherloop]
                    if halfoft < MAXHALF and halfoft >= MINHALF:
                        splithalf = halfoft.rawtime(2)
            
            speed = toft.rawspeed(doft)
            print(','.join([t.refid,speed, 
                                 unicode(lc),
                                 unicode(nc),
                                 unicode(doft),
                                 unicode(toft.as_seconds(2)),
                                 unicode(t.as_seconds(2)),
                                 laptm, splithalf, splitqtr, split200, split100, split50,
                           ]))
    last[refid] = (nc,nt)
    passmap[refid][nc] = nt
    sys.stdout.flush()

if __name__ == "__main__":
    import sys
    import time
    import glib

    metarace.init()
    syncmaster = u'192.168.96.254'
    h = thbchub()
    h.ipaddr = u'192.168.96.64'
    #lh = logging.StreamHandler()
    lh = logging.FileHandler(u'test.log')
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                      "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    h.log.addHandler(lh)
    h.setcb(dumbcb, h.log)
    def delayed():
        gap = tod.tod(0)
        while gap < tod.tod(30):
            tt = tod.tod(u'now').truncate(0)
            nt = tod.tod(60+ 60*(int(tt.as_seconds())//60))
            gap = nt - tt
            h.log.debug(u'Waiting: ' + tt.rawtime(0))
            time.sleep(5)
        print(u'ADD DECS')
        h.add(u'192.168.96.254', 'C1')
        time.sleep(1)
        h.add(u'192.168.96.208', 'C2')
        time.sleep(1)
        h.add(u'192.168.96.209', 'C3')
        time.sleep(1)
        h.add(u'192.168.96.210', 'C4')
        time.sleep(1)
        h.add(u'192.168.96.212', 'C5')
        time.sleep(1)

        print(u'STOP ALL DECODERS - Initial Conditions')
        for d in h.hub:
            print(u'\t' + repr(d))
            h.stopsession(d)
            h.wait()
            time.sleep(0.2)
            h.configset({"Sync Pulse": False}, d)
            h.wait()
            time.sleep(0.2)
            h.sendto(QUECMD, d)
            time.sleep(1)

        tt = tod.tod(u'now').truncate(0)
        print(u'nowtime: ' + repr(tt.meridian()))
        nt = tod.tod(60+ 60*(int(tt.as_seconds())//60))
        if (nt - tt) < tod.tod(u'15'):
            nt += tod.tod(u'60')	# add on a minute
        print(u'nexttime: ' + repr(nt.meridian()))
        (hr, mn, sc) = nt.rawtime(0, zeros=True,
                                  hoursep=u':', minsep=u':').split(u':')
        # prepare slaves for sync pulse
        confchg = {"Sync Pulse": False,
                     "CELL Sync Hour":int(hr),
                     "CELL Sync Min":int(mn),
                     "CELL Sync":True}
        for d in h.hub:
            if d != syncmaster:
                h.configset(confchg, d)

        # prepare master for trigger
        h.startsession(syncmaster)
        h.wait()
        h.sync()
        h.wait()
        h.configset({"Sync Pulse": True}, syncmaster)

        time.sleep(2)
        for d in h.hub:
            #print(u'req stat: ' + repr(d))
            h.status(d)

        # autofind test
        #h.autofind()
        #time.sleep(1)
        #h.pingall()
        #time.sleep(1)
        #for d in h.hub:
            #h.stopsession(d)
            #h.wait()
            #h.sane(d)
            #h.wait()
            #h.startsession(d)
            #h.wait()
        #h.sync()
        #h.autofind(False)

        # decoder IP change test
        #oldip = u'192.168.96.211'
        #newip = u'192.168.96.232'
        #h.add(oldip, 'chg')
        #h.startsession(oldip)
        #h.wait()
        #h.sync(oldip)
        #time.sleep(10)
        #newipcf = {
            #u'IP':newip,
            #u'Netmask':u'255.255.255.0',
            #u'Gateway':u'0.0.0.0',
            #u'Host':u'192.168.96.255'
        #}
        #h.ipconfigset(newipcf, oldip)
        #time.sleep(10)
        #h.status(newip)

        return None
    try:
        h.start()
        glib.timeout_add_seconds(5,delayed)
        metarace.mainloop()
    except:
        h.wait()
        #h.configset({"Sync Pulse": False}, syncmaster)
        time.sleep(0.2)
        for d in h.hub:
            #print(u'\t' + repr(d))
            h.stopsession(d)
            h.wait()
            time.sleep(0.2)
        h.exit('Exception')
        h.join()
        raise
