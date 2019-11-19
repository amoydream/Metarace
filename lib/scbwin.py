
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

"""Abstract and specific classes for scoreboard 'windows'.

This module provides a number of animated plaintext scoreboard window
objects for the display of lists, times and transitions.

A scoreboard window is a stateful information block that
may or may not be animated. All types of windows have the following
interface:

 reset()	reset state to start (calls redraw)
 pause()	toggle paused state, returns next val
 redraw()	redraw fixed screen elements
 update()	advance animation by one 'frame', caller is
		expected to repeatedly call update at ~20Hz

Specific scb-wins will have additional methods for setting internal
and incidental info.

Typical usage is something like:

    w = scbwintype(scb, typedata...)
    w.initstuff(typedata)
    w.reset()
    loop:
        w.update()

Shared properties for all scbwins:

	scb	A sender thread handle

Per-class init func should not draw onto screen, only redraw()
or first call to update() will emit anything to scb surface.

"""

import time		# for time.strftime() :(

import metarace
from metarace import strops
from metarace import unt4
from metarace import tod

PAGE_INIT=10		# delay before table data starts displaying
PAGE_DELAY=60		# def tenths of sec to hold each page of table
PAGE_ROWOFT=2		# first DHI row for table data
DATE_FMT=('%a %d/%m/%y')

def get_dateline(width=32):
    dpart = time.strftime(DATE_FMT) 
    tpart = tod.tod('now').meridian()
    ret = tpart 
    totlen = len(tpart)+len(dpart)
    if totlen >= width: # with a space
        ret = tpart.center(width)	# fall back to time
    else:
        ret = dpart + u' ' * (width-totlen) + tpart

    return ret

class scbwin(object):
    """Base class for all scoreboard windows.

    Classes inheriting from scbwin are required to override the
    update() and redraw() methods.

    """
    def __init__(self, scb=None):
        """Base class constructor."""
        self.paused = False
        self.scb = scb
        self.count = 0

    def reset(self):
        """Reset scbwin to initial state."""
        self.count = 0
        self.redraw()
        self.paused = False

    def pause(self, set=None):
        """Update the pause property.

        If param 'set' is not provided toggle the current pause state,
        otherwise update pause to equal 'set'.

        """
        if set is not None:
           self.paused = bool(set)
        else:
           self.paused = not self.paused
        return self.paused

    def redraw(self):
        """Virtual redraw method."""
        pass

    def update(self):
        """Virtual update method."""
        self.count += 1

class scbclock(scbwin):
    """Event clock window.

    Display event lines under a date and time string. Eg:

      012345678901234567890123
       Sat 02/02/10__12:12:12		'__' expands with W
      ------------------------
           CENTERED LINE 1
           CENTERED LINE 2
           CENTERED LINE 3

    """
    def __init__(self, scb=None, line1=u'', line2=u'', line3=u'',locstr=u''):
        scbwin.__init__(self, scb)
        self.line1 = line1
        self.line2 = line2
        self.line3 = line3
        self.locstr = locstr
        self.bodyoft = 1 # body text offset
        if self.scb.pagelen > 4:
            self.bodyoft = 2
        self.header = get_dateline(self.scb.linelen)

    def redraw(self):
        self.scb.setline(0, self.header,chan=u'#anascb')
        for i in range(1,self.scb.pagelen):
            self.scb.clrline(i,chan=u'#anascb')
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def update(self):
        """Animate the clock window.

        After an initial pause, animate the title lines onto
        the scorebord with approx 0.1s delay between lines.

        Date and time in the header are autmomatically updated
        from the system time.

        """
        if not self.paused:
            flush = False
            if self.count == 14:
                self.scb.setline(self.bodyoft,
                                 self.line1.strip().center(self.scb.linelen),chan=u'#anascb')
                flush = True
            if self.count == 16:
                self.scb.setline(self.bodyoft+1,
                                 self.line2.strip().center(self.scb.linelen),chan=u'#anascb')
                flush = True
            if self.count == 18:
                self.scb.setline(self.bodyoft+2,
                                 self.line3.strip().center(self.scb.linelen),chan=u'#anascb')
                flush = True
            if self.count == 20:
                self.scb.setline(self.bodyoft+4,
                                 self.locstr.strip().center(self.scb.linelen),chan=u'#anascb')
                flush = True
            if self.count % 2 == 0:
                next = get_dateline(self.scb.linelen)
                if next != self.header:
                    self.scb.setline(0, next,chan=u'#anascb')
                    flush = True
                    self.header = next
            self.count += 1
            if flush:
                self.scb.flush()

