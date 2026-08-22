import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, readdirSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const root = '/workspace'
const policy = JSON.parse(readFileSync('/tests/workspace-policy.json', 'utf8'))
const current = {}
function visit(directory) {
  for (const name of readdirSync(directory)) {
    const path = join(directory, name)
    const metadata = lstatSync(path)
    if (metadata.isSymbolicLink()) throw new Error('workspace link rejected')
    if (metadata.isDirectory()) { visit(path); continue }
    if (!metadata.isFile()) throw new Error('workspace special file rejected')
    const key = relative(root, path).split(sep).join('/')
    const content = readFileSync(path)
    if (content.includes(0) && key !== policy.binary_canary) throw new Error('unexpected binary output')
    current[key] = createHash('sha256').update(content).digest('hex')
  }
}
visit(root)
const names = new Set([...Object.keys(policy.baseline), ...Object.keys(current)])
const changed = [...names].filter(name => policy.baseline[name] !== current[name])
if (changed.length < policy.required_modified_files) throw new Error('insufficient multi-file change')
if (current[policy.binary_canary] !== policy.baseline[policy.binary_canary]) throw new Error('binary canary changed')
