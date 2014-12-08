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
import re
import random
import time

import msgpack
import tornado
from tornado import gen
from tornado import httputil
from tornado.httpserver import HTTPServer
from tornado import process

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
DEFAULT_TIMEOUT = 1

# active applications
cache = collections.defaultdict(list)
# application in reconnecting state
dying = collections.defaultdict(list)


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
        self.refreshPeriod = config.get("refresh_timeout") or DEFAULT_REFRESH_PERIOD
        self.timeouts = config.get("timeouts", {})

        self.logger = logging.getLogger('cocaine.proxy')

    def get_timeout(self, name):
        return self.timeouts.get(name, DEFAULT_TIMEOUT)

    def move_to_inactive(self, app, name):
        def wrapper():
            active_apps = len(cache[name])
            if active_apps < self.serviceCacheCount:
                self.io_loop.add_timeout(time.time() + self.get_timeout(name) + 1,
                                         self.move_to_inactive(app, name))
                return
            self.logger.info(MOVE_TO_INACTIVE, app.name, "{0}:{1}".format(*app.address), active_apps)
            # Move service to sandbox for waiting current sessions
            try:
                inx = cache[name].index(app)
                # To avoid gc collect
                dying[name].append(cache[name].pop(inx))
            except ValueError:
                self.logger.error("Broken cache")
                return

            self.io_loop.add_timeout(time.time() + self.get_timeout(name) + 1,
                                     functools.partial(self.async_reconnect, app, name))
        return wrapper

    @gen.coroutine
    def async_reconnect(self, app, name):
        try:
            self.logger.info(RECONNECTION_START, app.name)
            app.disconnect()
            yield app.connect()
            self.logger.info(RECONNECTION_SUCCESS, app.name,
                             id(app), "{0}:{1}".format(*app.address))
        except Exception as err:
            self.logger.exception(RECONNECTION_FAIL, name, err)
        finally:
            dying[name].remove(app)
            cache[name].append(app)
            next_refresh = (1 + random.random()) * self.refreshPeriod
            self.logger.info(NEXT_REFRESH, id(app), next_refresh)
            self.io_loop.add_timeout(time.time() + next_refresh, self.move_to_inactive(app, name))

    @gen.coroutine
    def handle_request(self, request):
        if "X-Cocaine-Service" in request.headers and "X-Cocaine-Event" in request.headers:
            self.logger.debug('Dispatch by headers')
            name = request.headers['X-Cocaine-Service']
            event = request.headers['X-Cocaine-Event']
        else:
            self.logger.debug('Dispatch by uri')
            match = URL_REGEX.match(request.uri)
            if match is None:
                fill_response_in(request, httplib.NOT_FOUND, "Not found", "Invalid url")
                return

            name, event, other = match.groups()
            if name == '' or event == '':
                message = "Invalid request"
                request.write("%s 404 Not found\r\nContent-Length: %d\r\n\r\n%s" % (
                    request.version, len(message), message))
                request.finish()
                return

            # Drop from query appname and event's name
            if not other.startswith('/'):
                other = "/%s" % other
            request.uri = other
            request.path = other.partition("?")[0]

        app = yield self.get_service(name)
        if app is None:
            message = "Current application %s is unavailable" % name
            fill_response_in(request, 404, "Not found", message)
            return

        self.logger.debug("Processing request.... %s %s", app, event)
        try:
            yield self.process(request, app, event, pack_httprequest(request))
        except Exception as err:
            self.logger.error("Error during processing request %s", err)
            fill_response_in(request, 502, "Server error", str(err))

    @gen.coroutine
    def process(self, request, service, event, data):
        code = 502
        headers = {}
        body_parts = []
        try:
            channel = yield service.enqueue(event)
            yield channel.tx.write(msgpack.packb(data))
            code_and_headers = yield channel.rx.get()
            code, raw_headers = msgpack.unpackb(code_and_headers)
            headers = tornado.httputil.HTTPHeaders(raw_headers)
            while True:
                body = yield channel.rx.get()
                if not isinstance(body, EmptyResponse):
                    body_parts.append(msgpack.unpackb(body))
                else:
                    break
        except ServiceError as err:
            self.logger.error(str(err))
            message = "Application error: %s" % str(err)
            code = 502
            fill_response_in(request, code, httplib.responses[code], message)
        except Exception as err:
            self.logger.error("Error %s", err)
            message = "Unknown error: %s" % str(err)
            code = 502
            fill_response_in(request, code, httplib.responses[code], message)
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
        # so create more instances
        if len(cache[name]) < self.spoolSize - len(dying[name]):
            self.logger.info("create more instances of %s", name)
            try:
                created = []
                for _ in xrange(self.spoolSize - len(cache[name])):
                    app = Service(name)
                    yield app.connect()
                    created.append(app)
                    self.logger.info("Connect to app: %s endpoint %s ", app.name, "{0}:{1}".format(*app.address))

                cache[name].extend(created)
                for app in created:
                    timeout = (1 + random.random()) * self.refreshPeriod
                    self.io_loop.add_timeout(time.time() + timeout, self.move_to_inactive(app, name))
            except Exception as err:
                self.logger.error("unable to connect to `%s`: %s", name, str(err))
                raise gen.Return()

        # get instance from cache
        chosen = random.choice(cache[name])
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
            self.logger.info('Received shutdown signal')
        except Exception as err:
            self.logger.error(err)
        finally:
            self._io_loop.stop()


def main():
    from tornado.options import define, options, parse_command_line

    define("port", default=8080, type=int, help="listening port number")
    define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
           type=int, help="count of instances per service")
    define("count", default=1,
           type=int, help="count of tornado processes")

    parse_command_line()
    CocaineProxy(port=options.port).run(options.count)

if __name__ == '__main__':
    main()
