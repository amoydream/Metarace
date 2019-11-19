
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
from __future__ import (division, print_function, absolute_import)

"""Basic string filtering, truncation and padding."""

import re
import os
import random

# replace codepoints 0->255 with space unless overridden
# "protective" against unencoded ascii strings and control chars
SPACEBLOCK = u''
for i in xrange(0,256):
    SPACEBLOCK += unichr(i)

# unicode translation 'map' class
class unicodetrans:
  def __init__(self, keep=u'', replace=SPACEBLOCK, replacechar=u' '):
    self.comp = dict((ord(c),replacechar) for c in replace)
    for c in keep:
        self.comp[ord(c)] = c
  def __getitem__(self, k):	# override to return a None
    return self.comp.get(k)

INTEGER_UTRANS = unicodetrans(u'-0123456789')
NUMERIC_UTRANS = unicodetrans(u'-0123456789.e')
PLACELIST_UTRANS = unicodetrans(
u'-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
PLACESERLIST_UTRANS = unicodetrans(
u'-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
BIBLIST_UTRANS = unicodetrans(
u'0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
RIDERNO_UTRANS = unicodetrans(
u'0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',u'',u'')
BIBSERLIST_UTRANS = unicodetrans(
u'.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
WEBFILE_UTRANS = unicodetrans(
u'_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',u'.',u'_')
# special case: map controls and spaces, but keep everything else
PRINT_UTRANS = {}
for cp in xrange(0,0x20):
    PRINT_UTRANS[cp] = u' '
for cp in xrange(0x7f,0xa1):
    PRINT_UTRANS[cp] = u' '
for cp in xrange(0x2000,0x200B):
    PRINT_UTRANS[cp] = u' '
PRINT_UTRANS[0x1680] = u' '
PRINT_UTRANS[0x180e] = u' '
PRINT_UTRANS[0x202f] = u' '
PRINT_UTRANS[0x205f] = u' '
PRINT_UTRANS[0x3000] = u' '
PRINT_UTRANS[0xffa0] = u' '

# timing channels - this duplicates defs in timy, but breaks the dep
# strops should not have any dependencies
CHAN_START = 0
CHAN_FINISH = 1
CHAN_PA = 2
CHAN_PB = 3
CHAN_200 = 4
CHAN_100 = 5
CHAN_50 = 0  # TODO: use for AUX line into C0
CHAN_AUX = 6 # channels 6-8 are not connected with original TSW-1 cable
CHAN_7 = 7
CHAN_8 = 8
CHAN_INT = 9  # complete the keypad - a key not from timy
CHAN_UNKNOWN = -1

# running number comparisons
RUNNER_NOS = {
 u'red': 0,
 u'whi': 1,
 u'blu': 2,
 u'yel': 3,
 u'grn': 4,
 u'pin': 5,
 u'bla': 6,
 u'gry': 7,
 u'ora': 8,
 u'pur': 9,
 u'rdw': 10,
 u'blw': 11,
 u'ylw': 12,
 u'grw': 13
}

# UCI penalties: EN/Track
UCITRACKCODES = {
 "A": "WARNING",
 "B": "FINED",
 "C": "RELEGATION",
 "D": "DISQUALIFICATION"
}

UCITRACKPEN = {
 u'1': u'for not holding his/her line during the final sprint',
 u'2': u'for riding on the blue band during the sprint',
 u'3': u'for deliberately riding on the blue band during the race',
 u'4': u'for not having held his/her line during the last 200 metres of the race',
 u'5': u'for irregular movement to prevent opponent from passing',
 u'6': u'for dangerous riding in the final bend',
 u'7': u'for dangerous riding during the race',
 u'8': u"for entering the sprinter's lane when the opponent was already there",
 u'9': u'for moving down towards the inside of the track when a rival was already there',
 u'10': u'for moving down towards the inside of the track and forcing the other competitor off the track',
 u'11': u'for crowding an opponent with the intention of causing them to slow down',
 u'12': u'for moving outward with the intention of forcing the opponent to go up',
 u'13': u'for going down too quickly after overtaking his/her opponent',
 u'14': u'for deliberate and flagrant fault against [ext]',
 u'15': u'for causing the crash of his/her opponent',
 u'16': u'for having blocked an opponent',
 u'17': u'for being late at the start line',
 u'18': u'for wearing only one number',    
 u'19': u'for incorrect gestures',
 u'20': u'for incorrect behaviour',
 u'21': u'for pushing his/her rival', 
 u'22': u'for carrying publicity on the back of the racing jersey',
 u'23': u'for incorrect behaviour or disrespect towards an official',
 u'24': u'for foling or mutilating the race number',    
 u'25': u'for improper advertising on national jersey or short',    
 u'26': u'qualified but did not start without justification',
 u'27': u'for protest with hands off handlebar',
 u'28': u'for using two persons to give information the team/rider',    
 u'29': u'for not being ready with extra wheels or other equipment at the start',    
 u'30': u'for ignoring commissaires instructions to leave track after being overlapped',
 u'31': u'for failure to obey commissaires instructions',
 u'32': u'for failing to maintain proper control of the bicycle',
 u'33': u'for taking off his/her helmet when on the track after passing the finish line'
}

def img_filename(path, basename=u''):
    """Return the 'best' filename for the given basename image."""
    ret = None
    srchs = [u'svg', u'eps', u'pdf', u'png', u'jpg', u'bmp']
    basename = basename.rstrip(u'.')
    for xtn in srchs:
        checka = os.path.join(path, basename + u'.' + xtn)
        if os.path.isfile(checka):
            ret = checka
            break
    return ret

DNFCODEMAP = { u'hd': 0,
               u'dsq': 1,
               u'dnf': 3,
               u'dns': 4,
               u'': 2}

def cmp_dnf(x, y):
    """Comparison func for two dnf codes."""
    if x not in DNFCODEMAP:
        x = u''
    if y not in DNFCODEMAP:
        y = u''
    return cmp(DNFCODEMAP[x], DNFCODEMAP[y])

def get_ucicode(x):
    """Return the UCI Code Descirption."""
    return UCITRACKCODES.get(x)

def get_ucipen(y):
    """Return the UCI Penalty Description."""
    return UCITRACKPEN.get(y)

def riderno_key(bib):
    """Return a comparison key for sorting rider number strings."""
    return bibstr_key(bib)

def dnfcode_key(code):
    """Return a dnf code sorting key."""
    # '' [rel] dsq hd|otl dnf dns
    dnfordmap = {u'':0, u'rel':2,
                 u'dsq':2,
                 u'hd':3,u'otl':3,
                 u'dnf':4,
                 u'dns':5}
    ret = 0
    if code is not None:
        code = code.lower()
        if code in dnfordmap:
            ret = dnfordmap[code]
    return ret

def bibstr_key(bibstr=u''):
    """Return a comparison key for sorting rider bib.ser strings."""
    (bib, ser) = bibstr2bibser(bibstr)
    bval = 0
    if bib.isdigit():
        bval = int(bib)
    else:
        sbib = bib.translate(INTEGER_UTRANS).strip()
        if sbib and sbib.isdigit():
            bval = int(sbib)
        else:
            if bib.lower()[0:3] in RUNNER_NOS:
                bval = RUNNER_NOS[bib.lower()[0:3]]
            else:
                bval = id(bib)
    sval = 0
    if ser != u'':
        sval = ord(ser[0])<<12
    return sval | (bval&0xfff)

def randstr():
    """Return a string of random digits."""
    return unicode(random.randint(10000,99999))

def promptstr(prompt=u'', value=u''):
    """Prefix a non-empty string with a prompt, or return empty."""
    ret = u''
    if value:
        ret = prompt + u' ' + value
    return ret

def listsplit(liststr=u''):
    """Return a split and stripped list."""
    ret = []
    for e in liststr.split(u','):
        ret.append(e.strip())
    return ret

def heatsplit(heatstr):
    """Return a failsafe heat/lane pair for the supplied heat string."""
    hv = heatstr.split(u'.')
    while len(hv) < 2:
        hv.append(u'0')
    return(riderno_key(hv[0]), riderno_key(hv[1]))
    
def fitname(first, last, width, trunc=False):
    """Return a 'nicely' truncated name field for display.

    Attempts to modify name to fit in width as follows:

    1: 'First Lastone-Lasttwo'    - simple concat
    2: 'First Lasttwo'            - ditch hypenated name
    3: 'First V Lastname'	  - abbrev Von part
    4: 'F. Lasttwo'               - abbrev first name
    5: 'F Lasttwo'                - get 1 xtra char omit period
    6: 'F. Lasttwo'               - give up and return name for truncation

    If optional param trunc is set and field would be longer than
    width, truncate and replace the last 3 chars with elipsis '...'
    Unless only two char longer than field - then just chop final chars

    """
    ret = u''
    fstr = first.strip().title()
    lstr = last.strip().upper()
    trystr = (fstr + u' ' + lstr).strip()
    if len(trystr) > width:
        lstr = lstr.split('-')[-1].strip()	# Hyphen
        trystr = fstr + u' ' + lstr
        if len(trystr) > width:
            lstr = lstr.replace(u'VON ',u'V ')	# Von part
            lstr = lstr.replace(u'VAN ',u'V ')
            trystr = fstr + u' ' + lstr
            if len(trystr) > width:
                if len(fstr) > 0:		# initial first name
                    trystr = fstr[0] + u'. ' + lstr
                else:
                    trystr = lstr
                if len(trystr) == width + 1 and len(fstr) > 0:  # opportunistic
                    trystr = fstr[0] + u' ' + lstr
    if trunc:
        ret = trystr[0:width]
        if width > 6:
            if len(trystr) > width+2:
                ret = trystr[0:(width - 3)] + u'...'
    else:
        ret = trystr
    return ret

def drawno_encirc(drawstr=u''):
    ret = drawstr
    if drawstr.isdigit():	# can toint
        try:
            ival = int(drawstr)
            if ival > 0 and ival <= 10:
                ret = (u'\u00a0' + 	# hack to get full line height?
                       unichr(0x245f + ival)) # CP U+2460 "Circled digit"
        except:
            pass
    return ret

def num2ord(place):
    """Return ordinal for the given place."""
    omap = { u'1' : u'st',
             u'2' : u'nd',
             u'3' : u'rd',
             u'11' : u'th',
             u'12' : u'th',
             u'13' : u'th' }
    if place in omap:
        return place + omap[place]
    elif place.isdigit():
        if len(place) > 1 and place[-1] in omap: # last digit 1,2,3
            return place + omap[place[-1]]
        else:
            return place + u'th'
    else:
        return place

def rank2int(rank):
    """Convert a rank/placing string into an integer."""
    ret = None
    try:
        ret = int(rank.replace(u'.',u''))
    except:
        pass
    return ret

def mark2int(handicap):
    """Convert a handicap string into an integer number of metres."""
    handicap = handicap.decode('utf-8','replace').strip().lower()
    ret = None				# not recognised as handicap
    if handicap != u'':
        if handicap[0:3] == u'scr':		# 'scr{atch}'
            ret = 0
        else:				# try [number]m form
           handicap = handicap.translate(INTEGER_UTRANS).strip()
           try:
               ret = int(handicap)
           except:
               pass
    return ret
       
def truncpad(srcline, length, align='l', elipsis=True):
    """Return srcline truncated and padded to length, aligned as requested."""
    ret = srcline[0:length]
    if length > 6:
        if len(srcline) > length+2 and elipsis:
            ret = srcline[0:(length - 3)] + u'...'	# repl with elipsis?
    if align == 'l':
        ret = ret.ljust(length)
    elif align == 'r':
        ret = ret.rjust(length)
    else:
        ret = ret.center(length)
    return ret

def search_name(namestr):
    return unicode(namestr).translate(RIDERNO_UTRANS).strip().lower().encode('ascii','ignore')

def resname_bib(bib, first, last, club):
    """Return rider name formatted for results with bib (champs/live)."""
    ret = bib + u' ' + fitname(first, last, 64)
    if club is not None and club != u'':
        if len(club) < 4:
            club=club.upper()
        ret += u' (' + club + u')'
    return ret

def resname(first, last=None, club=None):
    """Return rider name formatted for results."""
    ret = fitname(first, last, 64)
    if club is not None and club != u'':
        if len(club) < 4:
            club=club.upper()
        ret += u' (' + club + u')'
    return ret

def listname(first, last=None, club=None):
    """Return a rider name summary field for non-edit lists."""
    ret = fitname(first, last, 32)
    if club:
        if len(club) < 4:
            club=club.upper()
        ret += u' (' + club + u')'
    return ret

def reformat_bibserlist(bibserstr):
    """Filter and return a bib.ser start list."""
    return u' '.join(bibserstr.decode('utf-8','replace').translate(BIBSERLIST_UTRANS).split())

def reformat_bibserplacelist(placestr):
    """Filter and return a canonically formatted bib.ser place list."""
    placestr = placestr.decode('utf-8', 'replace')
    if u'-' not in placestr:		# This is the 'normal' case!
        return reformat_bibserlist(placestr)
    # otherwise, do the hard substitutions...
    # TODO: allow the '=' token to indicate RFPLACES ok 
    placestr = placestr.translate(PLACESERLIST_UTRANS).strip()
    placestr = re.sub(r'\s*\-\s*', r'-', placestr)	# remove surrounds
    placestr = re.sub(r'\-+', r'-', placestr)		# combine dupes
    return u' '.join(placestr.strip(u'-').split())

def reformat_biblist(bibstr):
    """Filter and return a canonically formatted start list."""
    return u' '.join(bibstr.decode('utf-8','replace').translate(BIBLIST_UTRANS).split())

def reformat_riderlist(riderstr, rdb=None, series=u''):
    """Filter, search and return a list of matching riders for entry."""
    ret = u''
    ##riderstr = riderstr.translate(PLACELIST_TRANS).lower()
    riderstr = riderstr.decode('utf-8', 'replace').lower()

    # special case: 'all' -> return all riders from the sepcified series.
    if rdb is not None and riderstr.strip().lower() == u'all':
        riderstr = u''
        for r in rdb:
            if r[5] == series:
                ret += u' ' + r[0]
    
    # pass 1: search for categories
    if rdb is not None:
        for cat in sorted(rdb.listcats(series), key=len, reverse=True):
            if len(cat) > 0 and cat.lower() in riderstr:
                ret += u' ' + rdb.biblistfromcat(cat, series)
                riderstr = riderstr.replace(cat.lower(), u'')

    # pass 2: append riders and expand any series if possible
    riderstr = reformat_placelist(riderstr)
    for nr in riderstr.split():
        if u'-' in nr:
            # try for a range...
            l = None
            n = None
            for r in nr.split(u'-'):
                if l is not None:
                    if l.isdigit() and r.isdigit():
                        start = int(l)
                        end = int(r)
                        if start < end:
                            c = start
                            while c < end:
                                ret += u' ' + unicode(c)
                                c += 1
                        else:
                            ret += u' ' + l	# give up on last val
                    else:
                        # one or both not ints
                        ret += u' ' + l
                else:
                    pass
                l = r
            if l is not None: # catch final value
                ret += u' ' + l
        else:
            ret += u' ' + nr
    # pass 3: reorder and join for return
    #rvec = list(set(ret.split()))
    ##rvec.sort(key=riderno_key)	# don't lose ordering for seeds
    #return u' '.join(rvec)
    return ret

def placeset(spec=u''):
    """Convert a place spec into an ordered set of place ints."""

    # NOTE: ordering of the set must be retained to correctly handle
    #       autospecs where the order of the places is not increasing
    #       eg: sprint semi -> sprint final, the auto spec is: 3,1,2,4
    #       so the 'winners' go to the gold final and the losers to the
    #       bronze final.
    spec = spec.decode('utf-8','replace')
    ret = u''
    spec = reformat_placelist(spec)
    # pass 1: expand ranges
    for nr in spec.split():
        if u'-' in spec:
            # try for a range...
            l = None
            n = None
            for r in nr.split(u'-'):
                if l is not None:
                    if l.isdigit() and r.isdigit():
                        start = int(l)
                        end = int(r)
                        if start < end:
                            c = start
                            while c < end:
                                ret += u' ' + unicode(c)
                                c += 1
                        else:
                            ret += u' ' + l	# give up on last val
                    else:
                        # one or both not ints
                        ret += u' ' + l
                else:
                    pass
                l = r
            if l is not None: # catch final value
                ret += u' ' + l
        else:
            ret += u' ' + nr
    # pass 2: filter out non-numbers
    rset = []
    for i in ret.split():
        if i.isdigit():
            ival = int(i)
            if ival not in rset:
                rset.append(ival)
    return rset

def reformat_placelist(placestr):
    """Filter and return a canonically formatted place list."""
    placestr = placestr.decode('utf-8','replace')
    if u'-' not in placestr:		# This is the 'normal' case!
        return reformat_biblist(placestr)
    # otherwise, do the hard substitutions...
    placestr = placestr.translate(PLACELIST_UTRANS).strip()
    placestr = re.sub(r'\s*\-\s*', r'-', placestr)	# remove surrounds
    placestr = re.sub(r'\-+', r'-', placestr)		# combine dupes
    return u' '.join(placestr.strip(u'-').split())

def confopt_bool(confstr):
    """Check and return a boolean option from config."""
    if type(confstr) in [str, unicode]:
        if confstr.lower() in ['yes', 'true', '1']:
            return True
        else:
            return False
    else:
        return bool(confstr)

def plural(count=0):
    """Return plural extension for provided count."""
    ret = 's'
    if count == 1:
        ret = ''
    return ret

def confopt_riderno(confstr, default=u''):
    """Check and return rider number, filtered only."""
    return confstr.translate(RIDERNO_UTRANS).strip()

def confopt_float(confstr, default=None):
    """Check and return a floating point number."""
    ret = default
    try:
        ret = float(confstr)
    except:	# catches the float(None) problem
        pass
    return ret

def confopt_distunits(confstr):
    """Check and return a valid unit from metres or laps."""
    if confstr.lower() == 'laps':
        return 'laps'
    else:
        return 'metres' 

def confopt_int(confstr, default=None):
    """Check and return a valid integer."""
    ret = default
    try:
        ret = int(confstr)
    except:
        pass	# ignore errors and fall back on default
    return ret

def confopt_posint(confstr, default=None):
    """Check and return a valid positive integer."""
    ret = default
    try:
        ret = int(confstr)
        if ret < 0:
            ret = default
    except:
        pass	# ignore errors and fall back on default
    return ret

def confopt_dist(confstr, default=None):
    """Check and return a valid distance unit."""
    return confopt_posint(confstr, default)

def chan2id(chanstr='0'):
    """Return a channel ID for the provided string, without fail."""
    ret = CHAN_UNKNOWN
    if (type(chanstr) in [unicode, str] and len(chanstr) > 1
        and chanstr[0] == u'C' and chanstr[1].isdigit()):
        ret = int(chanstr[1])
    else:
        try:
            ret = int(chanstr)
        except:
            pass # other errors will re-occur later anyhow
    if ret < CHAN_UNKNOWN or ret > CHAN_INT:
        ret = CHAN_UNKNOWN
    return ret

def id2chan(chanid=0):
    """Return a normalised channel string for the provided channel id."""
    ret = u'C?'
    if type(chanid) is int and chanid >= CHAN_START and chanid <= CHAN_INT:
        ret = u'C' + unicode(chanid)
    return ret

def confopt_chan(confstr, default=None):
    """Check and return a valid timing channel id string."""
    ret = chan2id(default)
    ival = chan2id(confstr)
    if ival != CHAN_UNKNOWN:
        ret = ival
    return ret

def confopt_pair(confstr, value, default=None):
    """Return value or the default."""
    ret = default
    if confstr.lower() == value.lower():
        ret = value
    return ret

def confopt_list(confstr, list=[], default=None):
    """Return an element from list or default."""
    ret = default
    for elem in list:
        if confstr.lower() == elem.lower():
            ret = elem
            break
    return ret

def bibstr2bibser(bibstr=u''):
    """Split a bib.series string and return bib and series."""
    a = bibstr.strip().split(u'.')
    ret_bib = u''
    ret_ser = u''
    if len(a) > 0:
        ret_bib = a[0]
    if len(a) > 1:
        ret_ser = a[1]
    return (ret_bib, ret_ser)

def lapstring(lapcount=None):
    lapstr = u''
    if lapcount:
        lapstr = unicode(lapcount) + u' Lap'
        if lapcount > 1:
            lapstr += u's'
    return lapstr

def bibser2bibstr(bib=u'', ser=u''):
    """Return a valid bib.series string."""
    ret = bib
    if ser != u'':
        ret += u'.' + ser
    return ret

def titlesplit(src=u'', linelen=24):
    """Split a string on word boundaries to try and fit into 3 fixed lines."""
    ret = [u'', u'', u'']
    words = src.split()
    wlen = len(words)
    if wlen > 0:
        line = 0
        ret[line] = words.pop(0)
        for word in words:
            pos = len(ret[line])
            if pos + len(word) >= linelen:
                # new line
                line += 1
                if line > 2:
                    break
                ret[line] = word
            else:
                ret[line] += u' ' + word
    return ret


class countback(object):
    __hash__ = None
    """Simple dict wrapper for countback store/compare."""
    def __init__(self, cbstr=None):
        self.__store = {}
        if cbstr is not None:
            self.fromstring(cbstr)

    def maxplace(self):
        """Return maximum non-zero place."""
        ret = 0
        if len(self.__store) > 0:
            ret = max(self.__store.keys())
        return ret
    def fromstring(self, cbstr):
        propmap = {}
        cbvec = cbstr.split(u',')
        if len(cbvec) > 0:
            for i in range(0,len(cbvec)):
                if cbvec[i].isdigit():
                    propmap[i] = int(cbvec[i])
        self.__store = {}
        for k in propmap:
            self.__store[k] = propmap[k]

    def __str__(self):
        ret = []
        for i in range(0,self.maxplace()+1):
            if i in self.__store and self.__store[i] != 0:
                ret.append(str(self.__store[i]))
            else:
                ret.append('-')
        return ','.join(ret)
    def __len__(self):
        return len(self.__store.len)
    def __getitem__(self, key):
        """Use a default value id, but don't save it."""
        if key in self.__store:
            return self.__store[key]
        else:
            return 0
    def __setitem__(self, key, value):
        self.__store[key] = value
    def __delitem__(self, key):
        del(self.__store[key])
    def __iter__(self):
        return self.__store.iterkeys()
    def iterkeys(self):
        return self.__store.iterkeys()
    def __contains__(self, item):
        return item in self.__store
    def __lt__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = False # assume all same
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            a = self[i]
            b = other[i]
            if a != b:
                ret = a < b
                break
        return ret
    def __le__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = True # assume all same
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            a = self[i]
            b = other[i]
            if a != b:
                ret = a < b
                break
        return ret
    def __eq__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = True
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            if self[i] != other[i]:
                ret = False
                break
        return ret
    def __ne__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = False
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            if self[i] != other[i]:
                ret = True
                break
        return ret
    def __gt__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = False # assume all same
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            a = self[i]
            b = other[i]
            if a != b:
                ret = a > b
                break
        return ret
    def __ge__(self, other):
        if type(other) is not countback: return NotImplemented
        ret = True # assume all same
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            a = self[i]
            b = other[i]
            if a != b:
                ret = a > b
                break
        return ret
    def __add__(self, other):
        """Add two countbacks together and return a new cb>=self >=other."""
        if type(other) is not countback: return NotImplemented
        ret = countback(str(self))
        for i in range(0,max(self.maxplace(), other.maxplace())+1):
            ret[i] += other[i]
        return ret

