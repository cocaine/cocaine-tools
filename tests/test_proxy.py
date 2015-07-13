# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
# Copyright (c) 2013+ Anton Tiurin <noxiouz@yandex.ru>
# Copyright (c) 2011-2014 Other contributors as noted in the AUTHORS file.
#
# This file is part of Cocaine-tools.
#
# Cocaine is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cocaine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

from tornado.httputil import HTTPServerRequest
from tornado.httputil import HTTPHeaders

from cocaine.proxy.proxy import pack_httprequest
from cocaine.proxy.proxy import scan_for_updates


class _FakeConnection():
    def __init__(self):
        self.remote_ip = None
        self.context = self


def test_proxy_pack_httprequest():
    method = "POST"
    uri = "/testapp/event1"
    version = 'HTTP/1.0'
    h = HTTPHeaders({"content-type": "text/html", "Ab": "blabla"})
    body = "BODY"
    host = "localhost"
    req = HTTPServerRequest(method=method, uri=uri,
                            version=version, headers=h, connection=_FakeConnection(),
                            body=body, host=host)
    res = pack_httprequest(req)
    assert res[0] == method, "method has been parsed unproperly"
    assert res[1] == uri, "uri has been parsed unproperly"
    assert res[2] == "1.0", "version has been parsed unproperly %s" % res[2]
    assert res[3] == h.items(), "headers has been parsed unproperly %s" % res[3]
    assert res[4] == body, "body has been parsed unproperly"


def test_scan_for_updates():
    current = {"A": 1, "B": 2, "C": 3}
    new = {"A": 2, "B": 2, "D": 0}
    updated = scan_for_updates(current, new)
    assert set(updated) == set(("A", "C", "D"))
