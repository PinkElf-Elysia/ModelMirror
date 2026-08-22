import type { Command } from './types'
import { useCommandPalette } from './useCommandPalette'

export function CommandPalette({ open, query, commands, onClose }: { open: boolean; query: string; commands: Command[]; onClose(): void }) {
  const { visible, selected, setSelected } = useCommandPalette(commands, query)
  if (!open) return null
  return <div className="overlay"><div><h2>Commands</h2><ul>{visible.map((command, index) => <li key={command.id}><button disabled={command.disabled} data-selected={index === selected} onMouseEnter={() => setSelected(index)} onClick={() => { command.run(); onClose() }}>{command.label}</button></li>)}</ul></div></div>
}
