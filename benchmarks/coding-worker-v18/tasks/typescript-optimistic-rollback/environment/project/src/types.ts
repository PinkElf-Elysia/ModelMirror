export type Todo = { id: string; title: string; done: boolean }
export type SaveTodo = (todo: Todo) => Promise<Todo>