class scbtt(scbwin):
    """Pursuit/ITT/Teams Timer window.

    Display a pursuit/time trial timer window with two names
    and two time values. Time values are copied onto the overlay
    within the update() method. No time calculations are conducted,
    this class only displays the strings provided.

    Example:

        012345678901234567890123
              Prefix Info
        ------------------------
        12 Blackburn Team 1
        >>>>>>>>(1) hh:mm:ss.dcm 
        10 Blackburn Team 2
        >>>>>>>>(3) hh:mm:ss.dcm 

    """
    def __init__(self, scb=None, header=u'',line1=u'', line2=u'',subheader=u''):
        scbwin.__init__(self, scb)
        self.header = header.strip().center(self.scb.linelen)
        self.subheader = subheader.strip().center(self.scb.linelen)
        self.line1 = line1
        self.nextline1 = line1
        self.line2 = line2
        self.nextline2 = line2
        self.curt1 = u''
        self.nextt1 = u''
        self.curr1 = u''
        self.nextr1 = u''
        self.curt2 = u''
        self.nextt2 = u''
        self.curr2 = u''
        self.nextr2 = u''
        self.singleoft = 0

    def set_single(self):
        self.singleoft = 1

    def redraw(self):
        clrset = list(range(0,self.scb.pagelen))
        self.scb.setline(0, self.header,chan=u'#anascb')
        clrset.remove(0)
        self.scb.setline(1, self.subheader,chan=u'#anascb')
        clrset.remove(1)
        self.scb.setline(2+self.singleoft, self.line1,chan=u'#anascb')
        clrset.remove(2+self.singleoft)
        self.scb.setline(4+self.singleoft, self.line2,chan=u'#anascb')
        clrset.remove(4+self.singleoft)
        # only clear rows not already set above.
        for i in clrset:
            self.scb.clrline(i,chan=u'#anascb')
        self.nextline1 = self.line1
        self.nextline2 = self.line2
        self.curt1 = u''
        self.nextt1 = u''
        self.curr1 = u''
        self.nextr1 = u''
        self.curt2 = u''
        self.nextt2 = u''
        self.curr2 = u''
        self.nextr2 = u''
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def setline1(self, line1str=u''):
        """Replace the line 1 text."""
        self.nextline1 = line1str
 
    def setline2(self, line2str=u''):
        """Replace the line 2 text."""
        self.nextline2 = line2str
 
    def sett1(self, timestr=u''):
        """Set the next front straight time string."""
        self.nextt1 = timestr

    def sett2(self, timestr=u''):
        """Set the next back straight time string."""
        self.nextt2 = timestr

    def setr1(self, rank=u''):
        """Set the next front straight rank string."""
        self.nextr1 = rank

    def setr2(self, rank=u''):
        """Set the next back straight rank string."""
        self.nextr2 = rank

    def update(self):
        """If any time or ranks change, copy new value onto overlay."""
        if not self.paused:
            flush = False
            if self.curr1 != self.nextr1 or self.curt1 != self.nextt1:
                self.scb.setline(3+self.singleoft,
                      strops.truncpad(self.nextr1, self.scb.linelen - 13,
                                      'r') + u' ' + self.nextt1,chan=u'#anascb')
                self.curr1 = self.nextr1
                self.curt1 = self.nextt1
                flush = True
            if self.curr2 != self.nextr2 or self.curt2 != self.nextt2:
                self.scb.setline(5,
                      strops.truncpad(self.nextr2, self.scb.linelen - 13,
                                      'r') + u' ' + self.nextt2,chan=u'#anascb')
                self.curr2 = self.nextr2
                self.curt2 = self.nextt2
                flush = True
            if self.line1 != self.nextline1:
                self.line1 = self.nextline1
                self.scb.setline(2+self.singleoft,
                                 self.nextline1,chan=u'#anascb')
                flush = True
            if self.line2 != self.nextline2:
                self.line2 = self.nextline2
                self.scb.setline(4+self.singleoft,
                                 self.nextline2,chan=u'#anascb')
                flush = True
            self.count += 1
            if flush:
                self.scb.flush()

