
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

"""Tag Heuer/Chronelec Decoder Interface

This module provides a thread object which interfaces with a
Tag Heuer/Chronelec Elite Decoder over serial or UDP.


  Messages are returned as TOD objects:

	index: 		power:batt	eg 99:0 or '' for trig/sts
	chan:  		BOX	rfid on box channel
			STA	rfid on sta channel
			MAN	manual trigger
			STS	status, refid gets the noise/level info
	timeval:	time of day of passing/trig/status
	refid:		transponder id with leading zeros stripped


  Sent to mainloop via glib.idle_add of the provided callback
"""
import threading
import Queue
import serial
import socket
import logging
import glib
import time	# for ipcompletion, this is a problem!

import metarace
from metarace import tod
from metarace import crc_algorithms

# Serial baudrate
THBC_BAUD = 19200
#THBC_BAUD = 38400
#THBC_BAUD = 57600
#THBC_BAUD = 115200

# GTK Priority for timing message callbacks sent to main loop
THBC_PRIORITY = glib.PRIORITY_HIGH

# UDP Port number for ethernet connection (can this be configured?)
THBC_UDP_PORT = 2008

# Photofinish threshold - ~20cm based on tests at DISC,
# Activator period is ~20ms... with a single active
# loop, all reads within 20ms must be considered same time.
THBCPHOTOTHRESH = tod.tod('0.03')

# THbC protocol messages
ESCAPE = chr(0x1b)
HELOCMD = 'MR1'
STOPCMD = ESCAPE + chr(0x13) + chr(0x5c)
REPEATCMD = ESCAPE + chr(0x12)
ACKCMD = ESCAPE + chr(0x11)

STATCMD = ESCAPE + chr(0x05)	# fetch status
CHKCMD = ESCAPE + chr(0x06)	# UNKNOWN
STARTCMD = ESCAPE + chr(0x07)	# start decoder
SETCMD = ESCAPE + chr(0x08)	# set configuration
IPCMD = ESCAPE + chr(0x09)	# set IP configuration
QUECMD = ESCAPE + chr(0x10)	# fetch configuration

STALVL = ESCAPE + chr(0x1e)
BOXLVL = ESCAPE + chr(0x1f)

NACK = chr(0x07)
CR = chr(0x0d)
LF = chr(0x0a)
SETTIME = ESCAPE + chr(0x48)
STATSTART = '['
PASSSTART = '<'

# thread queue commands -> private to timy thread
TCMDS = ('EXIT', 'PORT', 'MSG', 'TRIG', 'SYNC', 'SANE', 'IPCFG', 'REPL')

