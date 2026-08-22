import { useState } from 'react'
import { CommandPalette } from './CommandPalette'
import type { Command } from './types'

export function PaletteLauncher({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false)
  return <><button onClick={() => setOpen(true)}>Open commands</button><CommandPalette open={open} query="" commands={commands} onClose={() => setOpen(false)} /></>
}