class scbttvw(scbwin):
    """Pursuit/Teams Timer window.

    Display a pursuit/time trial timer window with two team names,
    riders and two time values. Time values are copied onto the overlay
    within the update() method. No time calculations are conducted,
    this class only displays the strings provided.

    Example:

        012345678901234567890123
              Prefix Info
        ------------------------
        12 Blackburn Team 1
        Rider>>>>>>>Rider
        Rider>>>>>>>Rider
        >>>>>>>>(1) hh:mm:ss.dcm 
        10 Blackburn Team 2
        Rider>>>>>>>Rider
        Rider>>>>>>>Rider
        >>>>>>>>(3) hh:mm:ss.dcm 

    """
    def __init__(self, scb=None, header=u'',line1=u'', line2=u'', line3=u'', line4=u'', line5=u'', line6=u'',subheader=u''):
        scbwin.__init__(self, scb)
        self.header = header.strip().center(self.scb.linelen)
        self.subheader = subheader.strip().center(self.scb.linelen)
        self.line1 = line1
        self.nextline1 = line1
        self.line2 = line2
        self.nextline2 = line2
        self.line3 = line3
        self.nextline3 = line3
        self.line4 = line4
        self.nextline4 = line4
        self.line5 = line5
        self.nextline5 = line5
        self.line6 = line6
        self.nextline6 = line6
        self.curt1 = u''
        self.nextt1 = u''
        self.curr1 = u''
        self.nextr1 = u''
        self.curt2 = u''
        self.nextt2 = u''
        self.curr2 = u''
        self.nextr2 = u''
        self.curt3 = u''
        self.nextt3 = u''
        self.curr3 = u''
        self.nextr3 = u''
        self.curt4 = u''
        self.nextt4 = u''
        self.curr4 = u''
        self.nextr4 = u''
        self.curt5 = u''
        self.nextt5 = u''
        self.curr5 = u''
        self.nextr5 = u''
        self.curt6 = u''
        self.nextt6 = u''
        self.curr6 = u''
        self.nextr6 = u''
        self.singleoft = 0

    def set_single(self):
        self.singleoft = 1

    def redraw(self):
        clrset = list(range(0,self.scb.pagelen))
        self.scb.setline(0, self.header,chan=u'#anascb')
        clrset.remove(0)
        self.scb.setline(1, self.subheader,chan=u'#anascb')
        clrset.remove(1)
        self.scb.setline(2+self.singleoft, self.line1,chan=u'#anascb')
        clrset.remove(2+self.singleoft)
        self.scb.setline(3+self.singleoft, self.line2,chan=u'#anascb')
        clrset.remove(3+self.singleoft)
        self.scb.setline(4+self.singleoft, self.line3,chan=u'#anascb')
        clrset.remove(4+self.singleoft)
        self.scb.setline(6+self.singleoft, self.line4,chan=u'#anascb')
        clrset.remove(6+self.singleoft)
        self.scb.setline(7+self.singleoft, self.line5,chan=u'#anascb')
        clrset.remove(7+self.singleoft)
        self.scb.setline(8+self.singleoft, self.line6,chan=u'#anascb')
        clrset.remove(8+self.singleoft)
        # only clear rows not already set above.
        for i in clrset:
            self.scb.clrline(i,chan=u'#anascb')
        self.nextline1 = self.line1
        self.nextline2 = self.line2
        self.nextline3 = self.line3
        self.nextline4 = self.line4
        self.nextline5 = self.line5
        self.nextline6 = self.line6
        self.curt1 = u''
        self.nextt1 = u''
        self.curr1 = u''
        self.nextr1 = u''
        self.curt2 = u''
        self.nextt2 = u''
        self.curr2 = u''
        self.nextr2 = u''
        self.curt3 = u''
        self.nextt3 = u''
        self.curr3 = u''
        self.nextr3 = u''
        self.curt4 = u''
        self.nextt4 = u''
        self.curr4 = u''
        self.nextr4 = u''
        self.curt5 = u''
        self.nextt5 = u''
        self.curr5 = u''
        self.nextr5 = u''
        self.curt6 = u''
        self.nextt6 = u''
        self.curr6 = u''
        self.nextr6 = u''
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def setline1(self, line1str=u''):
        """Replace the line 1 text."""
        self.nextline1 = line1str
 
    def setline2(self, line2str=u''):
        """Replace the line 2 text."""
        self.nextline2 = line2str
        
    def setline3(self, line3str=u''):
        """Replace the line 3 text."""
        self.nextline3 = line3str
        
    def setline4(self, line4str=u''):
        """Replace the line 4 text."""
        self.nextline4 = line4str
        
    def setline5(self, line5str=u''):
        """Replace the line 5 text."""
        self.nextline5 = line5str
        
    def setline6(self, line6str=u''):
        """Replace the line 6 text."""
        self.nextline6 = line6str
 
    def sett1(self, timestr=u''):
        """Set the next front straight time string."""
        self.nextt1 = timestr

    def sett2(self, timestr=u''):
        """Set the next back straight time string."""
        self.nextt2 = timestr

    def setr1(self, rank=u''):
        """Set the next front straight rank string."""
        self.nextr1 = rank

    def setr2(self, rank=u''):
        """Set the next back straight rank string."""
        self.nextr2 = rank

    def update(self):
        """If any time or ranks change, copy new value onto overlay."""
        if not self.paused:
            flush = False
            if self.curr1 != self.nextr1 or self.curt1 != self.nextt1:
                self.scb.setline(5+self.singleoft,
                      strops.truncpad(self.nextr1, self.scb.linelen - 13,
                                      'r') + u' ' + self.nextt1,chan=u'#anascb')
                self.curr1 = self.nextr1
                self.curt1 = self.nextt1
                flush = True
            if self.curr2 != self.nextr2 or self.curt2 != self.nextt2:
                self.scb.setline(9,
                      strops.truncpad(self.nextr2, self.scb.linelen - 13,
                                      'r') + u' ' + self.nextt2,chan=u'#anascb')
                self.curr2 = self.nextr2
                self.curt2 = self.nextt2
                flush = True
            if self.line1 != self.nextline1:
                self.line1 = self.nextline1
                self.scb.setline(2+self.singleoft,
                                 self.nextline1,chan=u'#anascb')
                flush = True
            if self.line2 != self.nextline2:
                self.line2 = self.nextline2
                self.scb.setline(3+self.singleoft,
                                 self.nextline2,chan=u'#anascb')
                flush = True
            if self.line3 != self.nextline3:
                self.line3 = self.nextline3
                self.scb.setline(4+self.singleoft,
                                 self.nextline3,chan=u'#anascb')
                flush = True
            if self.line4 != self.nextline4:
                self.line4 = self.nextline4
                self.scb.setline(6+self.singleoft,
                                 self.nextline4,chan=u'#anascb')
                flush = True  
            if self.line5 != self.nextline5:
                self.line5 = self.nextline5
                self.scb.setline(7+self.singleoft,
                                 self.nextline5,chan=u'#anascb')
                flush = True
            if self.line6 != self.nextline6:
                self.line6 = self.nextline6
                self.scb.setline(8+self.singleoft,
                                 self.nextline6,chan=u'#anascb')
                flush = True  
            self.count += 1
            if flush:
                self.scb.flush()

                
