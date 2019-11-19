
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

"""HACK : MySQLdb export facility for CA Track Nationals

   if MySQLdb library not present, this will just silently ignore data.

"""

import threading
import Queue
import logging

try:
    import MySQLdb
except ImportError:
    pass

from metarace import strops

# connection string:
# [user[:pass]@]host[:port][/db]
# defaults:
DB_USER = u'root'
DB_PASSWD = u'velodrome'
DB_HOST = u'localhost'
DB_PORT = 3306
DB_DATABASE = u'mysql'

# dispatch thread queue commands
TCMDS = ('EXIT', 'PORT', 'MSG', 'COMMIT','BACKUP')

def parse_portstr(portstr=u''):
    """Return a reasonable best guess user/pass/host/port/database."""
    user = DB_USER
    passwd = DB_PASSWD
    host = DB_HOST
    port = DB_PORT
    db = DB_DATABASE
    ## TODO: import system defaults from global config
    if len(portstr) > 0:
        # LHS: user:pass
        upart = u''
        if u'@' in portstr:
            (upart, sep, portstr) = portstr.partition(u'@')
        if len(upart) > 0:
            (user, sep, passwd) = upart.partition(u':')

        # RHS: host:port/db
        if u'/' in portstr:
            # sets Database
            (portstr, sep, db) = portstr.partition(u'/')
        if u':' in portstr:
            # sets host and port
            (host, sep, pstr) = portstr.partition(u':')
            if pstr.isdigit():	# but ensure ok to use
                port = int(pstr)
        else:
            # host resolved by elimination
            host = portstr
    return (user, passwd, host, port, db)
    
class dbexport(threading.Thread):
    """MySQLdb exporter

    """

    def __init__(self, port=None, bport=None):
        """Constructor."""
        threading.Thread.__init__(self) 
        self.name = u'dbexport'
        self.port = None
        self.bport = None
        self.queue = Queue.Queue()
        self.log = logging.getLogger(u'dbexport')
        self.log.setLevel(logging.DEBUG)
        self.running = False
        self.error = False
        if port is not None:
            self.setport(port, bport)

    def commit(self):
        """Queue a 'COMMIT' on the server."""
        self.queue.put_nowait(('COMMIT', ))

    def execute(self, sqlcmd, args=[()]):	# default is one exec no args
        """Execute a multiple sql query as provided, ignoring the return."""
        self.queue.put_nowait((u'MSG', sqlcmd, args))

    def exit(self, msg=None):
        """Request thread termination."""
        self.running = False
        self.queue.put_nowait(('EXIT', msg))

    def wait(self):             # NOTE: Do not call from cmd thread
        """Suspend calling thread until cqueue is empty."""
        self.queue.join()

    def setport(self, port=None, bport=None):
        """Dump command queue content and (re)connect

        Specify hostname and port for TCP connection:

            hostname:16372

        """
        try:
            while True:
                self.queue.get_nowait()
                self.queue.task_done()
        except Queue.Empty:
            pass 
        self.queue.put_nowait(('PORT', port, bport))

    def connected(self):
        """Return true if main db connected."""
        return self.port is not None

    def __commit(self):
        """Call for a commit on both connections."""
        if not self.error:
            if self.port is not None:
                self.port.commit()
            if self.bport is not None:
                self.bport.commit()
        else:
            self.log.info(u'Attempt to commit db in error state ignored.')
        return None

    def __procsql(self, cmd, args):
        """Send the sql to the dbs as a batch."""
        if self.port is not None:
            c = self.port.cursor()
            c.executemany(cmd, args)
            c.close()
        if self.bport is not None:
            c = self.bport.cursor()
            c.executemany(cmd, args)
            c.close()
        return None

    def __connect(self, portstr=''):
        """Connect to the db if possible, but allow error to raise."""
        (u, p, h, t, d) = parse_portstr(portstr) 
        self.log.info(u'Connecting to: {0}:{1}@{2}:{3}/{4}'.format(u,p,h,t,d))
        return MySQLdb.connect(host=h, port=t,
                               passwd=p, user=u, db=d,
                               use_unicode=True, charset='utf8')

    def run(self):
        """Called via threading.Thread.start()."""
        self.running = True
        self.log.debug(u'Starting')
        while self.running:
            m = self.queue.get()
            self.queue.task_done()
            try:
                if m[0] == 'MSG':
                    self.__procsql(m[1], m[2])
                elif m[0] == 'COMMIT':
                    self.__commit()
                elif m[0] == 'EXIT':
                    self.running = False
                elif m[0] == 'PORT':
                    if self.port is not None:
                        self.port.close()
                    if m[1] is not None and m[1] not in ['none', 'NULL']:
                        self.log.debug(u'Re-Connect port: ' + str(m[1]))
                        self.port = self.__connect(str(m[1]))
                    else:
                        self.log.debug(u'Not connected.')
                        self.port = None	# should get __del__ here
                    if self.bport is not None:
                        self.bport.close()
                    if m[2] is not None and m[2] not in ['none', 'NULL']:
                        self.log.debug(u'Re-Connect backup: ' + str(m[2]))
                        self.bport = self.__connect(str(m[2]))
                    else:
                        self.log.debug(u'Backup Not connected.')
                        self.bport = None	# should get __del__ here
                    self.error = False
            except Exception as e:
                # assume 'large' exception - take down both conns
                self.log.error(u'Exception: ' + repr(type(e)) + repr(e))
                if self.port is not None:
                    self.port.close()
                if self.bport is not None:
                    self.bport.close()
                self.port = None
                self.bport = None
                self.error = True
        if self.port is not None:
            self.port.close()
            self.port = None
        if self.bport is not None:
            self.bport.close()
            self.bport = None
        self.log.info(u'Exiting')
