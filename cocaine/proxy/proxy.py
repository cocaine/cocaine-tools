#!/usr/bin/env python
#
# Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
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

from __future__ import print_function

try:
    import httplib
except ImportError:
    import http.client as httplib  # pylint: disable=F0401

import collections
import datetime
import errno
import functools
import hashlib
import logging
import os
import random
import re
import socket
import sys
import time

import msgpack
import tornado
from tornado import gen
from tornado import httputil
from tornado import process
from tornado import web
from tornado.httpserver import HTTPServer
from tornado.iostream import StreamClosedError
from tornado.netutil import bind_sockets, bind_unix_socket

from cocaine.services import Service
from cocaine.services import Locator
from cocaine.exceptions import ServiceError
from cocaine.exceptions import DisconnectionError
from cocaine.services import EmptyResponse
from cocaine.detail.trace import Trace

try:
    from cocaine.tools.version import __version__ as tools_version
except ImportError:
    tools_version = "<undefinded>"


URL_REGEX = re.compile(r"/([^/]*)/([^/?]*)(.*)")

DEFAULT_SERVICE_CACHE_COUNT = 5
DEFAULT_REFRESH_PERIOD = 120
DEFAULT_TIMEOUT = 30

_DEFAULT_BACKLOG = 128

# sec Time to wait for the response chunk from locator
RESOLVE_TIMEOUT = 5

# cocaine system category, I hope it will never be changed
ESYSTEMCATEGORY = 255

# no such application
# we are mature enough to have our own status code
# but nginx proxy_next_upstream does NOT support custom codes
NO_SUCH_APP = httplib.SERVICE_UNAVAILABLE


def proxy_error_headers():
    return httputil.HTTPHeaders({
        "X-Error-Generated-By": "Cocaine-Tornado-Proxy",
    })


def support_reuseport():
    return hasattr(socket, "SO_REUSEPORT")


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {}).update(self.extra)
        return msg, kwargs


class NullLogger(object):
    def __call__(self, *args, **kwargs):
        return self

    def __getattribute__(self, name):
        return self

    def __setattr__(self, name, value):
        pass

    def __delattr__(self, name):
        pass


NULLLOGGER = NullLogger()


class FingersCrossedItem(object):
    def __init__(self):
        self.triggered = False
        self.records = list()

    def append(self, record):
        self.triggered |= record.levelno >= logging.ERROR
        self.records.append(record)


class FingersCrossedHandler(logging.Handler):
    cache = dict()

    def __init__(self, target, level=logging.NOTSET):
        super(FingersCrossedHandler, self).__init__(level=logging.NOTSET)
        self.target = target

    def emit(self, record):
        trace_id = getattr(record, "trace_id", None)
        if trace_id is None:
            return

        fitem = FingersCrossedHandler.cache.setdefault(trace_id, FingersCrossedItem())
        fitem.append(record)
        if fitem.triggered:
            for rec in fitem.records:
                self.target.handle(rec)
            fitem.records = fitem.records[:0]

    @classmethod
    def purge(cls, trace_id):
        if trace_id:
            cls.cache.pop(trace_id, None)


def generate_request_id(request):
    data = "%d:%f" % (id(request), time.time())
    return hashlib.md5(data).hexdigest()


def get_request_id(request_id_header, request, force=False):
    return request.headers.get(request_id_header) or\
        (generate_request_id(request) if force else None)


def context(func):
    @gen.coroutine
    def wrapper(self, request):
        self.requests_in_progress += 1
        self.requests_total += 1
        traceid = None
        try:
            generated_traceid = self.get_request_id(request)
            if generated_traceid is not None:
                # assume we have hexdigest form of number
                # get only 16 digits
                traceid = generated_traceid[:16]
                adaptor = ContextAdapter(self.access_log, {"trace_id": traceid})
                request.logger = adaptor
                # verify user input: request_header must be valid hexdigest
                try:
                    int(traceid, 16)
                except ValueError:
                    fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                     httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                     "Request-Id `%s` is not a hexdigest" % traceid,
                                     proxy_error_headers())
                    return
            else:
                request.logger = NULLLOGGER
            request.traceid = traceid
            request.logger.info("start request: %s %s %s", request.host, request.remote_ip, request.uri)
            yield func(self, request)
        finally:
            self.requests_in_progress -= 1
            FingersCrossedHandler.purge(traceid)
    return wrapper