class scbtimer(scbwin):
    """Sprint timer window with avg speed.

    Copy provided time strings into pre-determined fields
    on the overlay. No time calcs are performed - this module
    only works on strings.

    Example:

        012345678901234567890123
          Blahface Point Score
          intermediate sprint
        ------------------------
              200m: hh:mm:ss.000
               Avg:  xx.yyy km/h

    """
    def __init__(self, scb=None, line1=u'', line2=u'',
                 timepfx=u'', avgpfx=u'Avg:'):
        scbwin.__init__(self, scb)
        self.line1 = line1
        self.line2 = line2
        self.timepfx = timepfx
        self.avgpfx = avgpfx
        self.curtime = u''
        self.nexttime = u''
        self.curavg = u''
        self.nextavg = u''

    def redraw(self):
        clrset = list(range(0,self.scb.pagelen))
        self.scb.setline(0, self.line1.strip().center(self.scb.linelen),chan=u'#anascb')
        clrset.remove(0)
        self.scb.setline(1, self.line2.strip().center(self.scb.linelen),chan=u'#anascb')
        clrset.remove(1)
        #self.scb.setline(3, strops.truncpad(self.timepfx, 
                             #self.scb.linelen - 13, 'r'),chan=u'#anascb')
        #clrset.remove(3)
        for i in clrset:
            self.scb.clrline(i,chan=u'#anascb')

        self.curtime = u''
        self.nexttime = u''
        self.curavg = u''
        self.nextavg = u''
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def settime(self, timestr=u''):
        """Set the next time speed string."""
        self.nexttime = timestr

    def setavg(self, avgstr=u''):
        """Set the next average speed string."""
        self.nextavg = avgstr

    def update(self):
        """If time or avg change, copy new value onto overlay."""
        if not self.paused:
            flush = False
            if self.curtime != self.nexttime:
                #self.scb.postxt(3, self.scb.linelen - 13,
                     #strops.truncpad(self.nexttime,12,'r'), chan=u'#anascb')
                self.scb.setline(3,
                  strops.truncpad(self.timepfx, self.scb.linelen - 13, 'r')
                  +strops.truncpad(self.nexttime, 13, 'r'),
                  chan=u'#anascb')
                self.curtime = self.nexttime
                flush = True
            if self.curavg != self.nextavg:
                self.scb.setline(4,
                  strops.truncpad(self.avgpfx, self.scb.linelen - 13, 'r')
                  +strops.truncpad(self.nextavg, 12, 'r'),
                  chan=u'#anascb')	# timestr above provides 4places?
                self.curavg = self.nextavg
                flush = True
            self.count += 1
            if flush:
                self.scb.flush()

