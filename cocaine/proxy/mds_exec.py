try:
    import httplib
except ImportError:
    import http.client as httplib  # pylint: disable=F0401

import msgpack

from tornado import gen
from tornado import httputil
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPError
from tornado.httpclient import HTTPRequest

from cocaine.proxy.helpers import extract_app_and_event
from cocaine.proxy.helpers import fill_response_in
from cocaine.proxy.helpers import pack_httprequest

from cocaine.proxy.plugin import IPlugin
from cocaine.proxy.plugin import PluginApplicationError
from cocaine.proxy.plugin import PluginConfigurationError
from cocaine.proxy.plugin import PluginNoSuchApplication


def is_mds_stid(stid):
    _, _, tail = stid.split(".", 2)
    return tail.startswith('E') and ':' in tail


class MDSExec(IPlugin):
    def __init__(self, proxy, config):
        super(MDSExec, self).__init__(proxy)
        try:
            self.srw_host = config["srw_host"]
            self.filter_mds_stid = config.get("filter_stid", True)
            self.srw_httpclient = AsyncHTTPClient()
        except KeyError as err:
            raise PluginConfigurationError(self.name(), "option required %s" % err)

    @staticmethod
    def name():
        return "mds"

    def match(self, request):
        if "X-Srw-Key" in request.headers and "X-Srw-Key-Type" in request.headers and "X-Srw-Namespace" in request.headers:
            return not self.filter_mds_stid or is_mds_stid(request.headers["X-Srw-Key"])
        return False

    @gen.coroutine
    def process(self, request):
        name, event = extract_app_and_event(request)
        timeout = self.proxy.get_timeout(name, event)
        # as MDS proxy bypasses the mechanism of routing groups
        # the proxy is responsible to provide this feature
        name = self.proxy.resolve_group_to_version(name)
        headers = request.headers
        namespace = headers["X-Srw-Namespace"]
        key = headers["X-Srw-Key"]

        mds_request_headers = httputil.HTTPHeaders()
        if "Authorization" in request.headers:
            mds_request_headers["Authorization"] = request.headers["Authorization"]

        traceid = getattr(request, "traceid", None)
        if traceid is not None:
            mds_request_headers["X-Request-Id"] = traceid

        srw_request = HTTPRequest("%s/exec-%s/%s/%s/stid/%s?timeout=%d" % (self.srw_host, namespace, name, event, key, timeout),
                                  method="POST",
                                  headers=mds_request_headers,
                                  body=msgpack.packb(pack_httprequest(request)),
                                  allow_ipv6=True,
                                  request_timeout=timeout)

        try:
            # NOTE: we can do it in a streaming way
            resp = yield self.srw_httpclient.fetch(srw_request)
            code, reply_headers, body = decode_chunked_encoded_reply(resp)
            fill_response_in(request, code,
                             httplib.responses.get(code, httplib.OK),
                             body, reply_headers)
        except HTTPError as err:
            if err.code == 404:
                raise PluginNoSuchApplication("worker was not found")

            if err.code == 500:
                raise PluginApplicationError(42, 42, "worker replied with error")

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
