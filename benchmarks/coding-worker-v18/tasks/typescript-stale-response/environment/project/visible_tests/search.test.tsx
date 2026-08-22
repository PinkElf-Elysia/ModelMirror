import React from 'react'
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProfileSearch } from '../src/ProfileSearch'

describe('ProfileSearch', () => {
  it('renders the latest completed response', async () => {
    const fetchProfiles = async (query: string) => [{ id: query, label: query.toUpperCase() }]
    render(<ProfileSearch query="ada" fetchProfiles={fetchProfiles} />)
    await act(async () => {})
    expect(screen.getByText('ADA')).toBeTruthy()
  })
})
