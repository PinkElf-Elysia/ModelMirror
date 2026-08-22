#!/bin/sh
set -eu
cat > /workspace/src/useCommandPalette.ts <<'TS'
import { useEffect, useMemo, useState } from 'react'
import type { Command } from './types'
export function useCommandPalette(commands: Command[], query: string) {
  const visible = useMemo(() => commands.filter(item => item.label.toLowerCase().includes(query.toLowerCase())), [commands, query])
  const [selected, setSelected] = useState(0)
  useEffect(() => { const first = visible.findIndex(item => !item.disabled); setSelected(first < 0 ? 0 : first) }, [visible])
  const move = (delta: number) => { if (!visible.some(item => !item.disabled)) return; let next = selected; do { next = (next + delta + visible.length) % visible.length } while (visible[next].disabled); setSelected(next) }
  return { visible, selected, setSelected, move }
}
TS
cat > /workspace/src/CommandPalette.tsx <<'TS'
import { useEffect, useRef } from 'react'
import type { Command } from './types'
import { useCommandPalette } from './useCommandPalette'
export function CommandPalette({ open, query, commands, onClose }: { open: boolean; query: string; commands: Command[]; onClose(): void }) {
  const { visible, selected, setSelected, move } = useCommandPalette(commands, query)
  const buttons = useRef<Array<HTMLButtonElement | null>>([]); const opener = useRef<HTMLElement | null>(null)
  useEffect(() => { if (open) { opener.current = document.activeElement as HTMLElement; queueMicrotask(() => buttons.current[selected]?.focus()) } else { opener.current?.focus() } }, [open])
  useEffect(() => { if (open) buttons.current[selected]?.focus() }, [open, selected])
  if (!open) return null
  const invoke = () => { const command = visible[selected]; if (command && !command.disabled) { command.run(); onClose() } }
  return <div className="overlay"><div role="dialog" aria-modal="true" aria-labelledby="command-palette-title" onKeyDown={event => { if (event.key === 'Escape') onClose(); else if (event.key === 'ArrowDown') { event.preventDefault(); move(1) } else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1) } else if (event.key === 'Enter') { event.preventDefault(); invoke() } }}><h2 id="command-palette-title">Commands</h2><ul>{visible.map((command, index) => <li key={command.id}><button ref={node => { buttons.current[index] = node }} disabled={command.disabled} aria-selected={index === selected} onMouseEnter={() => !command.disabled && setSelected(index)} onClick={invoke}>{command.label}</button></li>)}</ul></div></div>
}
TS
