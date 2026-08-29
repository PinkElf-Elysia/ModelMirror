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
| Local Deep Research project | 1.10.6 | MIT | https://github.com/LearningCircuit/local-deep-research |
| all-MiniLM-L6-v2 model asset | revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` | Apache-2.0 | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 |

The Local Deep Research project source is MIT. Its official container image is an
aggregate work containing the project plus operating-system and Python packages,
so the image is not represented as MIT-only. The optional service pulls the
unmodified public image by its exact upstream digest. ModelMirror does not build,
mirror, bundle, or publish that image.

The official SPDX SBOM contains 436 packages. It records 100 declared GPL/LGPL
matches, 60 packages whose declared license is `NOASSERTION`, and 413 packages
whose concluded license is `NOASSERTION`. The last number does not mean that 413
licenses are unknown: SPDX permits `NOASSERTION` when the producer did not make a
license conclusion. Cross-checking declared and concluded values leaves 37
packages with no declared or concluded license assertion. Exact counts, the SBOM
hash, and the distribution-mode decision are recorded in `source-lock.json` and
`LDR_LICENSE_DISPOSITION.md`.

Use that pulls the exact public upstream digest is allowed with these notices.
Mirroring, offline bundling, modifying, or distributing the image under a
ModelMirror registry remains blocked until package-level obligations and the 37
effective unknowns are disposed.

## Research Console runtime components

| Component | Fixed version | License | Upstream |
| --- | --- | --- | --- |
| React | 19.2.7 | MIT | https://github.com/facebook/react |
| React Router DOM | 6.30.4 | MIT | https://github.com/remix-run/react-router |
| Lucide React | 1.27.0 | ISC | https://github.com/lucide-icons/lucide |
| React Markdown | 10.1.0 | MIT | https://github.com/remarkjs/react-markdown |
| remark-gfm | 4.0.1 | MIT | https://github.com/remarkjs/remark-gfm |

The console build toolchain is also locked in `ui/package-lock.json`. Its complete
machine-readable license inventory is distributed as
`/usr/share/doc/modelmirror-ai-research/ui-build-inventory.json`; build-only
dependencies are not present in the final Python runtime image.

The machine-readable source lock, npm lock, and hash-locked requirement files are the authoritative component inventory. The build must fail if a dependency has missing license metadata or falls outside the reviewed allowlist. Runtime license texts are copied into `/usr/share/doc/modelmirror-ai-research/licenses` in the optional images.

No upstream project listed here is represented as a ModelMirror-authored scientific method, benchmark, scorer, or model integration.
