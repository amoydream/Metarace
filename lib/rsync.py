
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

"""rsync mirror in a thread

This module provides a thread object for exporting a race report,
standing or result to an external website. Normally, rsync is used to
perform file mirroring over ssh, but any command can be run by
providing suitable arguments and site configuration. 

The configured command is run to completion in a separate thread.
Thread completion is flagged with an optional glib callback operation.
Argmuents passed to the command can be configured via system defaults
or by manipulating the object. Errors are reported via system log.

By default, this module calls the echo command as follows:

  echo dummy srcdir=SRCDIR dstdir=DSTDIR

The mirror object has the following properties for configuring the
command:

  callback:	a function to be called from within the rsync thread on
                sucessful completion of the mirror command
  localpath:	the path to read source files from (default is .)
  remotepath:	the path to write to (default is None)
  mirrorcmd:	command to be called (default is 'echo', or system default)
  arguments:	a list of format strings to be used as arguments (See
                Arguments below)
  data:		optional auxiliary data for argument lists.

mirrorcmd and arguments are read from the system defaults (if present)
but may be overridden in the object constructor. 

Arguments:

Each argument provided in the list of arguments is treated as a format
string, and is processed with the format() function before being run. This
function call is given exactly four arguments: srcdir, dstdir, command
and data. These are given the corresponding values of the localpath,
remotepath, mirrorcmd and data.

Examples:

Export via rsync/ssh:

   System config:

	command = "rsync"
	arguments = ["-av", "--rsh=ssh", "--rsync-path=bin/rsync",
                     "{srcdir}", "host:html/site/{dstdir}"]

   Progam:
 
     m = rsync.mirror(localpath=u'export', remotepath=u't')
     m.start()
     m.join()

   This will create a subprocess and call the following command:

       rsync -av --rsh=ssh --rsync-path=bin/rsync export host:html/site/t

   which will mirror the contents of the directory 'export' to the remote
   site under the directory html/site/t

Run a custom command with custom arguments, overriding the system defaults:

   m = rsync.mirror(mirrorcmd=u'export',
                    arguments=[u'--virtual={data[virtual]}',
                               u'--provisional={data[provisional]}',
                               u'--stage={data[stage]}',
                               u'--document={data[document]}'],
                    data={'virtual':'no',
                          'provisional':'yes',
                          'stage':2,
                          'document':203})
   m.start()
   m.join()

This will run the command 'export' with the command line:

  export --virtual=no --provisional=yes --stage=2 --document=203

"""

import threading
import subprocess
import logging
import glib
import os

import metarace

RSYNC_CMD = u'echo'		# Command/Argument defaults
RSYNC_ARGS = [u'dummy', u'srcdir={srcdir}', u'dstdir={dstdir}']

class mirror(threading.Thread):
    """Mirror thread object class."""
    def __init__(self, callback=None, callbackdata=None,
                       localpath=u'.', remotepath=None,
                       mirrorcmd=None, arguments=None, data=None):
        """Construct mirror thread object."""
        threading.Thread.__init__(self) 
        self.name = u'mirror'
        self.log = logging.getLogger(self.name)
        self.log.setLevel(logging.DEBUG)
        self.__cb = None
        if callback is not None:
            self.__cb = callback
        self.__cbdata = None
        if callbackdata is not None:
            self.__cbdata = callbackdata
        self.__localpath = localpath
        self.__remotepath = u''
        if remotepath is not None:
            self.__remotepath = remotepath

        # config starts with module defaults
        self.__mirrorcmd = RSYNC_CMD
        self.__arguments = RSYNC_ARGS

        # then overwrite with sysconf - if present
        if metarace.sysconf.has_section(u'rsync'):
            if metarace.sysconf.has_option(u'rsync', u'command'):
                self.__mirrorcmd = metarace.sysconf.get(u'rsync', u'command')
            if metarace.sysconf.has_option(u'rsync', u'arguments'):
                self.__arguments = metarace.sysconf.get(u'rsync', u'arguments')

        # and then finally allow override in object creation
        if mirrorcmd:
            self.__mirrorcmd = mirrorcmd
        if arguments:
            self.__arguments = arguments

        self.__data = data	# optional, for general purpose argument maps

    def set_remotepath(self, pathstr):
        """Set or clear the remote path value."""
        self.__remotepath = pathstr

    def set_localpath(self, pathstr):
        """Set or clear the local path value."""
        self.__localpath = pathstr

    def set_cb(self, func=None, cbdata=None):
        """Set or clear the event callback."""
        # if func is not callable, gtk mainloop will catch the error
        if func is not None:
            self.__cb = func
            self.__cbdata = cbdata
        else:
            self.__cb = None
            self.__cbdata = None

    def run(self):
        """Called via threading.Thread.start()."""
        running = True
        self.log.debug(u'Starting')
        ret = None
        try:
            # format errors in arguments caught as exception
            arglist = [a.format(srcdir=self.__localpath, 
                                dstdir=self.__remotepath,
                                command=self.__mirrorcmd,
                                data=self.__data) for a in self.__arguments]
            arglist.insert(0,self.__mirrorcmd)

            self.log.debug(u'Calling subprocess: ' + repr(arglist))
            if self.__mirrorcmd == u'FTPLIB':
                ret = ftpmirror(self.log, arglist)
            else:
                ret = subprocess.check_call(arglist)
            ## TODO: track process here and collect output to log.debug
            if self.__cb:
                self.__cb(ret, self.__cbdata)
        except Exception as e:
            self.log.error(u'Error: ' + unicode(type(e)) + unicode(e))
        self.log.info(u'Complete: Returned ' + repr(ret))

