import React from 'react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useOptimisticTodos } from '../src/useOptimisticTodos'
describe('todos', () => { it('publishes one successful update', async () => { const save = async (todo: any) => todo; const { result } = renderHook(() => useOptimisticTodos([{ id: '1', title: 'one', done: false }], save)); await act(async () => result.current.update('1', { done: true })); expect(result.current.todos[0].done).toBe(true) }) })
