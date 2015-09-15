#!/bin/sh

install_utility () {
    sudo apt-get update -qq && sudo apt-get install -qq devscripts build-essential equivs python-software-properties
}

build_cocaine () {
  git clone --recursive https://github.com/cocaine/cocaine-core.git -b v0.12
  cd cocaine-core
  # Travis has Cgroups unmounted
  yes | sudo mk-build-deps -i
  yes | debuild -uc -us
  cd .. && sudo dpkg -i *.deb || sudo apt-get install -f && rm -rf cocaine-core
}

build_node_service () {
  git clone --recursive https://github.com/cocaine/cocaine-plugins.git -b v0.12
  cd cocaine-plugins
  mkdir build && cd build
  cmake ../ -DCOCAINE_ALLOW_CGROUPS=OFF -DCACHE=OFF -DCHRONO=OFF -DDOCKER=OFF -DELASTICSEARCH=OFF -DIPVS=OFF -DMONGO=OFF -DURLFETCH=OFF -DGRAPHITE=OFF -DUNICORN=OFF &&\
  make && cp -v ./node/node.2* /usr/lib/cocaine/ &&\
  cd ../.. && sudo cp ci/cocaine-runtime.conf /etc/cocaine/ && rm -rf cocaine-plugins
  sudo service cocaine-runtime restart
}

make_env () {
    echo "Install utility packages..."
    install_utility
    echo "Build & install packages..."
    build_cocaine
    build_node_service
    echo "Waiting..."
    sleep 5
}

make_env
