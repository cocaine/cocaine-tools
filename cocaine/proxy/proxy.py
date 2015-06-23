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

try:
    import httplib
except ImportError:
    import http.client as httplib

import collections
import functools
import hashlib
import logging
import random
import re
import time

import msgpack
import tornado
from tornado import gen
from tornado import httputil
from tornado.httpserver import HTTPServer
from tornado import process
from toro import Timeout

from cocaine.services import Service
from cocaine.services import Locator
from cocaine.exceptions import ServiceError
from cocaine.exceptions import DisconnectionError
from cocaine.services import EmptyResponse


URL_REGEX = re.compile(r"/([^/]*)/([^/?]*)(.*)")

DEFAULT_SERVICE_CACHE_COUNT = 5
DEFAULT_REFRESH_PERIOD = 120
DEFAULT_TIMEOUT = 30


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return '%s\t%s' % (self.extra["id"], msg), kwargs


def generate_request_id(request):
    data = "%d:%f" % (id(request), time.time())
    return hashlib.md5(data).hexdigest()


def get_request_id(request_id_header, request):
    return request.headers.get(request_id_header) or generate_request_id(request)


def context(func):
    def wrapper(self, request):
        trace_id = self.get_request_id(request)[:16]
        adaptor = ContextAdapter(self.tracking_logger, {"id": trace_id})
        request.logger = adaptor
        request.logger.info("start request: %s %s %s", request.host, request.remote_ip, request.uri)
        return func(self, request)
    return wrapper


def pack_httprequest(request):
    headers = [(item.key, item.value) for item in request.cookies.itervalues()]
    headers.extend(request.headers.items())
    d = request.method, request.uri, request.version.split("/")[1], headers, request.body
    return d


def fill_response_in(request, code, status, message, headers=None):
    headers = headers or httputil.HTTPHeaders({"Content-Length": str(len(message))})
    if "Content-Length" not in headers:
        headers.add("Content-Length", str(len(message)))
    headers.add("X-Powered-By", "Cocaine")

    request.connection.write_headers(
        # start_line
        httputil.ResponseStartLine(request.version, code, status),
        # headers
        headers,
        # data
        message)
    request.connection.finish()
    request.logger.info("finish request: %d %s %.2fms", code, status, 1000.0 * request.request_time())


def parse_locators_endpoints(endpoint):
    host, _, port = endpoint.rpartition(":")
    if host and port:
        try:
            return (host, int(port))
        except ValueError:
            pass

    raise Exception("invalid endpoint: %s" % endpoint)


