#!/bin/sh
set -eu
mv /workspace/acme/reporting /workspace/acme/analytics
python - <<'PY'
import json
from pathlib import Path
for relative in ('app.py', 'plugin_registry.py'):
    path = Path('/workspace') / relative
    path.write_text(path.read_text().replace('acme.reporting', 'acme.analytics'))
package = Path('/workspace/acme/__init__.py')
package.write_text(package.read_text().replace("'reporting'", "'analytics'"))
config = Path('/workspace/config/plugins.json')
payload = json.loads(config.read_text())
payload['report'] = payload['report'].replace('acme.reporting', 'acme.analytics')
config.write_text(json.dumps(payload, sort_keys=True) + '\n')
PY