# A rider intro screen
#
# Line 1: 'header' displays immediately
# Line 2: number/hcap/'teaser' displays after 10th delay
# Line 3: Name/info types out char by char after 2-10th delay
# Line 4: (optional) types out after further 2-10th delay
#
## BROKEN AND UNUSED !!
#
# Pauses at completion of draw
class scbintro(scbwin):
    """Rider intro screen.

        Line 1: 'header' displays immediately
        Line 2: number/hcap/'teaser' displays after 10th delay
        Line 3: Name/info types out char by char after 2-10th delay
        Line 4: (optional) types out after further 2-10th delay
    
    """
    def redraw(self):
        self.scb.setline(0, self.header, chan=u'#anascb')
        for i in range(1,self.scb.pagelen):
            self.scb.clrline(i,chan=u'#anascb')
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def update(self):
        if not self.paused:
            flush = False
            msgstart = PAGE_INIT + 6
            msgend = msgstart + 25
            if self.count == PAGE_INIT:	# draw prompt at ~+0.5s
                self.scb.setline(2,self.prompt,chan=u'#anascb')
                flush = True
            elif self.count >= msgstart and self.count < msgend:
                oft = self.count-msgstart
                if len(self.info) > oft:
                    self.scb.postxt(3, oft, self.info[oft], chan=u'#anascb')
                    flush = True
            elif self.count == msgend:
                self.scb.setline(3, self.info,chan=u'#anascb')
                flush = True
                self.paused = True
            self.count += 1
            if flush:
                self.scb.flush()

    def setinfo(self, prompt=u'', info=u'', xtra=u''):
        """Update overlay info."""
        self.prompt = prompt[0:32]
        self.info = info[0:32]
        self.xtra = xtra[0:32]

    def __init__(self, scb=None, head=u'', lines=5):
        scbwin.__init__(self, scb)
        self.header = head
        self.lines = 5
        self.prompt = u''
        self.info = u''
        self.xtra = u''
        if lines != 5:
            self.lines = 7

