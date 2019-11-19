
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

"""JSON Configuration module.

  Replaces old-style ConfigParser config files with JSON file-based
  configurations. Some overlap with configparser, but based mostly on 
  metarace use patterns.

"""

import json	# TODO: import optimised JSON with indent support


class config(object):
    def __init__(self, default={}):
        self.__store = dict(default)
        
    def add_section(self, section):
        if type(section) is not unicode:
            raise TypeError(u'Section key must be unicode: ' + repr(section))
        if section not in self.__store:
            self.__store[section] = dict()

    def has_section(self, section):
        return section in self.__store

    def has_option(self, section, key):
        return section in self.__store and key in self.__store[section]

    def sections(self):
        for sec in self.__store:
            yield sec

    def options(self, section):
        for opt in self.__store[section]:
            yield opt

    def get(self, section, key):
        return self.__store[section][key]

    def set(self, section, key, value):
        if type(key) is not unicode:
            raise TypeError(u'Option key must be unicode: ' + repr(section)+ u':' + repr(key))
        self.__store[section][key] = value
 
    def convertconf(self):
        """Return an old style configparser but flattened to ascii."""
        import ConfigParser
        ret = ConfigParser.ConfigParser()
        for sec in self.__store:
            skey = sec.encode(u'ascii', u'ignore')
            ret.add_section(skey)
            for opt in self.__store[sec]:
                okey = opt.encode(u'ascii', u'ignore')
                oval = unicode(self.__store[sec][opt]).encode(u'ascii',
                                                              u'ignore')
                ret.set(skey, okey, oval)
        return ret

    def write(self, file):
        json.dump(self.__store, file, indent=2, sort_keys=True)

    def dumps(self):
        return json.dumps(self.__store)

    def dictcopy(self):
        """Return a copy of the configuration as a dictionary object."""
        return dict(self.__store)

    def merge(self, otherconfig, section=None, key=None):
        """Merge values from otherconfig into self."""
        if type(otherconfig) is not config:
            raise TypeError('Merge expects jsonconfig object.')
        if key is not None and section is not None:	# single value import
            if otherconfig.has_option(section, key):
                self.set(section, key, otherconfig.get(section, key))
        elif section is not None:
            self.add_section(section)	# force even if not already loaded
            if otherconfig.has_section(section):
                for opt in otherconfig.options(section):
                    self.set(section, opt, otherconfig.get(section, opt))
        else:
            for sec in otherconfig.sections():
                if self.has_section(sec):
                    # in this case, only add sections already defined
                    for opt in otherconfig.options(sec):
                        self.set(sec, opt, otherconfig.get(sec, opt))

    def read(self, file):
        addconf = json.load(file)
        if type(addconf) is not dict:
            raise TypeError('Configuration file is not dict: '
                            + repr(type(addconf)))
        for sec in addconf:
            thesec = addconf[sec]
            if type(thesec) is not dict:
                raise TypeError('Configuration section is not dict: '
                                 + repr(type(thesec)))
            self.add_section(sec)
            for k in thesec:
                self.set(sec, k, thesec[k])
 
if __name__ == "__main__":
    import os
    cr = config({u'thesec':{u'key1':['val1.0', 'val1.1', 'val1.2'],
                            u'key2':u'Uni\u2012\u0123code.'}})
    if os.path.exists('test.json'):
        with open('test.json', 'rb') as f:
            cr.read(f)

    cr.add_section(u'uni\u2034\u0124key')
    cr.add_section(u'uni\u2034\u0124kay')
    cr.set(u'uni\u2034\u0124kay',u'uni\u0987val',u'val\u0765ue')
    with open('test.outn', 'wb') as f:
        cr.write(f)
