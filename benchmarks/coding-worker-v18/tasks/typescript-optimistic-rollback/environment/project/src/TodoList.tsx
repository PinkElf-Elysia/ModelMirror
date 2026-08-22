import type { SaveTodo, Todo } from './types'
import { useOptimisticTodos } from './useOptimisticTodos'
export function TodoList({ initial, save }: { initial: Todo[]; save: SaveTodo }) { const { todos, update } = useOptimisticTodos(initial, save); return <ul>{todos.map(item => <li key={item.id}><span>{item.title}:{String(item.done)}</span><button onClick={() => void update(item.id, { done: !item.done })}>toggle {item.id}</button></li>)}</ul> }
