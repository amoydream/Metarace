
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

from xml.sax.saxutils import escape, quoteattr
import sys

"""HTML output lib.

Cheap and nasty functional primitives for HTML output. Each primitive
outputs a single string. No checking is performed on the structure of
the document produced. All elements take a named parameter 'attrs'
which is a dict of key/value attributes. Non-empty elements take a
parameter 'clist' which is a list of other constructed elements.

Example for an empty element:

    hr(attrs={'id':'thehr'})

Example for an element with content:

    a(['link text'], attrs={'href':'#target'})

Example paragraph:

    p(['Check the',
       a(['website'], attrs={'href':'#website'}),
       'for more.'])

"""

def html(headlist=[], bodylist=[]):
    """Emit HTML document."""
    return u'\n'.join([
      preamble(),
      u'<html lang="en">',
      head(headlist),
      body(bodylist),
      u'</html>'])

def preamble():
    """Emit HTML preamble."""
    return u'<!DOCTYPE html>'

def attrlist(attrs):
    """Convert attr dict into properly escaped attrlist."""
    alist = []
    for a in attrs:
        alist.append(a.lower() + u'=' + quoteattr(attrs[a]))
    if len(alist) > 0:
        return u' ' + u' '.join(alist) 
    else:
        return u''

def escapetext(text=u''):
    """Return escaped copy of text."""
    return escape(text, {u'"':u'&quot;'})

def tablestyles():
    """Emit the fixed table styles."""
    return u'\ntable.middle td { vertical-align: middle; }\n th.center { text-align: center; }\n th.right { text-align: right; }\n td.right { text-align: right; }\n'

def shim(shivlink, respondlink):
    """Emit the HTML5 shim for IE8."""
    return u'\n<!--[if le IE 9]>\n<script src=' + quoteattr(shivlink) + '>\n</script>\n<script src=' + quoteattr(respondlink) + '>\n</script>\n<![endif]-->\n'

def comment(commentstr=u''):
    """Insert comment."""
    return u'<!-- ' + commentstr.replace(u'--',u'') + u' -->'

# Declare all the empty types
for empty in [u'meta', u'link', u'base', u'param',
              u'hr', u'br', u'img', u'input', u'col']:
    def emptyfunc(attrs={}, elem=empty):
        return u'<' + elem + attrlist(attrs) + u'>'
    setattr(sys.modules[__name__], empty, emptyfunc) 

# Declare all the non-empties
for nonempty in [u'head', u'body', u'title', u'div', u'style', u'script',
                 u'p', u'h1', u'h2', u'h3', u'h4', u'h5', u'h6',
                 u'ul', u'ol', u'li', u'dl', u'dt', u'dd', u'address',
                 u'pre', u'blockquote', u'a', u'span', u'em', u'strong',
                 u'dfn', u'code', u'samp', u'kbd', u'var', u'cite',
                 u'abbr', u'acronym', u'q', u'sub', u'sup', u'tt',
                 u'i', u'big', u'small', u'label', u'form', u'select',
                 u'optgroup', u'option', u'textarea', u'fieldset',
                 u'legend', u'button', u'table', u'caption',
                 u'thead', u'tfoot', u'tbody', u'colgroup',
                 u'tr', u'th', u'td', ]:
    def nonemptyfunc(clist=[], attrs={}, elem=nonempty):
        if type(clist) in [str, unicode]:	# ERROR 2->3
            clist = [clist]
        return(u'<' + elem + attrlist(attrs) + u'>'
                + u'\n'.join(clist) + u'</' + elem + u'>')
    setattr(sys.modules[__name__], nonempty, nonemptyfunc) 

# output a valid but empty html templatye
def emptypage():
    return html([
                           meta(attrs={u'charset':u'utf-8'}),
           meta(attrs={u'http-equiv':u'X-UA-Compatible',
                       u'content':u'IE=edge'}),
           meta(attrs={u'name':u'viewport',
                       u'content':u'width=device-width, initial-scale=1'}),
           title(u'__REPORT_TITLE__'),
           link(attrs={u'href':u'https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css',
                       u'integrity':u'sha384-1q8mTJOASx8j1Au+a5WDVnPi2lkFfwwEAa8hDDdjZlpLegxhjVME1fgjWPGmkzs7',
                       u'crossorigin':u'anonymous',
                       u'rel':u'stylesheet'}),
           style(tablestyles(), {u'type':'text/css'}),
           shim(u'https://oss.maxcdn.com/html5shiv/3.7.2/html5shiv.min.js',
                u'https://oss.maxcdn.com/respond/1.4.2/respond.min.js'),
                ],
                div([h1(u'__REPORT_TITLE__'),
                     u'\n', comment(u'Begin report content'),
                     u'__REPORT_CONTENT__',
                     comment(u'End report content'),u'\n',
script(u'\n', attrs={'src':u'https://ajax.googleapis.com/ajax/libs/jquery/1.11.3/jquery.min.js'}),
script(u'\n', attrs={'src':u'https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/js/bootstrap.min.js', u'integrity':u'sha384-0mSbJDEHialfmuBBQP6A4Qrprq5OVfW37PRR3j5ELqxss1yVqOtnepnHVP9aJ7xS', u'crossorigin':u'anonymous'}),
                 ],
                    attrs={u'class':u'container'}))

# test run
if __name__ == "__main__":
    # NOTE: all terminal strings require escaping before being sent to
    #       htlib. Unescaped strings below are technically in error
    print(
        html([title(u'Test Title'),
            meta({u'name':u'Author',
      u'content':u'Voyt\u011bch \u0160afa\u0159\u00edk \u0026 John Smith'}),
            link({u'type':u'text/css', u'href':u'style.css',
                     u'rel':u'stylesheet'}),
            meta({u'http-equiv':u'Content-Style-Type',
                    u'content':u'text/css'})],
             [h1(u'Document Heading'),
              comment(u'inline comment'),
              p(em(escapetext(u'Text \u0026 "under\u02ee heading.'))),
              h2(u'Subheading'),
              p([u'The paragraph of subheading\u2026 and with some',
                 tt(u'0h12:34.56'),
                 u'time text.'])]).encode('utf-8','replace')
    )
