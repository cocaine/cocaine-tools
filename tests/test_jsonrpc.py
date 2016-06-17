import mock

import tornado
from tornado.httpclient import HTTPRequest
from tornado.httputil import HTTPServerRequest, ResponseStartLine, HTTPHeaders
from tornado.testing import AsyncTestCase

from cocaine.proxy.jsonrpc import JSONRPC
from cocaine.proxy.logutils import NULLLOGGER


class TestJSONRPC(AsyncTestCase):
    def test_match(self):
        proxy = mock.Mock()
        plugin = JSONRPC(proxy, {})
        connection = mock.Mock()
        request = HTTPServerRequest(method='PUT', uri='/', version='HTTP/1.1', headers={
            'X-Cocaine-JSON-RPC': 'Enable',
        }, connection=connection, body='{}', host='localhost')
        self.assertTrue(plugin.match(request))

    @tornado.testing.gen_test
    def test_400_parse_error(self):
        proxy = mock.Mock()
        plugin = JSONRPC(proxy, {})

        connection = mock.Mock(spec=[])
        connection.write_headers = mock.MagicMock()
        connection.finish = mock.MagicMock()
        request = HTTPServerRequest(method='PUT', uri='/', version='HTTP/1.1', headers={
            'X-Cocaine-JSON-RPC': 'Enable',
        }, connection=connection, body='{', host='localhost')
        request.logger = NULLLOGGER

        yield plugin.process(request)

        connection.write_headers.assert_called_with(
            ResponseStartLine(version='HTTP/1.1', code=400, reason='Bad JSON-RPC request'),
            mock.ANY,
            '{"message": "Parse error: Invalid JSON was received by the server.", "code": -32700}')

    @tornado.testing.gen_test
    def test_400_invalid_request_error(self):
        proxy = mock.Mock()
        plugin = JSONRPC(proxy, {})

        connection = mock.Mock(spec=[])
        connection.write_headers = mock.MagicMock()
        connection.finish = mock.MagicMock()
        request = HTTPServerRequest(method='PUT', uri='/', version='HTTP/1.1', headers={
            'X-Cocaine-JSON-RPC': 'Enable',
        }, connection=connection, body='{"jsonrpc": 2.0, "method": "method", "params_": [], "id": 1}', host='localhost')
        request.logger = NULLLOGGER

        yield plugin.process(request)

        connection.write_headers.assert_called_with(
            ResponseStartLine(version='HTTP/1.1', code=400, reason='Bad JSON-RPC request'),
            mock.ANY,
            '{"message": "The JSON sent is not a valid Request object.", "code": -32600}')
