#!/bin/sh

install_utility () {
    sudo apt-get update -qq && sudo apt-get install -qq devscripts build-essential equivs python-software-properties
}

build_cocaine () {
  git clone --recursive https://github.com/cocaine/cocaine-core.git -b v0.12
  cd cocaine-core
  # Travis has Cgroups unmounted
  echo "DEB_CMAKE_EXTRA_FLAGS=-DCOCAINE_ALLOW_CGROUPS=OFF" >> debian/rules
  yes | sudo mk-build-deps -i
  yes | debuild -uc -us
  cd .. && sudo dpkg -i *.deb || sudo apt-get install -f && rm -rf cocaine-core 
}

make_env () {
    echo "Install utility packages..."
    install_utility
    echo "Build & install packages..."
    build_cocaine
    echo "Waiting..."
    sleep 5
}

make_env