def pack_httprequest(request):
    headers = [(item.key, item.value) for item in request.cookies.itervalues()]
    headers.extend(request.headers.items())
    d = request.method, request.uri, request.version.split("/")[1], headers, request.body
    return d


def fill_response_in(request, code, status, message, headers=None):
    headers = headers or httputil.HTTPHeaders()
    if "Content-Length" not in headers:
        content_length = str(len(message))
        request.logger.debug("Content-Length header was generated by the proxy: %s", content_length)
        headers.add("Content-Length", content_length)

    headers.add("X-Powered-By", "Cocaine")
    headers["X-XSS-Protection"] = "1; mode=block"
    request.logger.debug("Content-Length: %s", headers["Content-Length"])

    if getattr(request, "traceid", None) is not None:
        headers.add("X-Request-Id", request.traceid)

    if request.method == "HEAD":
        message = None

    request.connection.write_headers(
        # start_line
        httputil.ResponseStartLine(request.version, code, status),
        # headers
        headers,
        # data
        message)
    request.connection.finish()
    request.logger.info("finish request: %d %s %.2fms",
                        code, status, 1000.0 * request.request_time())


def parse_locators_endpoints(endpoint):
    host, _, port = endpoint.rpartition(":")
    if host and port:
        try:
            return (host, int(port))
        except ValueError:
            pass

    raise Exception("invalid endpoint: %s" % endpoint)


def gen_uid():
    return "proxy:%s_%d_%f" % (socket.gethostname(), os.getpid(), time.time())


def scan_for_updates(current, new):
    # add removed groups and new groups to updated
    # mark routing group as updated if its current ring is not
    # the same as new
    updated = filter(lambda k: new[k] != current.pop(k, None), new.keys())
    updated.extend(current.keys())
    return updated