TEMPPREFIX = u'.tmpchg_'
# determine temp file for given file
def tempfilename(efile):
    ret = TEMPPREFIX + efile
    return ret

def ftpmirror(log, args):
    """Update remote result using built in FTP file copy."""
    import ftplib
    import filecmp
    import shutil

    SERVERISBROKEN = False
    if len(args) < 6:
        log.error(u'Usage: FTPLIB local_path remote_path host user pass [broken]')
        return -1
    local = args[1]
    remote = args[2]
    host = args[3]
    user = args[4]
    pwd = args[5]
    if len(args) > 6:
        SERVERISBROKEN = True
    if not os.path.exists(local):
        log.error(u'Local mirror path does not exist.')
        return -1
    log.info(u'Copying ' + repr(local)
              + ' to ' + repr(remote) + ' on ' + repr(host))
    # walk local path, prepare a local list
    props = {}
    for (dp, dn, fn) in os.walk(local):
        for f in fn:
            if TEMPPREFIX not in f:
                t = tempfilename(f)
                props[os.path.join(dp,f)] = os.path.join(dp,t)
    # check each file in local list for upload
    writes = {}
    for file in props:
        tfile = props[file]
        if os.path.exists(tfile):
            if not filecmp.cmp(file, tfile, shallow=False):
                # file is changed from last uploaded version
                writes[file] = tfile
        else:
            writes[file] = tfile
    ret = 0
    tfile = None
    if len(writes) > 0:
        try:
            fcnt = 0
            log.debug(u'Connecting to remote host...')
            f = ftplib.FTP()
            log.debug(u'Connect: ' + repr(f.connect(host)))
            log.debug(u'Login: ' + repr(f.login(user, pwd)))
            log.debug(u'Remote path: ' + repr(f.cwd(remote)))
            for file in sorted(writes):
                tfile = writes[file]
                # update temporary file
                shutil.copyfile(file, tfile)
                (path,tlip) = os.path.split(tfile)
                (path,flip) = os.path.split(file)
                with open(tfile, 'rb') as g:
                    log.debug(u'Upload ' + repr(file) + u': '
                          + repr(f.storbinary('STOR ' + tlip, g)))
                if SERVERISBROKEN:
                    # If the server is broken, delete before rename
                    try:
                        log.debug(u'Delete: ' + repr(f.delete(flip)))
                    except:
                        pass
                log.debug(u'Rename: ' + repr(f.rename(tlip, flip)))
                fcnt += 1
            f.quit()
            log.debug(u'Done, uploaded: ' + repr(fcnt))

        except Exception as e:
            if tfile is not None:
                os.unlink(tfile)    # not windows safe
            log.error('FTP error: ' + repr(e))
            ret = -1
    else:
        log.debug(u'No changes to upload.')
    return ret


def http_notify(url, user=None, passwd=None, event=None, subevent=None,
                     stage=None, path=None, div=None, log=logging):
    """Remote result update notification."""
    import urllib
    import urllib2		# import only here
    try: 
        opn = None
        if passwd is not None:  # use Basic AUTH
            pm = urllib2.HTTPPasswordMgrWithDefaultRealm()
            pm.add_password(None, url, user, passwd)
            ah = urllib2.HTTPBasicAuthHandler(pm)
            opn = urllib2.build_opener(ah)
        else:
            opn = urllib2.build_opener()

        urllib2.install_opener(opn)
        args = {}
        if event is not None:
            args['event']=event
        if subevent is not None:
            args['subevent']=subevent
        if stage is not None:
            args['stage']=stage
        if path is not None:
            args['path']=path
        if div is not None:
            args['div']=div
        params = urllib.urlencode(args)
        log.debug(u'Calling Notify: ' + repr(args))
        ph = urllib2.urlopen(url, data=params, timeout=20)
        log.debug(u'Notify Returns: ' + repr(ph.read(1024)))
    except Exception as e:
        log.info(u'Notify ERROR: ' + repr(e))

if __name__ == "__main__":
    import metarace
    def cb(code):
        print(u'sync returned: ' + repr(code))

    metarace.init()
    lh = logging.StreamHandler()
    lh.setLevel(logging.DEBUG)
    lh.setFormatter(logging.Formatter(
                      "%(asctime)s %(levelname)s:%(name)s: %(message)s"))
    m = mirror(cb, u'testing')
    m.log.addHandler(lh)
    m.start()
    m.join()

