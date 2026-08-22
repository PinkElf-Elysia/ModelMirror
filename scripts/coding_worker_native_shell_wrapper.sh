#!/bin/sh
set -eu

root=/tmp/modelmirror-native-opencode
tool_home=/tmp/modelmirror-native-tool-home
fault_command='python -m build_index'

if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then
    exit 126
fi

command=$2
chown -h -R -P 65534:65534 /workspace
if /usr/bin/setpriv \
    --reuid=65534 \
    --regid=65534 \
    --clear-groups \
    -- /usr/bin/env -i \
    HOME="$tool_home" \
    PATH=/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    /bin/sh -c "$command"
then
    status=0
else
    status=$?
fi

if [ "$status" -eq 0 ] && [ "$command" = "$fault_command" ] && [ -f "$root/fault.arm" ]; then
    umask 077
    printf '%s\n' 'command=python -m build_index' 'exit=0' > "$root/fault.result.tmp"
    mv "$root/fault.result.tmp" "$root/fault.result"
    while [ -f "$root/fault.arm" ]; do
        sleep 1
    done
fi

exit "$status"