class CocaineProxy(object):
    def __init__(self, locators=("localhost:10053",),
                 cache=DEFAULT_SERVICE_CACHE_COUNT,
                 request_id_header="", sticky_header="X-Cocaine-Sticky",
                 forcegen_request_header=False,
                 ioloop=None, **config):
        # stats
        self.requests_in_progress = 0
        self.requests_disconnections = 0
        self.requests_total = 0

        self.io_loop = ioloop or tornado.ioloop.IOLoop.current()
        self.service_cache_count = cache
        self.spool_size = int(self.service_cache_count * 1.5)
        self.refresh_period = config.get("refresh_timeout", DEFAULT_REFRESH_PERIOD)
        self.timeouts = config.get("timeouts", {})
        self.locator_endpoints = [parse_locators_endpoints(i) for i in locators]
        # it's initialized after start
        # to avoid an io_loop creation before fork
        self.locator = Locator(endpoints=self.locator_endpoints)
        # it's used to reply on `ping` method
        self.locator_status = False

        # active applications
        self.cache = collections.defaultdict(list)

        self.logger = logging.getLogger("cocaine.proxy.general")
        self.access_log = logging.getLogger("cocaine.proxy.access")
        self.access_log.propagate = False
        self.logger.info("locators %s",
                         ','.join("%s:%d" % (h, p) for h, p in self.locator_endpoints))

        self.sticky_header = sticky_header

        if request_id_header:
            self.get_request_id = functools.partial(get_request_id, request_id_header,
                                                    force=forcegen_request_header)
        else:
            self.get_request_id = generate_request_id

        # post the watcher for routing groups
        self.io_loop.add_future(self.on_routing_groups_update(),
                                lambda x: self.logger.error("the updater must not exit"))
        # run infinity check locator health status
        self.locator_health_check()

    @gen.coroutine
    def locator_health_check(self, period=5):
        wait_timeot = datetime.timedelta(seconds=period)
        while True:
            try:
                self.logger.debug("check health status of locator via cluster method")
                channel = yield gen.with_timeout(wait_timeot, self.locator.cluster())
                cluster = yield gen.with_timeout(wait_timeot, channel.rx.get())
                self.locator_status = True
                self.logger.debug("dumped cluster %s", cluster)
                yield gen.sleep(period)
            except Exception as err:
                self.logger.error("health status check failed: %s", err)
                self.locator_status = False
                yield gen.sleep(1)

    @gen.coroutine
    def on_routing_groups_update(self):
        uid = gen_uid()
        self.logger.info("generate new uniqque id %s", uid)
        maximum_timeout = 32  # sec
        timeout = 1  # sec
        while True:
            current = {}
            try:
                self.logger.info("subscribe to updates with id %s", uid)
                channel = yield self.locator.routing(uid, True)
                timeout = 1
                while True:
                    new = yield channel.rx.get()
                    if isinstance(new, EmptyResponse):
                        # it means that the cocaine has been stopped
                        self.logger.error("locator sends close")
                        break
                    updates = scan_for_updates(current, new)
                    # replace current
                    current = new
                    if len(updates) == 0:
                        self.logger.info("locator sends an update message, "
                                         "but no updates have been found")
                        continue

                    self.logger.info("%d routing groups have been refreshed %s",
                                     len(updates), updates)
                    for group in updates:
                        # if we have not created an instance of
                        # the group it is absent in cache
                        if group not in self.cache:
                            self.logger.debug("nothing to update in group %s", group)
                            continue

                        for app in self.cache[group]:
                            self.logger.debug("%d: move %s to the inactive queue to refresh"
                                              " routing group", app.id, app.name)
                            self.migrate_from_cache_to_inactive(app, group)
            except Exception as err:
                timeout = min(timeout << 1, maximum_timeout)
                self.logger.error("error occured while watching for group updates %s. Sleep %d",
                                  err, timeout)
                yield gen.sleep(timeout)

    def get_timeout(self, name):
        return self.timeouts.get(name, DEFAULT_TIMEOUT)

    def migrate_from_cache_to_inactive(self, app, name):
        try:
            self.cache[name].remove(app)
        except ValueError as err:
            self.logger.error("broken cache: %s", err)
        except KeyError as err:
            self.logger.error("broken cache: no such key %s", err)

        self.io_loop.call_later(self.get_timeout(name) * 3,
                                functools.partial(self.dispose, app, name))

    def move_to_inactive(self, app, name):
        def wrapper():
            active_apps = len(self.cache[name])
            if active_apps < self.service_cache_count:
                self.io_loop.call_later(self.get_timeout(name), self.move_to_inactive(app, name))
                return

            self.logger.info("%s: move %s %s to an inactive queue (active %d)",
                             app.id, app.name, "{0}:{1}".format(*app.address), active_apps)
            self.migrate_from_cache_to_inactive(app, name)
        return wrapper

    def dispose(self, app, name):
        self.logger.info("dispose service %s %s", name, app.id)
        app.disconnect()

    @context
    @gen.coroutine
    def __call__(self, request):
        if "X-Cocaine-Service" in request.headers and "X-Cocaine-Event" in request.headers:
            request.logger.debug('dispatch by headers')
            name = request.headers['X-Cocaine-Service']
            event = request.headers['X-Cocaine-Event']
        else:
            request.logger.debug('dispatch by uri')
            match = URL_REGEX.match(request.uri)
            if match is None:
                if request.path == "/ping":
                    if self.locator_status:
                        fill_response_in(request, httplib.OK, "OK", "OK")
                    else:
                        fill_response_in(request, httplib.SERVICE_UNAVAILABLE,
                                         httplib.responses[httplib.SERVICE_UNAVAILABLE],
                                         "Failed", proxy_error_headers())
                else:
                    fill_response_in(request, httplib.NOT_FOUND,
                                     httplib.responses[httplib.NOT_FOUND],
                                     "Invalid url", proxy_error_headers())
                return

            name, event, other = match.groups()
            if name == '' or event == '':
                fill_response_in(request, httplib.BAD_REQUEST,
                                 httplib.responses[httplib.BAD_REQUEST],
                                 "Proxy invalid request", proxy_error_headers())
                return

            # Drop from query appname and event's name
            if not other.startswith('/'):
                other = "/" + other
            request.uri = other
            request.path, _, _ = other.partition("?")

        if self.sticky_header not in request.headers:
            app = yield self.get_service(name, request)
        else:
            seed = request.headers.get(self.sticky_header)
            request.logger.info('sticky_header has been found: %s', seed)
            app = yield self.get_service_with_seed(name, seed, request)

        if app is None:
            message = "current application %s is unavailable" % name
            fill_response_in(request, NO_SUCH_APP, "No Such Application",
                             message, proxy_error_headers())
            return

        try:
            request.logger.debug("%s: processing request app: `%s`, event `%s`",
                                 app.id, app.name, event)
            yield self.process(request, name, app, event, pack_httprequest(request))
        except Exception as err:
            request.logger.error("error during processing request %s", err)
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                             "UID %s: %s" % (request.traceid, str(err)), proxy_error_headers())

        request.logger.info("exit from process")

    def info(self):
        return {'services': {'cache': dict(((k, len(v)) for k, v in self.cache.items()))},
                'requests': {'inprogress': self.requests_in_progress,
                             'total': self.requests_total},
                'errors': {'disconnections': self.requests_disconnections},
                }

    @gen.coroutine
    def process(self, request, name, app, event, data):
        request.logger.info("start processing request after %.3f ms", request.request_time() * 1000)
        timeout = self.get_timeout(name)
        # allow to reconnect this amount of times.
        attempts = 2  # make it configurable

        parentid = 0

        if request.traceid is not None:
            traceid = int(request.traceid, 16)
            trace = Trace(traceid=traceid, spanid=traceid, parentid=parentid)
        else:
            trace = None

        while attempts > 0:
            headers = {}
            body_parts = []
            attempts -= 1
            try:
                request.logger.debug("%s: enqueue event (attempt %d)", app.id, attempts)
                channel = yield app.enqueue(event, trace=trace)
                request.logger.debug("%s: send event data (attempt %d)", app.id, attempts)
                yield channel.tx.write(msgpack.packb(data), trace=trace)
                yield channel.tx.close(trace=trace)
                request.logger.debug("%s: waiting for a code and headers (attempt %d)",
                                     app.id, attempts)
                code_and_headers = yield channel.rx.get(timeout=timeout)
                request.logger.debug("%s: code and headers have been received (attempt %d)",
                                     app.id, attempts)
                code, raw_headers = msgpack.unpackb(code_and_headers)
                headers = httputil.HTTPHeaders(raw_headers)
                while True:
                    body = yield channel.rx.get(timeout=timeout)
                    if isinstance(body, EmptyResponse):
                        request.logger.info("%s: body finished (attempt %d)", app.id, attempts)
                        break

                    request.logger.debug("%s: received %d bytes as a body chunk (attempt %d)",
                                         app.id, len(body), attempts)
                    body_parts.append(body)
            except gen.TimeoutError as err:
                request.logger.error("%s %s:  %s", app.id, name, err)
                message = "UID %s: application `%s` error: TimeoutError" % (request.traceid, name)
                fill_response_in(request, httplib.GATEWAY_TIMEOUT,
                                 httplib.responses[httplib.GATEWAY_TIMEOUT],
                                 message, proxy_error_headers())

            except (DisconnectionError, StreamClosedError) as err:
                self.requests_disconnections += 1
                # Probably it's dangerous to retry requests all the time.
                # I must find the way to determine whether it failed during writing
                # or reading a reply. And retry only writing fails.
                request.logger.error("%s: %s", app.id, err)
                if attempts <= 0:
                    request.logger.error("%s: no more attempts", app.id)
                    fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                     httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                     "UID %s: Connection problem" % request.traceid,
                                     proxy_error_headers())
                    return

                # Seems on_close callback is not called in case of connecting through IPVS
                # We detect disconnection here to avoid unnecessary errors.
                # Try to reconnect here and give the request a go
                try:
                    start_time = time.time()
                    reconn_timeout = timeout - request.request_time()
                    request.logger.info("%s: connecting with timeout %.fms", app.id, reconn_timeout * 1000)
                    yield gen.with_timeout(start_time + reconn_timeout, app.connect(request.traceid))
                    reconn_time = time.time() - start_time
                    request.logger.info("%s: connecting took %.3fms", app.id, reconn_time * 1000)
                except Exception as err:
                    if attempts <= 0:
                        # we have no attempts more, so quit here
                        request.logger.error("%s: %s (no attempts left)", app.id, err)
                        message = "UID %s: application `%s` error: %s" % (request.traceid, name, str(err))
                        fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                         httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                         message, proxy_error_headers())
                        return

                    request.logger.error("%s: unable to reconnect: %s (%d attempts left)",
                                         err, attempts)
                # We have an attempt to process request again.
                # Jump to the begining of `while attempts > 0`, either we connected successfully
                # or we were failed to connect
                continue

            except ServiceError as err:
                # if the application has been restarted, we get broken pipe code
                # and system category
                if err.code == errno.EPIPE and err.category == ESYSTEMCATEGORY:
                    request.logger.error("%s: the application has been restarted", app.id)
                    app.disconnect()
                    continue

                request.logger.error("%s: %s", app.id, err)
                message = "UID %s: application `%s` error: %s" % (request.traceid, name, str(err))
                fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                 httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                 message, proxy_error_headers())

            except Exception as err:
                request.logger.error("%s: %s", app.id, err)
                message = "UID %s: unknown `%s` error: %s" % (request.traceid, name, str(err))
                fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                 httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                 message, proxy_error_headers())
            else:
                message = ''.join(body_parts)
                fill_response_in(request, code,
                                 httplib.responses.get(code, httplib.OK),
                                 message, headers)
            # to return from all errors except Disconnection
            # or receiving a good reply
            return

    @gen.coroutine
    def get_service(self, name, request):
        # cache isn't full for the current application
        if len(self.cache[name]) < self.spool_size:
            logger = request.logger
            try:
                app = Service(name, locator=self.locator, timeout=RESOLVE_TIMEOUT)
                logger.info("%s: creating an instance of %s", app.id, name)
                self.cache[name].append(app)
                yield app.connect(request.traceid)
                logger.info("%s: connect to an app %s endpoint %s ",
                            app.id, app.name, "{0}:{1}".format(*app.address))

                timeout = (1 + random.random()) * self.refresh_period
                self.io_loop.call_later(timeout, self.move_to_inactive(app, name))
            except Exception as err:
                logger.error("%s: unable to connect to `%s`: %s", app.id, name, err)
                if app in self.cache[name]:
                    self.cache[name].remove(app)
                raise gen.Return()
            else:
                raise gen.Return(app)

        # get an instance from cache
        chosen = random.choice(self.cache[name])
        raise gen.Return(chosen)

    @gen.coroutine
    def get_service_with_seed(self, name, seed, request):
        logger = request.logger
        app = Service(name, seed=seed, locator=self.locator)
        try:
            logger.info("%s: creating an instance of %s, seed %s", app.id, name, seed)
            yield app.connect(request.traceid)
        except Exception as err:
            logger.error("%s: unable to connect to `%s`: %s", app.id, name, err)
            raise gen.Return()

        raise gen.Return(app)