class CocaineProxy(object):
    def __init__(self, locators=("localhost:10053",),
                 cache=DEFAULT_SERVICE_CACHE_COUNT,
                 request_id_header="", sticky_header="X-Cocaine-Sticky",
                 ioloop=None, **config):

        self.io_loop = ioloop or tornado.ioloop.IOLoop.current()
        self.serviceCacheCount = cache
        self.spoolSize = int(self.serviceCacheCount * 1.5)
        self.refreshPeriod = config.get("refresh_timeout", DEFAULT_REFRESH_PERIOD)
        self.timeouts = config.get("timeouts", {})
        self.locator_endpoints = map(parse_locators_endpoints, locators)
        # it's initialized after start
        # to avoid an io_loop creation before fork
        self.locator = Locator(endpoints=self.locator_endpoints)

        # active applications
        self.cache = collections.defaultdict(list)

        self.logger = ContextAdapter(logging.getLogger(), {"id": "0" * 16})
        self.tracking_logger = logging.getLogger("proxy.tracking")
        self.logger.info("locators %s", ','.join("%s:%d" % (h, p) for h, p in self.locator_endpoints))

        self.sticky_header = sticky_header

        if request_id_header:
            self.get_request_id = functools.partial(get_request_id, request_id_header)
        else:
            self.get_request_id = generate_request_id

    def get_timeout(self, name):
        return self.timeouts.get(name, DEFAULT_TIMEOUT)

    def move_to_inactive(self, app, name):
        def wrapper():
            active_apps = len(self.cache[name])
            if active_apps < self.serviceCacheCount:
                self.io_loop.call_later(self.get_timeout(name), self.move_to_inactive(app, name))
                return

            self.logger.info("%d: move %s %s to an inactive queue (active %d)", id(app), app.name, "{0}:{1}".format(*app.address), active_apps)
            try:
                self.cache[name].remove(app)
            except ValueError:
                self.logger.error("broken cache")

            self.io_loop.call_later(self.get_timeout(name) * 3, functools.partial(self.dispose, app, name))
        return wrapper

    def dispose(self, app, name):
        self.logger.info("dispose service %s %d", name, id(app))
        app.disconnect()

    @gen.coroutine
    @context
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
                    fill_response_in(request, 200, "OK", "OK")
                elif request.path == '/__info':
                    import json

                    # It's likely I'm going to Hell for this one,
                    # but seems there is no other way to obtain internal
                    # statistics.
                    if tornado.version_info[0] == 4:
                        connections = len(self._connections)
                    else:
                        connections = len(self._sockets)

                    body = json.dumps({
                        'services': {
                            'cache': len(self.cache),
                        },
                        'connections': connections,
                        'pending': len(self._pending_sockets),
                    })
                    headers = httputil.HTTPHeaders({"Content-Type": "application/json"})
                    fill_response_in(request, httplib.OK, httplib.responses[httplib.OK], body, headers)
                else:
                    fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND], "Invalid url")
                return

            name, event, other = match.groups()
            if name == '' or event == '':
                fill_response_in(request, httplib.BAD_REQUEST, httplib.responses[httplib.BAD_REQUEST], "Proxy invalid request")
                return

            # Drop from query appname and event's name
            if not other.startswith('/'):
                other = "/" + other
            request.uri = other
            request.path, _, _ = other.partition("?")

        if self.sticky_header not in request.headers:
            app = yield self.get_service(name, request.logger)
        else:
            seed = request.headers.get(self.sticky_header)
            request.logger.debug('sticky_header has been found: %s', seed)
            app = yield self.get_service_with_seed(name, seed, request.logger)

        if app is None:
            message = "current application %s is unavailable" % name
            fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND], message)
            return

        try:
            request.logger.debug("%d: processing request app: `%s`, event `%s`", id(app), app.name, event)
            yield self.process(request, name, app, event, pack_httprequest(request))
        except Exception as err:
            request.logger.error("error during processing request %s", err)
            fill_response_in(request, 502, "Server error", str(err))

    @gen.coroutine
    def process(self, request, name, app, event, data):
        # ToDo: support chunked encoding
        headers = {}
        body_parts = []
        timeout = self.get_timeout(name)
        # allow to reconnect this amount of times.
        attempts = 2  # make it configurable
        while attempts > 0:
            attempts = attempts - 1
            try:
                request.logger.debug("%d: enqueue event (attempt %d)", id(app), attempts)
                channel = yield app.enqueue(event)
                request.logger.debug("%d: send event data (attempt %d)", id(app), attempts)
                yield channel.tx.write(msgpack.packb(data))
                request.logger.debug("%d: waiting for a code and headers (attempt %d)", id(app), attempts)
                code_and_headers = yield channel.rx.get(timeout=timeout)
                request.logger.debug("%d: code and headers have been received (attempt %d)", id(app), attempts)
                code, raw_headers = msgpack.unpackb(code_and_headers)
                headers = tornado.httputil.HTTPHeaders(raw_headers)
                while True:
                    body = yield channel.rx.get(timeout=timeout)
                    if not isinstance(body, EmptyResponse):
                        request.logger.debug("%d: received %d bytes as a body chunk (attempt %d)",
                                             id(app), len(body), attempts)
                        try:
                            # Temp solution. If the body is not packed
                            # an exception will be raised. Unfortunately,
                            # it doesn't work for single-letter string and
                            # for msgpack_python 0.1.1. So we have to check if
                            # the chunk is a string.
                            chunk = msgpack.unpackb(body)
                            if isinstance(chunk, str):
                                body_parts.append(chunk)
                            else:
                                body_parts.append(body)
                        except Exception:
                            body_parts.append(body)
                    else:
                        request.logger.debug("%d: body finished (attempt %d)", id(app), attempts)
                        break
            except Timeout as err:
                request.logger.error("%d: %s", id(app), err)
                message = "application `%s` error: %s" % (name, str(err))
                fill_response_in(request, httplib.GATEWAY_TIMEOUT,
                                 httplib.responses[httplib.GATEWAY_TIMEOUT], message)

            except DisconnectionError as err:
                request.logger.error("%d: %s", id(app), err)
                # Seems on_close callback is not called in case of connecting through IPVS
                # We detect disconnection here to avoid unnecessary errors.
                # Try to reconnect here and give the request a go
                try:
                    yield app.connect()
                except Exception as err:
                    if attempts > 0:
                        # there are still some attempts to reconnect
                        continue
                    else:
                        request.logger.error("%d: %s", id(app), err)
                        message = "application `%s` error: %s" % (name, str(err))
                        fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                         httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)
                        return

            except ServiceError as err:
                request.logger.error("%d: %s", id(app), err)
                message = "application `%s` error: %s" % (name, str(err))
                fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                 httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)

            except Exception as err:
                request.logger.error("%d: %s", id(app), err)
                message = "unknown `%s` error: %s" % (name, str(err))
                fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                 httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)
            else:
                message = ''.join(body_parts)
                fill_response_in(request, code,
                                 httplib.responses.get(code, httplib.OK),
                                 message, headers)
            return

    @gen.coroutine
    def get_service(self, name, logger):
        # cache isn't full for the current application
        if len(self.cache[name]) < self.spoolSize:
            try:
                app = Service(name, locator=self.locator)
                logger.info("%d: creating an instance of %s", id(app), name)
                self.cache[name].append(app)
                yield app.connect()
                logger.info("%d: connect to an app %s endpoint %s ", id(app), app.name, "{0}:{1}".format(*app.address))

                timeout = (1 + random.random()) * self.refreshPeriod
                self.io_loop.call_later(timeout, self.move_to_inactive(app, name))
            except Exception as err:
                logger.error("%d: unable to connect to `%s`: %s", id(app), name, err)
                if app in self.cache[name]:
                    self.cache[name].remove(app)
                raise gen.Return()
            else:
                raise gen.Return(app)

        # get an instance from cache
        chosen = random.choice(self.cache[name])
        raise gen.Return(chosen)

    @gen.coroutine
    def get_service_with_seed(self, name, seed, logger):
        app = Service(name, seed=seed, locator=self.locator)
        try:
            logger.info("%d: creating an instance of %s, seed %s", id(app), name, seed)
            yield app.connect()
        except Exception as err:
            logger.error("%d: unable to connect to `%s`: %s", id(app), name, err)
            raise gen.Return()

        raise gen.Return(app)


