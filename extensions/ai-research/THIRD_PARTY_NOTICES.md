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

The machine-readable source lock and hash-locked requirement files are the authoritative component inventory. The build must fail if a dependency has missing license metadata or falls outside the reviewed allowlist. Full license texts are copied into `/usr/share/doc/modelmirror-ai-research/licenses` in the optional images.

No upstream project listed here is represented as a ModelMirror-authored scientific method, benchmark, scorer, or model integration.