class PingHandler(web.RequestHandler):  # pylint: disable=W0223
    def get(self):
        self.write("OK")


class LogLevel(web.RequestHandler):  # pylint: disable=W0223
    def get(self):
        lvl = self.application.logger.getEffectiveLevel()
        self.write(logging.getLevelName(lvl))

    def post(self):
        lvlname = self.get_argument("level")
        lvl = getattr(logging, lvlname.upper(), None)
        if lvl is None:
            self.write("No such level %s" % lvlname)
            return

        logging.getLogger().setLevel(lvl)
        self.write("level %s has been set" % logging.getLevelName(lvl))


class InfoHandler(web.RequestHandler):
    def get(self):
        info = self.application.proxy.info()
        self.write(info)


class UtilServer(web.Application):  # pylint: disable=W0223
    def __init__(self, proxy):
        self.proxy = proxy
        self.logger = logging.getLogger("proxy.utilserver")
        handlers = [
            (r"/ping", PingHandler),
            (r"/info", InfoHandler),
            (r"/logger", LogLevel),
        ]
        super(UtilServer, self).__init__(handlers=handlers)

    def log_request(self, handler):
        request_time = 1000.0 * handler.request.request_time()
        self.logger.info("%d %s %.2fms", handler.get_status(),
                         handler._request_summary(), request_time)