class logoanim(scbwin):
    """Animated 'logo' screen."""
    ## NOTE: No effect on text board
    def redraw(self):
        pass

    def update(self):
        if not self.paused:
            if self.count % self.delay == 0:
                 dbline = 18
                 overlay = unt4.OVERLAY_IMAGE
                 # Alternate overlay
                 if self.curpg == 0:
                     self.curpg = 1
                     dbline = 19
                     overlay = unt4.OVERLAY_BLANK
                 else:
                     self.curpg = 0

                 # Set image content
                 curidx = (self.count//self.delay) % len(self.llist)
                 if self.llist[curidx] == u'CLOCK':
                     overlay = unt4.OVERLAY_CLOCK
                 else:
                     self.scb.setline(dbline, self.llist[curidx],chan=u'#anascb') # set img
                 
                 # select overlay
                 self.scb.setoverlay(overlay,chan=u'#anascb')
                 if len(self.llist) == 1:
                     self.paused = True
            self.count += 1

    def set_logos(self, logostr):
        self.llist = []
        for l in logostr.split():
            self.llist.append(l)
        if len(self.llist) == 0:
            self.llist.append(u'')	# ensure one entry
 
    def __init__(self, scb=None, logolist=u'', delay=100):
        scbwin.__init__(self, scb)
        self.curpg = 0
        self.llist = []
        self.delay = delay
        self.set_logos(logolist)

class scbtest(scbwin):
    """A "test pattern" that loops over all the overlays."""
    def redraw(self):
        for i in range(0,self.scb.pagelen):
            self.scb.setline(i, u'Line ' + unicode(i),chan=u'#anascb') 
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def update(self):
        if not self.paused:
            if self.count % 40 == 0:
                 #self.scb.setoverlay(self.ovrs[self.curov])
                 self.curov = (self.curov + 1) % 4
            self.count += 1

    def __init__(self, scb=None, head=u'', lines=5):
        scbwin.__init__(self, scb)
        self.ovrs = [unt4.OVERLAY_CLOCK,
                     unt4.OVERLAY_MATRIX,
                     unt4.OVERLAY_IMAGE,
                     unt4.OVERLAY_BLANK]
        self.curov = 0


class scbintsprint(scbwin):
    """Intermediate sprint window - scrolling table, with 2 headers.

    Parameters coldesc and rows as per scbtable)

    """
    def loadrows(self, coldesc=None, rows=None):
        self.rows=[]
        if coldesc is not None and rows is not None:
            for row in rows:
                nr = u''
                oft = 0
                for col in coldesc:
                    if type(col) in [unicode, str]:
                        nr += col
                    else:
                        if len(row) > oft:	# space pad 'short' rows
                            nr += strops.truncpad(row[oft], col[0], col[1])
                        else:
                            nr += u' ' * col[0]
                        oft += 1
                self.rows.append(nr[0:32])
        self.nrpages = len(self.rows)//self.pagesz + 1
        if self.nrpages > 1 and len(self.rows) % self.pagesz == 0:
            self.nrpages -= 1
        # avoid hanging residual by scooting 2nd last entry onto
        # last page with a 'dummy' row, or scoot single line down by one
        if len(self.rows) % self.pagesz == 1:
            self.rows.insert(len(self.rows) - 2, u' ')

    def redraw(self):
        self.scb.setline(0, self.line1.strip().center(self.scb.linelen),chan=u'#anascb')
        self.scb.setline(1, self.line2.strip().center(self.scb.linelen),chan=u'#anascb')
        for i in range(2,self.scb.pagelen):
            self.scb.clrline(i,chan=u'#anascb')
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def update(self):
        if self.count%2 == 0 and self.count > PAGE_INIT: # wait ~1/2 sec
            lclk = (self.count - PAGE_INIT) // 2
	    cpage = (lclk//self.delay) % self.nrpages
            pclk = lclk%self.delay
            flush = False
            if pclk < self.pagesz + 1:
                if pclk != self.pagesz:
                    self.scb.clrline(self.rowoft + pclk,chan=u'#anascb')
                    flush = True
                elif self.nrpages == 1:
                    self.count += 1
                    self.paused = True       # no animate on single page
                if pclk != 0:
                    roft = self.pagesz * cpage + pclk-1
                    if roft < len(self.rows):
                        self.scb.setline(self.rowoft + pclk-1,
                                         self.rows[roft],chan=u'#anascb')
                        flush = True
            if flush:
                self.scb.flush()
        if not self.paused:
            self.count += 1

    def __init__(self, scb=None, line1=u'', line2=u'',
                 coldesc=None, rows=None, delay=PAGE_DELAY):
        scbwin.__init__(self, scb)
        self.pagesz = 4
        self.nrpages = 0
        self.delay = delay
        self.rowoft = 3		# check for < 7

        # prepare header
        self.line1 = line1[0:self.scb.linelen]
        self.line2 = line2[0:self.scb.linelen]

        # load rows
        self.rows = []		# formatted rows
        self.loadrows(coldesc, rows)

class scbtable(scbwin):
    """A self-looping info table.

    Displays 'pages' of rows formatted to coldesc:
   
    Coldesc: set of column tuples, each containing a field width
             as integer and the string 'l' or 'r' for left
             or right space padded, or a string constant
   
	       [(fieldwidth, l|r)|'str' ...]
   
    Example:  [(3,'r'), ' ', '(20,'l')]
 		   Three columns:
			   1: 3 character str padded to right
			   2: constant string ' '
			   3: 20 character str padded to left

    ADDED: timepfx and timestr for appending a time field to results

    """
    def loadrows(self, coldesc=None, rows=None):
        self.rows=[]
        if coldesc is not None and rows is not None:
            for row in rows:
                nr = u''
                oft = 0
                for col in coldesc:
                    if type(col) in [unicode, str]:	# HACK py<3.0
                        nr += col
                    else:
                        if len(row) > oft:	# space pad 'short' rows
                            nr += strops.truncpad(row[oft], col[0], col[1])
                        else:
                            nr += u' ' * col[0]
                        oft += 1
                self.rows.append(nr)	# truncation in sender ok
        self.nrpages = len(self.rows)//self.pagesz + 1
        if self.nrpages > 1 and len(self.rows) % self.pagesz == 0:
            self.nrpages -= 1
        # avoid hanging residual by scooting 2nd last entry onto
        # last page with a 'dummy' row, or scoot single line down by one
        if len(self.rows) % self.pagesz == 1:
            self.rows.insert(len(self.rows) - 2, u' ')

    def redraw(self):
        self.scb.setline(0, self.header.center(self.scb.linelen),chan=u'#anascb')
        j = 1
        if self.rowoft == 2:	# space for subheader
            self.scb.setline(1, self.subhead.center(self.scb.linelen),chan=u'#anascb')
            j = 2
        for i in range(j,self.scb.pagelen):
            self.scb.clrline(i,chan=u'#anascb')
        self.scb.setoverlay(unt4.OVERLAY_MATRIX,chan=u'#anascb')
        self.scb.flush()

    def update(self):
        # if time field set and not a round number of rows, append
        # time line to last row of last page
        if self.count%2 == 0 and self.count > PAGE_INIT: # wait ~1/2 sec
            lclk = (self.count - PAGE_INIT) // 2
	    cpage = (lclk//self.delay) % self.nrpages
            pclk = lclk%self.delay
            flush = False
            if pclk < self.pagesz + 1:
                if pclk != self.pagesz:
                    self.scb.clrline(self.rowoft + pclk,chan=u'#anascb')
                    flush = True
                else:	# end of page
                    if self.nrpages == 1:
                        self.count += 1
                        self.paused = True # no further animate on single page
                    if self.timestr is not None:
                        self.scb.setline(self.rowoft + pclk,
                                 strops.truncpad(self.timepfx,
                                  self.scb.linelen - 13, 'r')
                                      + u' ' + self.timestr[0:12],chan=u'#anascb')
                flush = True
                if pclk != 0:
                    roft = self.pagesz * cpage + pclk-1
                    if roft < len(self.rows):
                        self.scb.setline(self.rowoft + pclk-1,
                                         self.rows[roft],chan=u'#anascb')
                        flush = True
            if flush:
                self.scb.flush()
        if not self.paused:
            self.count += 1

    def __init__(self, scb=None, head=u'', subhead=u'',
                 coldesc=None, rows=None, pagesz=None,
                 timepfx=u'', timestr=None, delay=PAGE_DELAY):
        scbwin.__init__(self, scb)
        # set page size
        self.pagesz = self.scb.pagelen - 2
        # page row offset ... hardcoded for now
        self.rowoft = 2
        self.nrpages = 0
        self.delay = delay
        self.timestr = timestr
        self.timepfx = timepfx
        if pagesz and pagesz > 5:
            self.pagesz = 6		# grab a line from the top
            self.rowoft = 2

        if self.timestr is not None:
            self.pagesz -= 1		# grab a line from the bottom

        # prepare header -> must be preformatted
        if pagesz < 6:	# this is a hack for the madison, maybe replace?
            self.header = head[0:self.scb.linelen].strip()
        else:
            self.header = head[0:self.scb.linelen]
        self.subhead = subhead

        # load rows
        self.rows = []		# formatted rows
        self.loadrows(coldesc, rows)

# TODO: Tests for all window types.
