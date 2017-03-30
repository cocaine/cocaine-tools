try:
    import httplib
except ImportError:
    import http.client as httplib  # pylint: disable=F0401

from datetime import timedelta
from random import shuffle

import msgpack

import json

from tornado import gen
from tornado import httputil
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPError
from tornado.httpclient import HTTPRequest

from cocaine.exceptions import ServiceError

from cocaine.services import Service, Locator

from cocaine.proxy.helpers import extract_app_and_event
from cocaine.proxy.helpers import fill_response_in
from cocaine.proxy.helpers import pack_httprequest

from cocaine.proxy.plugin import IPlugin
from cocaine.proxy.plugin import PluginApplicationError
from cocaine.proxy.plugin import PluginConfigurationError
from cocaine.proxy.plugin import PluginNoSuchApplication

from cocaine.proxy.proxy import RESOLVE_TIMEOUT, LOCATORCATEGORY, ESERVICENOTAVAILABLE


def is_mds_stid(stid):
    parts = stid.split(".", 2)
    return len(parts) == 3 and parts[2].startswith('E') and ':' in parts[2]


def is_mds_key(key):
    parts = key.split("/", 1)
    return len(parts) == 2 and parts[0].isdigit()


class MDSDirect(IPlugin):
    def __init__(self, proxy, config):
        super(MDSDirect, self).__init__(proxy)
        try:
            self.dist_info_endpoint = config["dist_info_endpoint"]
            self.mds_dist_info_endpoint = config["mds_dist_info_endpoint"]
            self.locator_port = config["locator_port"]
            self.filter_mds_stid = config.get("filter_stid", True)
            self.service_connect_timeout = timedelta(milliseconds=config.get("service_connect_timeout_ms", 1500))
            self.srw_httpclient = AsyncHTTPClient()
        except KeyError as err:
            raise PluginConfigurationError(self.name(), "option required %s" % err)

    @staticmethod
    def name():
        return "mds-direct"

    def match(self, request):
        if "X-Srw-Key" in request.headers and "X-Srw-Key-Type" in request.headers and "X-Srw-Namespace" in request.headers:
            key = request.headers["X-Srw-Key"]
            return not self.filter_mds_stid or is_mds_stid(key) or is_mds_key(key)
        return False

    @gen.coroutine
    def reelect_app(self, request, app):
        """tries to connect to the same app on differnet host from dist-info"""

        # store current endpoints of locator
        locator_endpoints = app.locator.endpoints

        # disconnect app explicitly to break possibly existing connection
        app.disconnect()
        app.locator = None
        endpoints_size = len(locator_endpoints)

        # last chance to take app from common pool
        if endpoints_size == 0:
            request.logger.info("giving up on connecting to dist-info hosts, falling back to common pool processing")
            app = yield self.proxy.reelect_app(request, app)
            raise gen.Return(app)

        # try x times, where x is the number of different endpoints in app locator.
        for _ in xrange(0, endpoints_size):
            try:
                # always create new locator to prevent locking as we do connect with timeout
                # however lock can be still held during TCP timeout
                locator = Locator(endpoints=locator_endpoints)
                request.logger.info("connecting to locator %s", locator.endpoints[0])

                # first try to connect to locator only on remote host with timeout
                yield gen.with_timeout(self.service_connect_timeout, locator.connect())
                request.logger.debug("connected to locator %s for %s", locator.endpoints[0], app.name)
                app = Service(app.name, locator=locator, timeout=RESOLVE_TIMEOUT)

                # try to resolve and connect to application itself
                yield gen.with_timeout(self.service_connect_timeout, app.connect())
                request.logger.debug("connected to application %s via %s", app.name, app.endpoints)
            except gen.TimeoutError:
                # on timeout try next endpoint first
                request.logger.warning("timed out while connecting to application")
                continue
            except ServiceError as err:
                request.logger.warning("got error while resolving app - %s", err)
                if err.category in LOCATORCATEGORY and err.code == ESERVICENOTAVAILABLE:
                    # if the application is down - also try next endpoint
                    continue
                else:
                    raise err
            # drop first endpoint to start next connection from different endpoint
            # we do this, as default logic of connection attempts in locator do not fit here
            app.locator.endpoints = app.locator.endpoints[1:]
            # return connected app
            raise gen.Return(app)
        raise PluginApplicationError(42, 42, "could not connect to application")

    def is_stid_request(self, request):
        return request.headers["X-Srw-Key-Type"].upper() == "STID"

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
        if self.is_stid_request(request):
            url = "%s/gate/dist-info/%s?primary-only" % (self.dist_info_endpoint, key)
            request.logger.debug("fetching endpoints via mulcagate dist-info - %s", url)
            srw_request = HTTPRequest(
                url,
                method="GET",
                headers=mds_request_headers,
                allow_ipv6=True,
                request_timeout=timeout)
        else:
            url = "%s/dist-info-%s/%s" % (self.mds_dist_info_endpoint, request.headers["X-Srw-Namespace"], key)
            request.logger.debug("fetching endpoints via mds dist-info - %s", url)
            srw_request = HTTPRequest(
                url,
                method="GET",
                headers=mds_request_headers,
                allow_ipv6=True,
                request_timeout=timeout)

        endpoints = yield self.fetch_mds_endpoints(request, srw_request)
        locator = Locator(endpoints=endpoints)
        app = Service(name, locator=locator, timeout=RESOLVE_TIMEOUT)
        request.logger.info("connecting to app %s", name)
        app = yield self.reelect_app(request, app)
        # TODO: attempts should be configurable
        yield self.proxy.process(request, name, app, event, pack_httprequest(request), self.reelect_app, 4, timeout)

    def decode_mulca_dist_info(self, body):
        lines = body.split("\n")
        endpoints = [(line.split()[0], self.locator_port) for line in lines if line]
        shuffle(endpoints)
        return endpoints

    def decode_mds_dist_info(self, body):
        obj = json.loads(body)
        endpoints = [(x['host'], self.locator_port) for x in obj['primary']]
        shuffle(endpoints)
        return endpoints

    @gen.coroutine
    def fetch_mds_endpoints(self, request, srw_request):
        try:
            # NOTE: we can do it in a streaming way
            resp = yield self.srw_httpclient.fetch(srw_request)
            body = resp.buffer.read(None)
            if self.is_stid_request(request):
                raise gen.Return(self.decode_mulca_dist_info(body))
            else:
                raise gen.Return(self.decode_mds_dist_info(body))

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
