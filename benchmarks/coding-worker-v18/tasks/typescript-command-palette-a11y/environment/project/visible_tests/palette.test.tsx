import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PaletteLauncher } from '../src/PaletteLauncher'

describe('palette', () => {
  it('opens and invokes a command by click', () => {
    const run = vi.fn(); render(<PaletteLauncher commands={[{ id: 'one', label: 'One', run }]} />)
    fireEvent.click(screen.getByText('Open commands')); fireEvent.click(screen.getByText('One'))
    expect(run).toHaveBeenCalledOnce()
  })
})
