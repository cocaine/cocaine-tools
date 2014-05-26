%{!?python_sitelib: %global python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print (get_python_lib())")}
Name:		cocaine-tools	
Version:	0.11.5.0
Release:	1%{?dist}
Summary:	Cocaine - Toolset

Group:		Development/Libraries
License:	LGPLv3
URL:		http://reverbrain.com
Source0:	http://repo.reverbrain.com/sources/%{name}/%{name}-%{version}.tar.bz2
BuildRoot:	%{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
BuildArch:	noarch

BuildRequires:	python-devel
BuildRequires:	python-setuptools

Requires:	cocaine-framework-python >= 0.11.0.0
Requires:	python-msgpack
Requires:	python-opster >= 4.0
Requires:   python-tornado >= 3.1

%description
Various tools to query and manipulate running Cocaine instances.


%prep
%setup -q -n %{name}-%{version}


%build

%install
rm -rf %{buildroot}

python setup.py install --root=%{buildroot}


%clean
rm -rf %{buildroot}


%files
%defattr(-,root,root,-)
%doc README* LICENSE
%{python_sitelib}/*
%{_bindir}/*

%changelog
* Mon May 26 2014 Evgeny Safronov <division494@gmail.com> - 0.11.5.0
- Feature: Added `name` optional parameter for `cocaine-tool info`.

* Mon Feb 03 2014 Evgeny Safronov <division494@gmail.com> - 0.11.3.2
- Feature: Crashlog clean tool.

* Thu Jan 31 2014 Evgeny Safronov <division494@gmail.com> - 0.11.3.1
- Feature: Bash completion support for group tools.

* Thu Dec 03 2013 Anton Tyurin <noxiouz@yandex-team.ru> - 0.11.0.1
- Initial build.