def enable_logging(options):
    if options.logging is None or options.logging.lower() == "none":
        return

    general_logger = logging.getLogger("cocaine.proxy.general")
    general_logger.setLevel(getattr(logging, options.logging.upper()))
    general_formatter = logging.Formatter(options.generallogfmt, datefmt=options.datefmt)

    access_logger = logging.getLogger("cocaine.proxy.access")
    access_logger.setLevel(getattr(logging, options.logging.upper()))
    access_formatter = logging.Formatter(options.accesslogfmt, datefmt=options.datefmt)

    cocainelogger = None
    if options.logframework:
        cocainelogger = logging.getLogger("cocaine.baseservice")
        cocainelogger.setLevel(getattr(logging, options.logging.upper()))

    if options.log_file_prefix:
        handler = logging.handlers.WatchedFileHandler(
            filename=options.log_file_prefix,
        )
        handler.setFormatter(general_formatter)
        general_logger.addHandler(handler)

        handler = logging.handlers.WatchedFileHandler(
            filename=options.log_file_prefix,
        )
        handler.setFormatter(access_formatter)
        if options.fingerscrossed:
            access_logger.addHandler(FingersCrossedHandler(handler))
        else:
            access_logger.addHandler(handler)

        if cocainelogger:
            cocainehandler = logging.handlers.WatchedFileHandler(
                filename=options.log_file_prefix + "framework.log"
            )
            cocainehandler.setFormatter(general_formatter)
            cocainelogger.addHandler(cocainehandler)

    if options.log_to_stderr or (options.log_to_stderr is None and not general_logger.handlers):
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(general_formatter)

        general_logger.addHandler(stderr_handler)
        if cocainelogger:
            cocainelogger.addHandler(stderr_handler)

        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(access_formatter)

        if options.fingerscrossed:
            access_logger.addHandler(FingersCrossedHandler(target=stderr_handler))
        else:
            access_logger.addHandler(stderr_handler)


