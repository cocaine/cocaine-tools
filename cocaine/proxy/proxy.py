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
import socket
import sys
import time

import msgpack
import tornado
from tornado import gen
from tornado import httputil
from tornado import process
from tornado.httpserver import HTTPServer
from tornado.iostream import StreamClosedError
from tornado.netutil import bind_sockets, bind_unix_socket
from tornado.util import import_object

try:
    from cocaine.logger import LoggerWithExtraInRecord
    logging.setLoggerClass(LoggerWithExtraInRecord)
except ImportError:
    print("current version of python framework does not provide LoggerWithExtraInRecord")

from cocaine.logger import CocaineHandler
from cocaine.logger import Logger
from cocaine.services import Service
from cocaine.services import Locator
from cocaine.exceptions import ServiceError
from cocaine.exceptions import DisconnectionError
from cocaine.services import EmptyResponse
from cocaine.detail.trace import Trace

from cocaine.proxy.helpers import Endpoints
from cocaine.proxy.helpers import extract_app_and_event
from cocaine.proxy.helpers import fill_response_in
from cocaine.proxy.helpers import write_chunk
from cocaine.proxy.helpers import finalize_chunked_response
from cocaine.proxy.helpers import header_to_seed
from cocaine.proxy.helpers import load_srw_config
from cocaine.proxy.helpers import pack_httprequest
from cocaine.proxy.helpers import parse_locators_endpoints
from cocaine.proxy.helpers import ProxyInvalidRequest
from cocaine.proxy.helpers import upper_bound
from cocaine.proxy.logutils import ContextAdapter
from cocaine.proxy.logutils import NULLLOGGER
from cocaine.proxy.plugin import IPlugin
from cocaine.proxy.plugin import PluginNoSuchApplication
from cocaine.proxy.plugin import PluginApplicationError
from cocaine.proxy.utilserver import UtilServer


try:
    from cocaine.tools.version import __version__ as tools_version
except ImportError:
    tools_version = "<undefinded>"


DEFAULT_SERVICE_CACHE_COUNT = 5
DEFAULT_REFRESH_PERIOD = 120
DEFAULT_TIMEOUT = 30
DEFAULT_TRACING_CHANCE = 5.  # %

_DEFAULT_BACKLOG = 128

# sec Time to wait for the response chunk from locator
RESOLVE_TIMEOUT = 5

# cocaine system category, I hope it will never change
SYSTEMCATEGORY = (0xff, 0xc)
EAPPSTOPPED = errno.EPIPE

LOCATORCATEGORY = (0xff, 0xa)
ESERVICENOTAVAILABLE = 1

OVERSEERCATEGORY = (0xff, 0x52ff)
EQUEUEISFULL = 1

# no such application
# we are mature enough to have our own status code
# but nginx proxy_next_upstream does NOT support custom codes
NO_SUCH_APP = httplib.SERVICE_UNAVAILABLE

X_COCAINE_HTTP_PROTO_VERSION = "X-Cocaine-HTTP-Proto-Version"

class BodyProcessor(object):

    def __init__(self, request, name, code, headers, init=''):
        self.request = request
        self.name = name
        self.code = code
        self.headers = headers
        self.message = init

    def __call__(self, part):
        pass

    def finish(self):
        pass

class ChunkedBodyProcessor(BodyProcessor):

    def __init__(self, request, name, code, headers, init=''):
        super(ChunkedBodyProcessor, self).__init__(
            request, name, code, headers, init)

        self.headers['X-Cocaine-Application'] = self.name
        self.headers['Transfer-Encoding'] = 'chunked'
        fill_response_in(self.request, self.code,
                         httplib.responses.get(self.code, httplib.OK),
                         self.message, self.headers)

    def __call__(self, part):
        write_chunk(self.request, part)

    def finish(self):
        finalize_chunked_response(self.request, self.code)

class CachedBodyProcessor(BodyProcessor):

    def __call__(self, part):
        self.message += part

    def finish(self):
        self.headers['X-Cocaine-Application'] = self.name
        fill_response_in(self.request, self.code,
                         httplib.responses.get(self.code, httplib.OK),
                         self.message, self.headers)

