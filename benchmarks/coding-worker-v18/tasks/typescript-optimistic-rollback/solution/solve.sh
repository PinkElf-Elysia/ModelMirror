#!/bin/sh
set -eu
cat > /workspace/src/types.ts <<'TS'
export type Todo = { id: string; title: string; done: boolean }
export type SaveTodo = (todo: Todo) => Promise<Todo>
export type Mutation = { token: number; id: string; before: Todo; optimistic: Todo }
TS
cat > /workspace/src/useOptimisticTodos.ts <<'TS'
import { useRef, useState } from 'react'
import type { Mutation, SaveTodo, Todo } from './types'
export function useOptimisticTodos(initial: Todo[], save: SaveTodo) {
  const [todos, setTodos] = useState(initial); const current = useRef(initial); const counter = useRef(0); const owners = useRef(new Map<string, number>())
  const publish = (next: Todo[]) => { current.current = next; setTodos(next) }
  const update = async (id: string, patch: Partial<Omit<Todo, 'id'>>) => {
    const before = current.current.find(item => item.id === id); if (!before) throw new Error('todo not found')
    const mutation: Mutation = { token: ++counter.current, id, before, optimistic: { ...before, ...patch } }; owners.current.set(id, mutation.token)
    publish(current.current.map(item => item.id === id ? mutation.optimistic : item))
    try { const saved = await save(mutation.optimistic); if (owners.current.get(id) === mutation.token) { owners.current.delete(id); publish(current.current.map(item => item.id === id ? saved : item)) } }
    catch (error) { if (owners.current.get(id) === mutation.token) { owners.current.delete(id); publish(current.current.map(item => item.id === id ? mutation.before : item)) } throw error }
  }
  return { todos, update }
}
TS
