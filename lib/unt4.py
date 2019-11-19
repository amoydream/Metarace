
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

"""UNT4 Protocol Wrapper.

Pack and unpack Omega style UNT4 protocol messages.

Note: Normal program code will not use this library directly
      except to access the pre-defined overlay messages. All
      scoreboard communication should go via the sender or
      scbwin classes.

Messages are manipulated in unicode - it is up to the transport
layer to encode/decode as appropriate.

"""

# mode 1 constants
NUL = 0x00
SOH = 0x01
STX = 0x02
ETX = 0x03
EOT = 0x04
ACK = 0x06
BEL = 0x07
HOME= 0x08
CR  = 0x0d
LF  = 0x0a
ERL = 0x0b
ERP = 0x0c
DLE = 0x10
DC2 = 0x12
DC3 = 0x13
DC4 = 0x14
SYN = 0x16
ETB = 0x17
FS = 0x1c
GS = 0x1d
RS = 0x1e
US = 0x1f

DAKLINELEN = 32	# HACK and limit


ENCMAP = {
   unichr(SOH):'<O>',
   unichr(STX):'<T>',
   unichr(ETX):'<X>',
   unichr(EOT):'<E>',
   unichr(HOME):'<H>',
   unichr(CR):'<R>',
   unichr(LF):'<A>',
   unichr(ERL):'<L>',
   unichr(ERP):'<P>',
   unichr(DLE):'<D>',
   unichr(DC2):'<2>',
   unichr(DC3):'<3>',
   unichr(DC4):'<4>',
   unichr(FS):'<F>',
   unichr(GS):'<G>',
   unichr(RS):'<R>',
   unichr(US):'<U>'}
  
def encode(ubuf=u''):
    """Encode the unt4 buffer for use with IRC."""
    # escape special char
    ubuf = ubuf.replace(u'<', u'<>')
    # escape control chars
    for key in ENCMAP:
        ubuf = ubuf.replace(key, ENCMAP[key])
    return ubuf

def decode(ircbuf=u''):
    """Decode the irc buffer to unt4msg pack."""
    # decode nulls (is this required anymore?)
    ircbuf = ircbuf.replace(u'<00>',u'')
    # decode control chars
    for key in ENCMAP:
        ircbuf = ircbuf.replace(ENCMAP[key], key)
    # decode special char
    ircbuf = ircbuf.replace(u'<>', u'<')
    return ircbuf

# byte type checksum for dak -> note will break with unichars
def daksum(msgbody):
    sum = 0x00
    for c in msgbody:
        sum += ord(c)
    return u'{0:02X}'.format(sum & 0xff)

# UNT4 Packet class
class unt4(object):
    """UNT4 Packet Class."""
    def __init__(self, unt4str=None, 
                   prefix=None, header=u'', 
                   erp=False, erl=False, 
                   xx=None, yy=None, text=u''):
        """Constructor.

        Parameters:

          unt4str -- packed unt4 string, overrides other params
          prefix -- prefix byte <DC2>, <DC3>, etc
          header -- header string eg 'R_F$'
          erp -- true for general clearing <ERP>
          erl -- true for <ERL>
          xx -- packet's column offset 0-99
          yy -- packet's row offset 0-99
          text -- packet content string

        """
        self.prefix = prefix    # <DC2>, <DC3>, etc
        self.header = header    # ident text string eg 'R_F$'
        self.erp = erp          # true for general clearing <ERP>
        self.erl = erl          # true for <ERL>
        self.xx = xx            # input column 0-99
        self.yy = yy            # input row 0-99
        self.text = text        # text string - assume encoded already?
        if unt4str is not None:
            self.unpack(unt4str)

    def unpack(self, unt4str=u''):
        """Unpack the UNT4 unicode string into this object."""
        if len(unt4str) > 2 and unt4str[0] is unichr(SOH) \
                            and unt4str[-1] is unichr(EOT):
            self.prefix = None
            newhead = u''
            newtext = u''
            self.erl = False
            self.erp = False
            head = True		# All text before STX is considered header
            stx = False
            dle = False
            dlebuf = u''
            i = 1
            packlen = len(unt4str) - 1
            while i < packlen:
                och = ord(unt4str[i])
                if och == STX:
                    stx = True
                    head = False
                elif och == DLE and stx:
                    dle = True
                elif dle:
                    dlebuf += unt4str[i]
                    if len(dlebuf) == 4:
                        dle = False
                elif head:
                    if och in (DC2, DC3, DC4):
                        self.prefix = och   # assume pfx before head text
                    else:
                        newhead += unt4str[i]
                elif stx:
                    if och == ERL:
                        self.erl = True
                    elif och == ERP:
                        self.erp = True
                    else:
                        newtext += unt4str[i]
                i += 1
            if len(dlebuf) == 4:
                self.xx = int(dlebuf[:2])
                self.yy = int(dlebuf[2:])
            self.header = newhead
            self.text = newtext

    def altpack(self):
        """Return DAK protocol (Hisense/Dunc Gray)."""
        ret = u''
        if self.erp:
            # special case 1: write space to all chars
            pass
        elif self.xx is not None and self.yy is not None and self.text:
            oft = self.yy * DAKLINELEN + self.xx
            control = u'004010{0:04d}'.format(oft)
            header = u'20000000'
            text = (header + unichr(SOH) + control
                           + unichr(STX) + self.text + unichr(EOT))
            ret = unichr(SYN) + text + daksum(text) + unichr(ETB)
        elif self.header:	# assume well formed packet in self.header
            pass	# todo
        return ret

    def pack(self):
        """Return Omega Style UNT4 unicode string packet."""
        head = u''
        text = u''
        if self.erp:	# overrides any other message content
            text = unichr(STX) + unichr(ERP)
        else:
            head = self.header
            if self.prefix is not None:
                head = unichr(self.prefix) + head
            if self.xx is not None and self.yy is not None:
                text += unichr(DLE) + u'{0:02d}{1:02d}'.format(self.xx, self.yy)
            if self.text:
                text += self.text
            if self.erl:
                text += unichr(ERL)
            if len(text) > 0:
                text = unichr(STX) + text
        return unichr(SOH) + head + text + unichr(EOT)
 
# 'Constant' messages
GENERAL_CLEARING = unt4(erp=True)
OVERLAY_ON = unt4(header=u'OVERLAY ON')
OVERLAY_OFF = unt4(header=u'OVERLAY OFF')
OVERLAY_CLOCK = unt4(header=u'OVERLAY 01')
OVERLAY_MATRIX = unt4(header=u'OVERLAY 00')
OVERLAY_IMAGE = unt4(header=u'OVERLAY 02')
OVERLAY_BLANK = unt4(header=u'OVERLAY 03')
OVERLAY_GBLANK = unt4(header=u'overlay', text=u'0')
OVERLAY_GTITLE = unt4(header=u'overlay', text=u'2')
OVERLAY_GMATRIX = unt4(header=u'overlay', text=u'1')

# Invercargill 'flush'/'ack' packet
ALT_ACK = unt4(header=unichr(ACK))
