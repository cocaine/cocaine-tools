#
#    Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
#    Copyright (c) 2011-2013 Other contributors as noted in the AUTHORS file.
#
#    This file is part of Cocaine.
#
#    Cocaine is free software; you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    Cocaine is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#

from tornado import template

dockerchef = template.Template("""
FROM {{ basecontainer }}
RUN mkdir -p /tmp/chef
ADD ./{{ cookbooks }} /tmp/chef/
ADD ./solo.rb /tmp/chef/
ADD ./solo.json /tmp/chef/
RUN ls -l /tmp/chef/
# install chef-solo
RUN apt-get install curl -y
RUN curl -L https://www.opscode.com/chef/install.sh | bash
RUN chef-solo -c /tmp/chef/solo.rb -j /tmp/chef/solo.json
""")

dockerpuppet = template.Template("""
FROM {{ basecontainer }}
RUN mkdir -p /tmp/puppet

ADD ./puppet /tmp/puppet

RUN ls -l /tmp/puppet

# install puppet
RUN apt-get install puppet -y

# Copy modules
RUN if [ -d /tmp/puppet/modules ]; then \
        echo "Copy Puppet modules into /etc/puppet/modules/";\
        cp -Rv /tmp/puppet/modules/* /etc/puppet/modules/ ;\
    fi

# Apply manifest
RUN echo "Apply cocaine.pp manifest"
RUN puppet apply /tmp/puppet/cocaine.pp --modulepath=/etc/puppet/modules
""")
