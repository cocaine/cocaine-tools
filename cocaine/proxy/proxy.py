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


RECONNECTION_START = "Start asynchronous reconnection %s"
RECONNECTION_SUCCESS = "reconnection %s %d to %s successfully."
RECONNECTION_FAIL = "Unable to reconnect %s, because %s"
NEXT_REFRESH = "Next update %d after %d second"
MOVE_TO_INACTIVE = "Move to inactive queue %s %s from pool with active %d"


URL_REGEX = re.compile(r"/([^/]*)/([^/?]*)(.*)")

DEFAULT_SERVICE_CACHE_COUNT = 5
DEFAULT_REFRESH_PERIOD = 120
DEFAULT_TIMEOUT = 5


def pack_httprequest(request):
    headers = [(item.key, item.value) for item in request.cookies.itervalues()]
    headers.extend(request.headers.items())
    d = request.method, request.uri, request.version.split("/")[1], headers, request.body
    return d


def fill_response_in(request, code, status, message):
    request.connection.write_headers(
        # start_line
        httputil.ResponseStartLine(request.version, code, status),
        # headers
        httputil.HTTPHeaders({"Content-Length": str(len(message))}),
        # data
        message)
    request.connection.finish()


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

    def get_timeout(self, name):
        return self.timeouts.get(name, DEFAULT_TIMEOUT)

    def move_to_inactive(self, app, name):
        def wrapper():
            active_apps = len(self.cache[name])
            if active_apps < self.serviceCacheCount:
                self.io_loop.call_later(self.get_timeout(name), self.move_to_inactive(app, name))
                return

            self.logger.info(MOVE_TO_INACTIVE, app.name, "{0}:{1}".format(*app.address), active_apps)
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
    def handle_request(self, request):
        if "X-Cocaine-Service" in request.headers and "X-Cocaine-Event" in request.headers:
            self.logger.debug('dispatch by headers')
            name = request.headers['X-Cocaine-Service']
            event = request.headers['X-Cocaine-Event']
        else:
            self.logger.debug('dispatch by uri')
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
                    request.connection.write_headers(
                        httputil.ResponseStartLine(request.version, httplib.OK, 'OK'),
                        httputil.HTTPHeaders({
                            'Content-Length': str(len(body))
                        }),
                        body
                    )
                    request.connection.finish()
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

        app = yield self.get_service(name)
        if app is None:
            message = "Current application %s is unavailable" % name
            fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND], message)
            return

        self.logger.debug("Processing request.... %s %s", app, event)
        try:
            yield self.process(request, name, app, event, pack_httprequest(request))
        except Exception as err:
            self.logger.error("Error during processing request %s", err)
            fill_response_in(request, 502, "Server error", str(err))

    @gen.coroutine
    def process(self, request, name, service, event, data):
        headers = {}
        body_parts = []
        timeout = self.get_timeout(name)
        try:
            channel = yield service.enqueue(event)
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
            self.logger.error(str(err))
            message = "Application `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.GATEWAY_TIMEOUT,
                             httplib.responses[httplib.GATEWAY_TIMEOUT], message)

        except ServiceError as err:
            self.logger.error(str(err))
            message = "Application `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)

        except Exception as err:
            self.logger.error("Error %s", err)
            message = "Unknown `%s` error: %s" % (name, str(err))
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR], message)
        else:
            message = ''.join(body_parts)
            request.connection.write_headers(
                httputil.ResponseStartLine(request.version,
                                           code,
                                           httplib.responses.get(code, 200)),
                headers, message)
            request.connection.finish()

    @gen.coroutine
    def get_service(self, name):
        # cache isn't full for the current application
        if len(self.cache[name]) < self.spoolSize:
            self.logger.info("create one more instance of %s", name)
            try:
                app = Service(name)
                self.cache[name].append(app)
                yield app.connect()
                self.logger.info("Connect to app: %s endpoint %s ", app.name, "{0}:{1}".format(*app.address))

                timeout = (1 + random.random()) * self.refreshPeriod
                self.io_loop.call_later(timeout, self.move_to_inactive(app, name))
            except Exception as err:
                self.logger.error("unable to connect to `%s`: %s", name, str(err))
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


def main():
    from tornado.options import define, options, parse_command_line, parse_config_file

    define("port", default=8080, type=int, help="listening port number")
    define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
           type=int, help="count of instances per service")
    define("count", default=1, type=int, help="count of tornado processes")
    define("config", help="path to configuration file", type=str,
           callback=lambda path: parse_config_file(path, final=False))
    parse_command_line()

    proxy = CocaineProxy(port=options.port,
                         cache=options.cache)
    proxy.run(options.count)


if __name__ == '__main__':
    main()