RFID_LOG_LEVEL = 16     # lower so not in status and on-screen logger.
logging.addLevelName(RFID_LOG_LEVEL, 'RFID')

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
CONFIG_SPARE = 25
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
        CONFIG_ACTIVE_LOOP: u'Active Loop',
        CONFIG_SPARE: u'[spare]'
}
DEFAULT_IPCFG = {
  u'IP':u'192.168.0.10',
  u'Netmask':u'255.255.255.0',
  u'Gateway':u'0.0.0.0',
  u'Host':u'192.168.0.255'	# default is broadcast :/
}
DEFPORT = u'/dev/ttyS0'		# fallback on serial

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
    ret |= ((val//10)&0x0f)<<4	# msd	97 -> 0x90
    ret |= (val%10)&0x0f	# lsd   97 -> 0x07
    return ret

def hexval2val(hexval):
    """Unconvert a decimal digit equivalent hex byte to int."""
    ret = 10*(hexval>>4)	# tens 0x97 -> 90
    ret += hexval&0x0f		# ones 0x97 ->  7
    return ret

class dgram(object):
    """UDP port object."""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.settimeout(0.2)
        self.s.bind(('', self.port))
        self.buf = ''	# these are not decoded!

    def read(self, sz=1):
        ret = u''	# check this condition
        # buf empty?
        if len(self.buf) == 0:
            nb, addr = self.s.recvfrom(4096)	# timeout raises exception
            if addr[0] == self.host:
                self.buf += nb
        if len(self.buf) > 0:
            ret = self.buf[0]
            self.buf = self.buf[1:]
        return ret

    def write(self, buf=''):
        return self.s.sendto(buf, (self.host, self.port))

    def close(self):
        return self.s.close()

class thbc(threading.Thread):
    """Tag Heuer Elite thread object class."""
    def __init__(self, port=None, name='thbc'):
        """Construct thread object.

        Named parameters:

          port -- serial port
          name -- text identifier for use in log messages

        """
        threading.Thread.__init__(self) 
        self.name = name

        self.port = None
        self.error = False
        self.errstr = ''
        self.unitno = u''		# fetched on reload
        self.decoderconfig = {}		# fetched on reload
        self.decoderipconfig = {}	# fetched on reload
        self.cqueue = Queue.Queue()	# command queue
        self.log = logging.getLogger(self.name)
        self.log.setLevel(logging.DEBUG)
        self.__cksumerr = 0
        self.__rdbuf = ''
        self.setcb()
        if port is not None:
            self.setport(port)

    def photothresh(self):
        """Return the relevant photo finish threshold."""
        return THBCPHOTOTHRESH		# allow override perhaps?

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
        """Request (re)opening port as specified.

        Device may be a port number or a device identifier string.
        For information on port numbers or strings, see the
        documentation for serial.Serial().

        Call setport with no argument, None, or an empty string
        to close an open port or to run the thread with no
        external device.

        """
        self.cqueue.put_nowait(('PORT', device))

    def sync(self):
        """Roughly synchronise Decoder to host PC clock."""
        self.cqueue.put_nowait(('SYNC', None))

    def sane(self):
        """Request sanity check in decoder thread."""
        self.cqueue.put_nowait(('SANE', None))

    def ipconfig(self):
        """Request sanity check in decoder thread."""
        self.cqueue.put_nowait(('IPCFG', None))

    def __ipcfg(self):
        """Alter the attached decoder's IP address."""
        #if type(self.port) is not serial.Serial:
            #self.log.error(u'Decoder not on serial link - IP update ignored.')
            #return False
        ipcfg = metarace.sysconf.get(u'thbc',u'ipconfig')
        cmd = chr(0x09) + chr(0x09)
        for i in [u'IP', u'Netmask', u'Gateway', u'Host']:
            if i not in ipcfg:
                ipcfg[i] = DEFAULT_IPCFG[i]
            cmd += socket.inet_aton(socket.gethostbyname(ipcfg[i]))
        self.log.info(u'Attempting IP config update...')
        self.__v3_cmd(cmd)
    
    def __sane(self):
        """Check decoder config against system settings."""
        doconf = False
        if self.unitno:	# set if config message parsed ok
            if metarace.sysconf.has_option(u'thbc',u'decoderconfig'):
                oconf = metarace.sysconf.get(u'thbc',u'decoderconfig')
                for flag in self.decoderconfig:
                    key = CONFIG_FLAGS[flag]
                    if key in oconf:
                        if oconf[key] != self.decoderconfig[flag]:
                            self.log.info(u'Key mismatch: ' + repr(key))
                            self.decoderconfig[flag] = oconf[key]
                            doconf = True

        # re-write config if required
        if doconf:
            self.log.info(u'Re-configuring decoder ' + repr(self.unitno))
            self.__set_config()

        # force decoder levels
        if metarace.sysconf.has_option(u'thbc',u'levels'):
            lvl = metarace.sysconf.get(u'thbc',u'levels')
            self.setlvl(box=lvl[0], sta=lvl[1])


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

    def start_session(self):
        """Send a depart command to decoder."""
        self.write(STARTCMD)

    def stop_session(self):
        """Send a stop command to decoder."""
        self.write(STOPCMD)

    def status(self):
        """Request status message from decoder."""
        self.write(STATCMD)

    def get_config(self):
        """Request decoder configuration."""
        self.write(QUECMD)

    def __v3_cmd(self, cmdstr=''):
        """Pack and send a v3 command: NOTE does not queue!"""
        crc = thbc_crc(cmdstr)
        crcstr = chr(crc>>8) + chr(crc&0xff)
        self.port.write(ESCAPE + cmdstr + crcstr + '>')
        # Danger: writes to port
        ##self.write(ESCAPE + cmdstr + crcstr + '>')

    def __serialise_config(self):
        """Convert current decoder setting into a config string"""
        obuf = bytearray(CONFIG_LEN)
        # fill in level bytes
        obuf[CONFIG_SPARE] = 0x13	# will be fixed by subsequent levelset
        obuf[CONFIG_SPARE+1] = 0x15

        # fill in tone values
	for opt in [CONFIG_TONE_STA, CONFIG_TONE_BOX, CONFIG_TONE_MAN,
                     CONFIG_TONE_CEL, CONFIG_TONE_BXX]:
            if opt in self.decoderconfig:
                obuf[opt] = val2hexval(self.decoderconfig[opt]//100) # xx00
                obuf[opt+1] = val2hexval(self.decoderconfig[opt]%100) # 00xx

        # fill in single byte values
        for opt in [CONFIG_TZ_HOUR, CONFIG_TZ_MIN, CONFIG_PROT,
                    CONFIG_PULSEINT, CONFIG_CELLTOD_HOUR, CONFIG_CELLTOD_MIN]:
            if opt in self.decoderconfig:
                obuf[opt] = val2hexval(self.decoderconfig[opt]%100) # ??

        # fill in flags
	for opt in [CONFIG_TOD, CONFIG_GPS, CONFIG_485, CONFIG_FIBRE,
	            CONFIG_PRINT, CONFIG_MAX, CONFIG_PULSE,
                    CONFIG_CELLSYNC, CONFIG_ACTIVE_LOOP]:
            if opt in self.decoderconfig:
                if self.decoderconfig[opt]:
                    obuf[opt] = 0x01
        return str(obuf)	# !! not unicode

    def __set_config(self):
        """Write current configuration to decoder."""
        cmd = chr(0x08) + chr(0x08) + self.__serialise_config()
        self.__v3_cmd(cmd)
        self.port.write(QUECMD)
        ##self.get_config()
       
    def __set_date(self, timestruct=None):
        """Set the date on the decoder."""
        if timestruct is None:
            timestruct = time.localtime()
        self.log.debug(u'Set date on decoder: '
                         + time.strftime('%Y-%m-%d',timestruct))
        cmd = chr(0x0a) + chr(0x0a)
        cmd += chr(0xff&timestruct[2])		# day
        cmd += chr(0xff&timestruct[1])		# day
        cmd += chr(0xff&(timestruct[0]-2000))	# year, after 2000
        self.__v3_cmd(cmd)

    def setlvl(self, box=u'10', sta=u'10'):
        """Set the read level on box and sta channels."""
        # TODO: verify opts
        self.write(BOXLVL + box.encode('ascii', 'ignore')[0:2])
        self.write(STALVL + sta.encode('ascii', 'ignore')[0:2])
        
    def replay(self, filename=''):
        """Read passings from file and process."""
        self.cqueue.put_nowait(('REPL', filename))

    def wait(self):
        """Suspend calling thread until the command queue is empty."""
        self.cqueue.join()

    def __set_time_cmd(self, t):
        """Return a set time command string for the provided time of day."""
        s = int(t.timeval)
        st = chr(s%60)
        mt = chr((s//60)%60)
        ht = chr(s//3600)
        return SETTIME + ht + mt + st + chr(0x74)

    def __parse_config(self, msg):
        # decoder configuration message.
        ibuf = bytearray(msg)
        #print(repr(ibuf))
        #self.log.debug(u'msg = ' + repr(msg))
        #self.log.debug(u'ibuf = ' + repr(ibuf))
        self.decoderconfig = {}
        #showflags = {}
        for flag in sorted(CONFIG_FLAGS):	# import all
            # tone values
            if flag in [CONFIG_TONE_STA, CONFIG_TONE_BOX, CONFIG_TONE_MAN,
                     CONFIG_TONE_CEL, CONFIG_TONE_BXX]:
                self.decoderconfig[flag] = 100*hexval2val(ibuf[flag])
                self.decoderconfig[flag] += hexval2val(ibuf[flag+1])
                #showflags[CONFIG_FLAGS[flag]] = self.decoderconfig[flag]

            # single byte values
            elif flag in [CONFIG_TZ_HOUR, CONFIG_TZ_MIN, CONFIG_PROT,
                    CONFIG_PULSEINT, CONFIG_CELLTOD_HOUR, CONFIG_CELLTOD_MIN]:
                self.decoderconfig[flag] = hexval2val(ibuf[flag])
                #showflags[CONFIG_FLAGS[flag]] = self.decoderconfig[flag]

            # 'booleans'
            elif flag in [CONFIG_TOD, CONFIG_GPS, CONFIG_485, CONFIG_FIBRE,
                    CONFIG_PRINT, CONFIG_MAX, CONFIG_PULSE,
                    CONFIG_CELLSYNC, CONFIG_ACTIVE_LOOP]:
                self.decoderconfig[flag] = bool(ibuf[flag])
                #if self.decoderconfig[flag]:
                    #showflags[CONFIG_FLAGS[flag]] = True

        self.unitno = u''
        for c in msg[43:47]:
            self.unitno += unichr(ord(c)+ord('0'))
        stalvl = hex(ord(msg[25]))	# ? question this
        boxlvl = hex(ord(msg[26]))
        self.log.info(u'Decoder: ' + repr(self.unitno) + u' connected.')

        # Decoder info
        self.log.debug(u'Decoder Levels: STA='+stalvl + u', BOX='+boxlvl)
        self.decoderipconfig[u'IP'] = socket.inet_ntoa(msg[27:31])
        self.decoderipconfig[u'Mask'] = socket.inet_ntoa(msg[31:35])
        self.decoderipconfig[u'Gateway'] = socket.inet_ntoa(msg[35:39])
        self.decoderipconfig[u'Host'] = socket.inet_ntoa(msg[39:43])
        for key in [u'IP', u'Mask', u'Gateway', u'Host']:
            self.log.debug(u'Decoder ' + key + ': ' 
                            + repr(self.decoderipconfig[key]))

    def __parse_message(self, msg, ack=True):
        """Return tod object from timing msg or None."""
        ret = None
        if len(msg) > 4:
            if msg[0] == PASSSTART:	# RFID message
                idx = msg.find('>')
                if idx == 37:		# Valid length
                    data = msg[1:33]
                    msum = msg[33:37]
                    tsum = thbc_sum(data)
                    if tsum == msum:	# Valid 'sum'
                        pvec = data.split()
                        istr = pvec[3] + ':' + pvec[5]
                        rstr = pvec[1].lstrip('0')
                        if pvec[5] == '3': # LOW BATTERY ALERT
                            self.log.warn('Low battery on id: ' + repr(rstr))
                        ret = tod.tod(pvec[2], index=istr, chan=pvec[0],
                                      refid=rstr, source=self.name)
                        self.log.log(RFID_LOG_LEVEL, msg.strip())
                        if ack:
                            self.port.write(ACKCMD)	# Acknowledge if ok
                        self.__cksumerr = 0
                    else:
                        self.log.info('Invalid checksum: ' 
                                      + repr(tsum) + ' != ' + repr(msum)
                                      + ' :: ' + repr(msg))
                        self.__cksumerr += 1
                        if self.__cksumerr > 3:
                            # assume error on decoder, so acknowledge and
                            # continue with log
                            # NOTE: This path is triggered when serial comms
                            # fail and a tag read happens before a manual trig
                            self.log.error('Erroneous message from decoder.')
                            if ack:
                                self.port.write(ACKCMD)	# Acknowledge if ok
                else:
                    self.log.info('Invalid message: ' + repr(msg))
            elif msg[0] == STATSTART:	# Status message
                data = msg[1:22]
                pvec = data.split()
                if len(pvec) == 5:
                    # Note: this path is not immune to error in stream
                    # but in the case of exception from tod construct
                    # it will be collected by the thread 'main loop'
                    rstr = ':'.join(pvec[1:])
                    ret = tod.tod(pvec[0].rstrip('"'), index='', chan='STS',
                                      refid=rstr, source=self.name)
                    self.log.log(RFID_LOG_LEVEL, msg.strip())
                else:
                    self.log.info('Invalid status: ' + repr(msg))
            elif '+++' == msg[0:3] and len(msg) > 53:
                self.__parse_config(msg[3:])
            else:
                self.log.log(RFID_LOG_LEVEL, repr(msg))
        else:        
            self.log.info('Short message: ' + repr(msg))
        return ret

    def __ipcompletion(self):
        """Blocking wait for ipconfig completion - horrible."""
        self.log.info(u'IP Config...')
        time.sleep(10)
        self.port.write(QUECMD)
 
    def __read(self):
        """Read messages from the decoder until a timeout condition."""
        ch = self.port.read(1)
        while ch != '':
            #if ch == NACK:
                # decoder has a passing to report
                #self.port.write(REPEATCMD)
                # 0x07 can appear in-band, so this is not allowed
            if ch == LF and len(self.__rdbuf) > 0 and self.__rdbuf[-1] == CR:
                # Return ends the current 'message', if preceeded by return
                self.__rdbuf += ch	# include trailing newline
                t = self.__parse_message(self.__rdbuf.lstrip('\0'))
                if t is not None:
                    glib.idle_add(self.__cb, t, priority=THBC_PRIORITY)
                self.__rdbuf = ''
            elif len(self.__rdbuf) > 40 and '\x1e\x86\x98' in self.__rdbuf:
                # Assume acknowledge from IP Command
                self.__rdbuf = ''
                self.__ipcompletion()
            else:
                self.__rdbuf += ch
            ch = self.port.read(1)

    def __readline(self, l):
        """Try to extract passing information from lines in a file."""
        t = self.__parse_message(l, False)
        if t is not None:
            glib.idle_add(self.__cb, t, priority=glib.PRIORITY_LOW)

    def __mkport(self, pstr):
        """Try to guess port type."""
        ret = None
        if u'/' not in pstr and u'.' in pstr:	# hack
            self.log.debug(u'Attempting UDP connection from ' + repr(pstr))
            ret = dgram(pstr, THBC_UDP_PORT)
        else:
            # assume device file
            ret = serial.Serial(pstr, THBC_BAUD,
                                      rtscts=False, timeout=0.2)
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
                    except socket.timeout:
                        pass
                    m = self.cqueue.get_nowait()
                else:
                    # when no read port avail, block on read of command queue
                    m = self.cqueue.get()
                self.cqueue.task_done()
                
                # Write phase
                if type(m) is tuple and type(m[0]) is str and m[0] in TCMDS:
                    if m[0] == 'MSG' and self.port and not self.error:
                        cmd = m[1] ##+ '\r\n'
                        self.log.debug('Sending rawmsg ' + repr(cmd))
                        self.port.write(cmd)
                    elif m[0] == 'TRIG':
                        if type(m[1]) is tod.tod:
                            self.log.log(RFID_LOG_LEVEL, str(m[1]))
                            glib.idle_add(self.__cb, m[1],
                                          priority=THBC_PRIORITY)
                    elif m[0] == 'SANE':
                        self.log.debug('Checking config.')
                        self.__sane()
                    elif m[0] == 'IPCFG':
                        self.log.debug('Updating Decoder IP.')
                        self.__ipcfg()
                    elif m[0] == 'SYNC':
                        t = tod.tod('now')
                        # DANGER: Busy wait may interfere with caller
                        while t-t.truncate(0) > tod.tod('0.02'):
                            t = tod.tod('now')
                        self.port.write(self.__set_time_cmd(t))
                        self.log.debug('Set time on decoder: ' + t.meridian())
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
                            self.unitno = u''
                            self.port.write(QUECMD) # re-identify decoder
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
            except socket.error as e:
                self.log.error('Network error: ' + str(type(e)) + str(e))
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
    print(t.refid + u'\t' + t.rawtime(2))
    return False

def showstatus(data=None):
    data.status()
    return True

def doconfig(t):
    t.sane()
    return False

def doipconfig(t):
    t.ipconfig()
    return False

def dostart(t):
    t.start_session()
    return False

def dosync(t):
    t.sync()
    return False

def dostop(t):
    t.stop_session()
    return False

if __name__ == "__main__":
    import metarace
    import gtk
    import time
    import random
    import json
    metarace.init()
    t = thbc()
    lh = logging.StreamHandler()
    #lh = logging.FileHandler(u'batlog')
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                      "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    t.log.addHandler(lh)
    try:
        t.start()
        #t.setport(u'192.168.95.240')
        #t.setport(u'192.168.96.254')
        #t.setport(u'192.168.96.231')
        #t.setport(u'/dev/ttyUSB0')
        t.setport(u'/dev/ttyS0')

        #t.set_date()	#?where did this go?
        t.setcb(printtag)
        glib.timeout_add_seconds(4, dostop, t)
        glib.timeout_add_seconds(8, doconfig, t)
        #glib.timeout_add_seconds(12, doipconfig, t)
        glib.timeout_add_seconds(20, dostart, t)
        glib.timeout_add_seconds(22, dosync, t)
        metarace.mainloop()
    except:
        t.stop_session()
        showconfig = {}
        for k in t.decoderconfig:
            key = k
            if k in CONFIG_FLAGS:
                key = CONFIG_FLAGS[k]
            showconfig[key] = t.decoderconfig[k]
        print(json.dumps(showconfig, indent=2, sort_keys=True))
        t.wait()
        t.exit('Exception')
        t.join()
        raise
