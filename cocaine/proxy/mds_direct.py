try:
    import httplib
except ImportError:
    import http.client as httplib  # pylint: disable=F0401

from random import shuffle

import msgpack

from tornado import gen
from tornado import httputil
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPError
from tornado.httpclient import HTTPRequest

from cocaine.services import Service

from cocaine.proxy.helpers import extract_app_and_event
from cocaine.proxy.helpers import fill_response_in
from cocaine.proxy.helpers import pack_httprequest

from cocaine.proxy.plugin import IPlugin
from cocaine.proxy.plugin import PluginApplicationError
from cocaine.proxy.plugin import PluginConfigurationError
from cocaine.proxy.plugin import PluginNoSuchApplication

from cocaine.proxy.proxy import RESOLVE_TIMEOUT


def is_mds_stid(stid):
    _, _, tail = stid.split(".", 2)
    return tail.startswith('E') and ':' in tail


class MDSDirect(IPlugin):
    def __init__(self, proxy, config):
        super(MDSDirect, self).__init__(proxy)
        try:
            self.dist_info_endpoint = config["dist_info_endpoint"]
            self.locator_port = config["locator_port"]
            self.filter_mds_stid = config.get("filter_stid", True)
            self.srw_httpclient = AsyncHTTPClient()
        except KeyError as err:
            raise PluginConfigurationError(self.name(), "option required %s" % err)

    @staticmethod
    def name():
        return "mds-direct"

    def match(self, request):
        if "X-Srw-Key" in request.headers and "X-Srw-Key-Type" in request.headers and "X-Srw-Namespace" in request.headers:
            return not self.filter_mds_stid or is_mds_stid(request.headers["X-Srw-Key"])
        return False

    @gen.coroutine
    def reelect_app(self, request, app):
        app.disconnect()
        app.locator_endpoints = app.locator_endpoints[1:] + app.locator_endpoints[:1]
        raise gen.Return(app)

    @gen.coroutine
    def process(self, request):

        mds_request_headers = httputil.HTTPHeaders()
        if "Authorization" in request.headers:
            mds_request_headers["Authorization"] = request.headers["Authorization"]

        traceid = getattr(request, "traceid", None)
        if traceid is not None:
            mds_request_headers["X-Request-Id"] = traceid

        key = request.headers["X-Srw-Key"]

        name, event = extract_app_and_event(request)
        timeout = self.proxy.get_timeout(name, event)
        name = self.proxy.resolve_group_to_version(name)
        srw_request = HTTPRequest(
            "%s/gate/dist-info/%s?primary-only" % (self.dist_info_endpoint, key),
            method="GET",
            headers=mds_request_headers,
            allow_ipv6=True,
            request_timeout=timeout)

        try:
            # NOTE: we can do it in a streaming way
            resp = yield self.srw_httpclient.fetch(srw_request)
            body = resp.buffer.read(None)
            lines = body.split("\n")
            endpoints = [(line.split()[0], self.locator_port) for line in lines if line]
            shuffle(endpoints)
            request.logger.debug("connecting to application %s via %s", name, endpoints)
            app = Service(name, endpoints=endpoints, timeout=RESOLVE_TIMEOUT)
            yield self.proxy.process(request, name, app, event, pack_httprequest(request), self.reelect_app, timeout)

        except HTTPError as err:
            if err.code == 404:
                raise PluginNoSuchApplication("404")

            if err.code == 500:
                raise PluginApplicationError(42, 42, "500")

            if err.code == 401:
                fill_response_in(request, err.code,
                                 httplib.responses.get(err.code, httplib.OK),
                                 err.response.body, err.response.headers)
                return

            raise err


def decode_chunked_encoded_reply(resp):
    # read_size is set to to prevent overead from BytesIO
    # in this case the rest of the buffer is not packed data
    code, raw_headers = msgpack.Unpacker(resp.buffer, read_size=1).unpack()
    body = resp.buffer.read(None)
    headers = httputil.HTTPHeaders(raw_headers)
    return code, headers, body