def proxy_error_headers(name=None):
    headers = {}
    if name is not None:
        headers['X-Cocaine-Application'] = name
    headers["X-Error-Generated-By"] = "Cocaine-Tornado-Proxy"
    return httputil.HTTPHeaders(headers)


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
                request.logger = NULLLOGGER  # pylint: disable=R0204
            request.traceid = traceid
            request.tracebit = True
            request.logger.info("start request: %s %s %s", request.host, request.remote_ip, request.uri)
            yield func(self, request)
        finally:
            self.requests_in_progress -= 1
    return wrapper


def gen_uid():
    return "proxy:%s_%d_%f" % (socket.gethostname(), os.getpid(), time.time())


def scan_for_updates(current, new):
    # add removed groups and new groups to updated
    # mark routing group as updated if its current ring is not
    # the same as new
    updated = [k for k, v in new.iteritems() if v != current.pop(k, None)]
    updated.extend(current.keys())
    return updated


def drop_app_from_cache(cache, app, name):
    # cache is defaultdict(list), so we can NOT rely on KeyError
    apps = cache.get(name)
    if apps is not None and app in apps:
        # remove app from cache
        apps.remove(app)
        # if there is no such apps in cache - remove key from dict
        # to avoid memory leak
        if len(apps) == 0:
            cache.pop(name)


def load_plugin(name, proxy, config):
    klass = import_object(name)
    if not issubclass(klass, IPlugin):
        raise Exception("%s is not a subclass of %s" % (klass.__name__, IPlugin.__name__))
    return klass(proxy, config)


