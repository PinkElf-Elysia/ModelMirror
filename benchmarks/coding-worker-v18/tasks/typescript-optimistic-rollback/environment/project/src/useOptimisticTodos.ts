import { useState } from 'react'
import type { SaveTodo, Todo } from './types'

export function useOptimisticTodos(initial: Todo[], save: SaveTodo) {
  const [todos, setTodos] = useState(initial)
  const update = async (id: string, patch: Partial<Omit<Todo, 'id'>>) => {
    const before = todos
    const optimistic = todos.map(item => item.id === id ? { ...item, ...patch } : item)
    setTodos(optimistic)
    try {
      const saved = await save(optimistic.find(item => item.id === id)!)
      setTodos(current => current.map(item => item.id === id ? saved : item))
    } catch (error) {
      setTodos(before)
      throw error
    }
  }
  return { todos, update }
}
