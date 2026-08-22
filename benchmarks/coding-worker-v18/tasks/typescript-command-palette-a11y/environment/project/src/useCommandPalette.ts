import { useMemo, useState } from 'react'
import type { Command } from './types'

export function useCommandPalette(commands: Command[], query: string) {
  const visible = useMemo(() => commands.filter(item => item.label.toLowerCase().includes(query.toLowerCase())), [commands, query])
  const [selected, setSelected] = useState(0)
  return { visible, selected, setSelected }
}