class CocaineProxy(object):
    def __init__(self, locators=("localhost:10053",),
                 cache=DEFAULT_SERVICE_CACHE_COUNT,
                 request_id_header="", sticky_header="X-Cocaine-Sticky",
                 forcegen_request_header=False,
                 default_tracing_chance=DEFAULT_TRACING_CHANCE,
                 configuration_service="unicorn",
                 tracing_conf_path="/zipkin_sampling",
                 timeouts_conf_path="/proxy_apps_timeouts",
                 srw_config=None,
                 allow_json_rpc=True,
                 ioloop=None, **config):
        # stats
        self.requests_in_progress = 0
        self.requests_disconnections = 0
        self.requests_total = 0

        self.io_loop = ioloop or tornado.ioloop.IOLoop.current()
        self.service_cache_count = cache
        self.spool_size = int(self.service_cache_count * 1.5)
        self.refresh_period = config.get("refresh_timeout", DEFAULT_REFRESH_PERIOD)
        self.locator_endpoints = [parse_locators_endpoints(i) for i in locators]
        # it's initialized after start
        # to avoid an io_loop creation before fork
        self.locator = Locator(endpoints=self.locator_endpoints)
        # it's used to reply on `ping` method
        self.locator_status = False

        # active applications
        self.cache = collections.defaultdict(list)
        # routing groups from Locator service
        self.current_rg = {}

        self.logger = logging.getLogger("cocaine.proxy.general")
        self.access_log = logging.getLogger("cocaine.proxy.access")
        self.access_log.propagate = False
        self.logger.info("locators %s",
                         ','.join("%s:%d" % (h, p) for h, p in self.locator_endpoints))

        self.sticky_header = sticky_header

        self.plugins = []
        if srw_config:
            for config in srw_config:
                name, cfg = config["type"], config["args"]
                self.logger.info("initialize plugin %s", name)
                self.plugins.append(load_plugin(name, self, cfg))

        if allow_json_rpc:
            self.plugins.append(load_plugin('cocaine.proxy.jsonrpc.JSONRPC', self, {}))

        self.logger.info("conf path in `%s` configuration service: %s",
                         configuration_service, tracing_conf_path)
        self.unicorn = Service(configuration_service, locator=self.locator)
        self.sampled_apps = {}
        self.default_tracing_chance = default_tracing_chance
        self.tracing_conf_path = tracing_conf_path

        self.io_loop.add_future(self.on_sampling_updates(),
                                lambda x: self.logger.error("the sample updater must not exit"))

        self.timeouts_conf_path = timeouts_conf_path
        self.timeouts = {}
        self.io_loop.add_future(self.on_timeouts_updates(),
                                lambda x: self.logger.error("the timeouts updater must not exit"))

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
        self.logger.info("generate new unique id %s", uid)
        maximum_timeout = 32  # sec
        timeout = 1  # sec
        while True:
            self.current_rg = {}
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
                    updates = scan_for_updates(self.current_rg, new)
                    # replace current
                    self.current_rg = new
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
                            self.logger.debug("%s: move %s to the inactive queue to refresh"
                                              " routing group", app.id, app.name)
                            self.migrate_from_cache_to_inactive(app, group)
            except Exception as err:
                timeout = min(timeout << 1, maximum_timeout)
                self.logger.error("error occurred while watching for group updates %s. Sleep %d",
                                  err, timeout)
                yield gen.sleep(timeout)

    @gen.coroutine
    def watch_app(self, name, path):
        version = 0
        self.sampled_apps[name] = self.default_tracing_chance
        try:
            self.logger.info("start watching for sampling updates of %s", name)
            watch_channel = yield self.unicorn.subscribe(path, version)
            while True:
                value, version = yield watch_channel.rx.get()
                self.logger.info("got sampling updates for %s: version %d value %.2f", name, version, value)
                try:
                    weight = float(value)
                    self.sampled_apps[name] = weight
                except ValueError as err:
                    self.logger.error("sample value %s for %s can NOT be converted: %s. Use %f",
                                      value, name, err, self.default_tracing_chance)
                    self.sampled_apps[name] = self.default_tracing_chance
        except ServiceError as err:
            # verify that the err is `zookeeper: no node [-101]``
            if err.code != -101:
                self.logger.error("watching of `%s` raised an unexpected service error (cat. %d): %s", name, err.category, err)
        except Exception as err:
            self.logger.error("watching of %s error: %s", name, err)
        finally:
            self.logger.info("stop watching for sampling updates of %s", name)
            self.sampled_apps.pop(name, None)
            try:
                watch_channel.tx.close()
            except Exception:
                pass

    @gen.coroutine
    def on_sampling_updates(self):
        maximum_timeout = 32  # sec
        timeout = 1  # sec
        listing_version = 0

        while True:
            try:
                listing_channel = yield self.unicorn.children_subscribe(self.tracing_conf_path, listing_version)
                while True:
                    listing_version, apps = yield listing_channel.rx.get()
                    self.logger.info("on_sampling_updates: version %d value %s", listing_version, apps)
                    for app in (i for i in apps if i not in self.sampled_apps):
                        self.watch_app(app, self.tracing_conf_path + "/" + app)
            except Exception as err:
                timeout = min(timeout << 1, maximum_timeout)
                listing_version = 0
                self.logger.error("error occurred while subscribing for sampling updates %s. Sleep %d",
                                  err, timeout)
                yield gen.sleep(timeout)

    @gen.coroutine
    def watch_app_timeouts(self, name, path):
        version = 0
        self.timeouts[name] = {}
        try:
            self.logger.info("start watching for timeouts updates of %s", name)
            watch_channel = yield self.unicorn.subscribe(path, version)
            while True:
                value, version = yield watch_channel.rx.get()
                self.logger.info("got timeouts updates for %s: version %d value %s", name, version, value)
                if isinstance(value, dict):
                    self.timeouts[name] = value
                else:
                    self.logger.error("timeout value %s for %s is not dict", value, name)
                    self.timeouts[name] = {}
        except ServiceError as err:
            # verify that the err is `zookeeper: no node [-101]``
            if err.code != -101:
                self.logger.error("watching of `%s` raised an unexpected service error (cat. %d): %s", name, err.category, err)
        except Exception as err:
            self.logger.error("watching of %s error: %s", name, err)
        finally:
            self.logger.info("stop watching for timeouts updates of %s", name)
            self.timeouts.pop(name, None)
            try:
                watch_channel.tx.close()
            except Exception:
                pass

    @gen.coroutine
    def on_timeouts_updates(self):
        maximum_timeout = 32  # sec
        timeout = 1  # sec
        listing_version = 0

        while True:
            try:
                listing_channel = yield self.unicorn.children_subscribe(self.timeouts_conf_path, listing_version)
                while True:
                    listing_version, apps = yield listing_channel.rx.get()
                    self.logger.info("on_timeouts_updates: version %d value %s", listing_version, apps)
                    for app in (i for i in apps if i not in self.timeouts):
                        self.watch_app_timeouts(app, self.timeouts_conf_path + "/" + app)
            except Exception as err:
                timeout = min(timeout << 1, maximum_timeout)
                listing_version = 0
                self.logger.error("error occurred while subscribing for sampling updates %s. Sleep %d",
                                  err, timeout)
                yield gen.sleep(timeout)

    def get_timeout(self, name, event=''):
        if name in self.timeouts:
            tmts = self.timeouts[name]
            return tmts.get(event) or tmts.get('', DEFAULT_TIMEOUT)

        return DEFAULT_TIMEOUT

    def migrate_from_cache_to_inactive(self, app, name):
        try:
            drop_app_from_cache(self.cache, app, name)
        except Exception as err:
            self.logger.error("app %s %s: drop cache error %s", app, name, err)

        # dispose service after 3 x timeouts
        # assume that all requests will be finished
        self.io_loop.call_later(self.get_timeout(name) * 3,
                                functools.partial(self.dispose, app, name))
        self.logger.info("app %s %s is scheduled to dispose", app, name)

    def move_to_inactive(self, app, name):
        @gen.coroutine
        def wrapper():
            active_apps = len(self.cache[name])
            self.logger.info("%s: preparing to moving %s %s to an inactive queue (active %d)",
                             app.id, app.name, "{0}:{1}".format(*app.address), active_apps)

            try:
                new_app = Service(name, locator=self.locator, timeout=RESOLVE_TIMEOUT)
                self.logger.info("%s: creating an instance of %s", new_app.id, name)
                yield new_app.connect()
                self.logger.info("%s: connect to an app %s endpoint %s ",
                                 new_app.id, new_app.name, "{0}:{1}".format(*new_app.address))
                timeout = (1 + random.random()) * self.refresh_period
                self.io_loop.call_later(timeout, self.move_to_inactive(new_app, name))
                # add to cache only after successfully connected
                self.cache[name].append(new_app)
            except Exception as err:
                self.logger.error("%s: unable to connect to `%s`: %s", new_app.id, name, err)
                # schedule later
                self.io_loop.call_later(self.get_timeout(name), self.move_to_inactive(app, name))
            else:
                self.logger.info("%s: move %s %s to an inactive queue",
                                 app.id, app.name, "{0}:{1}".format(*app.address))
                # current active app will be dropped here
                self.migrate_from_cache_to_inactive(app, name)

        return wrapper

    def dispose(self, app, name):
        self.logger.info("dispose service %s %s", name, app.id)
        app.disconnect()

    def resolve_group_to_version(self, name, value=None):
        """ Pick a version from a routing group using a random or provided value
            A routing group looks like (weight, version):
            {"APP": [[29431330, 'A'], [82426238, 'B'], [101760716, 'C'], [118725487, 'D'], [122951927, 'E']]}
        """
        if name not in self.current_rg:
            return name

        routing_group = self.current_rg[name]
        if len(routing_group) == 0:
            self.logger.warning("empty rounting group %s", name)
            return name

        value = value or random.randint(0, 1 << 32)
        index = upper_bound(routing_group, value)
        return routing_group[index if index < len(routing_group) else 0][1]

    def ping(self, request):
        if self.locator_status:
            fill_response_in(request, httplib.OK, "OK", "OK")
            return

        fill_response_in(request, httplib.SERVICE_UNAVAILABLE,
                         httplib.responses[httplib.SERVICE_UNAVAILABLE],
                         "Failed", proxy_error_headers())

    @context
    @gen.coroutine
    def __call__(self, request):
        for plugin in self.plugins:
            if plugin.match(request):
                request.logger.info('processed by %s plugin', plugin.name())
                try:
                    yield plugin.process(request)
                except PluginNoSuchApplication as err:
                    fill_response_in(request, NO_SUCH_APP, "No such application",
                                     str(err), proxy_error_headers())
                except PluginApplicationError:
                    message = "application error"
                    fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                     httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                     message, proxy_error_headers())
                except ProxyInvalidRequest:
                    if request.path == "/ping":
                        self.ping(request)
                    else:
                        fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND],
                                         "Invalid url", proxy_error_headers())
                except Exception as err:
                    request.logger.exception('plugin %s returned error: %s', plugin.name(), err)
                    message = "unknown error"
                    fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                                     httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                                     message, proxy_error_headers())
                return

        try:
            name, event = extract_app_and_event(request)
        except ProxyInvalidRequest:
            if request.path == "/ping":
                self.ping(request)
            else:
                fill_response_in(request, httplib.NOT_FOUND, httplib.responses[httplib.NOT_FOUND],
                                 "Invalid url", proxy_error_headers())
            return

        if getattr(request, "traceid", None) is not None:
            tracing_chance = self.sampled_apps.get(name, self.default_tracing_chance)
            rolled_dice = random.uniform(0, 100)
            request.logger.debug("tracing_chance %f, rolled dice %f", tracing_chance, rolled_dice)
            if tracing_chance < rolled_dice:
                request.logger.info('stop tracing the request')
                request.logger = NULLLOGGER
                request.tracebit = False

        if self.sticky_header in request.headers:
            seed = request.headers.get(self.sticky_header)
            seed_value = header_to_seed(seed)
            request.logger.info('sticky_header has been found: name %s, value %s, seed %d', name, seed, seed_value)
            name = self.resolve_group_to_version(name, seed_value)

        app = yield self.get_service(name, request)

        if app is None:
            message = "current application %s is unavailable" % name
            fill_response_in(request, NO_SUCH_APP, "No Such Application",
                             message, proxy_error_headers(name))
            return

        try:
            # TODO: attempts should be configurable
            yield self.process(request, name, app, event, pack_httprequest(request), self.reelect_app, 2)
        except Exception as err:
            request.logger.exception("error during processing request %s", err)
            fill_response_in(request, httplib.INTERNAL_SERVER_ERROR,
                             httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                             "UID %s: %s" % (request.traceid, str(err)), proxy_error_headers(name))

        request.logger.info("exit from process")

    def info(self):
        return {'services': {'cache': dict(((k, len(v)) for k, v in self.cache.items()))},
                'requests': {'inprogress': self.requests_in_progress,
                             'total': self.requests_total},
                'errors': {'disconnections': self.requests_disconnections},
                'sampling': self.sampled_apps}

    @gen.coroutine
    def reelect_app(self, request, app):
        cache_size = len(self.cache[app.name])
        if cache_size < self.spool_size:
            request.logger.info("spool is not full. Create a new application instance")
            app = yield self.get_service(app.name, request)
        elif cache_size == 1:
            # NOTE: if we have spool_size 1, the same app will be picked
            # Probably we can create a new one and mark the old one inactive
            request.logger.warning("spool size is limited by 1, cannot pick a new instance of th app. Use the old one")
            # pass
        else:
            request.logger.info("pick a random instance of the application")
            try:
                index = self.cache[app.name].index(app)
                request.logger.info("the app is located in cache at pos %d", index)
                if cache_size == 2:  # shortcut
                    picked = (index + 1) % 2
                else:
                    picked = index
                    while picked == index:
                        picked = random.randint(0, cache_size - 1)

                request.logger.info("an instance at pos %d has been picked", index)
                app = self.cache[app.name][picked]
            except ValueError:
                app = random.choice(self.cache[app.name])
        raise gen.Return(app)

    @gen.coroutine
    def process(self, request, name, app, event, data, reelect_app_fn, attempts, timeout=None):
        if timeout is None:
            timeout = self.get_timeout(name, event)
        request.logger.info("start processing event `%s` for an app `%s` (appid: %s) after %.3f ms with timeout %f",
                            event, app.name, app.id, request.request_time() * 1000, timeout)
        parentid = 0

        if request.traceid is not None:
            traceid = int(request.traceid, 16)
            trace = Trace(traceid=traceid, spanid=traceid, parentid=parentid)
        else:
            trace = None

        headers = {
            'trace_bit': '{:d}'.format(request.tracebit),
        }
        if 'authorization' in request.headers:
            headers['authorization'] = request.headers['authorization']

        def on_error(app, err, extra_msg, code=httplib.INTERNAL_SERVER_ERROR):
            if len(extra_msg) > 0 and not extra_msg.endswith(' '):
                extra_msg += ' '
            request.logger.error("%s: %s%s", app.id, extra_msg, err)

            message = "UID %s: application `%s` error: %s" % (request.traceid, app.name, str(err))
            fill_response_in(request, code, httplib.responses[code], message, proxy_error_headers(app.name))

        def check_attempts(app, err):
            if attempts > 0:
                return True
            # we have no attempts more, so quit here
            on_error(app, err, '(no attempts left) ')
            return False

        while attempts > 0:
            body_parts = []
            attempts -= 1
            processor = None
            try:
                request.logger.debug("%s: enqueue event (attempt %d)", app.id, attempts)
                channel = yield app.enqueue(event, trace=trace, **headers)
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

                cocaine_http_proto_version = headers.get(X_COCAINE_HTTP_PROTO_VERSION)
                if cocaine_http_proto_version is None or cocaine_http_proto_version == "1.0":
                    cocaine_http_proto_version = "1.0"

                    def stop_condition(body):
                        return isinstance(body, EmptyResponse)
                elif cocaine_http_proto_version == "1.1":
                    def stop_condition(body):
                        return isinstance(body, EmptyResponse) or len(body) == 0
                else:
                    raise Exception("unsupported X-Cocaine-HTTP-Proto-Version: %s" % cocaine_http_proto_version)

                if headers.get('Content-Length'):
                    processor = CachedBodyProcessor(request, name, code, headers)
                else:
                    processor = ChunkedBodyProcessor(request, name, code, headers)

                while True:
                    body = yield channel.rx.get(timeout=timeout)
                    if stop_condition(body):
                        request.logger.info("%s: body finished (attempt %d)", app.id, attempts)
                        break

                    request.logger.debug("%s: received %d bytes as a body chunk (attempt %d)",
                                         app.id, len(body), attempts)
                    processor(body)

            except gen.TimeoutError as err:
                on_error(app, err, '', httplib.GATEWAY_TIMEOUT)

            except (DisconnectionError, StreamClosedError) as err:
                self.requests_disconnections += 1
                # Probably it's dangerous to retry requests all the time.
                # I must find the way to determine whether it failed during writing
                # or reading a reply. And retry only writing fails.
                request.logger.error("%s: %s", app.id, err)
                if not check_attempts(app, err):
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
                    request.logger.error("%s: unable to reconnect: %s (%d attempts left)", err, attempts)
                # We have an attempt to process request again.
                # Jump to the begining of `while attempts > 0`, either we connected successfully
                # or we were failed to connect
                continue

            except ServiceError as err:
                if not check_attempts(app, err):
                    return

                # if the application has been restarted, we get broken pipe code
                # and system category
                if err.category in SYSTEMCATEGORY and err.code == EAPPSTOPPED:
                    request.logger.error("%s: the application has been restarted", app.id)
                    app.disconnect()
                    continue

                elif err.category in OVERSEERCATEGORY and err.code == EQUEUEISFULL:
                    request.logger.error("%s: queue is full. Pick another application instance", app.id)
                    try:
                        app = yield reelect_app_fn(request, app)
                    except Exception as reelect_err:
                        on_error(app, reelect_err, '(could not reelect app)')
                        return
                    request.logger.info("fetched new app from reelect_app_fn")
                    continue

                on_error(app, err, '')

            except Exception as err:
                on_error(app, err, '(unknown error) ')

            else:
                if processor:
                    processor.finish()

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
                drop_app_from_cache(self.cache, app, name)
                raise gen.Return()
            else:
                raise gen.Return(app)

        # get an instance from cache
        chosen = random.choice(self.cache[name])
        raise gen.Return(chosen)


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

    if options.log_to_cocaine:
        Logger().target = "tornado-proxy"
        handler = CocaineHandler()
        general_logger.addHandler(handler)
        if cocainelogger:
            cocainelogger.addHandler(handler)

        access_logger.addHandler(handler)

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
        access_logger.addHandler(stderr_handler)


