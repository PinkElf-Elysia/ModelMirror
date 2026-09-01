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

The official SPDX SBOM contains 438 packages. It records 100 declared GPL/LGPL
matches, 60 packages whose declared license is `NOASSERTION`, and 416 packages
whose concluded license is `NOASSERTION`. The last number does not mean that 416
licenses are unknown: SPDX permits `NOASSERTION` when the producer did not make a
license conclusion. Cross-checking declared and concluded values leaves 38
packages with no declared or concluded license assertion. Exact counts, the SBOM
hash, and the distribution-mode decision are recorded in `source-lock.json` and
`LDR_LICENSE_DISPOSITION.md`.

Use that pulls the exact public upstream digest is allowed with these notices.
Mirroring, offline bundling, modifying, or distributing the image under a
ModelMirror registry remains blocked until package-level obligations and the 38
effective unknowns are disposed.

## Qualification-only locked upstream references

| Component | Fixed source | License | Upstream |
| --- | --- | --- | --- |
| Microsoft ResearchStudio IdeaSpark source subset | commit `a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a` | MIT | https://github.com/microsoft/ResearchStudio |
| NoviScl AI-Researcher prompt/schema subset | commit `e5dd05a90bcadb436c07283c2f429367c6e525d3` | MIT | https://github.com/NoviScl/AI-Researcher |

These are source-locked qualification references, not enabled product runtime
capabilities. The post-coherence ResearchStudio contracts remain inactive, and
the AI-Researcher provider client and experiment execution surfaces are excluded.

## P2R connector qualification environment

The 17-wheel P2R connector dependency set is qualification-only and is not
copied into the Control or Worker images. The P2R adapter source is present in
the optional Worker image but remains disabled under the P2R NO-GO boundary.
The launcher constructs a temporary wheel view from exactly the 17 hashes in
`worker/p2r-connectors-linux-x86_64.requirements.lock`; unlocked files present
in a local download cache are not mounted into the qualification container.

| Component | Fixed version | METADATA field | Raw declared value |
| --- | --- | --- | --- |
| certifi | 2026.7.22 | `License` | `MPL-2.0` |
| charset-normalizer | 3.5.1 | `License` | `MIT` |
| Deprecated | 1.3.1 | `License` | `MIT` |
| editdistance | 0.8.1 | `License` | `MIT` |
| feedparser | 6.0.14 | `License` | `BSD-2-Clause` |
| feedparser-sgmllib | 2.1.0 | `License-Expression` | `PSF-2.0` |
| future | 1.0.0 | `License` | `MIT` |
| idna | 3.19 | `License-Expression` | `BSD-3-Clause` |
| openreview-py | 2.5.1 | `License` | `MIT` |
| pycryptodome | 3.23.0 | `License` | `BSD, Public Domain` |
| PyJWT | 2.13.0 | `License-Expression` | `MIT` |
| pylatexenc | 2.11 | `License` | `MIT` |
| requests | 2.34.2 | `License` | `Apache-2.0` |
| tld | 0.13.2 | `License-Expression` | `MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later` |
| tqdm | 4.70.0 | `License` | `MPL-2.0 AND MIT` |
| urllib3 | 2.7.0 | `License-Expression` | `MIT` |
| wrapt | 2.3.0 | `License-Expression` | `BSD-2-Clause` |

The locked set has no missing license metadata. It does contain known copyleft
choices, most notably `tld`; therefore it is approved only for the local,
ephemeral qualification run. The wheel set is not a redistribution candidate.
Bundling, mirroring, or publishing it requires a separate obligations review and
the corresponding license texts. PyMuPDF is intentionally absent from the lock
and is not installed or mounted by the qualification launcher.

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
