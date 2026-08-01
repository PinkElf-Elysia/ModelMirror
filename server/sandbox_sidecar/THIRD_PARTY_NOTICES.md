# ModelMirror Sandbox Third-Party Notices

The Sandbox sidecar is an independent ModelMirror implementation. No Xpert
or Dify source code is copied into this image.

Runtime components are distributed under their respective licenses:

- CPython: Python Software Foundation License.
- Node.js: MIT License; the official Node container also carries notices for
  its bundled dependencies.
- npm and npx: Artistic License 2.0.
- ripgrep: The Unlicense OR MIT License.
- Git: GNU General Public License version 2. Git is invoked only as a separate
  command-line program for local workspace content; ModelMirror does not link
  against or copy Git source code.
- Debian base packages retain their package license files under
  `/usr/share/doc`.

The Landlock integration calls the Linux kernel userspace ABI through Python
`ctypes`; it does not bundle a third-party Landlock library.
