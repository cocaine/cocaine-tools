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

import msgpack
import tornado
from tornado import gen
from tornado import httputil
from tornado.httpserver import HTTPServer
from tornado import process
from toro import Timeout

from cocaine.services import Service
from cocaine.exceptions import ServiceError
from cocaine.detail.service import EmptyResponse


URL_REGEX = re.compile(r"/([^/]*)/([^/?]*)(.*)")

DEFAULT_SERVICE_CACHE_COUNT = 5
DEFAULT_REFRESH_PERIOD = 120
DEFAULT_TIMEOUT = 5


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return '%s %s' % (self.extra["id"], msg), kwargs


def generate_request_id(request):
    m = hashlib.md5()
    m.update("%s" % id(request))
    return m.hexdigest()[:15]


def context(func):
    def wrapper(self, request):
        adaptor = ContextAdapter(self.tracking_logger, {"id": generate_request_id(request)})
        request.logger = adaptor
        request.logger.info("%s %s %s", request.host, request.remote_ip, request.uri)
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

    request.connection.write_headers(
        # start_line
        httputil.ResponseStartLine(request.version, code, status),
        # headers
        headers,
        # data
        message)
    request.connection.finish()
    request.logger.info("%s %d %.2fms", status, code, 1000.0 * request.request_time())


class CocaineProxy(HTTPServer):
    def __init__(self, port=8080, cache=DEFAULT_SERVICE_CACHE_COUNT, **config):
        super(CocaineProxy, self).__init__(self.handle_request, **config)
        self.port = port
        self.serviceCacheCount = cache
        self.spoolSize = int(self.serviceCacheCount * 1.5)
        self.refreshPeriod = config.get("refresh_timeout", DEFAULT_REFRESH_PERIOD)
        self.timeouts = config.get("timeouts", {})

        # active applications
        self.cache = collections.defaultdict(list)

        self.logger = logging.getLogger()
        self.tracking_logger = logging.getLogger("proxy.tracking")

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
    def handle_request(self, request):
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

        app = yield self.get_service(name, request.logger)
        if app is None:
            message = "Current application %s is unavailable" % name
            fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND], message)
            return

        try:
            request.logger.debug("%d: processing request %s %s", id(app), app.name, event)
            yield self.process(request, name, app, event, pack_httprequest(request))
        except Exception as err:
            request.logger.error("error during processing request %s", err)
            fill_response_in(request, 502, "Server error", str(err))

    @gen.coroutine
    def process(self, request, name, app, event, data):
        headers = {}
        body_parts = []
        timeout = self.get_timeout(name)
        try:
            channel = yield app.enqueue(event)
            yield channel.tx.write(msgpack.packb(data))
            code_and_headers = yield channel.rx.get(timeout=timeout)
            # the first chunk is packed code and headers
            code, raw_headers = msgpack.unpackb(code_and_headers)
            headers = tornado.httputil.HTTPHeaders(raw_headers)
            while True:
                body = yield channel.rx.get(timeout=timeout)
                if not isinstance(body, EmptyResponse):
                    body_parts.append(msgpack.unpackb(body))
                else:
                    break
        except Timeout as err:
            request.logger.error("%d: %s", id(app), err)
            message = "Application `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.GATEWAY_TIMEOUT,
                             httplib.responses[httplib.GATEWAY_TIMEOUT], message)

        except ServiceError as err:
            request.logger.error("%d: %s", id(app), err)
            message = "Application `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)

        except Exception as err:
            request.logger.error("%d: %s", id(app), err)
            message = "Unknown `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)
        else:
            message = ''.join(body_parts)
            fill_response_in(request, code,
                             httplib.responses.get(code, httplib.OK),
                             message, headers)

    @gen.coroutine
    def get_service(self, name, logger):
        # cache isn't full for the current application
        if len(self.cache[name]) < self.spoolSize:
            try:
                app = Service(name)
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

        # get an instance from cache
        chosen = random.choice(self.cache[name])
        raise gen.Return(chosen)

    def run(self, count=1):
        try:
            self.logger.info('Proxy will be started at %d port with %d instance(s)',
                             self.port, count if count >= 1 else process.cpu_count())
            self.bind(self.port)
            self.start(count)
            self._io_loop = tornado.ioloop.IOLoop.current()
            self._io_loop.start()
        except KeyboardInterrupt:
            pass
        except Exception as err:
            self.logger.error(err)

        if process.task_id() is not None:
            self._io_loop.stop()
        else:
            self.logger.info("stopped")


def enable_logging(options):
    if options.logging is None or options.logging.lower() == "none":
        return

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, options.logging.upper()))
    fmt = logging.Formatter("%(levelname)-5.5s %(asctime)s %(module)5.5s:%(lineno)-3d %(message)s",
                            datefmt="%d-%m-%Y %H:%M:%S %z")

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

    opts.define("port", default=8080, type=int, help="listening port number")
    opts.define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
                type=int, help="count of instances per service")
    opts.define("count", default=1, type=int, help="count of tornado processes")
    opts.define("config", help="path to configuration file", type=str,
                callback=lambda path: opts.parse_config_file(path, final=False))
    opts.define("logging", default="info",
                help=("Set the Python log level. If 'none', tornado won't touch the "
                      "logging configuration."),
                metavar="debug|info|warning|error|none")
    opts.define("log_to_stderr", type=bool, default=None,
                help=("Send log output to stderr. "
                      "By default use stderr if --log_file_prefix is not set and "
                      "no other logging is configured."))
    opts.define("log_file_prefix", type=str, default=None, metavar="PATH",
                help=("Path prefix for log file"))
    opts.parse_command_line()
    enable_logging(opts)

    proxy = CocaineProxy(port=opts.port,
                         cache=opts.cache)
    proxy.run(opts.count)


if __name__ == '__main__':
    main()
