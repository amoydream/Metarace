
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

"""Dumb Data sink"""

import threading
import logging
import socket
import glib
from metarace import tod
from metarace import unt4
from metarace import strops


# Global Defaults
RECEIVER_PORT=7273

class receiver(threading.Thread):
    def set_pub_cb(self, newcb=None, data=None):
        """Set Data Callback."""
        self.pub_cb = newcb

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False

    def connected(self):
        return True

    def wait(self):             # NOTE: Do not call from cmd thread
        """Suspend calling thread until cqueue is empty."""
        pass
        
    def __init__(self, linelen=28):
        """Constructor."""
        threading.Thread.__init__(self) 
        self.running = False
        self.pub_cb = None
        self.encoding = u'utf8'
        self.log = logging.getLogger('receiver')
        self.log.setLevel(logging.DEBUG)

    def run(self):
        """Called via threading.Thread.start()."""

        self.running = True
        so=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        so.bind(('',RECEIVER_PORT))
        so.settimeout(1.0)
        lbmsg = None
        while self.running:
            try:
                (buf,ad) = so.recvfrom(1024)
                bv = buf.decode(self.encoding, 'ignore') #.split()
                msg = unt4.unt4(bv)
                if self.pub_cb is not None:
                    glib.idle_add(self.pub_cb, msg)
            except socket.timeout:
                pass
            except Exception as e:
                self.log.error(u'Exception: ' + str(type(e)) + str(e))
        self.log.info('Exiting')

