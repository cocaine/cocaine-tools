# Copyright (c) 2016+ Anton Tiurin <noxiouz@yandex.ru>
# Copyright (c) 2011-2016 Other contributors as noted in the AUTHORS file.
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

import msgpack

from tornado import httputil
from tornado.httputil import HTTPServerRequest
from tornado.httpclient import HTTPRequest
from tornado.testing import AsyncHTTPTestCase
from tornado.testing import gen_test

from cocaine.proxy.proxy import CocaineProxy
from cocaine.proxy.logutils import NULLLOGGER
from cocaine.proxy.mds_exec import MDSExec
from cocaine.proxy.mds_exec import is_mds_stid


class _FakeConnection():
    def __init__(self):
        self.remote_ip = None
        self.context = self

        self.start_line = None
        self.headers = None
        self.chunks = list()

    def write_headers(self, start_line, headers, chunk=None, callback=None):
        self.start_line = start_line
        self.headers = headers
        if chunk is not None:
            self.chunks.append(chunk)
        if callback:
            callback()

    def write(self, chunk, callback=None):
        self.chunks.append(chunk)
        if callback:
            callback()

    def finish(self):
        pass


def test_is_mds_stid():
    assert not is_mds_stid("77777.270212926.1074746148309135132")
    assert is_mds_stid("1000017.tmp.E1572:2888034675120773296646650399583")
    assert is_mds_stid("1000017.yadisk:4001053055.E1370:148685303346007653037969607534")


class TestMDSExec(AsyncHTTPTestCase):
    def get_app(self):
        def handler(request):
            method, uri, version, headers, body = msgpack.unpackb(request.body)
            self.assertEqual(method, "PUT")
            self.assertEqual(uri, "/blabla")
            self.assertEqual(version, "1.1")
            self.assertEqual(body, "body")
            self.assertEqual(len(headers), 6)  # 4 + 2
            self.assertEqual(request.query_arguments["timeout"], ["30"])
            request.connection.write_headers(httputil.ResponseStartLine("HTTP/1.1", 200, "OK"),
                                             httputil.HTTPHeaders(), chunk=msgpack.packb((202, [("A", "B")])))
            request.connection.write("CHUNK1")
            request.connection.write("CHUNK2")
            request.connection.write("CHUNK3")
            request.connection.finish()

        return handler

    def test_mds_match(self):
        mdsplugin = MDSExec(CocaineProxy(), {"srw_host": ""})

        request = HTTPRequest("/", headers={
            "X-Srw-Key": "320.yadisk:301123837.E150591:1046883",
            "X-Srw-Namespace": "namespace",
            "X-Srw-Key-Type": "mds",
            "Authorization": "Basic aaabbb",
        })
        self.assertTrue(mdsplugin.match(request))

        request = HTTPRequest("/", headers={
            "X-Srw-Key": "77777.270212926.107474614",
            "X-Srw-Namespace": "namespace",
            "X-Srw-Key-Type": "mds",
        })
        self.assertFalse(mdsplugin.match(request))

    @gen_test
    def test_mds_process(self):
        mdsplugin = MDSExec(CocaineProxy(), {"srw_host": "http://localhost:%d" % self.get_http_port()})
        conn = _FakeConnection()
        req = HTTPServerRequest(method="PUT", uri="/blabla",
                                version="HTTP/1.1", headers={
                                    "X-Cocaine-Service": "application",
                                    "X-Cocaine-Event": "event",
                                    "X-Srw-Key": "320.namespace:301123837.E150591:1046883323",
                                    "X-Srw-Namespace": "namespace",
                                    "X-Srw-Key-Type": "mds",
                                    "Authorization": "Basic aaabbb",
                                },
                                connection=conn,
                                body="body", host="localhost")
        req.logger = NULLLOGGER
        yield mdsplugin.process(req)
        self.assertEqual(conn.start_line.code, 202)
        self.assertEqual(conn.start_line.version, "HTTP/1.1")
        self.assertEqual(len(conn.chunks), 1)
        self.assertEqual(''.join(conn.chunks), "CHUNK1CHUNK2CHUNK3")
        self.assertEqual(conn.headers["A"], "B")
