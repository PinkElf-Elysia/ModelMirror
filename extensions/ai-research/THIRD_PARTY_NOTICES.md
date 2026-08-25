# Third-Party Notices

ModelMirror AI Research is a ModelMirror optional module. The module name and product presentation do not replace the license obligations of the software distributed inside its optional images.

## Direct runtime components

| Component | Fixed version | License | Upstream |
| --- | --- | --- | --- |
| Inspect AI | 0.3.260 | MIT | https://github.com/UKGovernmentBEIS/inspect_ai |
| MLflow | 3.15.1 | Apache-2.0 | https://github.com/mlflow/mlflow |
| FastAPI | resolved and hash-locked with the control image | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | resolved and hash-locked with the control image | BSD-3-Clause | https://github.com/encode/uvicorn |
| Python | 3.12.13 | PSF-2.0 | https://www.python.org/ |

## Research Console runtime components

| Component | Fixed version | License | Upstream |
| --- | --- | --- | --- |
| React | 19.2.7 | MIT | https://github.com/facebook/react |
| React Router DOM | 6.30.4 | MIT | https://github.com/remix-run/react-router |
| Lucide React | 1.27.0 | ISC | https://github.com/lucide-icons/lucide |

The console build toolchain is also locked in `ui/package-lock.json`. Its complete
machine-readable license inventory is distributed as
`/usr/share/doc/modelmirror-ai-research/ui-build-inventory.json`; build-only
dependencies are not present in the final Python runtime image.

The machine-readable source lock, npm lock, and hash-locked requirement files are the authoritative component inventory. The build must fail if a dependency has missing license metadata or falls outside the reviewed allowlist. Runtime license texts are copied into `/usr/share/doc/modelmirror-ai-research/licenses` in the optional images.

No upstream project listed here is represented as a ModelMirror-authored scientific method, benchmark, scorer, or model integration.