TcpEndpoint = collections.namedtuple('TcpEndpoint', ["host", "port"])


class Endpoints(object):
    unix_prefix = "unix://"
    tcp_prefix = "tcp://"

    def __init__(self, endpoints):
        self.unix = []
        self.tcp = []
        for i in endpoints:
            if i.startswith(Endpoints.unix_prefix):
                self.unix.append(i[len(Endpoints.unix_prefix):])
            elif i.startswith(Endpoints.tcp_prefix):
                raw = i[len(Endpoints.tcp_prefix):]
                delim_count = raw.count(":")
                if delim_count == 0:  # no port
                    raise ValueError("Endpoint has to containt host:port: %s" % i)
                elif delim_count == 1:  # ipv4 or hostname
                    host, _, port = raw.partition(":")
                    self.tcp.append(TcpEndpoint(host=host, port=int(port)))
                elif delim_count > 1:  # ipv6
                    host, _, port = raw.rpartition(":")
                    if host[0] != "[" or host[-1] != "]":
                        raise ValueError("Invalid IPv6 address %s" % i)
                    self.tcp.append(TcpEndpoint(host=host.strip("[]"), port=int(port)))
            else:
                raise ValueError("Endpoint has to begin either unix:// or tcp:// %s" % i)

    @property
    def has_unix(self):
        return len(self.unix) > 0

    @property
    def has_tcp(self):
        return len(self.tcp) > 0


DEFAULT_GENERAL_LOGFORMAT = "[%(asctime)s.%(msecs)d]\t[%(filename).5s:%(lineno)d]\t%(levelname)s\t%(message)s"
DEFAULT_ACCESS_LOGFORMAT = "[%(asctime)s.%(msecs)d]\t[%(filename).5s:%(lineno)d]\t%(levelname)s\t%(trace_id)s\t%(message)s"


def show_version(dummy):
    print("cocaine tools & proxy: %s" % tools_version)
    sys.exit(0)


