#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    exec sudo sh "$script_dir/installLinux.sh" "$@"
fi
if [ "${1:-}" = uninstall ]; then
    if [ ! -x /usr/local/bin/npf-manager ]; then
        echo 'No registered NPF Server Manager installation was found.' >&2
        exit 1
    fi
    exec /usr/local/bin/npf-manager "$@"
fi
if [ "$(uname -m)" != x86_64 ]; then
    echo 'NPF Server Manager supports x64 only.' >&2
    exit 1
fi
. /etc/os-release
case " $ID ${ID_LIKE:-} " in
    *debian*|*ubuntu*)
        apt-get update
        apt-get -o DPkg::Lock::Timeout=300 install -y python3 python3-venv python3-pip openssl ca-certificates
        ;;
    *fedora*|*rhel*|*centos*)
        dnf install -y python3 python3-pip openssl ca-certificates
        ;;
    *) echo 'Supported Linux families: Debian and Fedora (systemd required).' >&2; exit 1 ;;
esac
if ! command -v systemctl >/dev/null 2>&1; then
    echo 'systemd is required.' >&2; exit 1
fi
exec python3 "$script_dir/installer.py" "$@"
