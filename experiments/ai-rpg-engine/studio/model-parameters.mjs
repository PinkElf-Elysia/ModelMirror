import {models} from './provider.mjs';
// The sole user-approved exception. Unknown models keep the card parameters.
export const parametersFor=model=>({...model==='openai/gpt-5.6-luna'?{max_tokens:16384}:models.earth.parameters});
export const sameParameters=(model,value)=>!!value&&JSON.stringify(Object.fromEntries(Object.entries(value).sort()))===JSON.stringify(Object.fromEntries(Object.entries(parametersFor(model)).sort()));
