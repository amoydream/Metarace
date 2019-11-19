
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

"""Configuration object module.

   dict-like configuration object with json backing. Replaces jsonconfig

"""

import json

class section(object):
    def __init__(self, *args, **kwargs):
        self.__sectionid = None
        self.__spec = {}
        self.__store = {}

    def set_id(self, sectionid):
        self.__sectionid = sectionid

    def get_id(self):
        return self.__sectionid

    def __len__(self):
        return len(self.__store)

    def __getitem__(self, key):
        """Use a default value id, but don't save it."""
        if key in self.__store:
            return self.__store[key]
        else:
            # exception from here
            return self.__spec[key][u'default']

    def __setitem__(self, key, value):
        """Only allow spec'd keys into config."""
        if key not in self.__spec:
            raise KeyError(u'Key not configured: ' + repr(key))
        self.__store[key] = value

    def __delitem__(self, key):
        del(self.__store[key])

    def __iter__(self):
        return self.__store.iterkeys()

    def __contains__(self, item):
        return key in self.__store