def main():
    from tornado import options

    opts = options.OptionParser()
    opts.define("version", type=bool, help="show version and exit", callback=show_version)
    opts.define("locators", default=["localhost:10053"],
                type=str, multiple=True, help="comma-separated endpoints of locators")
    opts.define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
                type=int, help="count of instances per service")
    opts.define("config", help="path to configuration file", type=str,
                callback=lambda path: opts.parse_config_file(path, final=False))
    opts.define("count", default=1, type=int, help="count of tornado processes")
    opts.define("port", default=8080, type=int, help="listening port number")
    opts.define("endpoints", default=["tcp://localhost:8080"], type=str, multiple=True,
                help="Specify endpoints to bind on: prefix unix:// or tcp:// should be used")
    opts.define("request_header", default="X-Request-Id", type=str,
                help="header used as a trace id")
    opts.define("forcegen_request_header", default=False, type=bool,
                help="enable force generation of the request header")
    opts.define("sticky_header", default="X-Cocaine-Sticky", type=str, help="sticky header name")

    # various logging options
    opts.define("logging", default="info",
                help=("Set the Python log level. If 'none', tornado won't touch the "
                      "logging configuration."), metavar="debug|info|warning|error|none")
    opts.define("log_to_stderr", type=bool, default=None,
                help=("Send log output to stderr. "
                      "By default use stderr if --log_file_prefix is not set and "
                      "no other logging is configured."))
    opts.define("log_file_prefix", type=str, default=None, metavar="PATH",
                help=("Path prefix for log file"))
    opts.define("datefmt", type=str, default="%z %d/%b/%Y:%H:%M:%S", help="datefmt")
    opts.define("generallogfmt", type=str, help="log format of general logging system",
                default=DEFAULT_GENERAL_LOGFORMAT)
    opts.define("accesslogfmt", type=str, help="log format of access logging system",
                default=DEFAULT_ACCESS_LOGFORMAT)
    opts.define("logframework", type=bool, default=False,
                help="enable logging various framework messages")
    opts.define("fingerscrossed", type=bool, default=True,
                help="enable lazy logging")

    # util server
    opts.define("utilport", default=8081, type=int, help="listening port number for an util server")
    opts.define("utiladdress", default="127.0.0.1", type=str, help="address for an util server")
    opts.define("enableutil", default=False, type=bool, help="enable util server")

    opts.define("so_reuseport", default=True, type=bool, help="use SO_REUSEPORT option")

    opts.parse_command_line()
    enable_logging(opts)

    logger = logging.getLogger("cocaine.proxy.general")

    use_reuseport = False

    endpoints = Endpoints(opts.endpoints)
    sockets = []

    if endpoints.has_unix:
        logger.info("Start binding on unix sockets")
        for path in endpoints.unix:
            logger.info("Binding on %s", path)
            sockets.append(bind_unix_socket(path, mode=0o666))

    if opts.so_reuseport:
        if not support_reuseport():
            logger.warning("Your system doesn't support SO_REUSEPORT."
                           " Bind and fork mechanism will be used")
        else:
            logger.info("SO_REUSEPORT will be used")
            use_reuseport = True

    if not use_reuseport and endpoints.has_tcp:
        logger.info("Start binding on tcp sockets")
        for endpoint in endpoints.tcp:
            logger.info("Binding on %s:%d", endpoint.host, endpoint.port)
            # We have to bind before fork to distribute sockets to our forks
            socks = bind_sockets(endpoint.port, address=endpoint.host)
            logger.info("Listening %s", ' '.join(str("%s:%s" % s.getsockname()[:2]) for s in socks))
            sockets.extend(socks)

    if opts.enableutil:
        utilsockets = bind_sockets(opts.utilport, address=opts.utiladdress)
        logger.info("Util server is listening on %s",
                    ' '.join(str("%s:%s" % s.getsockname()[:2]) for s in utilsockets))

    try:
        if opts.count != 1:
            process.fork_processes(opts.count)

        if use_reuseport and endpoints.has_tcp:
            logger.info("Start binding on tcp sockets")
            for endpoint in endpoints.tcp:
                logger.info("Binding on %s:%d", endpoint.host, endpoint.port)
                # We have to bind before fork to distribute sockets to our forks
                socks = bind_sockets(endpoint.port, address=endpoint.host, reuse_port=True)
                logger.info("Listening %s",
                            ' '.join(str("%s:%s" % s.getsockname()[:2]) for s in socks))
                sockets.extend(socks)

        proxy = CocaineProxy(locators=opts.locators, cache=opts.cache,
                             request_id_header=opts.request_header,
                             sticky_header=opts.sticky_header,
                             forcegen_request_header=opts.forcegen_request_header)
        server = HTTPServer(proxy)
        server.add_sockets(sockets)

        if opts.enableutil:
            utilsrv = HTTPServer(UtilServer(proxy=proxy))
            utilsrv.add_sockets(utilsockets)

        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
