import json

from tornado import gen
from tornado import httputil

from cocaine.proxy.helpers import fill_response_in
from cocaine.proxy.plugin import IPlugin


REQUEST_FIELDS = ('jsonrpc', 'method', 'params', 'id')


class JSONRPC(IPlugin):
    def __init__(self, proxy, _):
        super(JSONRPC, self).__init__(proxy)

    @staticmethod
    def name():
        return "jsonrpc"

    def match(self, request):
        return "X-Cocaine-JSON-RPC" in request.headers

    @gen.coroutine
    def process(self, request):
        try:
            jsonrpc = json.loads(request.body)
            if not all(k in jsonrpc for k in REQUEST_FIELDS):
                headers = httputil.HTTPHeaders({'Content-Type': 'application/json-rpc'})
                body = {'code': -32600, 'message': 'The JSON sent is not a valid Request object.'}
                fill_response_in(request, 400, 'Bad JSON-RPC request', json.dumps(body), headers)
                return
            args = jsonrpc['params']
            service_name, service_method = jsonrpc['method'].split('.', 2)
        except ValueError:
            headers = httputil.HTTPHeaders({'Content-Type': 'application/json-rpc'})
            body = {'code': -32700, 'message': 'Parse error	Invalid JSON was received by the server.'}
            fill_response_in(request, 400, 'Bad JSON-RPC request', json.dumps(body), headers)
            return

        try:
            service = yield self.proxy.get_service(service_name, request)
            methods = (data[0] for (id, data) in service.api.iteritems())
            if service_method not in methods:
                headers = httputil.HTTPHeaders({'Content-Type': 'application/json-rpc'})
                body = {'code': -32601, 'message': 'Method not found.'}
                fill_response_in(request, 400, 'Bad JSON-RPC request', json.dumps(body), headers)
                return
            named_api = dict((name, [name, tx, rx]) for (id, (name, tx, rx)) in service.api.iteritems())
            _, _, rxtree = named_api[service_method]
            if rxtree != {0: ['value', {}], 1: ['error', {}]}:
                headers = httputil.HTTPHeaders({'Content-Type': 'application/json-rpc'})
                body = {'code': -32000, 'message': 'Protocol is not primitive.'}
                fill_response_in(request, 400, 'Bad JSON-RPC request', json.dumps(body), headers)
                return
            channel = yield getattr(service, service_method)(*args)
            result = yield channel.rx.get()
        except Exception as err:
            headers = httputil.HTTPHeaders({'Content-Type': 'application/json-rpc'})
            body = {
                'jsonrpc': '2.0',
                'error': str(err),
                'id': jsonrpc['id'],
            }
            fill_response_in(request, 500, 'Internal Server Error', json.dumps(body), headers)
        else:
            headers = httputil.HTTPHeaders({
                'Content-Type': 'application/json-rpc'
            })
            body = {
                'jsonrpc': '2.0',
                'result': result,
                'id': jsonrpc['id'],
            }
            fill_response_in(request, 200, 'OK', json.dumps(body), headers)
