
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

from distutils.core import setup

import os
import sys
assert sys.version >= '2.6', "Missing pre-requisite: python >= 2.6"

scriptsrc = [
'bin/dump_namebank',
'bin/gfxpanel',
'bin/hl970',
'bin/mbrfind',
'bin/metarace',
'bin/report_tool',
'bin/road_announce',
'bin/roadrace',
'bin/roadtt',
'bin/roadtt_announce',
'bin/roadtt_start',
'bin/rtimerd',
'bin/sportif',
'bin/stagebuild',
'bin/track_announce',
'bin/update_namebank',
'bin/velotraind',
'bin/voladisp',
'bin/woodchop',
            ]

setup(name = 'metarace',
      version = '1.11.0',
      description = 'Cycle race abstractions',
      author = 'Nathan Fraser',
      author_email = 'ndf@metarace.com.au',
      url = 'http://metarace.com.au/_about/software',
      packages = ['metarace'],
      package_dir={'metarace': 'lib'},
      package_data={'metarace': ['ui/*', 'data/*']},
      requires = ['pygtk', 'gtk', 'glib', 'gobject', 'xlwt', 'serial', 'rsvg',
                  'cairo', 'pango', 'pangocairo' ],
      provides = ['metarace'],
      scripts = scriptsrc,
      classifiers = ['Development Status :: 3 - Alpha',
              'Environment :: X11 Applications :: GTK',
              'Intended Audience :: Other Audience',
              'License :: OSI Approved :: GNU General Public License (GPL)',
              'Natural Language :: English',
              'Operating System :: OS Independent',
              'Programming Language :: Python :: 2.6',
              'Topic :: Other/Nonlisted Topic' ],
      license = 'GNU GPL Version 3',
      long_description = """
A collection of utilities that support timing and result preparation
for track and road cycle races.
"""
)