def enable_logging(options):
    if options.logging is None or options.logging.lower() == "none":
        return

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, options.logging.upper()))
    fmt = logging.Formatter(options.logfmt, datefmt=options.datefmt)

    if options.log_file_prefix:
        handler = logging.handlers.WatchedFileHandler(
            filename=options.log_file_prefix,
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    if options.log_to_stderr or (options.log_to_stderr is None and not logger.handlers):
        channel = logging.StreamHandler()
        channel.setFormatter(fmt)
        logger.addHandler(channel)


def main():
    from tornado import options

    opts = options.OptionParser()

    opts.define("locators", default=["localhost:10053"],
                type=str, multiple=True, help="comma-separated endpoints of locators")
    opts.define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
                type=int, help="count of instances per service")
    opts.define("config", help="path to configuration file", type=str,
                callback=lambda path: opts.parse_config_file(path, final=False))
    opts.define("count", default=1, type=int, help="count of tornado processes")
    opts.define("port", default=8080, type=int, help="listening port number")
    opts.define("request_header", default="X-Request-Id", type=str, help="header used as a trace id")
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
    opts.define("logfmt", type=str, help="logfmt",
                default="[%(asctime)s.%(msecs)d]\t[%(module)s:%(filename)s:%(lineno)d]\t%(levelname)s\t%(message)s")
    opts.parse_command_line()
    enable_logging(opts)

    logger = logging.getLogger()
    sockets = tornado.netutil.bind_sockets(opts.port)
    logger.info("Listen %s", ' '.join(str("%s:%s" % s.getsockname()[:2]) for s in sockets))
    try:
        if opts.count != 1:
            process.fork_processes(opts.count)

        proxy = CocaineProxy(locators=opts.locators, cache=opts.cache,
                             request_id_header=opts.request_header,
                             sticky_header=opts.sticky_header)
        server = HTTPServer(proxy)
        server.add_sockets(sockets)

        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