def enable_gc_stats():
    import gc
    gc.set_debug(gc.DEBUG_STATS)


def show_version(dummy):
    print("cocaine tools & proxy: %s" % tools_version)
    sys.exit(0)


def main():
    from tornado import options

    default_general_logformat = "[%(asctime)s.%(msecs)d]\t[%(filename).5s:%(lineno)d]\t%(levelname)s\t%(message)s"
    default_access_logformat = "[%(asctime)s.%(msecs)d]\t[%(filename).5s:%(lineno)d]\t%(levelname)s\t%(trace_id)s\t%(message)s"

    opts = options.OptionParser()
    opts.define("version", type=bool, help="show version and exit", callback=show_version)
    opts.define("locators", default=["localhost:10053"],
                type=str, multiple=True, help="comma-separated endpoints of locators")
    opts.define("cache", default=DEFAULT_SERVICE_CACHE_COUNT,
                type=int, help="count of instances per service")
    opts.define("config", help="path to configuration file", type=str,
                callback=lambda path: opts.parse_config_file(path, final=False))
    opts.define("count", default=1, type=int, help="count of tornado processes")
    opts.define("endpoints", default=["tcp://localhost:8080"], type=str, multiple=True,
                help="Specify endpoints to bind on: prefix unix:// or tcp:// should be used")
    opts.define("request_header", default="X-Request-Id", type=str,
                help="header used as a trace id")
    opts.define("forcegen_request_header", default=False, type=bool,
                help="enable force generation of the request header")
    opts.define("sticky_header", default="X-Cocaine-Sticky", type=str, help="sticky header name")
    opts.define("gcstats", default=False, type=bool, help="print garbage collector stats to stderr")
    opts.define("srwconfig", default="", type=str, help="path to srwconfig")
    opts.define("allow_json_rpc", default=True, type=bool, help="allow JSON RPC module")

    # tracing options
    opts.define("tracing_chance", default=DEFAULT_TRACING_CHANCE,
                type=float, help="default chance for an app to be traced")
    opts.define("configuration_service", default="unicorn",
                type=str, help="name of configuration service")
    opts.define("tracing_conf_path", default="/zipkin_sampling",
                type=str, help="path to the configuration nodes in the configuration service")

    # various logging options
    opts.define("logging", default="info",
                help=("Set the Python log level. If 'none', tornado won't touch the "
                      "logging configuration."), metavar="debug|info|warning|error|none")
    opts.define("log_to_cocaine", default=False, type=bool, help="log to cocaine")
    opts.define("log_to_stderr", type=bool, default=None,
                help=("Send log output to stderr. "
                      "By default use stderr if --log_file_prefix is not set and "
                      "no other logging is configured."))
    opts.define("log_file_prefix", type=str, default=None, metavar="PATH",
                help=("Path prefix for log file"))
    opts.define("datefmt", type=str, default="%z %d/%b/%Y:%H:%M:%S", help="datefmt")
    opts.define("generallogfmt", type=str, help="log format of general logging system",
                default=default_general_logformat)
    opts.define("accesslogfmt", type=str, help="log format of access logging system",
                default=default_access_logformat)
    opts.define("logframework", type=bool, default=False,
                help="enable logging various framework messages")

    # util server
    opts.define("utilport", default=8081, type=int, help="listening port number for an util server")
    opts.define("utiladdress", default="127.0.0.1", type=str, help="address for an util server")
    opts.define("enableutil", default=False, type=bool, help="enable util server")
    opts.parse_command_line()

    srw_config = None
    if opts.srwconfig:
        try:
            srw_config = load_srw_config(opts.srwconfig)
        except Exception as err:
            print("unable to load SRW config: %s" % err)
            exit(1)

    use_reuseport = hasattr(socket, "SO_REUSEPORT")
    endpoints = Endpoints(opts.endpoints)
    sockets = []

    for path in endpoints.unix:
        sockets.append(bind_unix_socket(path, mode=0o666))

    if not use_reuseport:
        for endpoint in endpoints.tcp:
            # We have to bind before fork to distribute sockets to our forks
            socks = bind_sockets(endpoint.port, address=endpoint.host)
            sockets.extend(socks)

    if opts.enableutil:
        utilsockets = bind_sockets(opts.utilport, address=opts.utiladdress)

    try:
        if opts.count != 1:
            process.fork_processes(opts.count)

        enable_logging(opts)

        if opts.gcstats:
            enable_gc_stats()

        if use_reuseport:
            for endpoint in endpoints.tcp:
                # We have to bind before fork to distribute sockets to our forks
                socks = bind_sockets(endpoint.port, address=endpoint.host, reuse_port=True)
                sockets.extend(socks)

        proxy = CocaineProxy(locators=opts.locators, cache=opts.cache,
                             request_id_header=opts.request_header,
                             sticky_header=opts.sticky_header,
                             forcegen_request_header=opts.forcegen_request_header,
                             default_tracing_chance=opts.tracing_chance,
                             srw_config=srw_config,
                             allow_json_rpc=opts.allow_json_rpc)
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
