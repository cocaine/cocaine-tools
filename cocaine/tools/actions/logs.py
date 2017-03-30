#
# Copyright (c) 2016+ Anton Matveenko <antmat@me.com>
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

from collections import defaultdict
from datetime import datetime
import json

from tornado import gen

from cocaine.tools.error import ToolsError


class LoggingConfigurator(object):
    def __init__(self, logging_service):
        self.logging_service = logging_service

    @gen.coroutine
    def execute(self):
        raise NotImplementedError


class LoggingConfigListLoggers(LoggingConfigurator):
    @gen.coroutine
    def execute(self):
        channel = yield self.logging_service.list_loggers()
        filters = yield channel.rx.get()
        raise gen.Return({"loggers": filters})


class LoggingConfigSetFilter(LoggingConfigurator):
    def __init__(self, logging_service, logger_name, filter_def, ttl):
        super(LoggingConfigSetFilter, self).__init__(logging_service)
        if not logger_name:
            raise ToolsError("Logger name is required")
        if not filter_def:
            raise ToolsError("filter definition is required")
        if not ttl:
            raise ToolsError("filter TTL is required")
        self.name = logger_name
        try:
            self.filter_def = json.loads(filter_def)
        except:
            raise ToolsError("Filter definition is not parsable. It should be JSON array with at least one element")
        try:
            self.ttl = int(ttl)
        except:
            raise ToolsError("TTL should be numeric")
        if not isinstance(self.filter_def, (tuple, list)) or len(self.filter_def) == 0:
            raise ToolsError("Filter definition should be array and contain at least one element")

    @gen.coroutine
    def execute(self):
        channel = yield self.logging_service.set_filter(self.name, self.filter_def, self.ttl)
        filters = yield channel.rx.get()
        raise gen.Return(filters)


class LoggingConfigRemoveFilter(LoggingConfigurator):
    def __init__(self, logging_service, filter_id):
        super(LoggingConfigRemoveFilter, self).__init__(logging_service)
        if not filter_id:
            raise ToolsError("Filter id is required")
        self.filter_id = int(filter_id)

    @gen.coroutine
    def execute(self):
        channel = yield self.logging_service.remove_filter(self.filter_id)
        filters = yield channel.rx.get()
        raise gen.Return(filters)


class LoggingConfigListFilters(LoggingConfigurator):
    @gen.coroutine
    def execute(self):
        channel = yield self.logging_service.list_filters()
        filters = yield channel.rx.get()
        ret = defaultdict(list)
        for logger_name, filter_def, filter_id, deadline, disposition_id in filters:
            if disposition_id == 0:
                disposition = "local"
            elif disposition_id == 1:
                disposition = "clusterwide"
            else:
                disposition = "unknown disposition_id: {}".format(disposition_id)
            time = datetime.fromtimestamp(min(deadline, 2 ** 32))
            ret[logger_name].append({"id": filter_id, "deadline": str(time),
                                     "filter_definition": filter_def, "disposition": disposition})
        raise gen.Return(ret)


class LoggingConfigSetClusterFilter(LoggingConfigSetFilter):
    def __init__(self, logging_service, logger_name, filter_def, ttl):
        super(LoggingConfigSetClusterFilter, self).__init__(logging_service, logger_name, filter_def, ttl)

    @gen.coroutine
    def execute(self):
        channel = yield self.logging_service.set_cluster_filter(self.name, self.filter_def, self.ttl)
        filters = yield channel.rx.get()
        raise gen.Return(filters)
